import time
from datetime import datetime

import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from ta.momentum import RSIIndicator
from ta.trend import ADXIndicator, EMAIndicator, MACD
from ta.volatility import AverageTrueRange

from api.coingecko_data import fetch_global_volume
from api.futures_data import fetch_funding_rate, fetch_open_interest_change
from api.get_data import fetch_data_for_ticker, fetch_from_source, SOURCE_NAMES
from indicators.analysis import (
    GUIDES,
    calculate_risk_reward,
    fibonacci_levels,
    find_support_resistance,
    find_swing_range,
    nearest_levels,
    point_of_control,
)
from indicators.signal_zones import forecast_str, rsi_str, signal_str
from storage.coins_storage import add_coin, load_coins, remove_coin

st.set_page_config(layout="wide", page_title="POPCORN v2")

VALID_QUOTES = ("USDT", "BUSD", "BTC", "ETH")
EMOJI_SIZE_EM = 0.5  # значки в 2 раза мельче окружающего текста


def _mini(char: str) -> str:
    """Оборачивает символ (эмодзи/стрелку) в span с уменьшенным font-size."""
    if not char:
        return ""
    return f'<span style="font-size:{EMOJI_SIZE_EM}em">{char}</span>'


# Цвет текста, соответствующий эмодзи-зоне -- красим весь текст ячейки, не только значок
_EMOJI_COLOR = {"🟢": "#2ecc71", "🟡": "#f1c40f", "⚪": "inherit", "🔵": "#3498db", "🔴": "#e74c3c"}


def _colored(text: str, emoji: str) -> str:
    """Красит весь текст ячейки в цвет, соответствующий эмодзи-зоне."""
    color = _EMOJI_COLOR.get(emoji, "inherit")
    return f'<span style="color:{color}">{text}</span>'


# ---------------------------------------------------------------------------
# Session state
# ---------------------------------------------------------------------------
if "coins" not in st.session_state:
    st.session_state.coins = load_coins()
if "confirm_delete" not in st.session_state:
    st.session_state.confirm_delete = {}
if "delete_diagnostic" not in st.session_state:
    st.session_state.delete_diagnostic = None
if "selected_coin" not in st.session_state:
    st.session_state.selected_coin = None

# ---------------------------------------------------------------------------
# Константы
# ---------------------------------------------------------------------------
REFRESH_SEC = 10
DELETE_CONFIRM_WINDOW_SEC = 60  # отдельно от REFRESH_SEC -- rerun с кучей API-запросов может занять больше 10 сек
MAX_ROWS = 10
PATTERN_LOOKBACK = 30
HTF_INTERVAL = "15m"
HTF_LIMIT = 100
BREAKOUT_LOOKBACK_DAYS = 3
DAILY_CANDLES_LIMIT = 10
HOURLY_CANDLES_LIMIT = 52          # хватает на окно 24ч х 2 + запас
VOLUME_WINDOWS_HOURS = (1, 3, 6, 24)
ANALYSIS_LOOKBACK_DAYS = 90
FIBONACCI_SWING_DAYS = 30

COL_WIDTHS = [0.85, 0.8, 0.85, 1.0, 0.5, 0.85, 0.6, 0.65, 0.95,
              0.6, 0.6, 0.6, 0.65,
              0.95, 1.1, 1.1, 0.75, 0.95, 0.95, 1.0, 0.4]
COL_KEYS = ["Coin", "Price", "Δ %", "RSI", "Trend", "MACD", "ATR", "Volume", "Общий объём",
            "1ч", "3ч", "6ч", "24ч",
            "Funding", "OI", "Сигнал", "Pattern", "Мин", "Макс", "4ч Прогноз", ""]

# Столбцы, объединённые общим заголовком сверху (только объёмные окна)
COLUMN_GROUPS = {"1ч": "Объём", "3ч": "Объём", "6ч": "Объём", "24ч": "Объём"}

