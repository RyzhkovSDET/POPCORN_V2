import time
from datetime import datetime

import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from ta.momentum import RSIIndicator
from ta.trend import ADXIndicator, EMAIndicator, MACD
from ta.volatility import AverageTrueRange

from api.futures_data import fetch_funding_rate, fetch_open_interest_change
from api.get_data import fetch_data_for_ticker
from indicators.analysis import (
    GUIDES,
    calculate_risk_reward,
    fibonacci_levels,
    find_support_resistance,
    find_swing_range,
    nearest_levels,
    point_of_control,
)
from indicators.signal_zones import classify_score, forecast_str, rsi_str, signal_str
from storage.coins_storage import add_coin, load_coins, remove_coin

st.set_page_config(layout="wide", page_title="POPCORN v2")

VALID_QUOTES = ("USDT", "BUSD", "BTC", "ETH")

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
MAX_ROWS = 10
PATTERN_LOOKBACK = 30
HTF_INTERVAL = "15m"
HTF_LIMIT = 100
BREAKOUT_LOOKBACK_DAYS = 3
DAILY_CANDLES_LIMIT = 10
ANALYSIS_LOOKBACK_DAYS = 90       # 3 месяца дневных свечей для анализатора
FIBONACCI_SWING_DAYS = 30

COL_WIDTHS = [0.9, 0.85, 0.9, 1.15, 0.55, 0.95, 0.7, 0.7, 1.0, 1.0, 1.1, 1.3, 1.35, 0.9, 1.1, 1.1, 1.2, 0.45]
COL_KEYS = ["Coin", "Price", "Δ %", "RSI", "Trend", "MACD", "ATR", "Volume", "Объём 1", "Объём 3",
            "Funding", "OI", "Сигнал", "Pattern", "Мин", "Макс", "4ч Прогноз", ""]

