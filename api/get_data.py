"""
Загрузка OHLCV-свечей. Пробует источники по очереди, пока один не сработает:
1) Binance Spot -- обычно лучшее покрытие пар, но блокирует некоторые страны (HTTP 451), например США.
2) Bybit -- тоже не обслуживает США (HTTP 403 в этом случае).
3) Kraken -- официально доступен в США, используется как последний resort.

Если ты в США (или другом регионе, где заблокированы офшорные биржи) --
приложение всё равно будет работать через Kraken, просто набор пар у него
меньше, чем у Binance/Bybit.
"""
import logging
from typing import Optional

import pandas as pd
import requests
import streamlit as st

logger = logging.getLogger(__name__)

REQUEST_TIMEOUT = 10

_BINANCE_COLUMNS = [
    "time", "open", "high", "low", "close", "volume",
    "close_time", "qav", "trades", "tbbav", "tbqav", "ignore",
]
_NUMERIC_COLUMNS = ["open", "high", "low", "close", "volume", "qav"]

_INTERVAL_TO_BYBIT = {
    "1m": "1", "3m": "3", "5m": "5", "15m": "15", "30m": "30",
    "1h": "60", "2h": "120", "4h": "240", "6h": "360", "12h": "720",
    "1d": "D", "1w": "W",
}
_INTERVAL_TO_KRAKEN = {
    "1m": 1, "5m": 5, "15m": 15, "30m": 30, "1h": 60, "4h": 240, "1d": 1440, "1w": 10080,
}
_KRAKEN_ASSET_ALIASES = {"BTC": "XBT"}  # Kraken исторически называет биткоин XBT


def _fetch_binance(ticker: str, interval: str, limit: int) -> pd.DataFrame:
    # data-api.binance.vision -- отдельный поддомен Binance строго под публичные
    # рыночные данные (klines/тикеры), не завязан на гео-комплаенс-блокировку
    # основного api.binance.com. User-Agent нужен, иначе иногда режет анти-бот защита.
    resp = requests.get(
        "https://data-api.binance.vision/api/v3/klines",
        params={"symbol": ticker, "interval": interval, "limit": limit},
        headers={"User-Agent": "Mozilla/5.0"},
        timeout=REQUEST_TIMEOUT,
    )
    resp.raise_for_status()
    data = resp.json()
    if not data:
        raise ValueError("Пустой ответ от Binance")

    df = pd.DataFrame(data, columns=_BINANCE_COLUMNS)
    for col in _NUMERIC_COLUMNS:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    if df[_NUMERIC_COLUMNS].isnull().any().any():
        raise ValueError("Некорректные числовые данные в ответе Binance")

    df["time"] = pd.to_datetime(df["time"], unit="ms")
    df.set_index("time", inplace=True)
    return df


def _fetch_bybit(ticker: str, interval: str, limit: int) -> pd.DataFrame:
    bybit_interval = _INTERVAL_TO_BYBIT.get(interval)
    if bybit_interval is None:
        raise ValueError(f"Интервал {interval} не поддержан для Bybit")

    resp = requests.get(
        "https://api.bybit.com/v5/market/kline",
        params={"category": "spot", "symbol": ticker, "interval": bybit_interval, "limit": limit},
        headers={"User-Agent": "Mozilla/5.0"},
        timeout=REQUEST_TIMEOUT,
    )
    resp.raise_for_status()
    payload = resp.json()
    rows = payload.get("result", {}).get("list", [])
    if not rows:
        raise ValueError(f"Bybit не вернул данные (retCode={payload.get('retCode')}, retMsg={payload.get('retMsg')})")

    rows = list(reversed(rows))  # Bybit отдаёт от новых к старым
    df = pd.DataFrame(rows, columns=["time", "open", "high", "low", "close", "volume", "qav"])
    for col in ["open", "high", "low", "close", "volume", "qav"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    df["time"] = pd.to_datetime(pd.to_numeric(df["time"]), unit="ms")
    df.set_index("time", inplace=True)
    return df


def _to_kraken_pair(ticker: str) -> str:
    for quote in ("USDT", "USD", "BUSD", "BTC", "ETH"):
        if ticker.endswith(quote) and len(ticker) > len(quote):
            base = _KRAKEN_ASSET_ALIASES.get(ticker[: -len(quote)], ticker[: -len(quote)])
            return f"{base}{quote}"
    return ticker


def _fetch_kraken(ticker: str, interval: str, limit: int) -> pd.DataFrame:
    kraken_interval = _INTERVAL_TO_KRAKEN.get(interval)
    if kraken_interval is None:
        raise ValueError(f"Интервал {interval} не поддержан для Kraken")

    pair = _to_kraken_pair(ticker)
    resp = requests.get(
        "https://api.kraken.com/0/public/OHLC",
        params={"pair": pair, "interval": kraken_interval},
        headers={"User-Agent": "Mozilla/5.0"},
        timeout=REQUEST_TIMEOUT,
    )
    resp.raise_for_status()
    payload = resp.json()
    if payload.get("error"):
        raise ValueError(f"Kraken: {payload['error']}")

    result = payload.get("result", {})
    data_key = next((k for k in result if k != "last"), None)
    if data_key is None:
        raise ValueError(f"Kraken не вернул данные для {pair} (пары может не быть в листинге)")

    rows = result[data_key][-limit:]
    if not rows:
        raise ValueError(f"Kraken: пустой список свечей для {pair}")

    df = pd.DataFrame(rows, columns=["time", "open", "high", "low", "close", "vwap", "volume", "count"])
    for col in ["open", "high", "low", "close", "volume"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    df["qav"] = df["close"] * df["volume"]  # у Kraken нет отдельного quote-объёма -- аппроксимация
    df["time"] = pd.to_datetime(pd.to_numeric(df["time"]), unit="s")
    df.set_index("time", inplace=True)
    return df


_SOURCES = [
    ("Binance", _fetch_binance),
    ("Bybit", _fetch_bybit),
    ("Kraken", _fetch_kraken),
]


@st.cache_data(ttl=10, show_spinner=False)
def fetch_data_for_ticker(ticker: str, interval: str = "1m", limit: int = 100) -> pd.DataFrame:
    """
    Загружает klines, пробуя источники по очереди (см. _SOURCES).
    Raises:
        ValueError: если вообще ни один источник не сработал.
    """
    errors = []
    for name, fetch_fn in _SOURCES:
        try:
            return fetch_fn(ticker, interval, limit)
        except Exception as e:
            logger.warning(f"{name} недоступен для {ticker}: {e}")
            errors.append(f"{name}: {e}")

    raise ValueError(f"Все источники данных недоступны для {ticker}. " + " | ".join(errors))


def get_latest_price(ticker: str) -> Optional[float]:
    """Последняя цена закрытия, или None при ошибке (не роняет приложение)."""
    try:
        df = fetch_data_for_ticker(ticker, limit=1)
        return float(df["close"].iloc[-1]) if not df.empty else None
    except Exception as e:
        logger.warning(f"Не удалось получить цену {ticker}: {e}")
        return None