# Короткая справка на русском для подсказки при наведении на заголовок столбца
COLUMN_TOOLTIPS = {
    "Coin": "Название монеты. Клик открывает структурный анализ в боковой панели.",
    "Price": "Текущая цена последней 1-минутной свечи.",
    "Δ %": "Изменение цены в пределах загруженного окна (~100 последних минут).",
    "RSI": "Индекс относительной силы: <30 перепроданность (сигнал к покупке), >70 перекупленность (к продаже).",
    "Trend": "EMA20 выше EMA50 -- восходящий тренд, ниже -- нисходящий.",
    "MACD": "Разница EMA12/EMA26 против сигнальной линии. Зелёный -- бычий импульс.",
    "ATR": "Средняя волатильность в единицах цены за 14 периодов. Выше -- сильнее движения.",
    "Volume": "Объём последней свечи с той биржи, откуда сейчас берутся данные.",
    "Общий объём": "Объём торгов за последние 24 часа (скользящее окно). CoinGecko -> Coinbase -> Kraken по очереди.",
    "1ч": "Изменение объёма в деньгах за последний час относительно часа перед этим.",
    "3ч": "Изменение объёма в деньгах за последние 3 часа относительно предыдущих 3 часов.",
    "6ч": "Изменение объёма в деньгах за последние 6 часов относительно предыдущих 6 часов.",
    "24ч": "Изменение объёма в деньгах за последние 24 часа относительно предыдущих 24 часов.",
    "Funding": "Ставка финансирования по бессрочному фьючерсу. Контрарный сигнал перегрузки рынка, доступен только для фьючерсных пар.",
    "OI": "Открытый интерес по фьючерсу + тренд за час. Направление читай вместе со столбцом Δ%.",
    "Сигнал": "Внутренний скоринг 0-100: RSI + разрыв EMA + momentum MACD. Ориентир, не рекомендация.",
    "Pattern": "Направление обнаруженного свечного паттерна на 1-минутном таймфрейме.",
    "Мин": "Счётчик подряд идущих пробоев минимума цены за последние 3 дня.",
    "Макс": "Счётчик подряд идущих пробоев максимума цены за последние 3 дня.",
    "4ч Прогноз": "Мультитаймфрейм-прогноз: EMA/RSI/ADX на 15-минутках + текущий паттерн.",
    "": "",
}

BUY_HINTS = {
    "Coin": "", "Price": "", "Δ %": "рост",
    "RSI": "🟢 ПОК", "Trend": "🔼", "MACD": "🟢",
    "ATR": "", "Volume": "", "Общий объём": "",
    "1ч": "", "3ч": "", "6ч": "", "24ч": "рост вложений",
    "Funding": "🟢 шорты перегреты", "OI": "",
    "Сигнал": "🟢▲ ПОК", "Pattern": "🟢 ПОК",
    "Мин": "", "Макс": "новые хаи",
    "4ч Прогноз": "🟢▲ ПОК", "": "",
}
SELL_HINTS = {
    "Coin": "", "Price": "", "Δ %": "падение",
    "RSI": "🔴 ПРД", "Trend": "🔽", "MACD": "🔴",
    "ATR": "рост=риск", "Volume": "", "Общий объём": "",
    "1ч": "", "3ч": "", "6ч": "", "24ч": "отток денег",
    "Funding": "🔴 лонги перегреты", "OI": "",
    "Сигнал": "🔴▼ ПРД", "Pattern": "🔴 ПРД",
    "Мин": "новые лои", "Макс": "",
    "4ч Прогноз": "🔴▼ ПРД", "": "",
}