BUY_HINTS = {
    "Coin": "", "Price": "", "Δ %": "рост",
    "RSI": "🟢 ПОК", "Trend": "🔼", "MACD": "🟢",
    "ATR": "", "Volume": "",
    "Объём 1": "рост вложений", "Объём 3": "рост вложений",
    "Funding": "🟢 шорты перегреты", "OI": "",
    "Сигнал": "🟢▲ ПОК", "Pattern": "🟢 ПОК",
    "Мин": "", "Макс": "новые хаи",
    "4ч Прогноз": "🟢▲ ПОК", "": "",
}
SELL_HINTS = {
    "Coin": "", "Price": "", "Δ %": "падение",
    "RSI": "🔴 ПРД", "Trend": "🔽", "MACD": "🔴",
    "ATR": "рост=риск", "Volume": "",
    "Объём 1": "отток денег", "Объём 3": "отток денег",
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
    [data-testid="stHorizontalBlock"] { gap: 0.35rem; align-items: center; }
    [data-testid="column"] { padding: 1px 3px !important; }
    [data-testid="column"] p { font-size: 0.78rem; white-space: nowrap; margin-bottom: 0; }
    div.stButton > button { padding: 0px 10px; height: 26px; font-size: 0.75rem; min-height: 26px; }
    div[data-testid="stForm"] { padding: 0.6rem 0.8rem; }
    .quick-guide { font-size: 0.78em; line-height: 1.45; }
    .buy-hint { color: #2ecc71; font-size: 0.62rem; font-weight: 600; white-space: nowrap; }
    .sell-hint { color: #e74c3c; font-size: 0.62rem; font-weight: 600; white-space: nowrap; }
    button[title^="Удалить"] {
        border-radius: 50% !important; width: 26px !important; padding: 0 !important;
        border: 1px solid rgba(231, 76, 60, 0.3) !important; transition: all 0.15s ease;
    }
    button[title^="Удалить"]:hover {
        background: rgba(231, 76, 60, 0.15) !important; border-color: #e74c3c !important;
        transform: scale(1.08);
    }
    button[title^="Подтвердить"] {
        border-radius: 50% !important; width: 26px !important; padding: 0 !important;
        border: 1px solid rgba(46, 204, 113, 0.5) !important; background: rgba(46, 204, 113, 0.1) !important;
        transition: all 0.15s ease;
    }
    button[title^="Подтвердить"]:hover {
        background: rgba(46, 204, 113, 0.22) !important; border-color: #2ecc71 !important;
        transform: scale(1.08);
    }
    button[title^="Открыть анализ"] {
        font-weight: 600 !important; text-decoration: underline; text-underline-offset: 3px;
    }
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


def trend_arrow(ema_fast, ema_slow):
    if ema_fast > ema_slow:
        return "🔼"
    elif ema_fast < ema_slow:
        return "🔽"
    return "➡️"


def pct_change_str(pct):
    if pct > 0:
        return f"🟢+{pct:.2f}%"
    elif pct < 0:
        return f"🔴{pct:.2f}%"
    return f"⚪{pct:.2f}%"


def macd_signal_str(macd_val, macd_sig):
    arrow = "🟢" if macd_val > macd_sig else "🔴"
    return f"{arrow}{macd_val:.4f}"


def format_volume(vol):
    if vol >= 1_000_000:
        return f"{vol / 1_000_000:.2f}M"
    elif vol >= 1_000:
        return f"{vol / 1_000:.2f}K"
    return f"{vol:.2f}"


def calculate_score(rsi, price, ema_fast, ema_slow, macd_val, macd_sig):
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
        return "–", "neutral"

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
    return emoji if compact else f"{emoji} {label}"


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
        return "⚪" if compact else "⚪ —"
    if compact:
        return color
    hours_str = "?"
    if date is not None:
        try:
            elapsed = datetime.utcnow() - date.to_pydatetime()
            hours_str = f"{int(elapsed.total_seconds() // 3600)}ч"
        except Exception:
            hours_str = "?"
    return f"{color}×{count} ({hours_str})"


def compute_volume_change_pct(daily_df: pd.DataFrame, days: int = 1):
    """% изменения объёма в деньгах (qav): последние `days` дней vs предыдущие `days`."""
    if daily_df is None or daily_df.empty or "qav" not in daily_df.columns:
        return None
    qav = pd.to_numeric(daily_df["qav"], errors="coerce")
    completed = qav.iloc[:-1]
    if len(completed) < days * 2 or completed.tail(days * 2).isnull().any():
        return None
    recent_period = completed.iloc[-days:].sum()
    prior_period = completed.iloc[-2 * days:-days].sum()
    if prior_period == 0:
        return None
    return ((recent_period - prior_period) / prior_period) * 100


def format_volume_change(pct, compact: bool = False) -> str:
    dot = "🟢" if (pct or 0) > 0 else "🔴" if (pct or 0) < 0 else "⚪"
    if pct is None:
        return "⚪" if compact else "⚪ н/д"
    return dot if compact else f"{dot} {pct:+.1f}%"


def format_funding(rate, compact: bool = False) -> str:
    """Контрарный сигнал перегрузки рынка (не buy/sell): экстрим = риск разворота."""
    if rate is None:
        return "⚪" if compact else "⚪ н/д"
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
    return dot if compact else f"{dot} {rate:+.3f}%"


def format_open_interest(oi, pct_change, compact: bool = False) -> str:
    if oi is None:
        return "⚪" if compact else "⚪ н/д"
    dot = "🟢" if (pct_change or 0) > 0 else "🔴" if (pct_change or 0) < 0 else "⚪"
    if compact:
        return dot
    pct_str = f"{pct_change:+.1f}%" if pct_change is not None else "н/д"
    return f"{dot}{format_volume(oi)} {pct_str}"


def render_hint_row(hints_dict, css_class):
    cols = st.columns(COL_WIDTHS)
    for col, key in zip(cols, COL_KEYS):
        text = hints_dict.get(key, "")
        col.markdown(f"<span class='{css_class}'>{text}</span>" if text else "&nbsp;", unsafe_allow_html=True)


# ---------------------------------------------------------------------------
# Хелперы -- боковая панель анализа
# ---------------------------------------------------------------------------

def render_analysis_sidebar():
    with st.sidebar:
        st.header("🔍 Анализ монеты")
        ticker = st.session_state.selected_coin

        if not ticker:
            st.info("Кликни на монету в таблице (по названию), чтобы увидеть структурный анализ.")
            return

        st.subheader(ticker.replace("USDT", ""))

        try:
            daily_df = fetch_data_for_ticker(ticker, interval="1d", limit=ANALYSIS_LOOKBACK_DAYS)
        except Exception as e:
            st.error(f"Не удалось загрузить дневные данные для {ticker}: {e}")
            return

        if daily_df.empty or len(daily_df) < 10:
            st.warning("Недостаточно дневной истории для анализа (нужно минимум ~10 дней).")
            return

        price = float(daily_df["close"].iloc[-1])
        st.metric("Текущая цена", f"{price:,.4f}")

        support, resistance = find_support_resistance(daily_df)
        near_support, near_resistance = nearest_levels(support, resistance)

        # --- Поддержка / Сопротивление ---
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

        # --- Risk / Reward ---
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

        # --- Fibonacci ---
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

        # --- Volume Profile ---
        st.markdown("**📶 Объёмный профиль**", help=GUIDES["volume_profile"]["short"])
        poc = point_of_control(daily_df)
        if poc is not None:
            direction = "выше" if poc > price else "ниже"
            st.markdown(f"POC (макс. объём): **{poc:,.4f}** ({direction} текущей цены)")
        else:
            st.caption("Недостаточно данных для объёмного профиля.")
        with st.expander("ℹ️ Как пользоваться"):
            st.write(GUIDES["volume_profile"]["full"])

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

        break_counters = compute_break_counters(daily_df)
        min_col = format_break_col(
            break_counters["min_count"] if break_counters else 0,
            break_counters["min_date"] if break_counters else None, "min", compact=compact_mode,
        )
        max_col = format_break_col(
            break_counters["max_count"] if break_counters else 0,
            break_counters["max_date"] if break_counters else None, "max", compact=compact_mode,
        )
        volume_1_col = format_volume_change(compute_volume_change_pct(daily_df, days=1), compact=compact_mode)
        volume_3_col = format_volume_change(compute_volume_change_pct(daily_df, days=3), compact=compact_mode)

        funding_rate = fetch_funding_rate(ticker)
        oi_value, oi_pct_change = fetch_open_interest_change(ticker)
        funding_col = format_funding(funding_rate, compact=compact_mode)
        oi_col = format_open_interest(oi_value, oi_pct_change, compact=compact_mode)

        watchlist.append({
            "Coin": ticker.replace("USDT", ""),
            "Price": round(price, 4),
            "Δ %": pct_change_str(pct_change),
            "RSI": rsi_str(rsi, compact=compact_mode),
            "Trend": trend_arrow(ema_fast, ema_slow),
            "MACD": macd_signal_str(macd_val, macd_sig),
            "ATR": round(atr, 4),
            "Volume": format_volume(last_volume),
            "Объём 1": volume_1_col,
            "Объём 3": volume_3_col,
            "Funding": funding_col,
            "OI": oi_col,
            "ScoreValue": score,
            "Сигнал": signal_str(score, compact=compact_mode),
            "Pattern": pattern_str(pattern_bias, compact=compact_mode),
            "Мин": min_col,
            "Макс": max_col,
            "4ч Прогноз": forecast_str(forecast_score, compact=compact_mode),
            "_ticker": ticker,
        })
    except Exception as e:
        st.error(f"{ticker}: {e}")

st.caption(f"Монет загружено: {len(watchlist)} (показывается максимум {MAX_ROWS})")

watch_df = pd.DataFrame(watchlist)

if not watch_df.empty:
    watch_df = watch_df.sort_values(by="ScoreValue", ascending=False).head(MAX_ROWS)

    render_hint_row(BUY_HINTS, "buy-hint")

    header_cols = st.columns(COL_WIDTHS)
    for col, h in zip(header_cols, COL_KEYS):
        col.markdown(f"**{h}**")

    for _, row in watch_df.iterrows():
        ticker = row["_ticker"]
        cols = st.columns(COL_WIDTHS)

        for col, key in zip(cols[:-1], COL_KEYS[:-1]):
            if key == "Coin":
                if col.button(row["Coin"], key=f"select_{ticker}", help=f"Открыть анализ {row['Coin']}"):
                    st.session_state.selected_coin = ticker
                    st.rerun()
            else:
                col.write(row[key])

        armed_at = st.session_state.confirm_delete.get(ticker)
        pending = armed_at is not None and (time.time() - armed_at) < REFRESH_SEC
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
    <p><strong>Как читать таблицу</strong> — включи "Компактный режим", если не влезает на экран.
    Столбцы-сигналы схлопнутся до цветного значка без текста. Клик на название монеты открывает
    боковую панель со структурным анализом (поддержка/сопротивление, Fibonacci, объёмный профиль, risk/reward).</p>
    <p><strong>Короткие коды зон:</strong> 🟢▲ ПОК (покупка) · 🟡▲ ППК (предпокупка) ·
    ⚪ НЕЙТ (нейтрально) · 🔵▼ ППД (предпродажа) · 🔴▼ ПРД (продажа) — единая палитра для RSI, Сигнала,
    Pattern и 4ч Прогноза.</p>
    <p><strong>RSI:</strong> 🟢 &lt;30 · 🟡 30-40 · ⚪ 40-60 · 🔵 60-70 · 🔴 &gt;70.</p>
    <p><strong>Сигнал:</strong> внутренний скоринг 0-100 (RSI + EMA-разрыв + MACD momentum). Ориентир, не рекомендация.</p>
    <p><strong>Мин / Макс:</strong> счётчик подряд идущих пробоев экстремума за 3 дня, формат <code>🔴×2 (18ч)</code>.</p>
    <p><strong>Объём 1 / 3:</strong> изменение объёма в деньгах (USDT) за 1 и за 3 дня.</p>
    <p><strong>Funding:</strong> контрарный сигнал перегрузки рынка — 🔴 лонги перегреты (риск коррекции),
    🟢 шорты перегреты (риск отскока). Доступно только для фьючерсных пар.</p>
    <p><strong>OI:</strong> открытый интерес + тренд за час; направление читай вместе со столбцом Δ%.</p>
    <p><strong>4ч Прогноз:</strong> мультитаймфрейм-сигнал (EMA/RSI/ADX на 15m + текущий паттерн).</p>
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