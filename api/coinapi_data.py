"""
Интеграция с CoinAPI (coinapi.io) -- используется ТОЛЬКО для расширенного
"Заключения по монете" в боковой панели (по клику на монету), НЕ для
основной таблицы watchlist.

Причина именно такого разделения: бесплатный тариф CoinAPI ограничен 100
запросами в день. Таблица watchlist опрашивает каждую монету каждые
REFRESH_SEC (10) секунд -- это несовместимо с таким лимитом. Заключение же
запрашивается только когда пользователь реально кликнул на монету -- это
на порядки реже, такой квоты хватает с большим запасом (см. счётчик,
storage.coinapi_usage_storage).

ЧЕСТНО про "ликвидации": у CoinAPI на стандартном REST API нет отдельного
документированного публичного эндпоинта уровней ликвидации -- это специфика
дерривативных data-провайдеров вроде Coinalyze/Coinglass, не самого CoinAPI.
Ликвидации в этом приложении берутся отдельно, напрямую с Binance
(WebSocket forceOrder-стрим) и OKX (публичный REST) -- см.
api/liquidations_stream.py, api/liquidations_okx.py, api/liquidations_aggregator.py.

Ключ API нигде здесь не хранится и не хардкожен -- передаётся вызывающим
кодом на каждый вызов явным параметром.

Эти функции вызываются РЕДКО и ЯВНО -- только когда пользователь нажал
кнопку "Запросить/Обновить CoinAPI" в ui/analysis_sidebar.py (см. там же).
Поэтому кэширования в памяти (st.cache_data) здесь больше нет -- вместо
этого результат вызова сохраняется на диск на 4 часа модулем
storage.coinapi_cache_storage, и именно та логика решает, показывать ли
уже сохранённые данные или "пусто", а не эта функция.
"""
import logging
from typing import Optional

import requests

from storage.coinapi_usage_storage import record_request

logger = logging.getLogger(__name__)

BASE_URL = "https://rest.coinapi.io/v1"
REQUEST_TIMEOUT = 8


def fetch_asset_overview(symbol: str, api_key: str) -> Optional[dict]:
    """
    Агрегированные метрики по активу с CoinAPI (цена и объём, усреднённые
    по многим биржам, которые CoinAPI отслеживает) -- независимая от
    Binance/CoinGecko точка сверки для "Заключения по монете".

    symbol -- базовый актив БЕЗ пары к USDT (например "BTC", не "BTCUSDT").
    Возвращает None при любой ошибке -- вызывающий код должен относиться к
    этому как к "доп. данные недоступны в этот раз", а не падать.
    """
    if not api_key:
        return None
    try:
        resp = requests.get(
            f"{BASE_URL}/assets",
            params={"filter_asset_id": symbol},
            headers={"X-CoinAPI-Key": api_key},
            timeout=REQUEST_TIMEOUT,
        )
        resp.raise_for_status()
        rows = resp.json()
        record_request()  # считаем ТОЛЬКО реально дошедший до сети запрос
    except Exception as e:
        logger.warning(f"CoinAPI недоступен для {symbol}: {e}")
        return None

    match = next(
        (r for r in rows if r.get("asset_id") == symbol and r.get("type_is_crypto")),
        None,
    )
    if not match:
        return None
    return {
        "price_usd": match.get("price_usd"),
        "volume_1day_usd": match.get("volume_1day_usd"),
        "volume_1hrs_usd": match.get("volume_1hrs_usd"),
    }


# ---------------------------------------------------------------------------
# Order Book консенсус -- СО ВСЕХ бирж, которые CoinAPI отслеживает для
# данной пары, а не с фиксированного списка 2-3 бирж.
# ---------------------------------------------------------------------------
#
# Способ №1 (основной, экономный по квоте): ОДИН запрос к "current data"
# эндпоинту с wildcard-фильтром по symbol_id (`*_SPOT_{BASE}_{QUOTE}`) --
# CoinAPI поддерживает фильтрацию текущих снимков по маске и возвращает
# массив ответов сразу по ВСЕМ подходящим биржам одним вызовом.
# ЧЕСТНО: синтаксис wildcard-фильтра у "current data" эндпоинтов CoinAPI
# может отличаться в деталях от версии к версии их API -- если что-то в
# схеме поменяется и ответ окажется пустым/ошибочным, ниже есть Способ №2.
#
# Способ №2 (запасной, тратит больше квоты): если Способ №1 не сработал
# (пустой ответ или ошибка), опрашиваем по одному фиксированный список
# крупных бирж (Binance/OKX/Kraken) -- по одному запросу на биржу. Это
# гарантированно рабочий путь ценой большего расхода дневного лимита.
_ORDERBOOK_FALLBACK_TEMPLATES = {
    "Binance": "BINANCE_SPOT_{base}_{quote}",
    "OKX": "OKEX_SPOT_{base}_{quote}",
    "Kraken": "KRAKEN_SPOT_{base}_{quote}",
}


