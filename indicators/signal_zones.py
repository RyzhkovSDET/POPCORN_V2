"""
Единая точка истины для классификации индикаторов по 5 зонам:
ПОК (покупка) / ППК (предпокупка) / НЕЙТ (нейтрально) / ППД (предпродажа) / ПРД (продажа).
Используется и для RSI, и для столбца "Сигнал", и для "4ч Прогноз",
чтобы цвета/подписи не расходились между разными частями приложения.

Здесь же живёт общий хелпер _cell() -- он же используется в main.py, чтобы
вся таблица (и watchlist, и эти три столбца) красилась одной и той же
палитрой и одним и тем же способом (закрашенный фон ячейки, а не кружочек
рядом с текстом).
"""
from typing import Tuple

# ---------------------------------------------------------------------------
# Общая цветовая палитра ячеек -- единая для всего приложения.
# ---------------------------------------------------------------------------
ZONE_BG = {
    "green": "#2ecc71",   # покупать
    "red": "#e74c3c",     # продавать
    "yellow": "#f1c40f",  # предпокупка (между нейтральным и покупкой)
    "blue": "#3498db",    # предпродажа (между нейтральным и продажей)
    "white": "#d8d8d8",   # нейтрально
}
ZONE_FG = {
    "green": "#ffffff",
    "red": "#ffffff",
    "yellow": "#000000",
    "blue": "#000000",
    "white": "#000000",
}


def cell(text: str, zone: str) -> str:
    """Красит ячейку целиком в цвет зоны (green/red/yellow/blue/white)."""
    if zone not in ZONE_BG:
        return text
    bg, fg = ZONE_BG[zone], ZONE_FG[zone]
    return (
        f'<span style="background:{bg};color:{fg};padding:1px 6px;'
        f'border-radius:4px;font-weight:600;white-space:nowrap;'
        f'font-size:0.72rem;">{text}</span>'
    )


# (нижняя граница исключительно, цветовая зона, подпись, стрелка)
_SCORE_ZONES = [
    (80, "green", "ПОК", "▲"),
    (60, "yellow", "ППК", "▲"),
    (40, "white", "НЕЙТ", ""),
    (20, "blue", "ППД", "▼"),
    (0, "red", "ПРД", "▼"),
]


def classify_score(value: float) -> Tuple[str, str, str]:
    """Классифицирует значение 0-100 (Score / прогноз) -> (зона, подпись, стрелка)."""
    for threshold, zone, label, arrow in _SCORE_ZONES:
        if value > threshold:
            return zone, label, arrow
    return _SCORE_ZONES[-1][1], _SCORE_ZONES[-1][2], _SCORE_ZONES[-1][3]


def classify_rsi(rsi: float) -> Tuple[str, str, str]:
    """
    Классифицирует RSI (0-100) той же палитрой. RSI контрарный: низкий
    RSI (перепроданность) -> зона покупки, высокий -> зона продажи.
    """
    if rsi < 30:
        return "green", "ПОК", "▲"
    elif rsi < 40:
        return "yellow", "ППК", "▲"
    elif rsi < 60:
        return "white", "НЕЙТ", ""
    elif rsi < 70:
        return "blue", "ППД", "▼"
    return "red", "ПРД", "▼"


def signal_str(score: float, compact: bool = False) -> str:
    """Ячейка для столбца 'Сигнал': закрашенный фон + '84 ▲ ПОК'. В compact -- только стрелка."""
    zone, label, arrow = classify_score(score)
    if compact:
        return cell(arrow or "—", zone)
    return cell(f"{score} {arrow} {label}", zone)


def rsi_str(rsi: float, compact: bool = False) -> str:
    """Ячейка для столбца 'RSI'. В compact -- только стрелка."""
    zone, label, arrow = classify_rsi(rsi)
    if compact:
        return cell(arrow or "—", zone)
    return cell(f"{round(rsi, 1)} {arrow} {label}", zone)


def forecast_str(score, compact: bool = False) -> str:
    """Ячейка для '4ч Прогноз'. Та же палитра, что у 'Сигнал'."""
    if score is None:
        return cell("н/д", "white")
    zone, label, arrow = classify_score(score)
    if compact:
        return cell(arrow or "—", zone)
    return cell(f"{arrow} {label}", zone)