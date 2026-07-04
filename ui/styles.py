"""CSS для всего приложения. Вызови inject_styles() один раз в начале main.py."""
import streamlit as st

APP_CSS = """
<style>
    .block-container { padding-top: 1rem; padding-bottom: 1rem; max-width: 100%; }
    [data-testid="stHorizontalBlock"] { gap: 0.2rem; align-items: center; }
    [data-testid="column"] { padding: 1px 2px !important; }
    /* Вложенная пара колонок (кружочек-выбор + таблетка-имя) внутри ячейки
       Coin -- без этого у неё был свой вертикальный gap как у отдельного
       "элемента" Streamlit, из-за чего строка раздувалась по высоте. */
    div[class*="st-key-watchlist_table"] [data-testid="column"] [data-testid="stHorizontalBlock"] {
        gap: 0.15rem !important; margin: 0 !important;
    }
    [data-testid="column"] p { font-size: 0.72rem; white-space: nowrap; margin-bottom: 0; }
    div.stButton > button { padding: 0px 6px; height: 22px; font-size: 0.68rem; min-height: 22px;
        white-space: nowrap !important; overflow: visible !important; }
    div[class*="st-key-coinname_"] button, div[class*="st-key-pick_"] button {
        white-space: nowrap !important; overflow: hidden !important; text-overflow: ellipsis !important;
    }
    div[data-testid="stForm"] { padding: 0.6rem 0.8rem; }
    .quick-guide { font-size: 0.78em; line-height: 1.45; }
    .col-header { cursor: help; border-bottom: 1px dotted rgba(255,255,255,0.35); font-size: 0.69rem; }
    /* Кнопка удаления -- текстовая, красная, без иконок и подтверждений */
    div[class*="st-key-del_"] button {
        color: #e74c3c !important;
        border: 1px solid rgba(231, 76, 60, 0.35) !important;
        background: transparent !important;
        font-weight: 600 !important;
    }
    div[class*="st-key-del_"] button:hover {
        background: rgba(231, 76, 60, 0.14) !important;
        border-color: #e74c3c !important;
    }
    /* Скринер -- маленькие цветные "таблетки"-кнопки с названием монеты */
    div[class*="st-key-screener_bull_"] button, div[class*="st-key-screener_bear_"] button {
        border: none !important; border-radius: 10px !important;
        padding: 0px 8px !important; height: 22px !important; min-height: 22px !important;
        font-size: 0.62rem !important; font-weight: 600 !important; color: #ffffff !important;
    }
    div[class*="st-key-screener_bull_"] button { background: #2ecc71 !important; }
    div[class*="st-key-screener_bear_"] button { background: #e74c3c !important; }
    div[class*="st-key-screener_bull_"] button:hover,
    div[class*="st-key-screener_bear_"] button:hover { filter: brightness(1.12); }
    div[class*="st-key-screener_bull_"] button:disabled,
    div[class*="st-key-screener_bear_"] button:disabled { opacity: 0.5 !important; color: #ffffff !important; }
    .st-key-watchlist_table [data-testid="stHorizontalBlock"] {
        border-bottom: 1px solid rgba(255, 255, 255, 0.07); border-radius: 4px; transition: background 0.1s ease;
        position: relative;
    }
    .st-key-watchlist_table [data-testid="stHorizontalBlock"]:nth-of-type(even) { background: rgba(255, 255, 255, 0.025); }
    .st-key-watchlist_table [data-testid="stHorizontalBlock"]:hover { background: rgba(255, 255, 255, 0.07); }
</style>
"""


def inject_styles() -> None:
    st.markdown(APP_CSS, unsafe_allow_html=True)