"""
Простой бэктест-фреймворк -- проверяет на исторических свечах, есть ли у
Score (0-100 из ui.metrics.calculate_score) хоть какая-то предсказательная
сила, или это просто взвешенная сумма индикаторов без реального edge.

Логика: считаем Score по всей истории векторизованно (RSI/EMA/MACD),
затем симулируем простую long-only стратегию -- вход, когда Score
поднимается выше buy_threshold, выход, когда падает ниже sell_threshold.
Результат сравнивается с buy&hold за тот же период.

Это НЕ профессиональный бэктестер (нет шортов, частичных позиций,
проскальзывания сверх фиксированной комиссии) -- цель дать быструю и
честную первую оценку "работает сигнал или нет", а не заменить
полноценную систему валидации стратегий.
"""
from dataclasses import dataclass, field
from typing import List, Optional

import pandas as pd
from ta.momentum import RSIIndicator
from ta.trend import EMAIndicator, MACD


@dataclass
class Trade:
    entry_date: object
    entry_price: float
    exit_date: object = None
    exit_price: float = None

    @property
    def return_pct(self) -> Optional[float]:
        if self.exit_price is None:
            return None
        return ((self.exit_price - self.entry_price) / self.entry_price) * 100


@dataclass
class BacktestResult:
    trades: List[Trade] = field(default_factory=list)
    total_return_pct: float = 0.0
    buy_hold_return_pct: float = 0.0
    win_rate_pct: Optional[float] = None
    max_drawdown_pct: float = 0.0
    num_trades: int = 0
    fee_pct: float = 0.1

    @property
    def edge_vs_buy_hold_pct(self) -> float:
        """Насколько стратегия обогнала (или отстала от) простого удержания."""
        return self.total_return_pct - self.buy_hold_return_pct


def _compute_score_series(df: pd.DataFrame) -> pd.Series:
    """
    Та же формула, что и в ui.metrics.calculate_score(), но векторизованная
    по всему датафрейму сразу (для скорости на длинной истории). Веса и
    клампы должны оставаться синхронизированы вручную с ui.metrics --
    при изменении весов Score там же поправь и здесь.
    """
    close = df["close"]
    rsi = RSIIndicator(close, window=14).rsi()
    ema_fast = EMAIndicator(close, window=20).ema_indicator()
    ema_slow = EMAIndicator(close, window=50).ema_indicator()
    macd_ind = MACD(close)
    macd_val, macd_sig = macd_ind.macd(), macd_ind.macd_signal()

    score = pd.Series(50.0, index=close.index)
    score = score + ((50 - rsi) * 0.5).clip(-25, 25)
    ema_diff_pct = ((ema_fast - ema_slow) / ema_slow) * 100
    score = score + (ema_diff_pct * 10).clip(-15, 15)
    price_diff_pct = ((close - ema_fast) / ema_fast) * 100
    score = score + (price_diff_pct * 5).clip(-10, 10)
    macd_diff_pct = ((macd_val - macd_sig) / close) * 100
    score = score + (macd_diff_pct * 50).clip(-15, 15)
    return score.clip(0, 100)


def backtest_score_signal(
    df: pd.DataFrame,
    buy_threshold: float = 70,
    sell_threshold: float = 30,
    fee_pct: float = 0.1,
    warmup_bars: int = 60,
) -> Optional[BacktestResult]:
    """
    Симулирует long-only стратегию по Score на исторических свечах.

    fee_pct -- комиссия за сделку в процентах, списывается на входе и
    выходе (2 x fee_pct за круг), чтобы не показывать нереалистично
    хороший результат без учёта торговых издержек.
    warmup_bars -- сколько первых баров пропустить, пока EMA(50)/RSI не
    "разогреются" до стабильных значений.
    """
    if df is None or len(df) < warmup_bars + 20:
        return None

    score = _compute_score_series(df)
    close = df["close"]

    result = BacktestResult(fee_pct=fee_pct)
    in_position = False
    entry_price = None
    entry_date = None
    equity = 1.0
    peak_equity = 1.0
    max_dd = 0.0

    for i in range(warmup_bars, len(df)):
        s = score.iloc[i]
        if pd.isna(s):
            continue
        price = close.iloc[i]
        date = df.index[i]

        if not in_position and s >= buy_threshold:
            in_position = True
            entry_price = price
            entry_date = date
        elif in_position and s <= sell_threshold:
            trade_return = ((price - entry_price) / entry_price) * 100 - 2 * fee_pct
            equity *= (1 + trade_return / 100)
            peak_equity = max(peak_equity, equity)
            max_dd = max(max_dd, (peak_equity - equity) / peak_equity * 100)
            result.trades.append(Trade(entry_date, entry_price, date, price))
            in_position = False

    if in_position:
        price, date = close.iloc[-1], df.index[-1]
        trade_return = ((price - entry_price) / entry_price) * 100 - 2 * fee_pct
        equity *= (1 + trade_return / 100)
        peak_equity = max(peak_equity, equity)
        max_dd = max(max_dd, (peak_equity - equity) / peak_equity * 100)
        result.trades.append(Trade(entry_date, entry_price, date, price))

    result.num_trades = len(result.trades)
    result.total_return_pct = (equity - 1) * 100
    result.buy_hold_return_pct = ((close.iloc[-1] - close.iloc[warmup_bars]) / close.iloc[warmup_bars]) * 100
    result.max_drawdown_pct = max_dd

    wins = [t for t in result.trades if t.return_pct is not None and t.return_pct > 0]
    result.win_rate_pct = (len(wins) / result.num_trades * 100) if result.num_trades else None

    return result