# ---------------------------------------------------------------------------
# CSS
# ---------------------------------------------------------------------------
st.markdown("""
<style>
    .block-container { padding-top: 1rem; padding-bottom: 1rem; max-width: 100%; }
    [data-testid="stHorizontalBlock"] { gap: 0.2rem; align-items: center; }
    [data-testid="column"] { padding: 1px 2px !important; }
    [data-testid="column"] p { font-size: 0.25rem; white-space: nowrap; margin-bottom: 0; }
    div.stButton > button { padding: 0px 6px; height: 20px; font-size: 0.5rem; min-height: 20px;
        white-space: nowrap !important; overflow: visible !important; }
    div[data-testid="stForm"] { padding: 0.6rem 0.8rem; }
    .quick-guide { font-size: 0.78em; line-height: 1.45; }
    .buy-hint { color: #2ecc71; font-size: 0.5rem; font-weight: 600; white-space: nowrap; }
    .sell-hint { color: #e74c3c; font-size: 0.5rem; font-weight: 600; white-space: nowrap; }
    .group-header { text-align: center; font-size: 0.69rem; font-weight: 700;
        color: rgba(255,255,255,0.55); text-transform: uppercase; letter-spacing: 0.03em; }
    .col-header { cursor: help; border-bottom: 1px dotted rgba(255,255,255,0.35); font-size: 0.69rem; }
    button[title^="Удалить"] {
        border-radius: 50% !important; width: 22px !important; padding: 0 !important;
        border: 1px solid rgba(231, 76, 60, 0.3) !important; transition: all 0.15s ease;
    }
    button[title^="Удалить"]:hover {
        background: rgba(231, 76, 60, 0.15) !important; border-color: #e74c3c !important; transform: scale(1.08);
    }
    button[title^="Подтвердить"] {
        border-radius: 50% !important; width: 22px !important; padding: 0 !important;
        border: 1px solid rgba(46, 204, 113, 0.5) !important; background: rgba(46, 204, 113, 0.1) !important;
        transition: all 0.15s ease;
    }
    button[title^="Подтвердить"]:hover {
        background: rgba(46, 204, 113, 0.22) !important; border-color: #2ecc71 !important; transform: scale(1.08);
    }
    button[title^="Открыть анализ"] { font-weight: 600 !important; text-decoration: underline; text-underline-offset: 3px; }
    .st-key-watchlist_table [data-testid="stHorizontalBlock"] {
        border-bottom: 1px solid rgba(255, 255, 255, 0.07); border-radius: 4px; transition: background 0.1s ease;
    }
    .st-key-watchlist_table [data-testid="stHorizontalBlock"]:nth-of-type(even) { background: rgba(255, 255, 255, 0.025); }
    .st-key-watchlist_table [data-testid="stHorizontalBlock"]:hover { background: rgba(255, 255, 255, 0.07); }
</style>
""", unsafe_allow_html=True)


# ---------------------------------------------------------------------------
# Хелперы -- watchlist
# ---------------------------------------------------------------------------

def normalize_ticker(raw: str) -> str:
    t = raw.strip().upper()
    if not t.endswith(VALID_QUOTES):
        t += "USDT"
    return t


def trend_arrow(ema_fast, ema_slow) -> str:
    if ema_fast > ema_slow:
        return _colored(_mini("🔼"), "🟢")
    elif ema_fast < ema_slow:
        return _colored(_mini("🔽"), "🔴")
    return _mini("➡️")


def pct_change_str(pct) -> str:
    dot = "🟢" if pct > 0 else "🔴" if pct < 0 else "⚪"
    sign = "+" if pct > 0 else ""
    return _colored(f"{_mini(dot)}{sign}{pct:.2f}%", dot)


def macd_signal_str(macd_val, macd_sig) -> str:
    dot = "🟢" if macd_val > macd_sig else "🔴"
    return _colored(f"{_mini(dot)}{macd_val:.4f}", dot)


def format_volume(vol) -> str:
    if vol is None:
        return "н/д"
    if vol >= 1_000_000:
        return f"{vol / 1_000_000:.2f}M"
    elif vol >= 1_000:
        return f"{vol / 1_000:.2f}K"
    return f"{vol:.2f}"


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


