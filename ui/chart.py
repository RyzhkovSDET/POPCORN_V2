"""График цены (свечи + EMA + объём) с выбором таймфрейма. Вынесено из main.py."""
import plotly.graph_objects as go
import streamlit as st
from plotly.subplots import make_subplots
from ta.trend import EMAIndicator

from api.get_data import fetch_data_for_ticker
from ui.config import CHART_INTERVAL, CHART_TIMEFRAME_OPTIONS

# Порог свечей, начиная с которого имеет смысл считать EMA(20)/EMA(50) --
# на коротких (1м/5м) таймфреймах при малом лимите свечей EMA(50) была бы
# почти целиком "разогревом" без смысла, поэтому просто не показываем линии.
_MIN_CANDLES_FOR_EMA_FAST = 21
_MIN_CANDLES_FOR_EMA_SLOW = 51


def render_chart():
    st.subheader("📈 Chart")
    if not st.session_state.coins:
        st.info("Добавьте хотя бы одну монету, чтобы увидеть график.")
        return

    # График следует за монетой, выбранной кликом в таблице (тот же
    # selected_coin, что открывает боковую панель анализа) -- отдельного
    # выпадающего списка для выбора МОНЕТЫ нет. Таймфрейм переключается
    # отдельно (1m/5m/15m/30m/1h/4h/1d).
    chart_ticker = st.session_state.selected_coin or st.session_state.coins[0]

    chart_header_cols = st.columns([3, 2])
    with chart_header_cols[0]:
        st.caption(f"Монета: **{chart_ticker.replace('USDT', '')}** -- кружочек слева от названия монеты в таблице меняет график")
    with chart_header_cols[1]:
        tf_labels = [label for label, _, _ in CHART_TIMEFRAME_OPTIONS]
        default_idx = next(
            (i for i, (_, interval, _) in enumerate(CHART_TIMEFRAME_OPTIONS) if interval == CHART_INTERVAL), 3
        )
        chosen_label = st.radio(
            "Таймфрейм", tf_labels, index=default_idx, horizontal=True,
            key="chart_timeframe", label_visibility="collapsed",
        )
        chart_interval, chart_limit = next(
            (interval, limit) for label, interval, limit in CHART_TIMEFRAME_OPTIONS if label == chosen_label
        )

    chart_df = fetch_data_for_ticker(chart_ticker, interval=chart_interval, limit=chart_limit)
    if chart_df.empty:
        st.warning(f"Нет данных для {chart_ticker.replace('USDT', '')} на таймфрейме {chosen_label}.")
        return

    # Два ряда в одной фигуре: сверху свечи + EMA, снизу объём. Общая ось X
    # (shared_xaxes) -- зум/пан по цене автоматически двигает и объём тоже.
    fig = make_subplots(
        rows=2, cols=1, shared_xaxes=True,
        row_heights=[0.78, 0.22], vertical_spacing=0.03,
    )

    fig.add_trace(
        go.Candlestick(
            x=chart_df.index, open=chart_df["open"], high=chart_df["high"],
            low=chart_df["low"], close=chart_df["close"], name="Price",
        ),
        row=1, col=1,
    )

    # EMA(20)/EMA(50) поверх свечей -- тот же быстрый/медленный тренд, что
    # уже считается в таблице watchlist (столбец EMA), но здесь видно как
    # линии двигаются относительно цены и друг друга во времени, а не только
    # текущее число разрыва в одной ячейке.
    close = chart_df["close"]
    if len(close) >= _MIN_CANDLES_FOR_EMA_FAST:
        ema_fast = EMAIndicator(close, window=20).ema_indicator()
        fig.add_trace(
            go.Scatter(x=chart_df.index, y=ema_fast, name="EMA 20",
                       line=dict(color="#f1c40f", width=1.3)),
            row=1, col=1,
        )
    if len(close) >= _MIN_CANDLES_FOR_EMA_SLOW:
        ema_slow = EMAIndicator(close, window=50).ema_indicator()
        fig.add_trace(
            go.Scatter(x=chart_df.index, y=ema_slow, name="EMA 50",
                       line=dict(color="#3498db", width=1.3)),
            row=1, col=1,
        )

    # Объём под графиком, окрашенный по направлению свечи (зелёный -- закрытие
    # выше открытия, красный -- ниже) -- тот же принцип цвета, что и везде
    # в приложении, чтобы рост/падение было видно с первого взгляда.
    volume_colors = [
        "#2ecc71" if c >= o else "#e74c3c"
        for o, c in zip(chart_df["open"], chart_df["close"])
    ]
    fig.add_trace(
        go.Bar(x=chart_df.index, y=chart_df["volume"], name="Volume",
               marker_color=volume_colors, showlegend=False),
        row=2, col=1,
    )

    fig.update_layout(
        height=560,
        margin=dict(t=20, b=20),
        legend=dict(orientation="h", yanchor="bottom", y=1.01, xanchor="left", x=0),
        # Мини-график диапазона снизу -- можно быстро перетащить окно
        # просмотра, не сбрасывая zoom основного графика.
        xaxis2_rangeslider_visible=True,
        xaxis2_rangeslider_thickness=0.06,
        xaxis_rangeslider_visible=False,  # только у нижней (объём) оси -- одного слайдера достаточно
        dragmode="pan",  # перетаскивание мышью по умолчанию, зум -- колесом/пальцами
    )
    fig.update_yaxes(title_text=None, row=2, col=1)

    st.plotly_chart(
        fig,
        width="stretch",
        config={
            "scrollZoom": True,      # зум колесом мыши / щипком на тачпаде
            "displaylogo": False,
            "modeBarButtonsToAdd": ["drawline", "eraseshape"],
        },
    )
    st.caption("Колесо мыши / щипок -- zoom, зажать и потащить -- pan, двойной клик -- сброс масштаба. "
               "Жёлтая линия -- EMA 20, синяя -- EMA 50.")