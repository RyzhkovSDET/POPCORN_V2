"""
Единая формула Score (0-100) -- используется и в watchlist (ui/metrics.py),
и в бэктесте/grid-search/walk-forward (indicators/backtest.py).

РАНЬШЕ эта формула была продублирована в двух местах: скалярная версия
жила в ui/metrics.calculate_score(), а векторизованная -- отдельным кодом
в indicators/backtest._compute_score_series(), с комментарием "держать
в синхроне вручную". Это было хрупко: поправишь вес в одном месте --
бэктест начинает молча тестировать другую формулу, чем та, что реально
показана в таблице watchlist, и результаты расходятся без единой ошибки
в логах.

Теперь один источник истины:
- constants (веса и клампы) -- ровно одна копия;
- calculate_score() -- скалярная версия (один тикер, текущий момент);
- compute_score_series() -- та же формула, применённая к pd.Series целиком
  (для скорости на длинной истории в бэктесте).

Прогноз (ui.metrics.get_forecast_score) сюда сознательно НЕ включён -- у
него своя формула (ADX-множитель силы тренда, отдельный таймфрейм, вклад
свечного паттерна), это другой сигнал по дизайну, не альтернативная
реализация того же самого.
"""
import pandas as pd
from ta.momentum import RSIIndicator
from ta.trend import EMAIndicator, MACD

# Веса и клампы компонентов Score. Вынесены в константы (а не зашиты в тело
# функций), чтобы:
# 1) не иметь риска, что скалярная и векторная версия формулы разъедутся;
# 2) можно было быстро попробовать другие веса и прогнать
#    indicators.backtest.grid_search_thresholds / walk_forward_validation,
#    не редактируя саму логику в двух местах.
RSI_WEIGHT = 0.5
RSI_CLAMP = 25
EMA_GAP_WEIGHT = 10
EMA_GAP_CLAMP = 15
PRICE_EMA_WEIGHT = 5
PRICE_EMA_CLAMP = 10
MACD_WEIGHT = 50
MACD_CLAMP = 15


def calculate_score(rsi, price, ema_fast, ema_slow, macd_val, macd_sig) -> int:
    """Континуальный скор 0-100 из RSI, разрыва EMA, цены к EMA и MACD momentum.
    Скалярная версия -- один тикер, одна точка времени (текущий момент,
    используется в watchlist)."""
    score = 50.0
    score += max(-RSI_CLAMP, min(RSI_CLAMP, (50 - rsi) * RSI_WEIGHT))
    ema_diff_pct = ((ema_fast - ema_slow) / ema_slow) * 100
    score += max(-EMA_GAP_CLAMP, min(EMA_GAP_CLAMP, ema_diff_pct * EMA_GAP_WEIGHT))
    price_diff_pct = ((price - ema_fast) / ema_fast) * 100
    score += max(-PRICE_EMA_CLAMP, min(PRICE_EMA_CLAMP, price_diff_pct * PRICE_EMA_WEIGHT))
    macd_diff_pct = ((macd_val - macd_sig) / price) * 100
    score += max(-MACD_CLAMP, min(MACD_CLAMP, macd_diff_pct * MACD_WEIGHT))
    return int(round(max(0, min(100, score))))


def compute_score_series(df: pd.DataFrame) -> pd.Series:
    """
    Та же формула, что и calculate_score(), но векторизованная по всему
    датафрейму сразу -- для бэктеста/grid-search/walk-forward на длинной
    истории, где поэлементный вызов calculate_score() в питоновском цикле
    был бы заметно медленнее.

    Использует ровно те же константы (см. выше), что и calculate_score() --
    поправил вес там, он автоматически применяется и здесь, расхождение
    больше невозможно.
    """
    close = df["close"]
    rsi = RSIIndicator(close, window=14).rsi()
    ema_fast = EMAIndicator(close, window=20).ema_indicator()
    ema_slow = EMAIndicator(close, window=50).ema_indicator()
    macd_ind = MACD(close)
    macd_val, macd_sig = macd_ind.macd(), macd_ind.macd_signal()

    score = pd.Series(50.0, index=close.index)
    score = score + ((50 - rsi) * RSI_WEIGHT).clip(-RSI_CLAMP, RSI_CLAMP)
    ema_diff_pct = ((ema_fast - ema_slow) / ema_slow) * 100
    score = score + (ema_diff_pct * EMA_GAP_WEIGHT).clip(-EMA_GAP_CLAMP, EMA_GAP_CLAMP)
    price_diff_pct = ((close - ema_fast) / ema_fast) * 100
    score = score + (price_diff_pct * PRICE_EMA_WEIGHT).clip(-PRICE_EMA_CLAMP, PRICE_EMA_CLAMP)
    macd_diff_pct = ((macd_val - macd_sig) / close) * 100
    score = score + (macd_diff_pct * MACD_WEIGHT).clip(-MACD_CLAMP, MACD_CLAMP)
    return score.clip(0, 100)