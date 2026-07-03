"""
Функции, которые превращают "сырое" число/статус в готовую HTML-ячейку
таблицы. Вся закраска идёт через cell() из indicators.signal_zones -- это
единственное место, где определена цветовая палитра (green/red/yellow/
blue/white), чтобы весь проект был визуально согласован.
"""
from datetime import datetime

import pandas as pd

from indicators.signal_zones import cell, ZONE_BG, ZONE_FG
from ui.config import TREND_LOOKBACK_MIN, VALID_QUOTES


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


def trend_arrow_30m(df: pd.DataFrame) -> str:
    """Треугольник, направление которого зависит от движения цены за последние 30 минут (1m свечи)."""
    if df is None or len(df) < TREND_LOOKBACK_MIN + 1:
        return cell("▬", "white")
    price_now = df["close"].iloc[-1]
    price_prev = df["close"].iloc[-(TREND_LOOKBACK_MIN + 1)]
    if price_now > price_prev:
        return cell("▲", "green")
    elif price_now < price_prev:
        return cell("▼", "red")
    return cell("▬", "white")


def pct_change_str(pct) -> str:
    zone = "green" if pct > 0 else "red" if pct < 0 else "white"
    sign = "+" if pct > 0 else ""
    return cell(f"{sign}{pct:.2f}%", zone)


def macd_signal_str(macd_val, macd_sig) -> str:
    zone = "green" if macd_val > macd_sig else "red"
    return cell(f"{macd_val:.4f}", zone)


def format_volume(vol) -> str:
    if vol is None:
        return "н/д"
    if vol >= 1_000_000:
        return f"{vol / 1_000_000:.2f}M"
    elif vol >= 1_000:
        return f"{vol / 1_000:.2f}K"
    return f"{vol:.2f}"


def pattern_str(bias: str, compact: bool = False) -> str:
    if bias == "bull":
        zone, label = "green", "ПОК"
    elif bias == "bear":
        zone, label = "red", "ПРД"
    else:
        zone, label = "white", "НЕЙТ"
    return cell(label, zone)


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


def format_break_col(count: int, date, price, kind: str, compact: bool = False) -> str:
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


def format_breakout_col(break_counters, compact: bool = False) -> str:
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
            break_counters["max_price"], "max", compact=compact,
        )
    if break_counters.get("min_count"):
        return format_break_col(
            break_counters["min_count"], break_counters["min_date"],
            break_counters["min_price"], "min", compact=compact,
        )
    return cell("—", "white")


def format_volume_change(pct, compact: bool = False) -> str:
    if pct is None:
        return cell("н/д", "white")
    zone = "green" if pct > 0 else "red" if pct < 0 else "white"
    return cell(f"{pct:+.1f}%", zone)


def format_funding(rate, compact: bool = False) -> str:
    """Контрарный сигнал перегрузки рынка (не buy/sell): экстрим = риск разворота."""
    if rate is None:
        return cell("н/д", "white")
    if rate > 0.05:
        zone = "red"
    elif rate > 0.03:
        zone = "yellow"
    elif rate >= -0.01:
        zone = "white"
    elif rate >= -0.05:
        zone = "blue"
    else:
        zone = "green"
    return cell(f"{rate:+.3f}%", zone)


def format_open_interest(oi, pct_change, compact: bool = False) -> str:
    if oi is None:
        return cell("н/д", "white")
    zone = "green" if (pct_change or 0) > 0 else "red" if (pct_change or 0) < 0 else "white"
    pct_str = f"{pct_change:+.1f}%" if pct_change is not None else "н/д"
    return cell(f"{format_volume(oi)} {pct_str}", zone)