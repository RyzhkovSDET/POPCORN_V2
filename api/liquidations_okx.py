"""
Ликвидации с OKX -- публичный REST-эндпоинт, не требует API-ключа. В
отличие от Binance (см. api/liquidations_stream.py), у OKX публичная
история ликвидаций по рынку доступна обычным REST-запросом, WebSocket не нужен.
"""
import logging
from typing import Optional

import requests
import streamlit as st

logger = logging.getLogger(__name__)

REQUEST_TIMEOUT = 5
_HEADERS = {"User-Agent": "Mozilla/5.0"}


def _to_okx_inst(ticker: str) -> str:
    for quote in ("USDT", "USDC", "USD"):
        if ticker.endswith(quote) and len(ticker) > len(quote):
            return f"{ticker[: -len(quote)]}-{quote}-SWAP"
    return ticker


@st.cache_data(ttl=60, show_spinner=False)
def fetch_liquidation_pressure_okx(ticker: str) -> Optional[dict]:
    """
    Сумма объёма недавних ликвидаций (лонг vs шорт) по инструменту на OKX
    -- эндпоинт отдаёт последнюю пачку записей (обычно покрывает недавний
    период на ликвидных парах). None при ошибке/недоступности -- не
    "нулевые ликвидации", а именно "нет данных в этот раз".
    """
    try:
        resp = requests.get(
            "https://www.okx.com/api/v5/public/liquidation-orders",
            params={"instType": "SWAP", "instId": _to_okx_inst(ticker), "state": "filled"},
            headers=_HEADERS, timeout=REQUEST_TIMEOUT,
        )
        resp.raise_for_status()
        payload = resp.json()
        data = payload.get("data", [])
    except Exception as e:
        logger.warning(f"OKX ликвидации недоступны для {ticker}: {e}")
        return None

    if not data:
        return None
    details = data[0].get("details", [])
    if not details:
        return {"long_liquidated_qty": 0.0, "short_liquidated_qty": 0.0}

    long_liq, short_liq = 0.0, 0.0
    for d in details:
        try:
            sz = float(d.get("sz", 0))
        except (TypeError, ValueError):
            continue
        side = d.get("side")
        if side == "sell":     # принудительная продажа -- закрыта длинная позиция (лонг-ликвидация)
            long_liq += sz
        elif side == "buy":    # принудительная покупка -- закрыта короткая позиция (шорт-ликвидация)
            short_liq += sz
    return {"long_liquidated_qty": long_liq, "short_liquidated_qty": short_liq}