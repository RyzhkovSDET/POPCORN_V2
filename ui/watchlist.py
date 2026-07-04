"""
Watchlist: таблица монет с индикаторами + 3 компактных селектора настроек
(окно Trend, таймфрейм RSI/MACD/EMA, горизонт прогноза) + мини-скринер.

Вынесено из main.py, чтобы не раздувать точку входа -- вся логика опроса
бирж и отрисовки таблицы теперь живёт здесь, main.py просто вызывает
render_watchlist().
"""
from concurrent.futures import ThreadPoolExecutor, as_completed

import pandas as pd
import streamlit as st

from api.coingecko_data import fetch_global_volume
from api.futures_data import fetch_funding_rate, fetch_open_interest_change
from api.get_data import fetch_data_for_ticker, fetch_latest_bar, is_binance_known_bad
from api import ws_stream
from indicators.signal_zones import forecast_str, pattern_cell, rsi_str, signal_str
from storage.coins_storage import load_coin_click_counts, load_coins, remove_coin, set_coin_click_count
from ui.config import (
    COL_KEYS,
    COL_WIDTHS,
    FORECAST_HORIZON_DEFAULT_LABEL,
    FORECAST_HORIZON_OPTIONS,
    HOURLY_CANDLES_LIMIT,
    MAX_ROWS,
    REFRESH_SEC,
    SLOW_METRICS_INTERVAL,
    SLOW_METRICS_TIMEFRAME_OPTIONS,
    TREND_WINDOW_DEFAULT,
    TREND_WINDOW_OPTIONS,
    VOLUME_WINDOWS_HOURS,
)
from ui.formatters import (
    format_funding,
    format_global_volume_cell,
    format_open_interest,
    format_volume_cell,
    format_volume_change,
    pct_change_str,
)
from ui.metrics import compute_slow_metrics, compute_volume_change_pct_hours
from ui.screener import render_screener_widget
from ui.table import render_column_header_row

MAX_FETCH_WORKERS = 8  # сколько тикеров тянем параллельно (I/O-bound -- GIL не мешает)


def _init_watchlist_session_state():
    if "indicator_interval" not in st.session_state:
        st.session_state.indicator_interval = SLOW_METRICS_INTERVAL  # таймфрейм RSI/MACD/EMA/ATR
    if "trend_window" not in st.session_state:
        st.session_state.trend_window = TREND_WINDOW_DEFAULT  # окно для столбца Trend (в свечах)
    if "forecast_horizon_label" not in st.session_state:
        st.session_state.forecast_horizon_label = FORECAST_HORIZON_DEFAULT_LABEL  # горизонт столбца "Прогноз"
    if "coin_click_counts" not in st.session_state:
        st.session_state.coin_click_counts = load_coin_click_counts()  # ticker -> число кликов (определяет цвет по циклу)


# Цикл цветов кнопки монеты по числу кликов: 1-й клик -- без изменений
# (прозрачный, только открывает анализ), 2-й -- бирюзовый, 3-й -- серый,
# 4-й -- снова прозрачный, и так по кругу.
_COIN_COLOR_CYCLE = ("transparent", "teal", "gray")
_COIN_COLOR_HEX = {"teal": "#14b8a6", "gray": "#6b7280"}


