"""CSS для всего приложения. Вызови inject_styles() один раз в начале main.py."""
import streamlit as st

APP_CSS = """
<style>
    .block-container { padding-top: 1rem; padding-bottom: 1rem; max-width: 100%; }
    [data-testid="stHorizontalBlock"] { gap: 0.2rem; align-items: center; }
    [data-testid="column"] { padding: 1px 2px !important; }
    [data-testid="column"] p { font-size: 0.72rem; white-space: nowrap; margin-bottom: 0; }
    div.stButton > button { padding: 0px 6px; height: 22px; font-size: 0.68rem; min-height: 22px;
        white-space: nowrap !important; overflow: visible !important; }
    div[data-testid="stForm"] { padding: 0.6rem 0.8rem; }
    .quick-guide { font-size: 0.78em; line-height: 1.45; }
    .buy-hint { color: #2ecc71; font-size: 0.62rem; font-weight: 600; white-space: nowrap; }
    .sell-hint { color: #e74c3c; font-size: 0.62rem; font-weight: 600; white-space: nowrap; }
    .group-header { text-align: center; font-size: 0.69rem; font-weight: 700;
        color: rgba(255,255,255,0.55); text-transform: uppercase; letter-spacing: 0.03em; }
    .col-header { cursor: help; border-bottom: 1px dotted rgba(255,255,255,0.35); font-size: 0.69rem; }
    /* Кнопка удаления -- текстовая, красная, без иконок и подтверждений */
    button[class*="st-key-del_"] {
        color: #e74c3c !important;
        border: 1px solid rgba(231, 76, 60, 0.35) !important;
        background: transparent !important;
        font-weight: 600 !important;
    }
    button[class*="st-key-del_"]:hover {
        background: rgba(231, 76, 60, 0.14) !important;
        border-color: #e74c3c !important;
    }
    button[title^="Открыть анализ"] { font-weight: 600 !important; text-decoration: underline; text-underline-offset: 3px; }
    .st-key-watchlist_table [data-testid="stHorizontalBlock"] {
        border-bottom: 1px solid rgba(255, 255, 255, 0.07); border-radius: 4px; transition: background 0.1s ease;
    }
    .st-key-watchlist_table [data-testid="stHorizontalBlock"]:nth-of-type(even) { background: rgba(255, 255, 255, 0.025); }
    .st-key-watchlist_table [data-testid="stHorizontalBlock"]:hover { background: rgba(255, 255, 255, 0.07); }
</style>
"""


def inject_styles() -> None:
    st.markdown(APP_CSS, unsafe_allow_html=True)