"""
Единая точка истины для классификации индикаторов по 5 зонам:
ПОК (покупка) / ППК (предпокупка) / НЕЙТ (нейтрально) / ППД (предпродажа) / ПРД (продажа).
Используется и для RSI, и для столбца "Сигнал", и для "Прогноз",
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


def cell_titled(text: str, zone: str, title: str = "") -> str:
    """Как cell(), но с подсказкой при наведении (title)."""
    if zone not in ZONE_BG:
        return text
    bg, fg = ZONE_BG[zone], ZONE_FG[zone]
    title_attr = f' title="{title}"' if title else ""
    return (
        f'<span{title_attr} style="background:{bg};color:{fg};padding:1px 6px;'
        f'border-radius:4px;font-weight:600;white-space:nowrap;'
        f'font-size:0.72rem;">{text}</span>'
    )


def two_line_cell(line1: str, line2: str, zone: str, title: str = "") -> str:
    """
    Как cell(), но две строки в одной закрашенной ячейке: значение сверху,
    буквенное пояснение снизу (тот же формат, что уже был у столбца 'Пробой').
    title -- необязательная подсказка при наведении (например, полное
    название свечного паттерна для столбца Pattern).
    """
    if zone not in ZONE_BG:
        return f"{line1}<br>{line2}"
    bg, fg = ZONE_BG[zone], ZONE_FG[zone]
    title_attr = f' title="{title}"' if title else ""
    return (
        f'<div{title_attr} style="background:{bg};color:{fg};padding:2px 6px;border-radius:4px;'
        f'font-weight:600;line-height:1.25;font-size:0.68rem;text-align:center;'
        f'white-space:nowrap;">{line1}<br>{line2}</div>'
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


SIGNAL_TOOLTIP = (
    "Сводный скор 0-100 из RSI, разрыва EMA(20/50), положения цены "
    "относительно EMA и momentum MACD -- общая оценка текущего состояния монеты."
)
RSI_TOOLTIP = (
    "Индекс относительной силы (0-100). Ниже 30 -- перепроданность (потенциал "
    "к покупке), выше 70 -- перекупленность (потенциал к продаже)."
)
FORECAST_TOOLTIP = (
    "Мультитаймфрейм-прогноз: EMA-тренд + RSI + ADX + свечной паттерн на "
    "выбранном горизонте. Та же палитра 0-100, что и у 'Сигнал'."
)


def signal_str(score: float) -> str:
    """Ячейка для столбца 'Сигнал': сверху число, снизу буква+стрелка."""
    zone, label, arrow = classify_score(score)
    return two_line_cell(f"{score}", f"{arrow} {label}".strip(), zone, title=SIGNAL_TOOLTIP)


def rsi_str(rsi: float) -> str:
    """Ячейка для столбца 'RSI'."""
    zone, label, arrow = classify_rsi(rsi)
    return two_line_cell(f"{round(rsi, 1)}", f"{arrow} {label}".strip(), zone, title=RSI_TOOLTIP)


def forecast_str(score) -> str:
    """Ячейка для столбца 'Прогноз'. Та же палитра, что у 'Сигнал'."""
    if score is None:
        return cell_titled("н/д", "white", FORECAST_TOOLTIP)
    zone, label, arrow = classify_score(score)
    return two_line_cell(f"{round(score)}", f"{arrow} {label}".strip(), zone, title=FORECAST_TOOLTIP)


def classify_ema_gap(gap_pct: float) -> Tuple[str, str, str]:
    """Разрыв EMA(20) от EMA(50) в % -- чем больше быстрая выше медленной, тем сильнее бычий тренд."""
    if gap_pct > 0.5:
        return "green", "ПОК", "▲"
    elif gap_pct > 0.1:
        return "yellow", "ППК", "▲"
    elif gap_pct > -0.1:
        return "white", "НЕЙТ", ""
    elif gap_pct > -0.5:
        return "blue", "ППД", "▼"
    return "red", "ПРД", "▼"


EMA_TOOLTIP = (
    "Разрыв между быстрой EMA(20) и медленной EMA(50) в %. Положительный и "
    "растущий -- быстрая выше медленной, бычий тренд; отрицательный -- медвежий."
)


def ema_str(ema_fast: float, ema_slow: float) -> str:
    """Ячейка 'EMA': сверху % разрыва EMA(20)/EMA(50), снизу буква+стрелка."""
    gap_pct = ((ema_fast - ema_slow) / ema_slow) * 100 if ema_slow else 0.0
    zone, label, arrow = classify_ema_gap(gap_pct)
    return two_line_cell(f"{gap_pct:+.2f}%", f"{arrow} {label}".strip(), zone, title=EMA_TOOLTIP)


def classify_atr_pct(atr_pct: float) -> Tuple[str, str]:
    """
    ATR в % от цены -- волатильность, не направление. Низкая = спокойный
    рынок (обычно безопаснее для входа), высокая = риск резких движений
    в любую сторону -- поэтому зона read как "осторожно", а не "продавай".
    """
    if atr_pct < 1:
        return "green", "низкая"
    elif atr_pct < 2:
        return "yellow", "умерен."
    elif atr_pct < 3.5:
        return "white", "средняя"
    elif atr_pct < 5:
        return "blue", "повышен."
    return "red", "высокая"


ATR_TOOLTIP = (
    "Average True Range -- средний размах свечи, показатель волатильности "
    "(не направления). Низкая -- спокойный рынок, высокая -- риск резких движений в обе стороны."
)


def atr_str(atr: float, price: float) -> str:
    """Ячейка 'ATR': сверху абсолютное значение, снизу % от цены. Цвет ячейки
    по-прежнему показывает зону волатильности, но текстовая метка (низкая/
    средняя/высокая) убрана -- по запросу оставлены только цифры."""
    atr_pct = (atr / price) * 100 if price else 0.0
    zone, _label = classify_atr_pct(atr_pct)
    return two_line_cell(f"{atr:.4f}", f"{atr_pct:.1f}%", zone, title=ATR_TOOLTIP)


def classify_macd_pct(pct: float) -> Tuple[str, str, str]:
    """Разница MACD/сигнальной линии в % от цены -- сила бычьего/медвежьего импульса."""
    if pct > 0.05:
        return "green", "бычий", "▲"
    elif pct > 0.01:
        return "yellow", "сл.быч", "▲"
    elif pct > -0.01:
        return "white", "нейт", ""
    elif pct > -0.05:
        return "blue", "сл.медв", "▼"
    return "red", "медвежий", "▼"


MACD_TOOLTIP = (
    "Разница линии MACD и сигнальной линии в % от цены -- сила бычьего/медвежьего "
    "импульса. Чем дальше от нуля, тем сильнее momentum в соответствующую сторону."
)


def macd_cell(macd_val: float, macd_sig: float, price: float) -> str:
    """Ячейка 'MACD': сверху значение MACD, снизу буквенное пояснение импульса."""
    pct = ((macd_val - macd_sig) / price) * 100 if price else 0.0
    zone, label, arrow = classify_macd_pct(pct)
    return two_line_cell(f"{macd_val:.4f}", f"{arrow} {label}".strip(), zone, title=MACD_TOOLTIP)


# Свечные паттерны из ui.metrics.detect_pattern -> (краткая подпись под
# таблицей, полное описание в подсказке при наведении мышью).
PATTERN_LABELS = {
    "bull_engulf": ("Погл.быч", "Бычье поглощение -- потенциальный разворот вверх"),
    "bear_engulf": ("Погл.медв", "Медвежье поглощение -- потенциальный разворот вниз"),
    "hammer": ("Молот", "Молот -- разворот вверх после падения"),
    "shoot_star": ("Пад.звезда", "Падающая звезда -- разворот вниз после роста"),
    "doji": ("Доджи", "Доджи -- нерешительность рынка, направление неясно"),
    "hh_hl": ("HH/HL", "Выше хай / выше лоу -- восходящая структура цены"),
    "lh_ll": ("LH/LL", "Ниже хай / ниже лоу -- нисходящая структура цены"),
    "none": ("—", "Явный паттерн не обнаружен"),
    "-": ("—", "Недостаточно данных для определения паттерна"),
}


def pattern_cell(pattern_name: str, bias: str) -> str:
    """
    Ячейка 'Pattern': сверху сигнал (ПОК/ПРД/НЕЙТ), снизу краткое имя
    паттерна. Полное название паттерна -- в подсказке при наведении мышью.
    """
    if bias == "bull":
        zone, label = "green", "ПОК"
    elif bias == "bear":
        zone, label = "red", "ПРД"
    else:
        zone, label = "white", "НЕЙТ"
    short, full = PATTERN_LABELS.get(pattern_name, ("—", "Нет данных"))
    return two_line_cell(label, short, zone, title=full)