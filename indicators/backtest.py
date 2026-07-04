"""
Бэктест-фреймворк -- проверяет на исторических свечах, есть ли у Score
(0-100 из indicators.scoring.calculate_score) хоть какая-то предсказательная
сила, или это просто взвешенная сумма индикаторов без реального edge.

Три уровня проверки, от простого к честному:

1. backtest_score_signal()   -- одна пара порогов (buy/sell) на одном
   непрерывном отрезке истории. Быстрый первый взгляд, но легко обмануться:
   одна прибыльная комбинация чисел на одном отрезке ничего не доказывает.

2. grid_search_thresholds()  -- перебирает МНОГО комбинаций порогов на том
   же отрезке. Если прибыльна только одна случайно подобранная пара, а
   вокруг сплошные минусы -- это переобучение на конкретные слайдеры, а не
   реальный edge.

3. walk_forward_validation() -- делит историю на несколько последовательных
   периодов и тестирует ОДНИ И ТЕ ЖЕ (фиксированные) пороги на каждом
   отдельно. Показывает, работает ли сигнал стабильно в разных рыночных
   условиях (тренд/флэт/разные фазы), а не только в одном периоде, который
   случайно попал в общий бэктест.

Это всё ещё НЕ профессиональный бэктестер (нет шортов, частичных позиций,
проскальзывания сверх фиксированной комиссии) -- цель дать быструю и
честную оценку "работает сигнал или нет", а не заменить полноценную
систему валидации стратегий.
"""
from dataclasses import dataclass, field
from typing import List, Optional

import pandas as pd

from indicators.scoring import compute_score_series


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


def _simulate(
    df: pd.DataFrame,
    score: pd.Series,
    buy_threshold: float,
    sell_threshold: float,
    fee_pct: float,
    warmup_bars: int,
) -> Optional[BacktestResult]:
    """
    Общее ядро симуляции long-only стратегии по уже посчитанному Score.
    Вынесено отдельно от backtest_score_signal(), чтобы grid_search_thresholds()
    мог считать Score ОДИН раз для всей истории и затем прогонять через
    десятки комбинаций порогов без повторного расчёта RSI/EMA/MACD на
    каждую комбинацию -- иначе grid-search на 100+ ячеек был бы неоправданно
    медленным.
    """
    if df is None or len(df) < warmup_bars + 20:
        return None

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


def backtest_score_signal(
    df: pd.DataFrame,
    buy_threshold: float = 70,
    sell_threshold: float = 30,
    fee_pct: float = 0.1,
    warmup_bars: int = 60,
) -> Optional[BacktestResult]:
    """
    Симулирует long-only стратегию по Score на исторических свечах: вход,
    когда Score поднимается выше buy_threshold, выход, когда падает ниже
    sell_threshold. Результат сравнивается с buy&hold за тот же период.

    fee_pct -- комиссия за сделку в процентах, списывается на входе и
    выходе (2 x fee_pct за круг), чтобы не показывать нереалистично
    хороший результат без учёта торговых издержек.
    warmup_bars -- сколько первых баров пропустить, пока EMA(50)/RSI не
    "разогреются" до стабильных значений.
    """
    if df is None or len(df) < warmup_bars + 20:
        return None
    score = compute_score_series(df)
    return _simulate(df, score, buy_threshold, sell_threshold, fee_pct, warmup_bars)


# ---------------------------------------------------------------------------
# Grid-search: много комбинаций порогов на одном отрезке истории
# ---------------------------------------------------------------------------

@dataclass
class GridCell:
    buy_threshold: int
    sell_threshold: int
    result: BacktestResult


