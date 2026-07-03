import time

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from api.coingecko_data import fetch_global_volume
from api.futures_data import fetch_funding_rate, fetch_open_interest_change
from api.get_data import fetch_data_for_ticker
from indicators.signal_zones import forecast_str, rsi_str, signal_str
from storage.coins_storage import add_coin, load_coins, remove_coin
from ui.analysis_sidebar import render_analysis_sidebar
from ui.config import (
    BUY_HINTS,
    CHART_CANDLES_LIMIT,
    CHART_INTERVAL,
    COL_KEYS,
    COL_WIDTHS,
    HOURLY_CANDLES_LIMIT,
    MAX_ROWS,
    REFRESH_SEC,
    SELL_HINTS,
    SLOW_METRICS_TTL_SEC,
    VOLUME_WINDOWS_HOURS,
)
from ui.formatters import (
    format_funding,
    format_open_interest,
    format_volume,
    format_volume_change,
    normalize_ticker,
    pattern_str,
    pct_change_str,
)
from ui.metrics import compute_slow_metrics, compute_volume_change_pct_hours
from ui.styles import inject_styles
from ui.table import render_column_header_row, render_group_header_row, render_hint_row

st.set_page_config(layout="wide", page_title="POPCORN v2")
inject_styles()

# ---------------------------------------------------------------------------
# Session state
# ---------------------------------------------------------------------------
if "coins" not in st.session_state:
    st.session_state.coins = load_coins()
if "delete_diagnostic" not in st.session_state:
    st.session_state.delete_diagnostic = None
if "selected_coin" not in st.session_state:
    st.session_state.selected_coin = None

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
        # Быстрый блок: цена / Изм / объёмные окна / funding / OI -- каждые REFRESH_SEC секунд
        df = fetch_data_for_ticker(ticker)
        if df.empty or len(df) < 20:
            continue

        close, volume = df["close"], df["volume"]
        price = close.iloc[-1]
        pct_change = ((price - close.iloc[0]) / close.iloc[0]) * 100
        last_volume = volume.iloc[-1]

        # Медленный блок: RSI / MACD / ATR / Trend / Сигнал / Pattern / Мин / Макс --
        # пересчитывается не чаще раза в SLOW_METRICS_TTL_SEC секунд
        slow = compute_slow_metrics(ticker, compact_mode)
        if slow is None:
            continue

        try:
            hourly_df = fetch_data_for_ticker(ticker, interval="1h", limit=HOURLY_CANDLES_LIMIT)
        except Exception:
            hourly_df = None

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
            "Изм": pct_change_str(pct_change),
            "RSI": rsi_str(slow["rsi"], compact=compact_mode),
            "Trend": slow["trend_col"],
            "MACD": slow["macd_col"],
            "ATR": round(slow["atr"], 4),
            "Volume": format_volume(last_volume),
            "Общий объём": global_vol_col,
            "1ч": volume_cols["1ч"],
            "3ч": volume_cols["3ч"],
            "6ч": volume_cols["6ч"],
            "24ч": volume_cols["24ч"],
            "ScoreValue": slow["score"],
            "Funding": funding_col,
            "OI": oi_col,
            "Сигнал": signal_str(slow["score"], compact=compact_mode),
            "Pattern": pattern_str(slow["pattern_bias"], compact=compact_mode),
            "Мин": slow["min_col"],
            "Макс": slow["max_col"],
            "4ч Прогноз": forecast_str(slow["forecast_score"], compact=compact_mode),
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
            if err_cols[1].button("Удалить", key=f"del_failed_{bad_ticker}"):
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

            # Кнопка удаления -- обычная текстовая кнопка, красный текст, мгновенное
            # удаление без подтверждений и попапов.
            if cols[-1].button("Удалить", key=f"del_{ticker}"):
                remove_coin(ticker)
                st.session_state.coins = load_coins()
                if ticker in st.session_state.coins:
                    st.session_state.delete_diagnostic = (
                        f"{ticker} всё ещё в списке после remove_coin() -- проверь storage/coins_storage.py."
                    )
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
    # График следует за монетой, выбранной кликом в таблице (тот же
    # selected_coin, что открывает боковую панель анализа) -- отдельного
    # выпадающего списка больше нет. Если ещё ничего не кликали, по
    # умолчанию берётся первая монета из watchlist.
    chart_ticker = st.session_state.selected_coin or st.session_state.coins[0]
    st.caption(f"Монета: **{chart_ticker.replace('USDT', '')}** -- клик по названию монеты в таблице меняет график")
    chart_df = fetch_data_for_ticker(chart_ticker, interval=CHART_INTERVAL, limit=CHART_CANDLES_LIMIT)
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
    <p><strong>Цветовая палитра ячеек:</strong> зелёный фон -- покупка, красный -- продажа,
    жёлтый -- между нейтральным и покупкой, синий -- между нейтральным и продажей,
    белый/серый -- нейтрально. Цвет текста (чёрный/белый) подобран по контрасту с фоном.</p>
    <p><strong>Trend:</strong> треугольник ▲/▼ показывает, куда двигалась цена за последние 30 минут.</p>
    <p><strong>Объём (1ч / 3ч / 6ч / 24ч):</strong> изменение объёма в деньгах за соответствующее
    окно относительно предыдущего такого же окна. Например "3ч" сравнивает сумму объёма за последние
    3 часа с суммой за 3 часа перед этим.</p>
    <p><strong>Global Vol:</strong> суммарный 24ч объём монеты со всех бирж (CoinGecko) — в отличие
    от Volume (только та биржа, с которой сейчас берутся свечи).</p>
    <p><strong>Funding / OI:</strong> метрики фьючерсного рынка. Пробуем Binance → Bybit → OKX по
    очереди. Если у тебя регион, где офшорные биржи официально не работают (например США), эти два
    столбца могут остаться "н/д" даже после всех fallback'ов — это реальное регуляторное ограничение,
    не баг.</p>
    <p><strong>Скорость обновления:</strong> цена, Изм, объёмные окна, Funding и OI обновляются
    каждые {refresh}с. RSI, MACD, ATR, Trend, Сигнал, Pattern, Мин и Макс — не чаще раза в
    {slow}с, так как эти индикаторы физически не меняются так быстро, как цена.</p>
    <p style="color:#888; margin-top:12px;"><em>Ни один индикатор не гарантирует направление цены —
    используй как совокупность фильтров, а не автоматическую рекомендацию.</em></p>
    </div>
    """.format(refresh=REFRESH_SEC, slow=SLOW_METRICS_TTL_SEC), unsafe_allow_html=True)

# ---------------------------------------------------------------------------
# Автообновление
# ---------------------------------------------------------------------------
st.caption(f"Auto refresh: {REFRESH_SEC}s (RSI/MACD/ATR/Trend/Сигнал/Pattern/Мин/Макс -- раз в {SLOW_METRICS_TTL_SEC}s)")
time.sleep(REFRESH_SEC)
st.rerun()