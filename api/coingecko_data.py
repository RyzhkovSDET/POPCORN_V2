"""
Глобальный объём торгов (сумма со всех бирж, которые отслеживает
CoinGecko, а не только с одной биржи, как столбец Volume/Vol1/Vol3).
Публичный API, без ключа, но с более строгим rate-limit -- кэшируем
агрессивнее (5 минут на объём, 1 час на резолвинг id монеты).
"""
import logging
import threading
import time
from typing import Optional

import requests
import streamlit as st

logger = logging.getLogger(__name__)

COINGECKO_BASE_URL = "https://api.coingecko.com/api/v3"
REQUEST_TIMEOUT = 8
_RATE_LIMIT_RETRY_DELAYS_SEC = [1.5, 3.0]  # до двух повторных попыток при HTTP 429

# Глобальный троттлинг между ЛЮБЫМИ запросами к CoinGecko (не только retry).
# Раньше ретраи спасали от единичного 429, но реальная причина "н/д" даже у
# ADA/XRP/NEAR (которые ЕСТЬ в статической карте ниже) в том, что все тикеры
# опрашиваются параллельно (до 8 потоков в watchlist.py) и стреляют в
# CoinGecko почти одновременно каждые REFRESH_SEC -- бесплатный тир этого не
# переживает вообще, никакие ретраи внутри одного запроса не помогают, если
# весь залп сразу попадает под лимит. Лечится общим Lock + минимальным
# интервалом между запросами НА ВЕСЬ ПРОЦЕСС -- превращает залп в очередь.
_throttle_lock = threading.Lock()
_last_request_ts = [0.0]
_MIN_INTERVAL_SEC = 3.0  # ~20 запросов/мин -- предыдущее значение (1.3с) оказалось
# недостаточным на практике: лог показывает, что 429 продолжал сыпаться пачками
# даже с троттлингом. Бесплатный тир CoinGecko на практике жёстче официально
# заявленного -- 3с между запросами гораздо надёжнее держит нас под лимитом.


def throttle_coingecko() -> None:
    """
    Общий на весь процесс троттлинг перед ЛЮБЫМ запросом к CoinGecko --
    публичная версия, чтобы её мог использовать и api/screener.py (у него
    свой независимый запрос /coins/markets, который раньше обходил этот
    троттлинг стороной и мог наложиться по времени на запросы из этого
    модуля, снова провоцируя пачки 429).
    """
    with _throttle_lock:
        elapsed = time.monotonic() - _last_request_ts[0]
        if elapsed < _MIN_INTERVAL_SEC:
            time.sleep(_MIN_INTERVAL_SEC - elapsed)
        _last_request_ts[0] = time.monotonic()


# Старое приватное имя оставлено как алиас -- внутри этого файла уже везде
# используется _throttle(), переименовывать все вызовы ради самого имени смысла нет.
_throttle = throttle_coingecko

# Тикер (без котируемой валюты) -> CoinGecko coin id. Расширяй по мере надобности --
# для всего, чего нет в этой карте, id ищется динамически через /search.
_SYMBOL_TO_ID = {
    "BTC": "bitcoin", "ETH": "ethereum", "SOL": "solana", "XRP": "ripple",
    "DOGE": "dogecoin", "ADA": "cardano", "BNB": "binancecoin", "MATIC": "matic-network",
    "DOT": "polkadot", "LTC": "litecoin", "LINK": "chainlink", "AVAX": "avalanche-2",
    "TRX": "tron", "ATOM": "cosmos", "UNI": "uniswap", "SHIB": "shiba-inu",
    "NEAR": "near", "APT": "aptos", "ARB": "arbitrum", "OP": "optimism",
    "SUI": "sui", "TON": "the-open-network", "PEPE": "pepe", "FIL": "filecoin",
    "ETC": "ethereum-classic", "ICP": "internet-computer", "INJ": "injective-protocol",
}


def _extract_base_symbol(ticker: str) -> str:
    """'BTCUSDT' -> 'BTC'."""
    for quote in ("USDT", "BUSD", "USDC", "USD", "BTC", "ETH"):
        if ticker.endswith(quote) and len(ticker) > len(quote):
            return ticker[: -len(quote)]
    return ticker


