"""Боковая панель структурного анализа монеты (уровни, risk/reward, Fibonacci, объёмный профиль)."""
import pandas as pd
import streamlit as st

from api.get_data import fetch_from_source, SOURCE_NAMES
from indicators.analysis import (
    GUIDES,
    calculate_risk_reward,
    fibonacci_levels,
    find_support_resistance,
    find_swing_range,
    nearest_levels,
    point_of_control,
)
from ui.config import ANALYSIS_LOOKBACK_DAYS, FIBONACCI_SWING_DAYS


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