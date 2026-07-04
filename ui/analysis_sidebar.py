"""Боковая панель структурного анализа монеты (уровни, risk/reward, Fibonacci, объёмный профиль)."""
from datetime import datetime, timezone

import pandas as pd
import streamlit as st

from api.get_data import fetch_data_for_ticker, fetch_from_source, SOURCE_NAMES
from indicators.analysis import (
    GUIDES,
    calculate_risk_reward,
    fibonacci_levels,
    find_support_resistance,
    find_swing_range,
    nearest_levels,
    point_of_control,
    strength_label,
)
from indicators.backtest import backtest_score_signal
from ui.config import ANALYSIS_LOOKBACK_DAYS, FIBONACCI_SWING_DAYS, SLOW_METRICS_INTERVAL


_PATTERN_LABELS = {"bull": "бычий (ПОК)", "bear": "медвежий (ПРД)", "neutral": "нейтральный (НЕЙТ)"}


def _fmt(value, suffix: str = "", digits: int = 2) -> str:
    if value is None:
        return "н/д"
    return f"{value:,.{digits}f}{suffix}"


def build_analysis_report(ticker: str, metrics: dict, daily_df: pd.DataFrame) -> str:
    """
    Собирает всё, что видно в приложении по этой монете (таблица watchlist +
    структурный анализ) в один читаемый текстовый блок -- для копирования
    и вставки в чат.
    """
    coin = ticker.replace("USDT", "")
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    lines = [f"=== {coin} -- анализ на {now} ==="]

    if not metrics:
        lines.append("(нет данных из таблицы watchlist -- кликни на монету заново)")
    else:
        price = metrics.get("price")
        lines += [
            f"Текущая цена: {_fmt(price, digits=4)}",
            "",
            "ТАБЛИЦА WATCHLIST",
            f"Изменение 24ч: {_fmt(metrics.get('pct_change_24h'), '%')}",
            f"RSI: {_fmt(metrics.get('rsi'), digits=1)}",
            f"EMA(20/50): {_fmt(metrics.get('ema_fast'), digits=4)} / {_fmt(metrics.get('ema_slow'), digits=4)}",
            f"MACD: {_fmt(metrics.get('macd_val'), digits=4)} / сигнальная {_fmt(metrics.get('macd_sig'), digits=4)}",
            f"ATR: {_fmt(metrics.get('atr'), digits=4)}",
            f"Volume (последняя свеча): {_fmt(metrics.get('last_volume'), digits=2)}",
            f"Общий объём 24ч (все биржи): {_fmt(metrics.get('global_vol'), digits=0)}",
        ]
        vol_pct = metrics.get("volume_pct") or {}
        vol_line = " / ".join(f"{k}: {_fmt(vol_pct.get(k), '%')}" for k in ("1ч", "3ч", "6ч", "24ч"))
        lines.append(f"Изменение объёма: {vol_line}")
        lines += [
            f"Funding: {_fmt(metrics.get('funding_rate'), '%', digits=3)}",
            f"Open Interest: {_fmt(metrics.get('oi_value'), digits=0)} ({_fmt(metrics.get('oi_pct_change'), '%')})",
            f"Сигнал (скор 0-100): {metrics.get('score', 'н/д')}",
            f"Pattern: {_PATTERN_LABELS.get(metrics.get('pattern_bias'), 'н/д')}",
            f"Прогноз ({st.session_state.get('forecast_horizon_label', '4ч')}, скор 0-100): "
            f"{_fmt(metrics.get('forecast_score'), digits=0)}",
        ]
        bc = metrics.get("break_counters") or {}
        if bc.get("min_count"):
            lines.append(f"Пробой минимума: ×{bc['min_count']} на цене {_fmt(bc.get('min_price'), digits=4)}")
        if bc.get("max_count"):
            lines.append(f"Пробой максимума: ×{bc['max_count']} на цене {_fmt(bc.get('max_price'), digits=4)}")

    lines.append("")
    lines.append("СТРУКТУРНЫЙ АНАЛИЗ (дневные свечи, мультибиржевой fallback)")

    if daily_df is None or daily_df.empty or len(daily_df) < 10:
        lines.append("Недостаточно дневной истории для анализа.")
        return "\n".join(lines)

    price = float(daily_df["close"].iloc[-1])
    support, resistance = find_support_resistance(daily_df)
    near_support, near_resistance = nearest_levels(support, resistance)

    lines.append("Сопротивление (снизу вверх):")
    if resistance:
        for lvl in resistance:
            lines.append(f"  - {lvl.price:,.4f} (сила {strength_label(lvl.strength)})")
    else:
        lines.append("  - не найдено")

    lines.append("Поддержка (сверху вниз):")
    if support:
        for lvl in support:
            lines.append(f"  - {lvl.price:,.4f} (сила {strength_label(lvl.strength)})")
    else:
        lines.append("  - не найдено")

    rr = calculate_risk_reward(price, near_support, near_resistance)
    ratio_str = f"1 : {rr['ratio']:.2f}" if rr["ratio"] is not None else "н/д"
    lines.append(
        f"Risk/Reward: риск {_fmt(rr['risk_pct'], '%')}, потенциал {_fmt(rr['reward_pct'], '%')}, "
        f"соотношение {ratio_str}"
    )

    lines.append("Fibonacci:")
    swing_low, swing_high = find_swing_range(daily_df, lookback=FIBONACCI_SWING_DAYS)
    if swing_low is not None and swing_high is not None and swing_high > swing_low:
        fib = fibonacci_levels(swing_low, swing_high)
        fib_line = " | ".join(f"{label} = {lvl:,.4f}" for label, lvl in fib.items())
        lines.append(f"  {fib_line}")
    else:
        lines.append("  недостаточно данных")

    poc = point_of_control(daily_df)
    if poc is not None:
        direction = "ниже" if poc < price else "выше"
        lines.append(f"Объёмный профиль: POC = {poc:,.4f} ({direction} текущей цены)")
    else:
        lines.append("Объёмный профиль: недостаточно данных")

    return "\n".join(lines)


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
            st.markdown(f"🔴 {lvl.price:,.4f} &nbsp; _(сила {strength_label(lvl.strength)})_", unsafe_allow_html=True)
    if support:
        st.caption("Поддержка (сверху вниз):")
        for lvl in support:
            st.markdown(f"🟢 {lvl.price:,.4f} &nbsp; _(сила {strength_label(lvl.strength)})_", unsafe_allow_html=True)
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


