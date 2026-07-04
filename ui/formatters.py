"""
Функции, которые превращают "сырое" число/статус в готовую HTML-ячейку
таблицы. Вся закраска идёт через cell() из indicators.signal_zones -- это
единственное место, где определена цветовая палитра (green/red/yellow/
blue/white), чтобы весь проект был визуально согласован.
"""
from datetime import datetime

import pandas as pd

from indicators.signal_zones import cell, cell_titled, two_line_cell, ZONE_BG, ZONE_FG
from ui.config import VALID_QUOTES


def normalize_ticker(raw: str) -> str:
    """
    Добавляет котируемую валюту (USDT по умолчанию), если её ещё нет.

    Важно: тикер должен быть ДЛИННЕЕ самой валюты, иначе "BTC" (это просто
    название монеты, а не пара) ошибочно принимается за уже готовую пару
    только потому, что оканчивается на "BTC" -- один из VALID_QUOTES.
    """
    t = raw.strip().upper()
    already_quoted = any(t.endswith(q) and len(t) > len(q) for q in VALID_QUOTES)
    if not already_quoted:
        t += "USDT"
    return t


def trend_arrow_30m(df: pd.DataFrame, window: int = 1) -> str:
    """
    Треугольник, направление которого зависит от движения цены за
    `window` свечей назад выбранного таймфрейма (см. ui.config.TREND_WINDOW_OPTIONS).
    window=1 -- сравнение с предыдущей свечой (самое чувствительное,
    шумное), window=10 -- с 10 свечами назад (сглаженный, более надёжный тренд).
    """
    if df is None or len(df) < window + 1:
        return cell("▬", "white")
    price_now = df["close"].iloc[-1]
    price_prev = df["close"].iloc[-(window + 1)]
    if price_now > price_prev:
        return cell("▲", "green")
    elif price_now < price_prev:
        return cell("▼", "red")
    return cell("▬", "white")


def pct_change_str(pct) -> str:
    if pct is None:
        return cell("н/д", "white")
    zone = "green" if pct > 0 else "red" if pct < 0 else "white"
    sign = "+" if pct > 0 else ""
    return cell(f"{sign}{pct:.2f}%", zone)


def format_volume(vol) -> str:
    if vol is None:
        return "н/д"
    if vol >= 1_000_000:
        return f"{vol / 1_000_000:.2f}M"
    elif vol >= 1_000:
        return f"{vol / 1_000:.2f}K"
    return f"{vol:.2f}"


def format_volume_cell(vol, prev_vol=None) -> str:
    """
    Столбец 'Volume': та же цифра, что и раньше, но теперь с цветом --
    зелёный если объём последней свечи выше предыдущей, красный если ниже.
    """
    if vol is None:
        return cell("н/д", "white")
    text = format_volume(vol)
    if prev_vol is None or prev_vol == 0:
        return cell(text, "white")
    zone = "green" if vol > prev_vol else "red" if vol < prev_vol else "white"
    return cell(text, zone)


def format_global_volume_cell(vol, trend_pct=None) -> str:
    """
    Столбец 'Общий объём': цвет по тому же тренду, что уже посчитан для
    окна '24ч' (изменение объёма в деньгах за 24ч) -- если объём растёт,
    зелёный, если падает, красный.
    """
    if vol is None:
        return cell("н/д", "white")
    text = format_volume(vol)
    if trend_pct is None:
        return cell(text, "white")
    zone = "green" if trend_pct > 0 else "red" if trend_pct < 0 else "white"
    return cell(text, zone)


def _two_line_cell(line1: str, line2: str, zone: str) -> str:
    """Как cell(), но две строки в одной закрашенной ячейке (счётчик сверху, цена снизу)."""
    if zone not in ZONE_BG:
        return f"{line1}<br>{line2}"
    bg, fg = ZONE_BG[zone], ZONE_FG[zone]
    return (
        f'<div style="background:{bg};color:{fg};padding:2px 6px;border-radius:4px;'
        f'font-weight:600;line-height:1.3;font-size:0.66rem;text-align:center;'
        f'white-space:nowrap;">{line1}<br>{line2}</div>'
    )


