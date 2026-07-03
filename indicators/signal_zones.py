"""
Единая точка истины для классификации индикаторов по 5 зонам:
ПОК (покупка) / ППК (предпокупка) / НЕЙТ (нейтрально) / ППД (предпродажа) / ПРД (продажа).

Используется и для RSI, и для столбца "Сигнал", и для "4ч Прогноз",
чтобы цвета/подписи не расходились между разными частями приложения.

Все функции возвращают HTML (эмодзи-значок обёрнут в <span> с уменьшенным
font-size, чтобы значок был визуально мельче окружающего текста) --
рендерить через st.markdown(..., unsafe_allow_html=True), не st.write().
"""
from typing import Tuple

# Насколько значок мельче окружающего текста (0.5 = вдвое мельче)
EMOJI_SIZE_EM = 0.5

# (нижняя граница исключительно, эмодзи-цвет, подпись, стрелка)
_SCORE_ZONES = [
    (80, "🟢", "ПОК", "▲"),
    (60, "🟡", "ППК", "▲"),
    (40, "⚪", "НЕЙТ", ""),
    (20, "🔵", "ППД", "▼"),
    (0, "🔴", "ПРД", "▼"),
]


def _mini(char: str) -> str:
    """Оборачивает символ (эмодзи/стрелку) в span с уменьшенным font-size."""
    if not char:
        return ""
    return f'<span style="font-size:{EMOJI_SIZE_EM}em">{char}</span>'


def classify_score(value: float) -> Tuple[str, str, str]:
    """Классифицирует значение 0-100 (Score / прогноз) -> (эмодзи, подпись, стрелка)."""
    for threshold, emoji, label, arrow in _SCORE_ZONES:
        if value > threshold:
            return emoji, label, arrow
    return _SCORE_ZONES[-1][1], _SCORE_ZONES[-1][2], _SCORE_ZONES[-1][3]


def classify_rsi(rsi: float) -> Tuple[str, str, str]:
    """
    Классифицирует RSI (0-100) той же палитрой. RSI контрарный: низкий
    RSI (перепроданность) -> зона покупки, высокий -> зона продажи.
    """
    if rsi < 30:
        return "🟢", "ПОК", "▲"
    elif rsi < 40:
        return "🟡", "ППК", "▲"
    elif rsi < 60:
        return "⚪", "НЕЙТ", ""
    elif rsi < 70:
        return "🔵", "ППД", "▼"
    return "🔴", "ПРД", "▼"


def signal_str(score: float, compact: bool = False) -> str:
    """HTML для столбца 'Сигнал': '84 [мелкий 🟢▲] ПОК'. В compact -- только значок."""
    emoji, label, arrow = classify_score(score)
    dot = _mini(emoji) + _mini(arrow)
    return dot if compact else f"{score}{dot} {label}"


def rsi_str(rsi: float, compact: bool = False) -> str:
    """HTML для столбца 'RSI'. В compact -- только значок."""
    emoji, label, arrow = classify_rsi(rsi)
    dot = _mini(emoji) + _mini(arrow)
    return dot if compact else f"{round(rsi, 1)}{dot} {label}"


def forecast_str(score, compact: bool = False) -> str:
    """HTML для '4ч Прогноз'. Та же палитра, что у 'Сигнал'."""
    if score is None:
        return _mini("⚪") if compact else _mini("⚪") + " н/д"
    emoji, label, arrow = classify_score(score)
    dot = _mini(emoji) + _mini(arrow)
    return dot if compact else f"{dot} {label}"