def pattern_str(bias: str, compact: bool = False) -> str:
    if bias == "bull":
        emoji, label = "🟢", "ПОК"
    elif bias == "bear":
        emoji, label = "🔴", "ПРД"
    else:
        emoji, label = "⚪", "НЕЙТ"
    if compact:
        return _mini(emoji)
    return _colored(f"{_mini(emoji)} {label}", emoji)


def get_4h_forecast_score(ticker: str, pattern_bias: str = "neutral"):
    """Мультитаймфрейм-прогноз на 4ч: EMA-тренд + RSI + ADX (15m) + паттерн (1m)."""
    try:
        htf_df = fetch_data_for_ticker(ticker, interval=HTF_INTERVAL, limit=HTF_LIMIT)
        if htf_df.empty or len(htf_df) < 50:
            return None

        close, high, low = htf_df["close"], htf_df["high"], htf_df["low"]
        ema_fast = EMAIndicator(close, window=20).ema_indicator().iloc[-1]
        ema_slow = EMAIndicator(close, window=50).ema_indicator().iloc[-1]
        rsi = RSIIndicator(close, window=14).rsi().iloc[-1]
        adx = ADXIndicator(high, low, close, window=14).adx().iloc[-1]

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

    return {"min_count": min_count, "min_date": min_date, "max_count": max_count, "max_date": max_date}


def format_break_col(count: int, date, kind: str, compact: bool = False) -> str:
    color = "🔴" if kind == "min" else "🟢"
    if not count:
        return _mini("⚪")
    if compact:
        return _mini(color)
    hours_str = "?"
    if date is not None:
        try:
            elapsed = datetime.utcnow() - date.to_pydatetime()
            hours_str = f"{int(elapsed.total_seconds() // 3600)}ч"
        except Exception:
            hours_str = "?"
    return _colored(f"{_mini(color)}×{count} ({hours_str})", color)


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


def format_volume_change(pct, compact: bool = False) -> str:
    dot = "🟢" if (pct or 0) > 0 else "🔴" if (pct or 0) < 0 else "⚪"
    if pct is None:
        return _mini("⚪") if compact else _mini("⚪") + " н/д"
    if compact:
        return _mini(dot)
    return _colored(f"{_mini(dot)} {pct:+.1f}%", dot)


def format_funding(rate, compact: bool = False) -> str:
    """Контрарный сигнал перегрузки рынка (не buy/sell): экстрим = риск разворота."""
    if rate is None:
        return _mini("⚪") if compact else _mini("⚪") + " н/д"
    if rate > 0.05:
        dot = "🔴"
    elif rate > 0.03:
        dot = "🟡"
    elif rate >= -0.01:
        dot = "⚪"
    elif rate >= -0.05:
        dot = "🔵"
    else:
        dot = "🟢"
    if compact:
        return _mini(dot)
    return _colored(f"{_mini(dot)} {rate:+.3f}%", dot)


def format_open_interest(oi, pct_change, compact: bool = False) -> str:
    if oi is None:
        return _mini("⚪") if compact else _mini("⚪") + " н/д"
    dot = "🟢" if (pct_change or 0) > 0 else "🔴" if (pct_change or 0) < 0 else "⚪"
    if compact:
        return _mini(dot)
    pct_str = f"{pct_change:+.1f}%" if pct_change is not None else "н/д"
    return _colored(f"{_mini(dot)}{format_volume(oi)} {pct_str}", dot)


def render_hint_row(hints_dict, css_class):
    cols = st.columns(COL_WIDTHS)
    for col, key in zip(cols, COL_KEYS):
        text = hints_dict.get(key, "")
        col.markdown(f"<span class='{css_class}'>{text}</span>" if text else "&nbsp;", unsafe_allow_html=True)


def render_group_header_row():
    """Строка над основным заголовком: общее название для группы столбцов (например 'Объём')."""
    cols = st.columns(COL_WIDTHS)
    prev_group = None
    for col, key in zip(cols, COL_KEYS):
        group = COLUMN_GROUPS.get(key)
        if group and group != prev_group:
            col.markdown(f"<div class='group-header'>{group}</div>", unsafe_allow_html=True)
        else:
            col.markdown("&nbsp;", unsafe_allow_html=True)
        prev_group = group


