"""
Живой поток цены/объёма с Binance WebSocket вместо REST-опроса каждые
REFRESH_SEC секунд отдельным HTTP-запросом на каждую монету.

Как это работает:
- ОДИН персистентный WebSocket на ВСЕ тикеры watchlist сразу (комбинированный
  стрим Binance), а не N отдельных HTTP GET каждые 10 секунд.
- Данные (kline 1m: open/high/low/close/volume/qav) складываются в
  потокобезопасный словарь в памяти по мере прихода событий (обычно
  каждые ~1-2 секунды на активную пару) -- то есть цена в таблице
  обновляется практически мгновенно, а не раз в REFRESH_SEC.
- ui/watchlist.py спрашивает у этого модуля свежие данные ПЕРВЫМ делом
  (через api.get_data.fetch_latest_bar); если их нет (только что
  запустились, тикер ещё не переподписан, или WS вообще недоступен --
  например геоблок США на market-data стрим) -- прозрачный откат на
  старый REST-путь (Binance/Coinbase/Kraken), ничего не ломается.

Особенности жизненного цикла:
- WS живёт в отдельном потоке с собственным asyncio event loop -- не
  мешает потоку рендера Streamlit и не требует переписывать остальную
  кодовую базу на async.
- Список тикеров подписки обновляется на лету: при добавлении/удалении
  монеты в watchlist сокет переподключается с новым списком стримов
  (у публичного Binance WS нет метода "добавить стрим в уже открытое
  соединение" без переподключения).
- После нескольких подряд неудачных попыток подключения (например, если
  регион блокирует market-data стрим так же, как основной api.binance.com)
  модуль сам себя отключает на весь процесс и больше не мешает -- REST
  остаётся единственным путём, как было раньше.

ЧЕСТНО: этот модуль не тестировался против реального соединения с Binance
(в среде, где он писался, нет доступа в сеть) -- логика написана
защитно (try/except на каждом шаге, авто-откат на REST), но при первом
запуске стоит последить за логами на случай нюансов реального протокола.
"""
import asyncio
import json
import logging
import threading
import time
from typing import Dict, List, Optional

import pandas as pd

logger = logging.getLogger(__name__)

try:
    import websockets
    _WEBSOCKETS_AVAILABLE = True
except ImportError:  # библиотека может отсутствовать в окружении -- WS просто не запустится
    _WEBSOCKETS_AVAILABLE = False

_WS_BASE_URL = "wss://stream.binance.com:9443/stream"
_MAX_CANDLES_PER_TICKER = 5      # 2 хватило бы (цена + пред. объём), запас на всякий случай
_STALE_AFTER_SEC = 30            # нет свежих данных дольше этого -- считаем поток протухшим, откат на REST
_MAX_CONSECUTIVE_FAILURES = 5    # столько неудачных попыток подряд -- выключаемся насовсем (вероятно геоблок)
_RECONNECT_DELAY_SEC = 3

_lock = threading.Lock()
_candles: Dict[str, List[dict]] = {}   # ticker -> [{open,high,low,close,volume,qav,close_time,is_closed}, ...]
_last_update_ts: Dict[str, float] = {}  # ticker -> time.monotonic() последнего сообщения
_subscribed_tickers: List[str] = []
_thread: Optional[threading.Thread] = None
_stop_flag = threading.Event()
_consecutive_failures = 0
_permanently_disabled = False


def _stream_name(ticker: str) -> str:
    return f"{ticker.lower()}@kline_1m"


def _handle_message(raw: str) -> None:
    try:
        msg = json.loads(raw)
        payload = msg.get("data", msg)  # комбинированный стрим оборачивает в {"stream": .., "data": ..}
        k = payload.get("k")
        if not k:
            return
        ticker = payload.get("s")  # символ в верхнем регистре, например "BTCUSDT"
        if not ticker:
            return
        candle = {
            "open": float(k["o"]), "high": float(k["h"]), "low": float(k["l"]),
            "close": float(k["c"]), "volume": float(k["v"]), "qav": float(k["q"]),
            "close_time": pd.to_datetime(int(k["T"]), unit="ms"),
            "is_closed": bool(k["x"]),
        }
        with _lock:
            bucket = _candles.setdefault(ticker, [])
            if bucket and not bucket[-1]["is_closed"] and bucket[-1]["close_time"] == candle["close_time"]:
                bucket[-1] = candle  # текущая формирующаяся свеча -- обновляем на месте, не плодим дубликаты
            else:
                bucket.append(candle)
                if len(bucket) > _MAX_CANDLES_PER_TICKER:
                    bucket.pop(0)
            _last_update_ts[ticker] = time.monotonic()
    except Exception as e:
        logger.debug(f"WS: не удалось разобрать сообщение: {e}")