def _orderbook_row_to_entry(row: dict) -> Optional[dict]:
    """
    Превращает один снимок Order Book (одна биржа) в компактную запись.
    "Всё или ничего": если у биржи нет ОДНОВРЕМЕННО и bids, и asks --
    запись отбрасывается целиком (return None), а не подмешивается частично
    в консенсус -- иначе одна перекошенная сторона стакана с одной биржи
    могла бы исказить средний уровень для всех.
    """
    bids = row.get("bids") or []
    asks = row.get("asks") or []
    if not bids or not asks:
        return None
    best_bid = max(bids, key=lambda b: b["price"])
    best_ask = min(asks, key=lambda a: a["price"])
    # Верхние 10 уровней стакана с каждой стороны -- достаточно, чтобы
    # увидеть реальный перевес объёма, не только лучшую цену.
    bid_volume = sum(b["size"] for b in bids[:10])
    ask_volume = sum(a["size"] for a in asks[:10])
    return {
        "symbol_id": row.get("symbol_id", "?"),
        "best_bid": best_bid["price"], "best_ask": best_ask["price"],
        "bid_volume": bid_volume, "ask_volume": ask_volume,
    }


def _consensus_from_entries(entries: list) -> Optional[dict]:
    if not entries:
        return None
    avg_bid = sum(e["best_bid"] for e in entries) / len(entries)
    avg_ask = sum(e["best_ask"] for e in entries) / len(entries)
    total_bid_vol = sum(e["bid_volume"] for e in entries)
    total_ask_vol = sum(e["ask_volume"] for e in entries)
    total_vol = total_bid_vol + total_ask_vol
    imbalance = (total_bid_vol - total_ask_vol) / total_vol if total_vol else 0.0
    return {
        "exchanges_used": len(entries),
        "exchange_names": [e["symbol_id"] for e in entries],
        # Консенсус: где стоят покупатели -- ориентир для входа в LONG.
        "support_price": avg_bid,
        # Консенсус: где стоят продавцы -- ориентир для входа в SHORT.
        "resistance_price": avg_ask,
        # -1..+1: >0 -- перевес объёма покупателей в стакане, <0 -- перевес продавцов.
        "bid_ask_imbalance": imbalance,
    }


def _fetch_orderbook_all_exchanges(base: str, quote: str, api_key: str) -> Optional[dict]:
    """Способ №1: один запрос, wildcard по всем биржам сразу."""
    symbol_filter = f"*_SPOT_{base.upper()}_{quote.upper()}"
    resp = requests.get(
        f"{BASE_URL}/orderbooks/current",
        params={"filter_symbol_id": symbol_filter},
        headers={"X-CoinAPI-Key": api_key},
        timeout=REQUEST_TIMEOUT,
    )
    resp.raise_for_status()
    rows = resp.json()
    record_request()
    if not isinstance(rows, list) or not rows:
        return None
    entries = [e for e in (_orderbook_row_to_entry(row) for row in rows) if e is not None]
    return _consensus_from_entries(entries)


def _fetch_orderbook_fixed_list(base: str, quote: str, api_key: str) -> Optional[dict]:
    """Способ №2 (запасной): по одному запросу на каждую биржу из фиксированного списка."""
    entries = []
    for template in _ORDERBOOK_FALLBACK_TEMPLATES.values():
        symbol_id = template.format(base=base.upper(), quote=quote.upper())
        try:
            resp = requests.get(
                f"{BASE_URL}/orderbooks/{symbol_id}/current",
                headers={"X-CoinAPI-Key": api_key},
                timeout=REQUEST_TIMEOUT,
            )
            resp.raise_for_status()
            row = resp.json()
            record_request()
            entry = _orderbook_row_to_entry(row)
            if entry:
                entries.append(entry)
        except Exception as e:
            logger.warning(f"CoinAPI order book недоступен для {symbol_id}: {e}")
    return _consensus_from_entries(entries)


def fetch_orderbook_consensus(base: str, quote: str, api_key: str) -> Optional[dict]:
    """
    Order Book консенсус со всех бирж, которые CoinAPI отслеживает для этой
    пары. Сначала пробует Способ №1 (один запрос, все биржи разом), при
    неудаче -- Способ №2 (по одному запросу на биржу из фиксированного
    списка). См. комментарий над _ORDERBOOK_FALLBACK_TEMPLATES.

    Возвращает None, если оба способа не дали ни одной полной записи
    (нет ключа, лимит исчерпан, пара нигде не листингована и т.п.).
    """
    if not api_key:
        return None
    try:
        result = _fetch_orderbook_all_exchanges(base, quote, api_key)
        if result:
            return result
    except Exception as e:
        logger.warning(f"CoinAPI order book (все биржи) недоступен для {base}/{quote}: {e}")

    try:
        return _fetch_orderbook_fixed_list(base, quote, api_key)
    except Exception as e:
        logger.warning(f"CoinAPI order book (запасной список) недоступен для {base}/{quote}: {e}")
        return None