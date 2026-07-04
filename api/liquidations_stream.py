"""
Живой поток ликвидаций с Binance Futures (forceOrder WebSocket) -- по
аналогии с api/ws_stream.py (тот же паттерн: один персистентный WS,
фоновый поток, потокобезопасный кэш в памяти, автооткат при недоступности).

Отдельный модуль от ws_stream.py: тот подписан на kline-стримы (цена/объём
свечи) -- другой формат сообщений и другой стрим Binance (forceOrder, а не
kline). Раздельные модули проще отключать/диагностировать независимо друг
от друга -- если ликвидации недоступны в регионе, это не должно тянуть за
собой основной поток цены и наоборот.

ЧЕСТНО про источник: Binance Futures публикует поток ликвидаций через
комбинированный стрим !forceOrder@arr (все инструменты сразу) -- публичный,
без API-ключа. Это единственный практичный публичный источник ликвидаций
Binance: REST-эндпоинт истории ликвидаций по всему рынку у Binance закрыт
от публичного доступа (доступен только по конкретному аккаунту с ключом),
поэтому здесь именно WebSocket, а не REST-опрос, в отличие от OKX (см.
api/liquidations_okx.py -- там как раз обычный публичный REST).

Поток подписывается на ВСЕ инструменты сразу одним соединением -- не нужно
пересоздавать подписку при добавлении/удалении монеты в watchlist (в
отличие от ws_stream.py, где подписка -- это конкретный список тикеров).
"""
import asyncio
import json
import logging
import threading
import time
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

try:
    import websockets
    _WEBSOCKETS_AVAILABLE = True
except ImportError:  # библиотека может отсутствовать в окружении -- поток просто не запустится
    _WEBSOCKETS_AVAILABLE = False

_WS_URL = "wss://fstream.binance.com/ws/!forceOrder@arr"
_WINDOW_SEC = 30 * 60          # скользящее окно агрегации -- последние 30 минут
_STALE_AFTER_SEC = 120         # нет вообще никаких сообщений дольше этого -- поток считается мёртвым
_MAX_CONSECUTIVE_FAILURES = 5  # столько неудачных попыток подряд -- выключаемся насовсем (вероятно геоблок)
_RECONNECT_DELAY_SEC = 3

_lock = threading.Lock()
_events: Dict[str, List[dict]] = {}       # ticker -> [{side, qty, ts}, ...] за последние _WINDOW_SEC
_last_message_ts: Optional[float] = None  # время последнего ЛЮБОГО сообщения (по любому тикеру) -- индикатор "поток жив"
_thread: Optional[threading.Thread] = None
_stop_flag = threading.Event()
_consecutive_failures = 0
_permanently_disabled = False
_started = False


def _prune_old(ticker: str, now: float) -> None:
    events = _events.get(ticker)
    if not events:
        return
    _events[ticker] = [e for e in events if now - e["ts"] <= _WINDOW_SEC]


def _handle_message(raw: str) -> None:
    global _last_message_ts
    try:
        msg = json.loads(raw)
        order = msg.get("o")
        if not order:
            return
        ticker = order.get("s")   # символ, например "BTCUSDT"
        side = order.get("S")     # "SELL" -- принудительно закрыта ДЛИННАЯ позиция (лонг-ликвидация)
                                   # "BUY"  -- принудительно закрыта КОРОТКАЯ позиция (шорт-ликвидация)
        qty = float(order.get("q", 0) or 0)
        if not ticker or qty <= 0 or side not in ("BUY", "SELL"):
            return
        now = time.time()
        with _lock:
            bucket = _events.setdefault(ticker, [])
            bucket.append({"side": side, "qty": qty, "ts": now})
            _prune_old(ticker, now)
            _last_message_ts = now
    except Exception as e:
        logger.debug(f"Liquidations WS: не удалось разобрать сообщение: {e}")


async def _run_loop() -> None:
    global _consecutive_failures, _permanently_disabled
    while not _stop_flag.is_set() and not _permanently_disabled:
        try:
            async with websockets.connect(_WS_URL, ping_interval=20, ping_timeout=20, open_timeout=10) as ws:
                _consecutive_failures = 0
                logger.info("Liquidations WS: подключились к Binance forceOrder-стриму")
                async for raw in ws:
                    if _stop_flag.is_set():
                        break
                    _handle_message(raw)
        except Exception as e:
            _consecutive_failures += 1
            logger.warning(f"Liquidations WS: соединение оборвалось ({e}), попытка {_consecutive_failures}/{_MAX_CONSECUTIVE_FAILURES}")
            if _consecutive_failures >= _MAX_CONSECUTIVE_FAILURES:
                _permanently_disabled = True
                logger.warning(
                    "Liquidations WS: слишком много неудачных попыток подряд -- похоже, "
                    "поток недоступен в этом регионе. Отключаю до перезапуска приложения; "
                    "OKX-источник (api/liquidations_okx.py) продолжит работать сам по себе."
                )
                return
        if not _stop_flag.is_set() and not _permanently_disabled:
            time.sleep(_RECONNECT_DELAY_SEC)


def _thread_main() -> None:
    try:
        asyncio.run(_run_loop())
    except Exception as e:
        logger.warning(f"Liquidations WS: фоновый поток завершился с ошибкой: {e}")


def ensure_started() -> None:
    """
    Запускает фоновый поток один раз за жизнь процесса -- поток слушает ВСЕ
    инструменты сразу (!forceOrder@arr), поэтому, в отличие от ws_stream.py,
    здесь не нужно пересоздавать подписку при изменении списка монет.
    Безопасно вызывать многократно (например на каждый клик по монете) --
    повторные вызовы после первого ничего не делают.
    """
    global _thread, _started
    if not _WEBSOCKETS_AVAILABLE or _permanently_disabled or _started:
        return
    _started = True
    _thread = threading.Thread(target=_thread_main, daemon=True)
    _thread.start()


def get_liquidation_pressure(ticker: str) -> Optional[dict]:
    """
    Агрегированные ликвидации по тикеру за последние _WINDOW_SEC секунд:
    суммарный объём лонг-ликвидаций и шорт-ликвидаций.

    Возвращает None, если поток в принципе недоступен (нет библиотеки
    websockets, либо навсегда отключился после серии неудач), или если
    вообще никаких сообщений не было дольше _STALE_AFTER_SEC (поток скорее
    всего оборван / геоблок) -- в обоих случаях вызывающий код должен
    считать источник недоступным, а не подставлять нули как настоящий факт.

    Если поток жив, но для конкретного тикера событий за окно не было
    (тихий период) -- это ВАЛИДНЫЙ результат с нулями, не None.
    """
    if not _WEBSOCKETS_AVAILABLE or _permanently_disabled:
        return None
    with _lock:
        if _last_message_ts is None or time.time() - _last_message_ts > _STALE_AFTER_SEC:
            return None
        now = time.time()
        _prune_old(ticker, now)
        events = list(_events.get(ticker, []))

    long_liq = sum(e["qty"] for e in events if e["side"] == "SELL")
    short_liq = sum(e["qty"] for e in events if e["side"] == "BUY")
    return {"long_liquidated_qty": long_liq, "short_liquidated_qty": short_liq}


def is_active() -> bool:
    """True, если поток в принципе может использоваться (библиотека есть и не выключен навсегда)."""
    return _WEBSOCKETS_AVAILABLE and not _permanently_disabled