async def _run_ws_loop(tickers: List[str]) -> None:
    global _consecutive_failures, _permanently_disabled

    streams = "/".join(_stream_name(t) for t in tickers)
    url = f"{_WS_BASE_URL}?streams={streams}"

    while not _stop_flag.is_set() and not _permanently_disabled:
        try:
            async with websockets.connect(url, ping_interval=20, ping_timeout=20, open_timeout=10) as ws:
                _consecutive_failures = 0  # успешное подключение -- сбрасываем счётчик отказов
                logger.info(f"WS: подключились, {len(tickers)} тикеров")
                async for raw in ws:
                    if _stop_flag.is_set():
                        break
                    _handle_message(raw)
        except Exception as e:
            _consecutive_failures += 1
            logger.warning(f"WS: соединение оборвалось ({e}), попытка {_consecutive_failures}/{_MAX_CONSECUTIVE_FAILURES}")
            if _consecutive_failures >= _MAX_CONSECUTIVE_FAILURES:
                _permanently_disabled = True
                logger.warning(
                    "WS: слишком много неудачных попыток подряд -- похоже, market-data "
                    "стрим Binance недоступен в этом регионе (аналогично геоблоку REST) "
                    "или один из тикеров невалиден для Binance. Отключаю WS до "
                    "перезапуска приложения, дальше работаем через обычный REST-опрос."
                )
                return
        if not _stop_flag.is_set() and not _permanently_disabled:
            time.sleep(_RECONNECT_DELAY_SEC)


def _thread_main(tickers: List[str]) -> None:
    try:
        asyncio.run(_run_ws_loop(tickers))
    except Exception as e:
        logger.warning(f"WS: фоновый поток завершился с ошибкой: {e}")


def ensure_subscribed(tickers: List[str]) -> None:
    """
    Гарантирует, что WS-поток подписан ровно на переданный список тикеров.
    Если список изменился (монета добавлена/удалена) -- пересоздаёт
    соединение. Безопасно вызывать на каждом рендере (каждые REFRESH_SEC) --
    если набор тикеров тот же, что уже подписан, не делает ничего.

    Вызывающий код (ui/watchlist.py) должен заранее отфильтровать тикеры,
    заведомо не торгующиеся на Binance Spot (см. api.get_data.is_binance_known_bad) --
    иначе один невалидный символ в комбинированном стриме может сорвать
    подписку сразу для всех остальных тикеров тоже.
    """
    global _thread, _subscribed_tickers

    if not _WEBSOCKETS_AVAILABLE or _permanently_disabled or not tickers:
        return

    tickers_sorted = sorted(set(tickers))
    if tickers_sorted == _subscribed_tickers and _thread is not None and _thread.is_alive():
        return  # уже подписаны на этот же набор

    if _thread is not None and _thread.is_alive():
        _stop_flag.set()
        _thread.join(timeout=5)

    _stop_flag.clear()
    _subscribed_tickers = tickers_sorted
    _thread = threading.Thread(target=_thread_main, args=(tickers_sorted,), daemon=True)
    _thread.start()


def get_live_candles(ticker: str) -> Optional[pd.DataFrame]:
    """
    Мини-DataFrame (2-5 последних 1m свечей) из WS-кэша для тикера, если
    данные есть и не протухли. None -- если WS не подключён, тикер ещё не
    успел прислать данные, или данные старше _STALE_AFTER_SEC (тогда
    вызывающий код должен откатиться на обычный REST-запрос).
    """
    if not _WEBSOCKETS_AVAILABLE or _permanently_disabled:
        return None
    with _lock:
        bucket = list(_candles.get(ticker, []))
        last_ts = _last_update_ts.get(ticker)
    if not bucket or last_ts is None:
        return None
    if time.monotonic() - last_ts > _STALE_AFTER_SEC:
        return None  # давно не обновлялось -- не доверяем, пусть REST освежит

    df = pd.DataFrame(bucket).set_index("close_time")
    return df[["open", "high", "low", "close", "volume", "qav"]]


def is_active() -> bool:
    """True, если WS в принципе может использоваться (библиотека есть и не выключен навсегда)."""
    return _WEBSOCKETS_AVAILABLE and not _permanently_disabled


def is_connected() -> bool:
    """True, если поток сейчас реально жив -- используется для индикатора '⚡ live' в UI."""
    return _thread is not None and _thread.is_alive() and not _permanently_disabled