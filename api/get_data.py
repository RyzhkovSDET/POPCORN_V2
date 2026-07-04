"""
Загрузка OHLCV-свечей. Пробует источники по очереди, пока один не сработает:
1) Binance Spot -- обычно лучшее покрытие пар, но блокирует некоторые страны (HTTP 451), например США.
2) Coinbase -- доступен в США, но покрытие пар меньше.
3) Kraken -- официально доступен в США, используется как последний resort.

Если ты в США (или другом регионе, где заблокированы офшорные биржи) --
приложение всё равно будет работать через Coinbase/Kraken, просто набор пар
у них меньше, чем у Binance.
"""
import logging

import pandas as pd
import requests
import streamlit as st

from api import ws_stream

logger = logging.getLogger(__name__)

REQUEST_TIMEOUT = 10

# Если конкретный тикер уже дал HTTP 400 (invalid symbol) на конкретном
# источнике -- это не транзиентная ошибка, а постоянный факт "этой пары тут
# нет" (например HYPEUSDT не торгуется на Binance Spot вообще). Без этого
# кэша каждый из ~5 разных таймфреймов, которые дёргает приложение для
# одного тикера (слоу-метрики, дневные свечи, прогноз и т.д.), заново
# натыкался бы на те же 2 гарантированно провальных запроса (Binance,
# Coinbase) прежде чем дойти до рабочего источника -- лишние задержки и шум
# в логах. Ключ: (source_name, ticker).
_known_bad_symbol: set = set()

_BINANCE_COLUMNS = [
    "time", "open", "high", "low", "close", "volume",
    "close_time", "qav", "trades", "tbbav", "tbqav", "ignore",
]
_NUMERIC_COLUMNS = ["open", "high", "low", "close", "volume", "qav"]

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


def _kraken_pair_candidates(ticker: str) -> list:
    """
    Варианты Kraken-пары для тикера: сначала как задан (обычно USDT),
    затем с USD -- многие альткоины на Kraken листингованы только к USD,
    без пары к USDT (например ZEC).
    """
    for quote in ("USDT", "USD", "BUSD", "BTC", "ETH"):
        if ticker.endswith(quote) and len(ticker) > len(quote):
            base = _KRAKEN_ASSET_ALIASES.get(ticker[: -len(quote)], ticker[: -len(quote)])
            candidates = [f"{base}{quote}"]
            if quote == "USDT":
                candidates.append(f"{base}USD")
            return candidates
    return [ticker]


def _fetch_kraken(ticker: str, interval: str, limit: int) -> pd.DataFrame:
    kraken_interval = _INTERVAL_TO_KRAKEN.get(interval)
    if kraken_interval is None:
        raise ValueError(f"Интервал {interval} не поддержан для Kraken")

    errors = []
    for pair in _kraken_pair_candidates(ticker):
        try:
            resp = requests.get(
                "https://api.kraken.com/0/public/OHLC",
                params={"pair": pair, "interval": kraken_interval},
                headers={"User-Agent": "Mozilla/5.0"},
                timeout=REQUEST_TIMEOUT,
            )
            resp.raise_for_status()
            payload = resp.json()
            if payload.get("error"):
                errors.append(f"{pair}: {payload['error']}")
                continue

            result = payload.get("result", {})
            data_key = next((k for k in result if k != "last"), None)
            if data_key is None:
                errors.append(f"{pair}: нет данных в ответе")
                continue

            rows = result[data_key][-limit:]
            if not rows:
                errors.append(f"{pair}: пустой список свечей")
                continue

            df = pd.DataFrame(rows, columns=["time", "open", "high", "low", "close", "vwap", "volume", "count"])
            for col in ["open", "high", "low", "close", "volume"]:
                df[col] = pd.to_numeric(df[col], errors="coerce")
            df["qav"] = df["close"] * df["volume"]  # у Kraken нет отдельного quote-объёма -- аппроксимация
            df["time"] = pd.to_datetime(pd.to_numeric(df["time"]), unit="s")
            df.set_index("time", inplace=True)
            return df
        except requests.exceptions.RequestException as e:
            errors.append(f"{pair}: {e}")
            continue

    raise ValueError(f"Kraken: ни одна пара не сработала для {ticker} ({'; '.join(errors)})")


