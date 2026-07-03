"""
Мини-виджет скринера рынка -- компактные цветные "таблетки"-кнопки с
названием монеты (зелёные -- бычьи сигналы, красные -- медвежьи). Клик по
монете сразу добавляет её в watchlist. Количество медвежьих подстраивается
под количество бычьих (симметрия), а не жёстко фиксировано.
"""
import streamlit as st

from api.screener import fetch_bearish_candidates, fetch_bullish_candidates
from storage.coins_storage import add_coin, load_coins
from ui.config import SCREENER_DROP_THRESHOLD_PCT, SCREENER_RISE_THRESHOLD_PCT, SCREENER_TOP_N


def _text_width_units(symbol: str) -> float:
    """
    Примерная 'ширина' текста в условных единицах -- иероглифы (китайский и
    т.п.) визуально почти вдвое шире латинских букв при том же font-size,
    поэтому считаем их за 2 единицы, а не за 1.
    """
    units = 0.0
    for ch in symbol:
        code = ord(ch)
        is_wide = (
            0x4E00 <= code <= 0x9FFF or   # CJK Unified Ideographs
            0x3400 <= code <= 0x4DBF or   # CJK Extension A
            0xF900 <= code <= 0xFAFF      # CJK Compatibility Ideographs
        )
        units += 2.0 if is_wide else 1.0
    return units


def _render_pill_row(candidates, key_prefix: str) -> None:
    if not candidates:
        return
    # Ширина колонки пропорциональна длине названия монеты -- иначе Streamlit
    # делает все колонки одинаковыми, и таблетка не может стать шире/уже
    # своего текста (CSS внутри фиксированной по ширине колонки бессилен).
    widths = [max(_text_width_units(c["symbol"]), 2) + 1.5 for c in candidates]
    cols = st.columns(widths)
    current_coins = set(st.session_state.coins)

    for col, c in zip(cols, candidates):
        ticker = f"{c['symbol']}USDT"
        already_added = ticker in current_coins
        with col:
            clicked = st.button(
                c["symbol"], key=f"{key_prefix}_{c['symbol']}",
                disabled=already_added, help="Уже в watchlist" if already_added else "Добавить в watchlist",
            )
        if clicked and not already_added:
            success, _ = add_coin(ticker)
            if success:
                st.session_state.coins = load_coins()
                st.rerun()


def render_screener_widget() -> None:
    bullish = fetch_bullish_candidates(top_n=SCREENER_TOP_N, rise_threshold_pct=SCREENER_RISE_THRESHOLD_PCT)
    # Медвежьих показываем ровно столько же, сколько нашлось бычьих (симметрия).
    bearish_n = len(bullish) if bullish else SCREENER_TOP_N
    bearish = fetch_bearish_candidates(top_n=bearish_n, drop_threshold_pct=SCREENER_DROP_THRESHOLD_PCT)

    _render_pill_row(bullish, "screener_bull")
    _render_pill_row(bearish, "screener_bear")