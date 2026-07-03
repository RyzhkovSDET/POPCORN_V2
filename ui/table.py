"""Отрисовка служебных строк таблицы watchlist: подсказки, групповой и обычный заголовок."""
import streamlit as st

from ui.config import COL_KEYS, COL_WIDTHS, COLUMN_GROUPS, COLUMN_TOOLTIPS


def render_hint_row(hints_dict, css_class):
    cols = st.columns(COL_WIDTHS)
    for col, key in zip(cols, COL_KEYS):
        text = hints_dict.get(key, "")
        col.markdown(f"<span class='{css_class}'>{text}</span>" if text else "&nbsp;", unsafe_allow_html=True)


def render_group_header_row():
    """Строка над основным заголовком: общее название для группы столбцов (например 'Объём')."""
    cols = st.columns(COL_WIDTHS)
    prev_group = None
    for col, key in zip(cols, COL_KEYS):
        group = COLUMN_GROUPS.get(key)
        if group and group != prev_group:
            col.markdown(f"<div class='group-header'>{group}</div>", unsafe_allow_html=True)
        else:
            col.markdown("&nbsp;", unsafe_allow_html=True)
        prev_group = group


def render_column_header_row():
    cols = st.columns(COL_WIDTHS)
    for col, key in zip(cols, COL_KEYS):
        tooltip = COLUMN_TOOLTIPS.get(key, "")
        if key and tooltip:
            col.markdown(f"<span class='col-header' title='{tooltip}'><strong>{key}</strong></span>", unsafe_allow_html=True)
        else:
            col.markdown(f"**{key}**")