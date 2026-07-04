"""
Точка входа. Сама логика (таблица, график, справка) вынесена в модули
ui/watchlist.py, ui/chart.py, ui/quick_guide.py, ui/analysis_sidebar.py --
здесь только общая инициализация страницы и порядок вызова блоков.
"""
import streamlit as st

from storage.coins_storage import add_coin, load_coins
from ui.analysis_sidebar import render_analysis_sidebar
from ui.backtest_lab import render_watchlist_backtest_summary
from ui.chart import render_chart
from ui.formatters import normalize_ticker
from ui.quick_guide import render_quick_guide
from ui.styles import inject_styles
from ui.watchlist import render_watchlist

st.set_page_config(layout="wide", page_title="POPCORN v2")
inject_styles()

# ---------------------------------------------------------------------------
# Session state -- общее для всех модулей ниже. Настройки, специфичные
# только для одного модуля (например селекторы таймфреймов watchlist),
# инициализируются внутри самого этого модуля.
# ---------------------------------------------------------------------------
if "coins" not in st.session_state:
    st.session_state.coins = load_coins()
if "delete_diagnostic" not in st.session_state:
    st.session_state.delete_diagnostic = None
if "selected_coin" not in st.session_state:
    st.session_state.selected_coin = None
if "selected_coin_metrics" not in st.session_state:
    st.session_state.selected_coin_metrics = None

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

st.markdown('<div style="margin-top:-28px"></div>', unsafe_allow_html=True)  # подтягиваем секцию ближе к форме добавления

# ---------------------------------------------------------------------------
# Основные блоки страницы
# ---------------------------------------------------------------------------
render_watchlist()
render_watchlist_backtest_summary()
render_analysis_sidebar()
render_chart()
render_quick_guide()