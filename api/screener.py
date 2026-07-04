"""
Сканер рынка: ищет монеты с самым сильным движением (вверх или вниз) на
высоком объёме -- то есть туда, где сейчас активнее всего торгуют трейдеры.

Источник -- CoinGecko /coins/markets, отсортировано по капитализации
(market_cap_desc). Топ-100 по капитализации уже сам по себе исключает
мусорные/скам-токены -- на такую капитализацию они не дорастают. Это даёт
тот же результат, что и проверка "существует ли монета на реальном
блокчейне", но без необходимости дёргать API конкретных обозревателей
блокчейна по каждой монете отдельно.
"""
import logging
from typing import List, TypedDict

import requests
import streamlit as st

from api.coingecko_data import throttle_coingecko

logger = logging.getLogger(__name__)

COINGECKO_BASE_URL = "https://api.coingecko.com/api/v3"
REQUEST_TIMEOUT = 8
MARKETS_POOL_SIZE = 100  # топ монет по капитализации, рассматриваемых скринером


class ScreenerCandidate(TypedDict):
    symbol: str          # "BTC"
    name: str             # "Bitcoin"
    price: float
    pct_change_24h: float
    volume_24h: float


@st.cache_data(ttl=600, show_spinner=False)  # 10 минут -- см. ui.config.SCREENER_TTL_SEC
def _fetch_market_pool() -> List[ScreenerCandidate]:
    """Топ-100 монет по капитализации с CoinGecko -- общий пул для бычьего и медвежьего скринера."""
    try:
        # Тот же общий троттлинг на весь процесс, что и в api/coingecko_data.py --
        # раньше этот запрос шёл в обход него и мог наложиться по времени на
        # параллельные запросы объёма из watchlist, провоцируя пачки HTTP 429.
        throttle_coingecko()
        resp = requests.get(
            f"{COINGECKO_BASE_URL}/coins/markets",
            params={
                "vs_currency": "usd",
                "order": "market_cap_desc",
                "per_page": MARKETS_POOL_SIZE,
                "page": 1,
                "sparkline": "false",
            },
            headers={"User-Agent": "Mozilla/5.0"},
            timeout=REQUEST_TIMEOUT,
        )
        resp.raise_for_status()
        rows = resp.json()
    except Exception as e:
        logger.warning(f"Не удалось загрузить пул монет для скринера с CoinGecko: {e}")
        return []

    pool = []
    for r in rows:
        pct = r.get("price_change_percentage_24h")
        vol = r.get("total_volume")
        price = r.get("current_price")
        symbol = r.get("symbol")
        if pct is None or vol is None or price is None or not symbol:
            continue
        pool.append(ScreenerCandidate(
            symbol=symbol.upper(), name=r.get("name", symbol.upper()),
            price=float(price), pct_change_24h=float(pct), volume_24h=float(vol),
        ))
    return pool


def _rank_candidates(pool: List[ScreenerCandidate], top_n: int, threshold_pct: float, direction: str) -> List[ScreenerCandidate]:
    """
    direction='up' -> бычьи (рост сильнее threshold_pct), direction='down' -> медвежьи (падение сильнее threshold_pct).
    Среди прошедших порог сортируем по объёму (больше объём = больше трейдеров активно торгует).
    Если прошедших порог меньше top_n, докидываем следующих по силе движения из общего пула.
    """
    if direction == "up":
        matched = [c for c in pool if c["pct_change_24h"] >= threshold_pct]
        def rank_key_rest(c):
            return -c["pct_change_24h"]  # сильнее выросшие -- первыми
    else:
        matched = [c for c in pool if c["pct_change_24h"] <= threshold_pct]
        def rank_key_rest(c):
            return c["pct_change_24h"]  # сильнее упавшие -- первыми

    matched.sort(key=lambda c: -c["volume_24h"])

    if len(matched) < top_n:
        rest = [c for c in pool if c not in matched]
        rest.sort(key=rank_key_rest)
        matched += rest[: top_n - len(matched)]

    return matched[:top_n]


def fetch_bullish_candidates(top_n: int = 6, rise_threshold_pct: float = 3.0) -> List[ScreenerCandidate]:
    """Монеты, которые сильно выросли за 24ч на высоком объёме."""
    return _rank_candidates(_fetch_market_pool(), top_n, rise_threshold_pct, direction="up")


def fetch_bearish_candidates(top_n: int = 6, drop_threshold_pct: float = -3.0) -> List[ScreenerCandidate]:
    """Монеты, которые сильно упали за 24ч на высоком объёме."""
    return _rank_candidates(_fetch_market_pool(), top_n, drop_threshold_pct, direction="down")