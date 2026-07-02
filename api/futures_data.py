"""
Funding Rate и Open Interest. Основной источник -- Binance Futures API.
Если Binance недоступен (регионная блокировка HTTP 451 или сетевая
ошибка) -- автоматически переключается на Bybit (линейные перпетуалы).
"""
import logging
from typing import Optional, Tuple

import requests
import streamlit as st

logger = logging.getLogger(__name__)

BINANCE_FUTURES_URL = "https://fapi.binance.com"
BYBIT_URL = "https://api.bybit.com"
REQUEST_TIMEOUT = 5

_OI_PERIOD_TO_BYBIT = {"5m": "5min", "15m": "15min", "30m": "30min", "1h": "1h", "4h": "4h", "1d": "1d"}


def _funding_binance(ticker: str) -> float:
    resp = requests.get(
        f"{BINANCE_FUTURES_URL}/fapi/v1/premiumIndex",
        params={"symbol": ticker}, timeout=REQUEST_TIMEOUT,
    )
    resp.raise_for_status()
    return float(resp.json()["lastFundingRate"]) * 100


def _funding_bybit(ticker: str) -> float:
    resp = requests.get(
        f"{BYBIT_URL}/v5/market/funding/history",
        params={"category": "linear", "symbol": ticker, "limit": 1},
        timeout=REQUEST_TIMEOUT,
    )
    resp.raise_for_status()
    rows = resp.json().get("result", {}).get("list", [])
    if not rows:
        raise ValueError("Bybit не вернул funding rate")
    return float(rows[0]["fundingRate"]) * 100


@st.cache_data(ttl=60, show_spinner=False)
def fetch_funding_rate(ticker: str) -> Optional[float]:
    """Текущая ставка финансирования в процентах. Binance -> fallback Bybit -> None."""
    try:
        return _funding_binance(ticker)
    except Exception as e:
        logger.warning(f"Binance funding rate недоступен для {ticker} ({e}), пробую Bybit...")
        try:
            return _funding_bybit(ticker)
        except Exception as e2:
            logger.warning(f"Funding rate недоступен для {ticker} нигде: {e2}")
            return None


def _oi_binance(ticker: str, period: str) -> Tuple[Optional[float], Optional[float]]:
    resp = requests.get(
        f"{BINANCE_FUTURES_URL}/futures/data/openInterestHist",
        params={"symbol": ticker, "period": period, "limit": 2},
        timeout=REQUEST_TIMEOUT,
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
        f"{BYBIT_URL}/v5/market/open-interest",
        params={
            "category": "linear", "symbol": ticker,
            "intervalTime": _OI_PERIOD_TO_BYBIT.get(period, "1h"), "limit": 2,
        },
        timeout=REQUEST_TIMEOUT,
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


@st.cache_data(ttl=300, show_spinner=False)
def fetch_open_interest_change(ticker: str, period: str = "1h") -> Tuple[Optional[float], Optional[float]]:
    """(latest_oi, pct_change). Binance -> fallback Bybit -> (None, None)."""
    try:
        return _oi_binance(ticker, period)
    except Exception as e:
        logger.warning(f"Binance OI недоступен для {ticker} ({e}), пробую Bybit...")
        try:
            return _oi_bybit(ticker, period)
        except Exception as e2:
            logger.warning(f"OI недоступен для {ticker} нигде: {e2}")
            return None, None