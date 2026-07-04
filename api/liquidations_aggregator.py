"""
Консенсус ликвидаций Binance (WebSocket forceOrder-стрим) + OKX (публичный
REST) -- та же политика "всё или ничего" по источнику, что и у Order Book
консенсуса (см. api.coinapi_data): источник участвует в сумме, только если
вернул ПОЛНЫЙ набор полей (long_liquidated_qty И short_liquidated_qty),
частичные/битые данные не подмешиваются.

Не требует CoinAPI-ключа -- оба источника публичны и бесплатны без лимитов,
поэтому вызывается независимо от того, настроен ли CoinAPI.
"""
from typing import Optional

from api import liquidations_stream
from api.liquidations_okx import fetch_liquidation_pressure_okx


def _is_complete(source: Optional[dict]) -> bool:
    return source is not None and "long_liquidated_qty" in source and "short_liquidated_qty" in source


def fetch_liquidation_consensus(ticker: str) -> Optional[dict]:
    """
    Возвращает {"sources_used": int, "long_liquidated_qty": float,
    "short_liquidated_qty": float, "liquidation_bias": float(-1..+1)}
    или None, если ни один источник не дал полных данных.

    liquidation_bias > 0 -- преобладают ликвидации ЛОНГОВ (принудительно
    закрыты на падении) -- часто предвестник краткосрочного дна/отскока.
    liquidation_bias < 0 -- преобладают ликвидации ШОРТОВ -- часто
    предвестник локального пика/отката вниз (шорт-сквиз исчерпан).
    """
    liquidations_stream.ensure_started()

    sources = []
    binance = liquidations_stream.get_liquidation_pressure(ticker)
    if _is_complete(binance):
        sources.append(binance)

    okx = fetch_liquidation_pressure_okx(ticker)
    if _is_complete(okx):
        sources.append(okx)

    if not sources:
        return None

    total_long = sum(s["long_liquidated_qty"] for s in sources)
    total_short = sum(s["short_liquidated_qty"] for s in sources)
    total = total_long + total_short
    bias = (total_long - total_short) / total if total else 0.0

    return {
        "sources_used": len(sources),
        "long_liquidated_qty": total_long,
        "short_liquidated_qty": total_short,
        "liquidation_bias": bias,
    }