def grid_search_thresholds(
    df: pd.DataFrame,
    buy_range=range(55, 91, 5),
    sell_range=range(10, 46, 5),
    fee_pct: float = 0.1,
    warmup_bars: int = 60,
    min_trades: int = 3,
) -> List[GridCell]:
    """
    Перебирает комбинации порогов buy/sell (buy всегда строго больше sell)
    и считает результат бэктеста для каждой. Score считается ОДИН раз для
    всей истории (см. _simulate), а не заново на каждую комбинацию.

    Комбинации с меньше min_trades сделок отбрасываются -- одна-две сделки
    ничего не доказывают, их прибыльность может быть чистой случайностью.

    Возвращает список ячеек, отсортированный по edge_vs_buy_hold (лучшие
    первыми). Используется, чтобы увидеть, есть ли РЕАЛЬНЫЙ edge у сигнала
    в широком диапазоне порогов, или прибыльна только одна случайно
    подобранная пара чисел (переобучение на конкретный слайдер).
    """
    if df is None or len(df) < warmup_bars + 20:
        return []

    score = compute_score_series(df)
    cells: List[GridCell] = []
    for buy_th in buy_range:
        for sell_th in sell_range:
            if sell_th >= buy_th:
                continue
            result = _simulate(df, score, buy_th, sell_th, fee_pct, warmup_bars)
            if result is not None and result.num_trades >= min_trades:
                cells.append(GridCell(buy_th, sell_th, result))

    cells.sort(key=lambda c: -c.result.edge_vs_buy_hold_pct)
    return cells


# ---------------------------------------------------------------------------
# Walk-forward: одни и те же пороги на нескольких последовательных периодах
# ---------------------------------------------------------------------------

@dataclass
class FoldResult:
    fold_index: int
    start_date: object
    end_date: object
    result: Optional[BacktestResult]


def _split_folds(df: pd.DataFrame, n_folds: int) -> List[pd.DataFrame]:
    """Делит df на n_folds последовательных НЕПЕРЕСЕКАЮЩИХСЯ сегментов по
    времени (без перемешивания -- порядок времени важен для честной
    проверки). Последний сегмент забирает остаток, если длина не делится
    ровно."""
    fold_size = len(df) // n_folds
    folds = []
    for i in range(n_folds):
        start = i * fold_size
        end = (i + 1) * fold_size if i < n_folds - 1 else len(df)
        folds.append(df.iloc[start:end])
    return folds


def walk_forward_validation(
    df: pd.DataFrame,
    buy_threshold: float = 70,
    sell_threshold: float = 30,
    fee_pct: float = 0.1,
    n_folds: int = 3,
    warmup_bars: int = 60,
) -> List[FoldResult]:
    """
    Делит историю на n_folds последовательных периодов и тестирует
    ФИКСИРОВАННЫЕ пороги (не подбирает их заново на каждом периоде -- это
    было бы подгонкой) на каждом отдельно. Пороги по умолчанию (70/30) --
    те же, что предлагаются пользователю как дефолт в UI, специально не
    оптимальные "задним числом" под конкретную монету.

    Если стратегия обгоняет buy&hold в большинстве периодов -- это куда
    более сильный аргумент в пользу реального edge, чем один хороший
    результат на всей истории целиком (где ранние прибыльные периоды могли
    просто "перевесить" поздние убыточные при суммарном подсчёте).
    """
    if df is None or len(df) < warmup_bars * n_folds + 20 * n_folds:
        return []

    folds = _split_folds(df, n_folds)
    out = []
    for i, fold in enumerate(folds):
        # На маленьком фолде warmup_bars может не влезть -- уменьшаем его
        # пропорционально вместо того, чтобы просто вернуть "недостаточно
        # данных" для всего периода.
        fold_warmup = min(warmup_bars, max(0, len(fold) - 21))
        result = backtest_score_signal(fold, buy_threshold, sell_threshold, fee_pct, warmup_bars=fold_warmup)
        out.append(FoldResult(
            fold_index=i,
            start_date=fold.index[0] if len(fold) else None,
            end_date=fold.index[-1] if len(fold) else None,
            result=result,
        ))
    return out