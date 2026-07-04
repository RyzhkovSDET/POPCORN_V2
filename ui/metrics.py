"""
Расчётная логика индикаторов -- всё, что превращает свечи (OHLCV) в цифры:
скоринг, детектор паттернов, мультитаймфрейм-прогноз, счётчики пробоев.

compute_slow_metrics() -- главная точка входа для watchlist: объединяет все
"медленные" метрики (RSI/MACD/ATR/Trend/Сигнал/Pattern/Мин/Макс) в один
закэшированный (ttl=SLOW_METRICS_TTL_SEC) вызов на тикер.
"""
import pandas as pd
import streamlit as st
from ta.momentum import RSIIndicator
from ta.trend import ADXIndicator, EMAIndicator, MACD
from ta.volatility import AverageTrueRange

from api.get_data import fetch_data_for_ticker
from indicators.signal_zones import atr_str, ema_str, macd_cell
from ui.config import (
    BREAKOUT_LOOKBACK_DAYS,
    DAILY_CANDLES_LIMIT,
    HTF_INTERVAL,
    HTF_LIMIT,
    PATTERN_LOOKBACK,
    SLOW_METRICS_CANDLES_LIMIT,
    SLOW_METRICS_INTERVAL,
    SLOW_METRICS_TTL_SEC,
    TREND_WINDOW_DEFAULT,
)
from ui.formatters import format_breakout_col, trend_arrow_30m


def calculate_score(rsi, price, ema_fast, ema_slow, macd_val, macd_sig) -> int:
    """Континуальный скор 0-100 из RSI, разрыва EMA, цены к EMA и MACD momentum."""
    score = 50.0
    score += max(-25, min(25, (50 - rsi) * 0.5))
    ema_diff_pct = ((ema_fast - ema_slow) / ema_slow) * 100
    score += max(-15, min(15, ema_diff_pct * 10))
    price_diff_pct = ((price - ema_fast) / ema_fast) * 100
    score += max(-10, min(10, price_diff_pct * 5))
    macd_diff_pct = ((macd_val - macd_sig) / price) * 100
    score += max(-15, min(15, macd_diff_pct * 50))
    return int(round(max(0, min(100, score))))


def detect_pattern(df: pd.DataFrame, lookback: int = PATTERN_LOOKBACK):
    """Лёгкий детектор свечных паттернов без TA-Lib. Возвращает (label, bias)."""
    recent = df.tail(lookback)
    if len(recent) < 5:
        return "-", "neutral"

    o, h, l, c = recent["open"], recent["high"], recent["low"], recent["close"]
    last_o, last_h, last_l, last_c = o.iloc[-1], h.iloc[-1], l.iloc[-1], c.iloc[-1]
    prev_o, prev_c = o.iloc[-2], c.iloc[-2]

    body = abs(last_c - last_o)
    range_ = max(last_h - last_l, 1e-9)
    upper_wick = last_h - max(last_c, last_o)
    lower_wick = min(last_c, last_o) - last_l

    if prev_c < prev_o and last_c > last_o and last_c > prev_o and last_o < prev_c:
        return "bull_engulf", "bull"
    if prev_c > prev_o and last_c < last_o and last_c < prev_o and last_o > prev_c:
        return "bear_engulf", "bear"
    if body / range_ < 0.35 and lower_wick > body * 2 and last_c <= c.min() * 1.01:
        return "hammer", "bull"
    if body / range_ < 0.35 and upper_wick > body * 2 and last_c >= c.max() * 0.99:
        return "shoot_star", "bear"
    if body / range_ < 0.1:
        return "doji", "neutral"

    mid = len(recent) // 2
    first_high, second_high = h.iloc[:mid].max(), h.iloc[mid:].max()
    first_low, second_low = l.iloc[:mid].min(), l.iloc[mid:].min()
    if second_high > first_high and second_low > first_low:
        return "hh_hl", "bull"
    if second_high < first_high and second_low < first_low:
        return "lh_ll", "bear"
    return "none", "neutral"


