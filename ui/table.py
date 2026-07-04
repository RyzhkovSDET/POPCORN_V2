"""Отрисовка заголовка таблицы watchlist."""
import streamlit as st

from ui.config import COL_KEYS, COL_WIDTHS, COLUMN_TOOLTIPS


def render_column_header_row():
    cols = st.columns(COL_WIDTHS)
    for col, key in zip(cols, COL_KEYS):
        tooltip = COLUMN_TOOLTIPS.get(key, "")
        # Столбец "Прогноз" -- заголовок показывает выбранный пользователем
        # горизонт (2ч/4ч/6ч/12ч/1д), а не жёстко зашитое "4ч".
        display_key = key
        if key == "Прогноз":
            horizon_label = st.session_state.get("forecast_horizon_label", "4ч")
            display_key = f"{horizon_label} Прогноз"
        if key and tooltip:
            col.markdown(f"<span class='col-header' title='{tooltip}'><strong>{display_key}</strong></span>", unsafe_allow_html=True)
        else:
            col.markdown(f"**{display_key}**")