def render_column_header_row():
    cols = st.columns(COL_WIDTHS)
    for col, key in zip(cols, COL_KEYS):
        tooltip = COLUMN_TOOLTIPS.get(key, "")
        if key and tooltip:
            col.markdown(f"<span class='col-header' title='{tooltip}'><strong>{key}</strong></span>", unsafe_allow_html=True)
        else:
            col.markdown(f"**{key}**")


# ---------------------------------------------------------------------------
# Хелперы -- боковая панель анализа
# ---------------------------------------------------------------------------

def _render_analysis_body(daily_df: pd.DataFrame):
    """Общее тело анализа (используется внутри каждой вкладки-биржи)."""
    if daily_df.empty or len(daily_df) < 10:
        st.warning("Недостаточно дневной истории для анализа (нужно минимум ~10 дней).")
        return

    price = float(daily_df["close"].iloc[-1])
    st.metric("Текущая цена", f"{price:,.4f}")

    support, resistance = find_support_resistance(daily_df)
    near_support, near_resistance = nearest_levels(support, resistance)

    st.markdown("**📊 Поддержка / Сопротивление**", help=GUIDES["support_resistance"]["short"])
    if resistance:
        st.caption("Сопротивление (сверху вниз):")
        for lvl in reversed(resistance):
            st.markdown(f"🔴 {lvl.price:,.4f} &nbsp; _(сила {lvl.strength})_", unsafe_allow_html=True)
    if support:
        st.caption("Поддержка (сверху вниз):")
        for lvl in support:
            st.markdown(f"🟢 {lvl.price:,.4f} &nbsp; _(сила {lvl.strength})_", unsafe_allow_html=True)
    if not support and not resistance:
        st.caption("Уровни не найдены -- возможно, слишком мало истории.")
    with st.expander("ℹ️ Как пользоваться"):
        st.write(GUIDES["support_resistance"]["full"])

    st.divider()

    st.markdown("**⚖️ Risk / Reward**", help=GUIDES["risk_reward"]["short"])
    rr = calculate_risk_reward(price, near_support, near_resistance)
    col_a, col_b = st.columns(2)
    col_a.metric("Риск", f"{rr['risk_pct']:.1f}%" if rr["risk_pct"] is not None else "н/д")
    col_b.metric("Потенциал", f"{rr['reward_pct']:.1f}%" if rr["reward_pct"] is not None else "н/д")
    if rr["ratio"] is not None:
        ratio_color = "🟢" if rr["ratio"] >= 2 else "🟡" if rr["ratio"] >= 1.5 else "🔴"
        st.markdown(f"{ratio_color} Соотношение: **1 : {rr['ratio']:.2f}**")
    else:
        st.caption("Недостаточно уровней для расчёта соотношения.")
    with st.expander("ℹ️ Как пользоваться"):
        st.write(GUIDES["risk_reward"]["full"])

    st.divider()

    st.markdown("**🌀 Fibonacci**", help=GUIDES["fibonacci"]["short"])
    swing_low, swing_high = find_swing_range(daily_df, lookback=FIBONACCI_SWING_DAYS)
    if swing_low is not None and swing_high is not None and swing_high > swing_low:
        fib = fibonacci_levels(swing_low, swing_high)
        for label, level_price in fib.items():
            marker = "👉" if abs(level_price - price) / price < 0.01 else "  "
            st.markdown(f"{marker} {label}: {level_price:,.4f}")
    else:
        st.caption("Недостаточно данных для расчёта диапазона.")
    with st.expander("ℹ️ Как пользоваться"):
        st.write(GUIDES["fibonacci"]["full"])

    st.divider()

    st.markdown("**📶 Объёмный профиль**", help=GUIDES["volume_profile"]["short"])
    poc = point_of_control(daily_df)
    if poc is not None:
        direction = "выше" if poc > price else "ниже"
        st.markdown(f"POC (макс. объём): **{poc:,.4f}** ({direction} текущей цены)")
    else:
        st.caption("Недостаточно данных для объёмного профиля.")
    with st.expander("ℹ️ Как пользоваться"):
        st.write(GUIDES["volume_profile"]["full"])


