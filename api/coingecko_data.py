"""
Глобальный объём торгов (сумма со всех бирж, которые отслеживает
CoinGecko, а не только с одной биржи, как столбец Volume/Vol1/Vol3).
Публичный API, без ключа, но с более строгим rate-limit -- кэшируем
агрессивнее (5 минут на объём, 1 час на резолвинг id монеты).
"""
import logging
from typing import Optional

import requests
import streamlit as st

logger = logging.getLogger(__name__)

COINGECKO_BASE_URL = "https://api.coingecko.com/api/v3"
REQUEST_TIMEOUT = 8

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


@st.cache_data(ttl=3600, show_spinner=False)
def _resolve_coin_id(symbol: str) -> Optional[str]:
    """Ищет CoinGecko id для символа, которого нет в статической карте."""
    if symbol in _SYMBOL_TO_ID:
        return _SYMBOL_TO_ID[symbol]
    try:
        resp = requests.get(
            f"{COINGECKO_BASE_URL}/search",
            params={"query": symbol},
            headers={"User-Agent": "Mozilla/5.0"},
            timeout=REQUEST_TIMEOUT,
        )
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
def fetch_global_volume(ticker: str) -> Optional[float]:
    """
    Суммарный 24ч объём торгов монеты в долларах со всех бирж, которые
    отслеживает CoinGecko. None, если монету не удалось сопоставить с id
    или запрос не удался.
    """
    symbol = _extract_base_symbol(ticker)
    coin_id = _resolve_coin_id(symbol)
    if coin_id is None:
        return None

    try:
        resp = requests.get(
            f"{COINGECKO_BASE_URL}/simple/price",
            params={"ids": coin_id, "vs_currencies": "usd", "include_24hr_vol": "true"},
            headers={"User-Agent": "Mozilla/5.0"},
            timeout=REQUEST_TIMEOUT,
        )
        resp.raise_for_status()
        data = resp.json()
        return float(data[coin_id]["usd_24h_vol"])
    except Exception as e:
        logger.warning(f"Глобальный объём недоступен для {ticker} ({coin_id}): {e}")
        return None