def get_forecast_score(ticker: str, interval: str = HTF_INTERVAL, limit: int = HTF_LIMIT):
    """
    Мультитаймфрейм-прогноз (горизонт настраивается в UI, см.
    ui.config.FORECAST_HORIZON_OPTIONS): EMA-тренд + RSI + ADX + паттерн --
    ВСЁ считается на одном и том же таймфрейме (interval).

    Короткие горизонты (2ч/4ч) считаются на 15-минутках. Длинные (6ч/12ч/1д)
    на 15м были бы слишком шумными для такого дальнего прогноза, поэтому
    вызывающий код (compute_slow_metrics) передаёт interval="1h" для них --
    более сглаженные данные, надёжнее для длинного горизонта.

    Паттерн детектируется на ТЕХ ЖЕ свечах (interval), что и остальные
    компоненты прогноза -- таймфреймы всегда согласованы, независимо от
    выбранного горизонта.
    """
    try:
        htf_df = fetch_data_for_ticker(ticker, interval=interval, limit=limit)
        if htf_df.empty or len(htf_df) < 50:
            return None

        close, high, low = htf_df["close"], htf_df["high"], htf_df["low"]
        ema_fast = EMAIndicator(close, window=20).ema_indicator().iloc[-1]
        ema_slow = EMAIndicator(close, window=50).ema_indicator().iloc[-1]
        rsi = RSIIndicator(close, window=14).rsi().iloc[-1]
        adx = ADXIndicator(high, low, close, window=14).adx().iloc[-1]
        _, pattern_bias = detect_pattern(htf_df)

        score = 50.0
        ema_gap_pct = ((ema_fast - ema_slow) / ema_slow) * 100
        score += max(-25, min(25, ema_gap_pct * 15))
        score += max(-15, min(15, (rsi - 50) * 0.4))
        price_vs_ema_pct = ((close.iloc[-1] - ema_fast) / ema_fast) * 100
        score += max(-10, min(10, price_vs_ema_pct * 6))

        strength = min(adx / 40, 1.0)
        score = 50 + (score - 50) * strength

        pattern_component = {"bull": 8, "bear": -8, "neutral": 0}.get(pattern_bias, 0)
        score += pattern_component

        return max(0, min(100, score))
    except Exception:
        return None


def compute_break_counters(daily_df: pd.DataFrame, lookback_days: int = BREAKOUT_LOOKBACK_DAYS):
    """Счётчик подряд идущих пробоев мин/макс за последние lookback_days дней."""
    if daily_df is None or daily_df.empty or len(daily_df) < 2:
        return None
    recent = daily_df.tail(lookback_days + 1)
    if len(recent) < 2:
        return None

    running_low, running_high = recent["low"].iloc[0], recent["high"].iloc[0]
    min_count, max_count = 0, 0
    min_date, max_date = None, None

    for i in range(1, len(recent)):
        day, day_date = recent.iloc[i], recent.index[i]
        if day["high"] > running_high:
            max_count += 1
            min_count = 0
            max_date = day_date
            running_high = day["high"]
        elif day["low"] < running_low:
            min_count += 1
            max_count = 0
            min_date = day_date
            running_low = day["low"]

    return {
        "min_count": min_count, "min_date": min_date, "min_price": running_low if min_count else None,
        "max_count": max_count, "max_date": max_date, "max_price": running_high if max_count else None,
    }


def compute_volume_change_pct_hours(hourly_df: pd.DataFrame, hours: int = 1):
    """
    % изменения объёма в деньгах (qav): сумма последних `hours` завершённых
    часов против суммы `hours` часов перед этим. Последняя (текущая,
    незавершённая) свеча исключается.
    """
    if hourly_df is None or hourly_df.empty or "qav" not in hourly_df.columns:
        return None
    qav = pd.to_numeric(hourly_df["qav"], errors="coerce")
    completed = qav.iloc[:-1]
    if len(completed) < hours * 2 or completed.tail(hours * 2).isnull().any():
        return None
    recent_period = completed.iloc[-hours:].sum()
    prior_period = completed.iloc[-2 * hours:-hours].sum()
    if prior_period == 0:
        return None
    return ((recent_period - prior_period) / prior_period) * 100