def render_analysis_sidebar():
    with st.sidebar:
        st.header("🔍 Анализ монеты")
        ticker = st.session_state.selected_coin

        if not ticker:
            st.info("Кликни на монету в таблице (по названию), чтобы увидеть структурный анализ.")
            return

        st.subheader(ticker.replace("USDT", ""))
        st.caption("Один и тот же расчёт по данным разных бирж -- удобно сравнить, совпадают ли уровни.")

        tabs = st.tabs(SOURCE_NAMES)
        for tab, source_name in zip(tabs, SOURCE_NAMES):
            with tab:
                try:
                    daily_df = fetch_from_source(source_name, ticker, interval="1d", limit=ANALYSIS_LOOKBACK_DAYS)
                except Exception as e:
                    st.warning(f"{source_name} недоступен для {ticker}: {e}")
                    continue
                _render_analysis_body(daily_df)

        st.divider()
        if st.button("✖ Закрыть анализ", key="close_analysis"):
            st.session_state.selected_coin = None
            st.rerun()


# ---------------------------------------------------------------------------
# UI: заголовок + форма добавления
# ---------------------------------------------------------------------------

st.title("🍿 POPCORN v2")

with st.form("add_coin_form", clear_on_submit=True):
    form_cols = st.columns([4, 1])
    raw_ticker = form_cols[0].text_input("➕ Добавить монету (например btc, ETH, solusdt)", "")
    form_cols[1].write("")
    submitted = form_cols[1].form_submit_button("Добавить")
    if submitted and raw_ticker.strip():
        ticker_clean = normalize_ticker(raw_ticker)
        success, message = add_coin(ticker_clean)
        if success:
            st.session_state.coins = load_coins()
            st.success(f"{ticker_clean}: {message}")
        else:
            st.warning(f"{ticker_clean}: {message}")

# ---------------------------------------------------------------------------
# Watchlist
# ---------------------------------------------------------------------------

st.subheader("📋 Watchlist")
compact_mode = st.checkbox("Компактный режим (столбцы-сигналы -> только цветной значок)", value=False)

if st.session_state.delete_diagnostic:
    st.warning(st.session_state.delete_diagnostic)
    st.session_state.delete_diagnostic = None