def _render_selectors_row():
    """Заголовок Watchlist + 3 компактных селектора + мини-скринер, всё в одну строку."""
    watchlist_header_col, trend_window_col, indicator_tf_col, forecast_horizon_col, screener_col = st.columns(
        [1, 0.72, 0.72, 0.72, 3.8]
    )
    with watchlist_header_col:
        st.subheader("📋 Watchlist")
    with trend_window_col:
        tw_labels = [label for label, _ in TREND_WINDOW_OPTIONS]
        default_tw_idx = next(
            (i for i, (_, w) in enumerate(TREND_WINDOW_OPTIONS) if w == st.session_state.trend_window), 2
        )
        chosen_tw_label = st.selectbox(
            "Окно Trend", tw_labels, index=default_tw_idx, key="trend_window_select",
            help="На сколько свечей назад сравнивать цену для столбца Trend. "
                 "Больше свечей -- меньше шума, более надёжный сигнал.",
        )
        st.session_state.trend_window = next(
            w for label, w in TREND_WINDOW_OPTIONS if label == chosen_tw_label
        )
    with indicator_tf_col:
        tf_labels = [label for label, _ in SLOW_METRICS_TIMEFRAME_OPTIONS]
        default_idx = next(
            (i for i, (_, interval) in enumerate(SLOW_METRICS_TIMEFRAME_OPTIONS)
             if interval == st.session_state.indicator_interval), 0
        )
        chosen_tf_label = st.selectbox(
            "RSI/MACD/EMA", tf_labels, index=default_idx, key="indicator_tf_select",
            help="Таймфрейм, на котором считаются RSI/MACD/EMA/ATR/Trend/Pattern во всей таблице. "
                 "Трейдеры чаще смотрят 1ч/4ч/1д для менее шумного сигнала.",
        )
        st.session_state.indicator_interval = next(
            interval for label, interval in SLOW_METRICS_TIMEFRAME_OPTIONS if label == chosen_tf_label
        )
    with forecast_horizon_col:
        fh_labels = [label for label, _, _, _ in FORECAST_HORIZON_OPTIONS]
        default_fh_idx = next(
            (i for i, (label, *_ ) in enumerate(FORECAST_HORIZON_OPTIONS)
             if label == st.session_state.forecast_horizon_label), 1
        )
        chosen_fh_label = st.selectbox(
            "Гор. прогноза", fh_labels, index=default_fh_idx, key="forecast_horizon_select",
            help="Горизонт столбца 'Прогноз'. 2ч/4ч считаются на 15-минутках, "
                 "6ч/12ч/1д -- на часовых свечах (менее шумно для дальнего горизонта).",
        )
        st.session_state.forecast_horizon_label = chosen_fh_label
    with screener_col:
        render_screener_widget()


def _fetch_ticker_row(
    ticker: str,
    indicator_interval: str,
    trend_window: int,
    forecast_interval: str,
    forecast_limit: int,
):
    """
    Тянет и считает все данные по ОДНОМУ тикеру. Не вызывает никакие
    Streamlit UI-функции (st.markdown/st.button и т.п.) -- их нельзя
    безопасно вызывать из фонового потока. Вызывается параллельно для
    разных тикеров через ThreadPoolExecutor, поэтому сетевые запросы по
    разным монетам идут одновременно, а не один за другим.

    indicator_interval -- таймфрейм для RSI/MACD/EMA/ATR/Trend/Pattern.
    trend_window -- сколько свечей назад сравнивать для столбца Trend.
    forecast_interval/forecast_limit -- таймфрейм и лимит свечей для
    столбца "Прогноз", зависят от выбранного горизонта.
    Все выбираются пользователем над таблицей.
    """
    df = fetch_latest_bar(ticker)
    # Порог -- 2 строки, а не как раньше 20: из df здесь реально используются
    # только close.iloc[-1] (цена), volume.iloc[-1] и volume.iloc[-2] (для
    # цвета столбца Volume). WS-путь (fetch_latest_bar) отдаёт всего 2-5
    # последних свечей вместо полной истории в 100 -- порог в 20 сделал бы
    # WS-данные бесполезными, всегда откатывая на REST.
    if df.empty or len(df) < 2:
        return None

    close, volume = df["close"], df["volume"]
    price = close.iloc[-1]
    last_volume = volume.iloc[-1]
    prev_volume = volume.iloc[-2] if len(volume) >= 2 else None

    slow = compute_slow_metrics(
        ticker, indicator_interval, trend_window, forecast_interval, forecast_limit
    )
    if slow is None:
        return None

    try:
        hourly_df = fetch_data_for_ticker(ticker, interval="1h", limit=HOURLY_CANDLES_LIMIT)
    except Exception:
        hourly_df = None

    # "Изм" -- изменение цены за 24 часа, беру цену закрытия 24 часа назад
    # из часовых свечей (hourly_df[-1] -- текущий незавершённый час,
    # hourly_df[-25] -- ровно 24 полных часа назад). Если часовых данных
    # не хватает -- честно None -> "н/д", а не подмена другим окном.
    if hourly_df is not None and len(hourly_df) >= 25:
        price_24h_ago = hourly_df["close"].iloc[-25]
        pct_change = ((price - price_24h_ago) / price_24h_ago) * 100
    else:
        pct_change = None

    volume_cols = {}
    volume_pct_raw = {}
    for h in VOLUME_WINDOWS_HOURS:
        key = f"{h}ч"
        pct = compute_volume_change_pct_hours(hourly_df, hours=h)
        volume_pct_raw[key] = pct
        volume_cols[key] = format_volume_change(pct)

    funding_rate = fetch_funding_rate(ticker)
    oi_value, oi_pct_change = fetch_open_interest_change(ticker)
    funding_col = format_funding(funding_rate)
    oi_col = format_open_interest(oi_value, oi_pct_change)

    global_vol_col = None  # заполняется позже, в основном потоке (см. round-robin ниже)

    row = {
        "Coin": ticker.replace("USDT", ""),
        "Price": round(price, 4),
        "Изм": pct_change_str(pct_change),
        "RSI": rsi_str(slow["rsi"]),
        "Trend": slow["trend_col"],
        "EMA": slow["ema_col"],
        "MACD": slow["macd_col"],
        "ATR": slow["atr_col"],
        "Volume": format_volume_cell(last_volume, prev_volume),
        "Общий объём": global_vol_col,
        "1ч": volume_cols["1ч"],
        "3ч": volume_cols["3ч"],
        "6ч": volume_cols["6ч"],
        "24ч": volume_cols["24ч"],
        "ScoreValue": slow["score"],
        "Funding": funding_col,
        "OI": oi_col,
        "Сигнал": signal_str(slow["score"]),
        "Pattern": pattern_cell(slow["pattern_name"], slow["pattern_bias"]),
        "Пробой": slow["breakout_col"],
        "Прогноз": forecast_str(slow["forecast_score"]),
        "_ticker": ticker,
    }

    # Сырые значения этой же строки -- используются кнопкой "Копировать
    # анализ" в боковой панели, когда кликнут именно этот тикер.
    raw = {
        "price": float(price),
        "pct_change_24h": float(pct_change) if pct_change is not None else None,
        "rsi": float(slow["rsi"]),
        "atr": float(slow["atr"]),
        "ema_fast": float(slow["ema_fast"]),
        "ema_slow": float(slow["ema_slow"]),
        "macd_val": float(slow["macd_val"]),
        "macd_sig": float(slow["macd_sig"]),
        "score": slow["score"],
        "pattern_bias": slow["pattern_bias"],
        "forecast_score": slow["forecast_score"],
        "break_counters": slow["break_counters"],
        "last_volume": float(last_volume),
        "global_vol": None,  # заполняется позже, в основном потоке
        "volume_pct": volume_pct_raw,
        "funding_rate": funding_rate,
        "oi_value": oi_value,
        "oi_pct_change": oi_pct_change,
    }
    return {"row": row, "raw": raw}