_INTERVAL_TO_COINBASE_GRANULARITY = {
    "1m": 60, "5m": 300, "15m": 900, "1h": 3600, "6h": 21600, "1d": 86400,
}


def _coinbase_product_candidates(ticker: str) -> list:
    """
    Варианты Coinbase-продукта: сначала как задан (обычно USDT), затем с USD --
    Coinbase торгует в основном в USD, у многих монет вообще нет USDT-пары.
    """
    for quote in ("USDT", "USDC", "USD", "BTC", "ETH"):
        if ticker.endswith(quote) and len(ticker) > len(quote):
            base = ticker[: -len(quote)]
            candidates = [f"{base}-{quote}"]
            if quote in ("USDT", "USDC"):
                candidates.append(f"{base}-USD")
            return candidates
    return [ticker]


def _fetch_coinbase(ticker: str, interval: str, limit: int) -> pd.DataFrame:
    granularity = _INTERVAL_TO_COINBASE_GRANULARITY.get(interval)
    if granularity is None:
        raise ValueError(f"Интервал {interval} не поддержан для Coinbase (нет 4h у их API)")

    errors = []
    for product in _coinbase_product_candidates(ticker):
        try:
            resp = requests.get(
                f"https://api.exchange.coinbase.com/products/{product}/candles",
                params={"granularity": granularity},
                headers={"User-Agent": "Mozilla/5.0"},
                timeout=REQUEST_TIMEOUT,
            )
            resp.raise_for_status()
            rows = resp.json()
            if not rows:
                errors.append(f"{product}: пустой ответ")
                continue

            rows = list(reversed(rows))[-limit:]  # Coinbase отдаёт от новых к старым
            df = pd.DataFrame(rows, columns=["time", "low", "high", "open", "close", "volume"])
            for col in ["open", "high", "low", "close", "volume"]:
                df[col] = pd.to_numeric(df[col], errors="coerce")
            df["qav"] = df["close"] * df["volume"]  # у Coinbase нет отдельного quote-объёма -- аппроксимация
            df["time"] = pd.to_datetime(pd.to_numeric(df["time"]), unit="s")
            df.set_index("time", inplace=True)
            return df
        except requests.exceptions.RequestException as e:
            errors.append(f"{product}: {e}")
            continue

    raise ValueError(f"Coinbase: ни один продукт не сработал для {ticker} ({'; '.join(errors)})")


_SOURCES = [
    ("Binance", _fetch_binance),
    ("Coinbase", _fetch_coinbase),
    ("Kraken", _fetch_kraken),
]

SOURCE_NAMES = [name for name, _ in _SOURCES]


@st.cache_data(ttl=600, show_spinner=False)
def fetch_from_source(source: str, ticker: str, interval: str = "1d", limit: int = 100) -> pd.DataFrame:
    """
    Загружает klines с конкретной биржи БЕЗ автоматического fallback --
    используется, когда нужно явно сравнить разные источники (например,
    вкладки в боковой панели анализа), а не молча взять первый рабочий.
    source: одно из значений SOURCE_NAMES ('Binance' | 'Coinbase' | 'Kraken').
    Кэш 10 минут -- используется с дневными свечами, чаще не нужно.
    """
    sources_map = dict(_SOURCES)
    fetch_fn = sources_map.get(source)
    if fetch_fn is None:
        raise ValueError(f"Неизвестный источник: {source}")
    return fetch_fn(ticker, interval, limit)


def _fetch_with_fallback(ticker: str, interval: str, limit: int) -> pd.DataFrame:
    """Пробует источники по очереди (см. _SOURCES). Raises ValueError, если все отказали."""
    errors = []
    for name, fetch_fn in _SOURCES:
        if (name, ticker) in _known_bad_symbol:
            continue  # уже знаем: эта пара тут не торгуется вообще, не тратим запрос
        try:
            return fetch_fn(ticker, interval, limit)
        except Exception as e:
            # HTTP 400 = "invalid symbol" -- постоянный факт, а не временный сбой сети.
            # Другие ошибки (таймаут, 5xx, rate limit) остаются транзиентными и не кэшируются.
            if "400 Client Error" in str(e):
                _known_bad_symbol.add((name, ticker))
            logger.warning(f"{name} недоступен для {ticker}: {e}")
            errors.append(f"{name}: {e}")

    raise ValueError(f"Все источники данных недоступны для {ticker}. " + " | ".join(errors))