watchlist = []
failed_tickers = []
for ticker in st.session_state.coins:
    try:
        df = fetch_data_for_ticker(ticker)
        if df.empty or len(df) < 20:
            continue

        close, volume = df["close"], df["volume"]
        rsi = RSIIndicator(close, window=14).rsi().iloc[-1]
        ema_fast = EMAIndicator(close, window=20).ema_indicator().iloc[-1]
        ema_slow = (
            EMAIndicator(close, window=50).ema_indicator().iloc[-1] if len(close) >= 50 else ema_fast
        )
        macd_ind = MACD(close)
        macd_val, macd_sig = macd_ind.macd().iloc[-1], macd_ind.macd_signal().iloc[-1]
        atr = AverageTrueRange(df["high"], df["low"], close, window=14).average_true_range().iloc[-1]

        price = close.iloc[-1]
        pct_change = ((price - close.iloc[0]) / close.iloc[0]) * 100
        last_volume = volume.iloc[-1]

        score = calculate_score(rsi, price, ema_fast, ema_slow, macd_val, macd_sig)
        _, pattern_bias = detect_pattern(df)
        forecast_score = get_4h_forecast_score(ticker, pattern_bias)

        try:
            daily_df = fetch_data_for_ticker(ticker, interval="1d", limit=DAILY_CANDLES_LIMIT)
        except Exception:
            daily_df = None

        try:
            hourly_df = fetch_data_for_ticker(ticker, interval="1h", limit=HOURLY_CANDLES_LIMIT)
        except Exception:
            hourly_df = None

        break_counters = compute_break_counters(daily_df)
        min_col = format_break_col(
            break_counters["min_count"] if break_counters else 0,
            break_counters["min_date"] if break_counters else None, "min", compact=compact_mode,
        )
        max_col = format_break_col(
            break_counters["max_count"] if break_counters else 0,
            break_counters["max_date"] if break_counters else None, "max", compact=compact_mode,
        )

        volume_cols = {}
        for h in VOLUME_WINDOWS_HOURS:
            key = f"{h}ч"
            volume_cols[key] = format_volume_change(
                compute_volume_change_pct_hours(hourly_df, hours=h), compact=compact_mode
            )

        funding_rate = fetch_funding_rate(ticker)
        oi_value, oi_pct_change = fetch_open_interest_change(ticker)
        funding_col = format_funding(funding_rate, compact=compact_mode)
        oi_col = format_open_interest(oi_value, oi_pct_change, compact=compact_mode)

        global_vol = fetch_global_volume(ticker)
        global_vol_col = format_volume(global_vol) if global_vol is not None else "н/д"

        watchlist.append({
            "Coin": ticker.replace("USDT", ""),
            "Price": round(price, 4),
            "Δ %": pct_change_str(pct_change),
            "RSI": rsi_str(rsi, compact=compact_mode),
            "Trend": trend_arrow(ema_fast, ema_slow),
            "MACD": macd_signal_str(macd_val, macd_sig),
            "ATR": round(atr, 4),
            "Volume": format_volume(last_volume),
            "Общий объём": global_vol_col,
            "1ч": volume_cols["1ч"],
            "3ч": volume_cols["3ч"],
            "6ч": volume_cols["6ч"],
            "24ч": volume_cols["24ч"],
            "ScoreValue": score,
            "Funding": funding_col,
            "OI": oi_col,
            "Сигнал": signal_str(score, compact=compact_mode),
            "Pattern": pattern_str(pattern_bias, compact=compact_mode),
            "Мин": min_col,
            "Макс": max_col,
            "4ч Прогноз": forecast_str(forecast_score, compact=compact_mode),
            "_ticker": ticker,
        })
    except Exception as e:
        failed_tickers.append((ticker, str(e)))

st.caption(f"Монет загружено: {len(watchlist)} (показывается максимум {MAX_ROWS})")

if failed_tickers:
    with st.expander(f"⚠️ Не удалось загрузить {len(failed_tickers)} монет(у) -- нажми, чтобы удалить", expanded=True):
        for bad_ticker, err in failed_tickers:
            err_cols = st.columns([5, 1])
            err_cols[0].markdown(f"**{bad_ticker}**: {err}")
            if err_cols[1].button("🗑 Удалить", key=f"del_failed_{bad_ticker}"):
                remove_coin(bad_ticker)
                st.session_state.coins = load_coins()
                st.rerun()

watch_df = pd.DataFrame(watchlist)

if not watch_df.empty:
    watch_df = watch_df.sort_values(by="ScoreValue", ascending=False).head(MAX_ROWS)

    with st.container(key="watchlist_table"):
        render_hint_row(BUY_HINTS, "buy-hint")
        render_group_header_row()
        render_column_header_row()

        for _, row in watch_df.iterrows():
            ticker = row["_ticker"]
            cols = st.columns(COL_WIDTHS)

            for col, key in zip(cols[:-1], COL_KEYS[:-1]):
                if key == "Coin":
                    if col.button(row["Coin"], key=f"select_{ticker}", help=f"Открыть анализ {row['Coin']}"):
                        st.session_state.selected_coin = ticker
                        st.rerun()
                else:
                    col.markdown(str(row[key]), unsafe_allow_html=True)

            armed_at = st.session_state.confirm_delete.get(ticker)
            pending = armed_at is not None and (time.time() - armed_at) < DELETE_CONFIRM_WINDOW_SEC
            label = "✅" if pending else "🗑"
            tooltip = f"Подтвердить: удалить {row['Coin']}?" if pending else f"Удалить {row['Coin']} из списка"

            if cols[-1].button(label, key=f"del_{ticker}", help=tooltip):
                if pending:
                    remove_coin(ticker)
                    st.session_state.confirm_delete.pop(ticker, None)
                    st.session_state.coins = load_coins()
                    if ticker in st.session_state.coins:
                        st.session_state.delete_diagnostic = (
                            f"{ticker} всё ещё в списке после remove_coin() -- проверь storage/coins_storage.py."
                        )
                else:
                    st.session_state.confirm_delete[ticker] = time.time()
                st.rerun()

        render_hint_row(SELL_HINTS, "sell-hint")