@st.fragment(run_every=f"{REFRESH_SEC}s")
def _render_watchlist_table():
    """
    Автообновляемая часть страницы -- ТОЛЬКО эта функция перевыполняется
    каждые REFRESH_SEC секунд (Streamlit fragment), а не вся страница
    целиком. Заголовок, форма, скринер, боковая панель анализа, график и
    Quick Guide больше не перерисовываются каждые 10 секунд без надобности.
    """
    indicator_interval = st.session_state.indicator_interval  # таймфрейм RSI/MACD/EMA, выбранный над таблицей
    trend_window = st.session_state.trend_window  # окно Trend (в свечах), выбранное над таблицей
    # Горизонт прогноза (label) -> (интервал свечей, лимит) для расчёта.
    _, _, forecast_interval, forecast_limit = next(
        opt for opt in FORECAST_HORIZON_OPTIONS if opt[0] == st.session_state.forecast_horizon_label
    )

    if st.session_state.delete_diagnostic:
        st.warning(st.session_state.delete_diagnostic)
        st.session_state.delete_diagnostic = None

    watchlist = []
    failed_tickers = []
    raw_by_ticker = {}

    coins = st.session_state.coins
    if coins:
        # Держим WebSocket-подписку синхронизированной с текущим watchlist --
        # дёшево вызывать каждый цикл: если набор тикеров не изменился,
        # ensure_subscribed ничего не делает. Тикеры, уже известные как
        # невалидные для Binance Spot (см. is_binance_known_bad), исключаем --
        # иначе один такой тикер (например HYPEUSDT) может сорвать общий
        # WS-стрим сразу для всех остальных монет тоже.
        ws_stream.ensure_subscribed([t for t in coins if not is_binance_known_bad(t)])
        with ThreadPoolExecutor(max_workers=min(MAX_FETCH_WORKERS, len(coins))) as executor:
            futures = {
                executor.submit(
                    _fetch_ticker_row, ticker, indicator_interval,
                    trend_window, forecast_interval, forecast_limit,
                ): ticker
                for ticker in coins
            }
            for future in as_completed(futures):
                ticker = futures[future]
                try:
                    result = future.result()
                except Exception as e:
                    failed_tickers.append((ticker, str(e)))
                    continue
                if result is None:
                    continue
                watchlist.append(result["row"])
                raw_by_ticker[ticker] = result["raw"]

    # Round-robin для "Общего объёма" (CoinGecko): вместо того, чтобы дёргать
    # ВСЕ монеты каждый раз, когда их общий 5-минутный кэш синхронно
    # истекает (что и вызывало пачки 429 -- см. лог), обновляем ЗА ОДИН
    # ЦИКЛ ТОЛЬКО ОДНУ монету по кругу. При 12 монетах и REFRESH_SEC=10с
    # это значит, что объём каждой монеты обновляется примерно раз в 2
    # минуты, а не все 12 разом раз в 5 минут -- и свежее, и без всплеска
    # запросов. Монеты, для которых значения ещё вообще нет (только что
    # добавлены), подгружаются сразу вне очереди, чтобы не показывать
    # пустую ячейку до своего хода в ротации.
    if "global_volume_cache" not in st.session_state:
        st.session_state.global_volume_cache = {}  # ticker -> последнее известное значение объёма
    if "global_volume_rotation_idx" not in st.session_state:
        st.session_state.global_volume_rotation_idx = 0

    tickers_this_cycle = [row["_ticker"] for row in watchlist]
    if tickers_this_cycle:
        need_fetch = [t for t in tickers_this_cycle if t not in st.session_state.global_volume_cache]
        if not need_fetch:
            idx = st.session_state.global_volume_rotation_idx % len(tickers_this_cycle)
            need_fetch = [tickers_this_cycle[idx]]
            st.session_state.global_volume_rotation_idx += 1

        for ticker in need_fetch:
            st.session_state.global_volume_cache[ticker] = fetch_global_volume(ticker)

        for row in watchlist:
            ticker = row["_ticker"]
            raw = raw_by_ticker.get(ticker, {})
            vol = st.session_state.global_volume_cache.get(ticker)
            row["Общий объём"] = format_global_volume_cell(vol, raw.get("volume_pct", {}).get("24ч"))
            raw["global_vol"] = vol

    # Fallback для тренда OI: если Binance не доступен (частая
    # причина -- регион), остаётся OKX, а у него нет истории (только текущий
    # снимок), поэтому oi_pct_change всегда None -> столбец OI никогда не
    # красится. Раз мы сами уже опрашиваем каждый тикер раз в REFRESH_SEC,
    # можем посчитать тренд вручную: сравниваем текущий oi_value с тем, что
    # видели в прошлый раз (храним в session_state). Работает здесь, в
    # основном потоке -- session_state недоступен из фоновых потоков
    # ThreadPoolExecutor.
    #
    # ВАЖНО (баг, исправлен): fetch_open_interest_change кэшируется на
    # SLOW-уровне (сейчас 60с), а опрос идёт каждые REFRESH_SEC (10с) --
    # то есть один и тот же oi_value прилетает несколько раз подряд между
    # обновлениями кэша. Раньше эталон в oi_history перезаписывался КАЖДЫЙ
    # рендер, поэтому почти всегда сравнивали значение само с собой ->
    # получали ровно 0.0%, и лишь на один рендер раз в TTL проскакивало
    # настоящее изменение, тут же затираясь. Теперь: (1) эталон обновляем
    # только когда oi_value реально изменился, (2) последний посчитанный
    # % храним и переиспользуем между обновлениями, а не сбрасываем в 0.
    if "oi_history" not in st.session_state:
        st.session_state.oi_history = {}  # ticker -> {"value": last oi seen, "pct": last computed % change}
    for row in watchlist:
        ticker = row["_ticker"]
        raw = raw_by_ticker.get(ticker, {})
        oi_value = raw.get("oi_value")
        oi_pct_change = raw.get("oi_pct_change")
        if oi_value is not None and oi_pct_change is None:
            hist = st.session_state.oi_history.get(ticker)
            if hist is None:
                st.session_state.oi_history[ticker] = {"value": oi_value, "pct": None}
            elif hist["value"] != oi_value:
                fallback_pct = ((oi_value - hist["value"]) / hist["value"]) * 100 if hist["value"] else None
                st.session_state.oi_history[ticker] = {"value": oi_value, "pct": fallback_pct}
            stored_pct = st.session_state.oi_history.get(ticker, {}).get("pct")
            if stored_pct is not None:
                row["OI"] = format_open_interest(oi_value, stored_pct)
                raw["oi_pct_change"] = stored_pct
        elif oi_value is not None:
            st.session_state.oi_history[ticker] = {"value": oi_value, "pct": oi_pct_change}

    live_indicator = "⚡ live (WebSocket)" if ws_stream.is_connected() else "🔄 REST-опрос"
    st.caption(
        f"Монет загружено: {len(watchlist)} (показывается максимум {MAX_ROWS}) -- "
        f"обновление раз в {REFRESH_SEC}с -- цена: {live_indicator}"
    )

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
            render_column_header_row()

            # Все CSS-переопределения для строк собираем в ОДИН список и
            # выводим ОДНИМ st.markdown() после цикла. Раньше на каждую
            # кнопку в каждой строке шёл отдельный st.markdown(f"<style>...")
            # -- каждый такой вызов создаёт свой блок в вертикальной вёрстке
            # Streamlit со своим отступом, и на 12 строк x 3-4 стиля это
            # заметно раздувало высоту таблицы. Теперь строк с CSS всего одна
            # на всю таблицу, независимо от числа монет.
            row_styles = []

            for _, row in watch_df.iterrows():
                ticker = row["_ticker"]
                cols = st.columns(COL_WIDTHS)

                for col, key in zip(cols[:-1], COL_KEYS[:-1]):
                    if key == "Coin":
                        # Ячейка Coin делится на два независимых элемента:
                        # 1) кружочек слева -- клик выбирает монету для анализа/графика
                        #    (фиолетовая подсветка, активна только ОДНА монета за раз --
                        #    определяется по st.session_state.selected_coin, отдельного
                        #    состояния не нужно);
                        # 2) таблетка с именем справа -- клик циклит её ЦВЕТ ФОНА
                        #    независимо от выбора для анализа: прозрачный -> бирюзовый
                        #    -> серый -> прозрачный, по кругу, у каждой монеты свой цикл.
                        dot_col, name_col = col.columns([1, 5])

                        is_selected = st.session_state.selected_coin == ticker
                        dot_label = "●" if is_selected else "○"
                        if dot_col.button(dot_label, key=f"pick_{ticker}", help=f"Выбрать {row['Coin']} для анализа"):
                            st.session_state.selected_coin = ticker
                            st.session_state.selected_coin_metrics = raw_by_ticker.get(ticker)
                            # Таблица -- это st.fragment (см. render_watchlist ниже),
                            # обычный st.rerun() внутри него перерисовал бы ТОЛЬКО саму
                            # таблицу -- боковая панель анализа и график (они вне
                            # фрагмента) остались бы со старыми данными. scope="app"
                            # форсирует полный перерендер всей страницы.
                            st.rerun(scope="app")
                        if is_selected:
                            row_styles.append(
                                f'div[class*="st-key-pick_{ticker}"] button {{'
                                f'background: #9b59b6 !important; color: #ffffff !important;'
                                f'border-color: #9b59b6 !important; }}'
                            )

                        click_count = st.session_state.coin_click_counts.get(ticker, 0)
                        if not isinstance(click_count, int):
                            click_count = 0
                        color_idx = click_count % len(_COIN_COLOR_CYCLE)
                        current_color = _COIN_COLOR_CYCLE[color_idx]
                        if current_color in _COIN_COLOR_HEX:
                            hex_color = _COIN_COLOR_HEX[current_color]
                            row_styles.append(
                                f'div[class*="st-key-coinname_{ticker}"] button {{'
                                f'background: {hex_color} !important; color: #ffffff !important;'
                                f'border-color: {hex_color} !important; }}'
                            )
                        if name_col.button(row["Coin"], key=f"coinname_{ticker}", help="Клик меняет цвет метки"):
                            new_count = click_count + 1
                            st.session_state.coin_click_counts[ticker] = new_count
                            set_coin_click_count(ticker, new_count)
                            st.rerun()
                    else:
                        col.markdown(str(row[key]), unsafe_allow_html=True)

                # Кнопка удаления -- простой текстовый крестик, красный текст, мгновенное
                # удаление без подтверждений и попапов.
                if cols[-1].button("×", key=f"del_{ticker}", help=f"Удалить {row['Coin']}"):
                    remove_coin(ticker)
                    st.session_state.coins = load_coins()
                    if ticker in st.session_state.coins:
                        st.session_state.delete_diagnostic = (
                            f"{ticker} всё ещё в списке после remove_coin() -- проверь storage/coins_storage.py."
                        )
                    st.rerun()

            if row_styles:
                st.markdown(f"<style>{''.join(row_styles)}</style>", unsafe_allow_html=True)

    else:
        st.info("Список пуст. Добавьте монету выше.")


def render_watchlist():
    """Единая точка входа -- вызывается из main.py. Селекторы + автообновляемая таблица."""
    _init_watchlist_session_state()
    _render_selectors_row()
    _render_watchlist_table()