def _get_with_retry(url: str, params: dict) -> requests.Response:
    """
    GET с несколькими повторными попытками при HTTP 429 (rate limit).

    Раньше была ОДНА попытка повтора с паузой 1.5с, а сами запросы шли без
    какой-либо координации между потоками -- теперь плюс к ретраям каждый
    запрос (включая первую попытку) проходит через _throttle(), которая
    превращает залп параллельных запросов в равномерную очередь.
    """
    _throttle()
    resp = requests.get(url, params=params, headers={"User-Agent": "Mozilla/5.0"}, timeout=REQUEST_TIMEOUT)
    for delay in _RATE_LIMIT_RETRY_DELAYS_SEC:
        if resp.status_code != 429:
            break
        logger.warning(f"CoinGecko rate limit (429) на {url}, жду {delay}с и пробую снова")
        time.sleep(delay)
        _throttle()
        resp = requests.get(url, params=params, headers={"User-Agent": "Mozilla/5.0"}, timeout=REQUEST_TIMEOUT)
    return resp


@st.cache_data(ttl=3600, show_spinner=False)
def _resolve_coin_id(symbol: str) -> Optional[str]:
    """Ищет CoinGecko id для символа, которого нет в статической карте."""
    if symbol in _SYMBOL_TO_ID:
        return _SYMBOL_TO_ID[symbol]
    try:
        resp = _get_with_retry(f"{COINGECKO_BASE_URL}/search", {"query": symbol})
        resp.raise_for_status()
        coins = resp.json().get("coins", [])
        for c in coins:
            if c.get("symbol", "").upper() == symbol:
                return c.get("id")
        return coins[0]["id"] if coins else None
    except Exception as e:
        logger.warning(f"Не удалось найти CoinGecko id для {symbol}: {e}")
        return None


@st.cache_data(ttl=300, show_spinner=False)
def _fetch_okx_volume(ticker: str) -> Optional[float]:
    """
    Фолбэк, если CoinGecko не дал объём (лимит запросов, монета не
    резолвится и т.п.). Это объём торгов ЭТОЙ КОНКРЕТНОЙ пары на OKX (не
    сумма со всех бирж, как у CoinGecko) -- честно меньше по охвату, но
    лучше, чем "н/д".
    """
    for quote in ("USDT", "USDC", "USD"):
        if ticker.endswith(quote) and len(ticker) > len(quote):
            inst_id = f"{ticker[: -len(quote)]}-{quote}"
            break
    else:
        return None
    try:
        _throttle()
        resp = requests.get(
            "https://www.okx.com/api/v5/market/ticker",
            params={"instId": inst_id},
            headers={"User-Agent": "Mozilla/5.0"}, timeout=REQUEST_TIMEOUT,
        )
        resp.raise_for_status()
        data = resp.json().get("data", [])
        if not data:
            return None
        vol_ccy_24h = data[0].get("volCcy24h")  # объём в валюте котировки (~USD для USDT-пар)
        return float(vol_ccy_24h) if vol_ccy_24h is not None else None
    except Exception as e:
        logger.warning(f"OKX объём недоступен для {ticker}: {e}")
        return None


@st.cache_data(ttl=60, show_spinner=False)
def fetch_global_volume(ticker: str) -> Optional[float]:
    """
    Суммарный 24ч объём торгов монеты в долларах со всех бирж, которые
    отслеживает CoinGecko. Если CoinGecko не дал ответ (rate limit,
    монета не резолвится и т.п.) -- фолбэк на объём пары на OKX (см.
    _fetch_okx_volume). Только если оба источника молчат -- None ("н/д").

    ttl=60 (а не 300, как раньше): вызывающий код (ui.watchlist) теперь
    сам ограничивает частоту вызовов через round-robin -- за один цикл
    обновления таблицы реально запрашивается только ОДНА монета, а не все
    сразу. При таком редком вызове можно позволить себе кэш покороче и
    получать более свежие данные без риска зачастить с запросами.
    """
    symbol = _extract_base_symbol(ticker)
    coin_id = _resolve_coin_id(symbol)

    if coin_id is not None:
        try:
            resp = _get_with_retry(
                f"{COINGECKO_BASE_URL}/simple/price",
                {"ids": coin_id, "vs_currencies": "usd", "include_24hr_vol": "true"},
            )
            resp.raise_for_status()
            data = resp.json()
            vol = data.get(coin_id, {}).get("usd_24h_vol")
            if vol is not None:
                return float(vol)
        except Exception as e:
            logger.warning(f"Глобальный объём (CoinGecko) недоступен для {ticker} ({coin_id}): {e}")

    return _fetch_okx_volume(ticker)