@st.cache_data(ttl=SLOW_METRICS_TTL_SEC, show_spinner=False)
def compute_slow_metrics(
    ticker: str,
    interval: str = SLOW_METRICS_INTERVAL,
    trend_window: int = TREND_WINDOW_DEFAULT,
    forecast_interval: str = HTF_INTERVAL,
    forecast_limit: int = HTF_LIMIT,
):
    """
    Медленный блок метрик (RSI/MACD/ATR/Trend/Сигнал/Pattern/Мин/Макс) --
    пересчитывается не чаще раза в SLOW_METRICS_TTL_SEC секунд, даже если
    сама страница перерисовывается каждые REFRESH_SEC секунд ради цены.

    interval -- таймфрейм для RSI/EMA/MACD/ATR/Pattern/Trend, выбирается
    пользователем в UI (см. ui.config.SLOW_METRICS_TIMEFRAME_OPTIONS).
    trend_window -- сколько свечей назад сравнивать для столбца Trend
    (см. ui.config.TREND_WINDOW_OPTIONS).
    forecast_interval/forecast_limit -- таймфрейм и лимит свечей для
    столбца "Прогноз", зависят от выбранного горизонта (см.
    ui.config.FORECAST_HORIZON_OPTIONS) -- 2ч/4ч считаются на 15м, а
    6ч/12ч/1д на 1ч (менее шумно для дальнего горизонта).
    Все параметры входят в ключ кэша Streamlit автоматически (это просто
    аргументы функции), так что переключение любого из них в интерфейсе
    сразу тянет новые данные, а не старые закэшированные.
    """
    df = fetch_data_for_ticker(ticker, interval=interval, limit=SLOW_METRICS_CANDLES_LIMIT)
    if df.empty or len(df) < 20:
        return None

    close = df["close"]
    rsi = RSIIndicator(close, window=14).rsi().iloc[-1]
    ema_fast = EMAIndicator(close, window=20).ema_indicator().iloc[-1]
    ema_slow = EMAIndicator(close, window=50).ema_indicator().iloc[-1] if len(close) >= 50 else ema_fast
    macd_ind = MACD(close)
    macd_val, macd_sig = macd_ind.macd().iloc[-1], macd_ind.macd_signal().iloc[-1]
    atr = AverageTrueRange(df["high"], df["low"], close, window=14).average_true_range().iloc[-1]

    trend_col = trend_arrow_30m(df, window=trend_window)
    macd_col = macd_cell(macd_val, macd_sig, close.iloc[-1])
    ema_col = ema_str(ema_fast, ema_slow)
    atr_col = atr_str(atr, close.iloc[-1])
    score = calculate_score(rsi, close.iloc[-1], ema_fast, ema_slow, macd_val, macd_sig)
    pattern_name, pattern_bias = detect_pattern(df)
    forecast_score = get_forecast_score(ticker, interval=forecast_interval, limit=forecast_limit)

    try:
        daily_df = fetch_data_for_ticker(ticker, interval="1d", limit=DAILY_CANDLES_LIMIT)
    except Exception:
        daily_df = None

    break_counters = compute_break_counters(daily_df)
    breakout_col = format_breakout_col(break_counters)

    return {
        # готовые HTML-ячейки для отрисовки таблицы
        "trend_col": trend_col,
        "macd_col": macd_col,
        "ema_col": ema_col,
        "atr_col": atr_col,
        "breakout_col": breakout_col,
        # сырые значения -- нужны и для расчётов, и для текстового отчёта
        # "Копировать анализ" в боковой панели
        "rsi": rsi,
        "atr": atr,
        "ema_fast": ema_fast,
        "ema_slow": ema_slow,
        "macd_val": macd_val,
        "macd_sig": macd_sig,
        "score": score,
        "pattern_name": pattern_name,
        "pattern_bias": pattern_bias,
        "forecast_score": forecast_score,
        "break_counters": break_counters,
    }