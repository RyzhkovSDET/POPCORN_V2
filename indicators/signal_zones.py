"""
Единая точка истины для классификации индикаторов по 5 зонам:
ПОК (покупка) / ППК (предпокупка) / НЕЙТ (нейтрально) / ППД (предпродажа) / ПРД (продажа).

Используется и для RSI, и для столбца "Сигнал", и для "4ч Прогноз",
чтобы цвета/подписи не расходились между разными частями приложения.
"""
from typing import Tuple

# (нижняя граница исключительно, эмодзи-цвет, подпись, стрелка)
_SCORE_ZONES = [
    (80, "🟢", "ПОК", "▲"),
    (60, "🟡", "ППК", "▲"),
    (40, "⚪", "НЕЙТ", ""),
    (20, "🔵", "ППД", "▼"),
    (0, "🔴", "ПРД", "▼"),
]


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
    """Строка для столбца 'Сигнал': '84🟢▲ ПОК'. В compact -- только эмодзи."""
    emoji, label, arrow = classify_score(score)
    return emoji if compact else f"{score}{emoji}{arrow} {label}"


def rsi_str(rsi: float, compact: bool = False) -> str:
    """Строка для столбца 'RSI': '28.4🟢▲ ПОК'. В compact -- только эмодзи."""
    emoji, label, arrow = classify_rsi(rsi)
    return emoji if compact else f"{round(rsi, 1)}{emoji}{arrow} {label}"


def forecast_str(score, compact: bool = False) -> str:
    """Строка для '4ч Прогноз'. Та же палитра, что у 'Сигнал'."""
    if score is None:
        return "⚪" if compact else "⚪ н/д"
    emoji, label, arrow = classify_score(score)
    return emoji if compact else f"{emoji}{arrow} {label}"