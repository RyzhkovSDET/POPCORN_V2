"""
Funding Rate и Open Interest. Пробует по очереди Binance Futures -> OKX.
Честно: в отличие от klines (где у Binance есть выделенный "чистый" домен
для рыночных данных без комплаенс-блокировки), для фьючерсных метрик
такого документированного обхода нет ни у одной из бирж -- если обе
откажут, это, скорее всего, реальное регуляторное ограничение по региону,
а не баг.
"""
import logging
from typing import Optional, Tuple

import requests
import streamlit as st

logger = logging.getLogger(__name__)

REQUEST_TIMEOUT = 5
_HEADERS = {"User-Agent": "Mozilla/5.0"}

# HTTP 451 от Binance -- это не транзиентная ошибка, а постоянная региональная
# блокировка (США и др.): она не "чинится" сама через минуту, поэтому нет
# смысла долбить Binance каждые 60 секунд заново для КАЖДОЙ монеты -- это
# просто шум в логах и небольшая лишняя задержка. Как только поймали 451
# один раз, запоминаем на весь процесс и сразу идём на OKX.
_binance_futures_blocked = [False]


def _mark_binance_blocked_if_geo(exc: Exception) -> None:
    if "451" in str(exc):
        _binance_futures_blocked[0] = True


# ---------------------------------------------------------------------------
# Funding rate
# ---------------------------------------------------------------------------

def _funding_binance(ticker: str) -> float:
    resp = requests.get(
        "https://fapi.binance.com/fapi/v1/premiumIndex",
        params={"symbol": ticker}, headers=_HEADERS, timeout=REQUEST_TIMEOUT,
    )
    resp.raise_for_status()
    return float(resp.json()["lastFundingRate"]) * 100


def _to_okx_inst(ticker: str) -> str:
    for quote in ("USDT", "USDC", "USD"):
        if ticker.endswith(quote) and len(ticker) > len(quote):
            return f"{ticker[: -len(quote)]}-{quote}-SWAP"
    return ticker


def _funding_okx(ticker: str) -> float:
    resp = requests.get(
        "https://www.okx.com/api/v5/public/funding-rate",
        params={"instId": _to_okx_inst(ticker)},
        headers=_HEADERS, timeout=REQUEST_TIMEOUT,
    )
    resp.raise_for_status()
    payload = resp.json()
    data = payload.get("data", [])
    if not data:
        raise ValueError(f"OKX не вернул funding rate ({payload.get('msg')})")
    return float(data[0]["fundingRate"]) * 100


_FUNDING_SOURCES = [("Binance", _funding_binance), ("OKX", _funding_okx)]


@st.cache_data(ttl=60, show_spinner=False)
def fetch_funding_rate(ticker: str) -> Optional[float]:
    """Текущая ставка финансирования в процентах, или None если все источники недоступны."""
    for name, fn in _FUNDING_SOURCES:
        if name == "Binance" and _binance_futures_blocked[0]:
            continue  # уже знаем, что регион заблокирован -- не тратим запрос
        try:
            return fn(ticker)
        except Exception as e:
            if name == "Binance":
                _mark_binance_blocked_if_geo(e)
            logger.warning(f"{name} funding rate недоступен для {ticker}: {e}")
    return None


# ---------------------------------------------------------------------------
# Open interest
# ---------------------------------------------------------------------------

def _oi_binance(ticker: str, period: str) -> Tuple[Optional[float], Optional[float]]:
    resp = requests.get(
        "https://fapi.binance.com/futures/data/openInterestHist",
        params={"symbol": ticker, "period": period, "limit": 2},
        headers=_HEADERS, timeout=REQUEST_TIMEOUT,
    )
    resp.raise_for_status()
    data = resp.json()
    if len(data) < 2:
        return (float(data[0]["sumOpenInterest"]), None) if data else (None, None)
    prev_oi = float(data[0]["sumOpenInterest"])
    latest_oi = float(data[1]["sumOpenInterest"])
    if prev_oi == 0:
        return latest_oi, None
    return latest_oi, ((latest_oi - prev_oi) / prev_oi) * 100


def _oi_okx(ticker: str, period: str) -> Tuple[Optional[float], Optional[float]]:
    """OKX отдаёт только текущий снимок OI без истории -- тренд вернуть не можем (None)."""
    resp = requests.get(
        "https://www.okx.com/api/v5/public/open-interest",
        params={"instId": _to_okx_inst(ticker)},
        headers=_HEADERS, timeout=REQUEST_TIMEOUT,
    )
    resp.raise_for_status()
    payload = resp.json()
    data = payload.get("data", [])
    if not data:
        raise ValueError(f"OKX не вернул open interest ({payload.get('msg')})")
    return float(data[0]["oi"]), None


_OI_SOURCES = [("Binance", _oi_binance), ("OKX", _oi_okx)]


@st.cache_data(ttl=60, show_spinner=False)
def fetch_open_interest_change(ticker: str, period: str = "1h") -> Tuple[Optional[float], Optional[float]]:
    """(latest_oi, pct_change). Пробует Binance -> OKX -> (None, None)."""
    for name, fn in _OI_SOURCES:
        if name == "Binance" and _binance_futures_blocked[0]:
            continue  # уже знаем, что регион заблокирован -- не тратим запрос
        try:
            return fn(ticker, period)
        except Exception as e:
            if name == "Binance":
                _mark_binance_blocked_if_geo(e)
            logger.warning(f"{name} OI недоступен для {ticker}: {e}")
    return None, None