def format_break_col(count: int, date, price, kind: str) -> str:
    if not count:
        return cell("—", "white")
    zone = "red" if kind == "min" else "green"
    hours_str = "?"
    if date is not None:
        try:
            elapsed = datetime.utcnow() - date.to_pydatetime()
            hours_str = f"{int(elapsed.total_seconds() // 3600)}ч"
        except Exception:
            hours_str = "?"
    price_str = f"{price:,.2f}$" if price is not None else "?"
    return _two_line_cell(f"×{count} ({hours_str})", price_str, zone)


def format_breakout_col(break_counters) -> str:
    """
    Единая ячейка 'Пробой' -- показывает последний пробой (минимума ИЛИ
    максимума). Оба одновременно активными быть не могут: compute_break_counters
    обнуляет счётчик одного направления, как только пробивается другое.
    """
    if not break_counters:
        return cell("—", "white")
    if break_counters.get("max_count"):
        return format_break_col(
            break_counters["max_count"], break_counters["max_date"],
            break_counters["max_price"], "max",
        )
    if break_counters.get("min_count"):
        return format_break_col(
            break_counters["min_count"], break_counters["min_date"],
            break_counters["min_price"], "min",
        )
    return cell("—", "white")


def format_volume_change(pct) -> str:
    if pct is None:
        return cell("н/д", "white")
    zone = "green" if pct > 0 else "red" if pct < 0 else "white"
    return cell(f"{pct:+.1f}%", zone)


FUNDING_TOOLTIP = (
    "Ставка финансирования фьючерса (раз в 8ч между лонгами и шортами). Это "
    "контрарный сигнал перегрузки рынка, а не buy/sell: сильно положительная -- "
    "лонги перегреты (риск лонг-сквиза), сильно отрицательная -- перегреты шорты."
)


def format_funding(rate) -> str:
    """
    Контрарный сигнал перегрузки рынка (не buy/sell): экстрим = риск
    разворота. Пороги подобраны под реальный диапазон funding rate
    (обычно -0.02%..+0.02%, редко выходит за ±0.05%) -- раньше пороги
    были слишком широкими (0.03/0.05), и цвет почти никогда не менялся
    с белого/синего.
    """
    if rate is None:
        return cell_titled("н/д", "white", FUNDING_TOOLTIP)
    if rate > 0.03:
        zone = "red"       # лонги сильно перегреты -- риск лонг-сквиза
    elif rate > 0.015:
        zone = "yellow"
    elif rate >= -0.005:
        zone = "white"
    elif rate >= -0.015:
        zone = "blue"
    else:
        zone = "green"     # шорты сильно перегреты -- риск шорт-сквиза
    return cell_titled(f"{rate:+.3f}%", zone, FUNDING_TOOLTIP)


def classify_oi_pct(pct_change) -> str:
    """
    OI по величине изменения (не только знаку) -- сильный рост открытого
    интереса = деньги заходят агрессивно, слабое изменение = вялый
    интерес. Раньше цвет был бинарным (только рост/падение), теперь 5
    зон по интенсивности, как и у остальных индикаторов.
    """
    if pct_change is None:
        return "white"
    if pct_change > 5:
        return "green"
    elif pct_change > 1:
        return "yellow"
    elif pct_change >= -1:
        return "white"
    elif pct_change >= -5:
        return "blue"
    return "red"


OI_TOOLTIP = (
    "Открытый интерес (сумма всех открытых фьючерсных позиций) и его изменение. "
    "Сильный рост -- деньги заходят агрессивно, слабое изменение -- вялый интерес."
)


def format_open_interest(oi, pct_change) -> str:
    if oi is None:
        return cell_titled("н/д", "white", OI_TOOLTIP)
    zone = classify_oi_pct(pct_change)
    pct_str = f"{pct_change:+.1f}%" if pct_change is not None else "н/д"
    return two_line_cell(format_volume(oi), pct_str, zone, title=OI_TOOLTIP)