# Свечи разного таймфрейма закрываются с разной частотой -- нет смысла дёргать
# биржу за дневными свечами так же часто, как за минутными. Кэш разведён на
# три "скорости", подобранные под то, когда там реально появляются новые данные.
_FAST_INTERVALS = {"1m", "3m", "5m"}       # свеча раз в 1-5 мин -> кэш 10 сек
_MEDIUM_INTERVALS = {"15m", "30m", "1h"}   # свеча раз в 15-60 мин -> кэш 60 сек
# всё остальное (4h, 6h, 12h, 1d, 1w) -> кэш 600 сек (10 мин)


@st.cache_data(ttl=10, show_spinner=False)
def _fetch_cached_fast(ticker: str, interval: str, limit: int) -> pd.DataFrame:
    return _fetch_with_fallback(ticker, interval, limit)


@st.cache_data(ttl=60, show_spinner=False)
def _fetch_cached_medium(ticker: str, interval: str, limit: int) -> pd.DataFrame:
    return _fetch_with_fallback(ticker, interval, limit)


@st.cache_data(ttl=600, show_spinner=False)
def _fetch_cached_slow(ticker: str, interval: str, limit: int) -> pd.DataFrame:
    return _fetch_with_fallback(ticker, interval, limit)


def fetch_data_for_ticker(ticker: str, interval: str = "1m", limit: int = 100) -> pd.DataFrame:
    """
    Загружает klines, пробуя источники по очереди. Кэш-TTL подбирается
    автоматически по таймфрейму (см. _FAST_INTERVALS/_MEDIUM_INTERVALS выше) --
    не имеет смысла опрашивать биржу за дневной свечой так же часто, как за минутной.

    Raises:
        ValueError: если вообще ни один источник не сработал.
    """
    if interval in _FAST_INTERVALS:
        return _fetch_cached_fast(ticker, interval, limit)
    elif interval in _MEDIUM_INTERVALS:
        return _fetch_cached_medium(ticker, interval, limit)
    return _fetch_cached_slow(ticker, interval, limit)


def is_binance_known_bad(ticker: str) -> bool:
    """
    True, если этот тикер уже давал HTTP 400 (invalid symbol) на Binance --
    используется api.ws_stream, чтобы не пытаться подписываться на
    WebSocket-стрим заведомо несуществующей на Binance Spot пары (например
    HYPEUSDT, который торгуется только на Kraken). Один невалидный символ
    в комбинированном WS-стриме иначе мог бы сорвать подписку сразу для
    всех остальных тикеров тоже.
    """
    return ("Binance", ticker) in _known_bad_symbol


def fetch_latest_bar(ticker: str) -> pd.DataFrame:
    """
    Быстрый путь для 'горячего' запроса (текущая цена + объём последней и
    предыдущей 1m-свечи) -- именно это нужно ui.watchlist на каждом цикле
    обновления (каждые REFRESH_SEC секунд на каждую монету), а не полная
    история в 100 свечей.

    Сначала пробует живой WebSocket-кэш (api.ws_stream) -- если там есть
    хотя бы 2 свечи и данные не протухли, отдаёт их МГНОВЕННО, без единого
    HTTP-запроса на эту монету. Если WS недоступен, ещё не готов для этого
    тикера, или данные протухли -- прозрачный откат на обычный REST-путь
    (fetch_data_for_ticker), как было раньше до добавления WS.
    """
    ws_df = ws_stream.get_live_candles(ticker)
    if ws_df is not None and len(ws_df) >= 2:
        return ws_df
    return fetch_data_for_ticker(ticker, interval="1m", limit=100)