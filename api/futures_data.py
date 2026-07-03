"""
Funding Rate и Open Interest. Пробует по очереди Binance Futures -> Bybit
-> OKX. Честно: в отличие от klines (где у Binance есть выделенный
"чистый" домен для рыночных данных без комплаенс-блокировки), для
фьючерсных метрик такого документированного обхода нет ни у одной из
трёх бирж -- если все три откажут, это, скорее всего, реальное
регуляторное ограничение по региону, а не баг.
"""
import logging
from typing import Optional, Tuple

import requests
import streamlit as st

logger = logging.getLogger(__name__)

REQUEST_TIMEOUT = 5
_HEADERS = {"User-Agent": "Mozilla/5.0"}

_OI_PERIOD_TO_BYBIT = {"5m": "5min", "15m": "15min", "30m": "30min", "1h": "1h", "4h": "4h", "1d": "1d"}


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


def _funding_bybit(ticker: str) -> float:
    resp = requests.get(
        "https://api.bybit.com/v5/market/funding/history",
        params={"category": "linear", "symbol": ticker, "limit": 1},
        headers=_HEADERS, timeout=REQUEST_TIMEOUT,
    )
    resp.raise_for_status()
    rows = resp.json().get("result", {}).get("list", [])
    if not rows:
        raise ValueError("Bybit не вернул funding rate")
    return float(rows[0]["fundingRate"]) * 100


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


_FUNDING_SOURCES = [("Binance", _funding_binance), ("Bybit", _funding_bybit), ("OKX", _funding_okx)]


@st.cache_data(ttl=60, show_spinner=False)
def fetch_funding_rate(ticker: str) -> Optional[float]:
    """Текущая ставка финансирования в процентах, или None если все источники недоступны."""
    for name, fn in _FUNDING_SOURCES:
        try:
            return fn(ticker)
        except Exception as e:
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


def _oi_bybit(ticker: str, period: str) -> Tuple[Optional[float], Optional[float]]:
    resp = requests.get(
        "https://api.bybit.com/v5/market/open-interest",
        params={"category": "linear", "symbol": ticker,
                "intervalTime": _OI_PERIOD_TO_BYBIT.get(period, "1h"), "limit": 2},
        headers=_HEADERS, timeout=REQUEST_TIMEOUT,
    )
    resp.raise_for_status()
    rows = resp.json().get("result", {}).get("list", [])
    if len(rows) < 2:
        return (float(rows[0]["openInterest"]), None) if rows else (None, None)
    rows_sorted = sorted(rows, key=lambda r: int(r["timestamp"]))
    prev_oi = float(rows_sorted[0]["openInterest"])
    latest_oi = float(rows_sorted[-1]["openInterest"])
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


_OI_SOURCES = [("Binance", _oi_binance), ("Bybit", _oi_bybit), ("OKX", _oi_okx)]


@st.cache_data(ttl=300, show_spinner=False)
def fetch_open_interest_change(ticker: str, period: str = "1h") -> Tuple[Optional[float], Optional[float]]:
    """(latest_oi, pct_change). Пробует Binance -> Bybit -> OKX -> (None, None)."""
    for name, fn in _OI_SOURCES:
        try:
            return fn(ticker, period)
        except Exception as e:
            logger.warning(f"{name} OI недоступен для {ticker}: {e}")
    return None, None