else:
    st.info("Список пуст. Добавьте монету выше.")

# ---------------------------------------------------------------------------
# Боковая панель анализа
# ---------------------------------------------------------------------------
render_analysis_sidebar()

# ---------------------------------------------------------------------------
# График
# ---------------------------------------------------------------------------
st.subheader("📈 Chart")
if st.session_state.coins:
    selected = st.selectbox("Select Coin", st.session_state.coins)
    chart_df = fetch_data_for_ticker(selected)
    if not chart_df.empty:
        fig = go.Figure()
        fig.add_trace(go.Candlestick(
            x=chart_df.index, open=chart_df["open"], high=chart_df["high"],
            low=chart_df["low"], close=chart_df["close"], name="Price",
        ))
        fig.update_layout(height=420, xaxis_rangeslider_visible=False, margin=dict(t=20, b=20))
        st.plotly_chart(fig, use_container_width=True)
else:
    st.info("Добавьте хотя бы одну монету, чтобы увидеть график.")

# ---------------------------------------------------------------------------
# Quick Guide
# ---------------------------------------------------------------------------
with st.expander("📖 Quick Guide", expanded=False):
    st.markdown("""
    <div class="quick-guide">
    <p><strong>Как читать таблицу</strong> — наведи курсор на любой заголовок столбца, появится
    короткая подсказка. Включи "Компактный режим", если не влезает на экран — столбцы-сигналы
    схлопнутся до значка. Клик на название монеты открывает боковую панель со структурным анализом.</p>
    <p><strong>Короткие коды зон:</strong> 🟢▲ ПОК (покупка) · 🟡▲ ППК (предпокупка) ·
    ⚪ НЕЙТ (нейтрально) · 🔵▼ ППД (предпродажа) · 🔴▼ ПРД (продажа) — единая палитра для RSI, Сигнала,
    Pattern и 4ч Прогноза.</p>
    <p><strong>Объём (1ч / 3ч / 6ч / 24ч):</strong> изменение объёма в деньгах за соответствующее
    окно относительно предыдущего такого же окна. Например "3ч" сравнивает сумму объёма за последние
    3 часа с суммой за 3 часа перед этим.</p>
    <p><strong>Global Vol:</strong> суммарный 24ч объём монеты со всех бирж (CoinGecko) — в отличие
    от Volume (только та биржа, с которой сейчас берутся свечи).</p>
    <p><strong>Funding / OI:</strong> метрики фьючерсного рынка. Пробуем Binance → Bybit → OKX по
    очереди. Если у тебя регион, где офшорные биржи официально не работают (например США), эти два
    столбца могут остаться "н/д" даже после всех fallback'ов — это реальное регуляторное ограничение,
    не баг.</p>
    <p style="color:#888; margin-top:12px;"><em>Ни один индикатор не гарантирует направление цены —
    используй как совокупность фильтров, а не автоматическую рекомендацию.</em></p>
    </div>
    """, unsafe_allow_html=True)

# ---------------------------------------------------------------------------
# Автообновление
# ---------------------------------------------------------------------------
st.caption(f"Auto refresh: {REFRESH_SEC}s")
time.sleep(REFRESH_SEC)
st.rerun()