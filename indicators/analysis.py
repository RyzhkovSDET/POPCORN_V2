"""
Структурный анализ монеты для боковой панели: уровни поддержки/
сопротивления, коррекции Фибоначчи, объёмный профиль и расчёт
риск/прибыль. Используется, когда пользователь кликает на монету
в watchlist.
"""
from dataclasses import dataclass
from typing import List, Optional, Tuple

import pandas as pd


@dataclass
class Level:
    price: float
    strength: int  # сколько раз цена коснулась этого уровня -- чем больше, тем значимее


# ---------------------------------------------------------------------------
# Поддержка / сопротивление
# ---------------------------------------------------------------------------

def find_support_resistance(
    daily_df: pd.DataFrame, window: int = 3, max_levels: int = 5, tolerance_pct: float = 0.5
) -> Tuple[List[Level], List[Level]]:
    """
    Находит уровни как локальные экстремумы (свеча выше/ниже `window`
    соседей с обеих сторон), группирует близкие цены в один уровень
    (в пределах tolerance_pct%) и сортирует по силе.

    Возвращает (support, resistance), оба относительно текущей цены
    (последнее закрытие daily_df).
    """
    if daily_df is None or len(daily_df) < window * 2 + 1:
        return [], []

    highs, lows = daily_df["high"], daily_df["low"]
    price_now = daily_df["close"].iloc[-1]

    swing_highs, swing_lows = [], []
    for i in range(window, len(daily_df) - window):
        segment_h = highs.iloc[i - window:i + window + 1]
        segment_l = lows.iloc[i - window:i + window + 1]
        if highs.iloc[i] == segment_h.max():
            swing_highs.append(float(highs.iloc[i]))
        if lows.iloc[i] == segment_l.min():
            swing_lows.append(float(lows.iloc[i]))

    def _cluster(points: List[float]) -> List[Level]:
        if not points:
            return []
        points = sorted(points)
        clusters = [[points[0]]]
        for p in points[1:]:
            if abs(p - clusters[-1][-1]) / clusters[-1][-1] * 100 <= tolerance_pct:
                clusters[-1].append(p)
            else:
                clusters.append([p])
        return [Level(price=sum(c) / len(c), strength=len(c)) for c in clusters]

    resistance = sorted(
        [lvl for lvl in _cluster(swing_highs) if lvl.price > price_now],
        key=lambda lvl: lvl.price,
    )[:max_levels]
    support = sorted(
        [lvl for lvl in _cluster(swing_lows) if lvl.price < price_now],
        key=lambda lvl: -lvl.price,
    )[:max_levels]

    return support, resistance


def nearest_levels(support: List[Level], resistance: List[Level]) -> Tuple[Optional[Level], Optional[Level]]:
    """Ближайшая поддержка снизу и ближайшее сопротивление сверху."""
    return (support[0] if support else None), (resistance[0] if resistance else None)


# ---------------------------------------------------------------------------
# Risk / reward
# ---------------------------------------------------------------------------

def calculate_risk_reward(price: float, support: Optional[Level], resistance: Optional[Level]) -> dict:
    """
    risk_pct -- % от цены до ближайшей поддержки (потенциальный стоп).
    reward_pct -- % от цены до ближайшего сопротивления (потенциальный тейк).
    ratio -- reward_pct / risk_pct (больше = лучше сделка).
    Любое поле может быть None, если соответствующего уровня нет.
    """
    result = {"risk_pct": None, "reward_pct": None, "ratio": None}
    if support is not None and price > 0:
        result["risk_pct"] = ((price - support.price) / price) * 100
    if resistance is not None and price > 0:
        result["reward_pct"] = ((resistance.price - price) / price) * 100
    if result["risk_pct"] and result["risk_pct"] > 0 and result["reward_pct"]:
        result["ratio"] = result["reward_pct"] / result["risk_pct"]
    return result


# ---------------------------------------------------------------------------
# Fibonacci
# ---------------------------------------------------------------------------

def find_swing_range(daily_df: pd.DataFrame, lookback: int = 30) -> Tuple[Optional[float], Optional[float]]:
    """Мин/макс за последние `lookback` дневных свечей -- база для Фибоначчи."""
    if daily_df is None or daily_df.empty:
        return None, None
    recent = daily_df.tail(lookback)
    return float(recent["low"].min()), float(recent["high"].max())