def _render_backtest_section(ticker: str):
    """
    Бэктест Score-сигнала на исторических 30m свечах -- честная проверка,
    есть ли у "Сигнала" из watchlist реальный edge, или это просто цифра.
    Сравнивает простую стратегию (вход >=70, выход <=30) с buy&hold за тот
    же период.
    """
    st.markdown("**🧪 Бэктест сигнала (Score)**", help="Проверка на истории: обгоняет ли Score buy&hold")

    col_a, col_b = st.columns(2)
    buy_th = col_a.slider("Порог входа", 50, 90, 70, key=f"bt_buy_{ticker}")
    sell_th = col_b.slider("Порог выхода", 10, 50, 30, key=f"bt_sell_{ticker}")

    try:
        bt_df = fetch_data_for_ticker(ticker, interval=SLOW_METRICS_INTERVAL, limit=1000)
    except Exception as e:
        st.caption(f"Не удалось загрузить историю для бэктеста: {e}")
        return

    result = backtest_score_signal(bt_df, buy_threshold=buy_th, sell_threshold=sell_th)
    if result is None:
        st.caption("Недостаточно истории для бэктеста (нужно больше 30-минутных свечей).")
        return

    if result.num_trades == 0:
        st.caption("За доступный период не было ни одной сделки по этим порогам -- попробуй понизить порог входа.")
        return

    period_days = (bt_df.index[-1] - bt_df.index[0]).total_seconds() / 86400
    st.caption(f"Период: ~{period_days:.0f} дней, {result.num_trades} сделок, комиссия {result.fee_pct}% за сторону.")

    m1, m2, m3 = st.columns(3)
    m1.metric("Стратегия", f"{result.total_return_pct:+.1f}%")
    m2.metric("Buy & Hold", f"{result.buy_hold_return_pct:+.1f}%")
    m3.metric("Win rate", f"{result.win_rate_pct:.0f}%" if result.win_rate_pct is not None else "н/д")

    if result.edge_vs_buy_hold_pct > 0:
        st.markdown(
            f"🟢 Сигнал Score работал: выиграл на **{result.edge_vs_buy_hold_pct:+.1f}%** "
            f"против простого удержания монеты."
        )
    else:
        st.markdown(
            f"🔴 Сигнал Score не работал: проиграл **{abs(result.edge_vs_buy_hold_pct):.1f}%** "
            f"простому удержанию монеты на этом периоде."
        )
    st.caption(f"Макс. просадка стратегии: {result.max_drawdown_pct:.1f}%")

    st.caption(
        "⚠️ Это простой long-only бэктест без проскальзывания и частичных позиций -- "
        "не финансовая рекомендация. Разные монеты и периоды могут дать разный результат; "
        "тестируй регулярно, а не один раз."
    )


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
        _render_backtest_section(ticker)

        st.divider()
        st.markdown("**📋 Скопировать анализ**")
        st.caption("Наведи на блок ниже -- в правом верхнем углу появится иконка копирования.")
        try:
            report_daily_df = fetch_data_for_ticker(ticker, interval="1d", limit=ANALYSIS_LOOKBACK_DAYS)
        except Exception:
            report_daily_df = None
        report_text = build_analysis_report(ticker, st.session_state.selected_coin_metrics, report_daily_df)
        st.code(report_text, language=None)

        st.divider()
        if st.button("✖ Закрыть анализ", key="close_analysis"):
            st.session_state.selected_coin = None
            st.session_state.selected_coin_metrics = None
            st.rerun()