def fibonacci_levels(swing_low: float, swing_high: float) -> dict:
    """Уровни коррекции Фибоначчи между минимумом и максимумом диапазона."""
    diff = swing_high - swing_low
    ratios = [0.0, 0.236, 0.382, 0.5, 0.618, 0.786, 1.0]
    return {f"{r * 100:.1f}%": swing_high - diff * r for r in ratios}


# ---------------------------------------------------------------------------
# Объёмный профиль
# ---------------------------------------------------------------------------

def volume_profile(daily_df: pd.DataFrame, bins: int = 20) -> List[Tuple[float, float]]:
    """
    Разбивает диапазон цены на `bins` корзин, суммирует объём в деньгах
    (qav) свечей, чья средняя цена (high+low)/2 попала в корзину.
    Возвращает [(price_level, volume), ...], отсортировано по объёму убыв.
    """
    if daily_df is None or daily_df.empty or "qav" not in daily_df.columns:
        return []

    df = daily_df.copy()
    df["qav"] = pd.to_numeric(df["qav"], errors="coerce").fillna(0)
    price_min, price_max = float(df["low"].min()), float(df["high"].max())
    if price_max <= price_min:
        return []

    bin_edges = [price_min + (price_max - price_min) * i / bins for i in range(bins + 1)]
    volumes = [0.0] * bins

    for _, row in df.iterrows():
        mid = (row["high"] + row["low"]) / 2
        idx = min(int((mid - price_min) / (price_max - price_min) * bins), bins - 1)
        volumes[idx] += row["qav"]

    levels = [((bin_edges[i] + bin_edges[i + 1]) / 2, volumes[i]) for i in range(bins)]
    return sorted(levels, key=lambda x: -x[1])


def point_of_control(daily_df: pd.DataFrame, bins: int = 20) -> Optional[float]:
    """Цена с наибольшим объёмом торгов (POC) -- часто действует как магнит для цены."""
    profile = volume_profile(daily_df, bins=bins)
    return profile[0][0] if profile else None


# ---------------------------------------------------------------------------
# Тексты гайдов -- короткий (для help=/tooltip) и полный (для st.expander)
# ---------------------------------------------------------------------------

GUIDES = {
    "support_resistance": {
        "short": "Уровни, где цена исторически разворачивалась",
        "full": (
            "Поддержка — цена ниже текущей, где раньше вставали покупатели и цена "
            "отскакивала вверх. Сопротивление — цена выше текущей, где вставали "
            "продавцы и цена разворачивалась вниз.\n\n"
            "Чем больше касаний уровня (сила) — тем он значимее. Если цена "
            "приближается к поддержке — возможен отскок вверх. Если пробивает "
            "поддержку вниз — тренд может продолжиться дальше. То же зеркально "
            "для сопротивления."
        ),
    },
    "fibonacci": {
        "short": "Уровни коррекции внутри последнего движения цены",
        "full": (
            "Строятся между последним значимым минимумом и максимумом за выбранный "
            "период. 38.2% и 61.8% — самые популярные уровни для входа при откате "
            "внутри тренда: если тренд восходящий и цена откатилась к 61.8%, это "
            "часто рассматривают как точку для покупки с расчётом на продолжение "
            "роста. Работает статистически чаще, чем случайно, но не гарантированно "
            "— используй вместе с поддержкой/сопротивлением для подтверждения."
        ),
    },
    "volume_profile": {
        "short": "На каких ценах прошло больше всего объёма",
        "full": (
            "Показывает, на каких ценовых уровнях было совершено больше всего сделок "
            "в деньгах за период. Уровень с максимальным объёмом (POC — point of "
            "control) часто действует как магнит: цена туда притягивается и может "
            "какое-то время консолидироваться. Зоны с низким объёмом цена обычно "
            "проходит быстро, без задержки."
        ),
    },
    "risk_reward": {
        "short": "Соотношение потенциального убытка и прибыли",
        "full": (
            "Риск — расстояние в % от текущей цены до ближайшей поддержки (куда "
            "можно поставить стоп-лосс). Прибыль — расстояние в % до ближайшего "
            "сопротивления (куда можно поставить тейк-профит). Ratio 1:2 означает, "
            "что потенциальная прибыль вдвое больше риска. Общепринятое правило — "
            "не входить в сделку при ratio хуже 1:1.5, иначе даже при 50% "
            "угадываний сделок ты будешь в минусе после комиссий."
        ),
    },
}