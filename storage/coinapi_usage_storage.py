"""
Счётчик реальных запросов к CoinAPI за текущие сутки -- бесплатный тариф
ограничен 100 запросами/день, лимит важно видеть заранее, а не узнавать
постфактум по ошибке 429.

Считаются ТОЛЬКО реальные сетевые вызовы (см. api/coinapi_data.py --
record_request() вызывается внутри функций, кэшированных через
st.cache_data, ПОСЛЕ успешного HTTP-запроса). Попадание в кэш Streamlit не
выполняет тело функции повторно -- значит не тратит квоту CoinAPI и не
увеличивает счётчик, что и требуется: счётчик должен отражать реальный
расход дневного лимита, а не число кликов пользователя.

Хранится в JSON на диске (не в session_state), чтобы счётчик переживал
перезапуск streamlit-процесса В ТЕЧЕНИЕ ОДНОГО ДНЯ -- иначе перезапуск
"обнулял" бы видимый счётчик, хотя реальная дневная квота у CoinAPI не
обнулялась.
"""
import json
import logging
from datetime import date
from pathlib import Path

logger = logging.getLogger(__name__)

_USAGE_FILE = Path(__file__).resolve().parent.parent / "data" / "coinapi_usage.json"
DAILY_LIMIT = 100


def _load() -> dict:
    if not _USAGE_FILE.exists():
        return {"date": str(date.today()), "count": 0}
    try:
        raw = json.loads(_USAGE_FILE.read_text())
        if raw.get("date") != str(date.today()):
            return {"date": str(date.today()), "count": 0}  # новый день -- квота у CoinAPI обнулилась, обнуляем и у себя
        return raw
    except Exception as e:
        logger.warning(f"Не удалось прочитать {_USAGE_FILE}: {e}")
        return {"date": str(date.today()), "count": 0}


def _save(data: dict) -> None:
    _USAGE_FILE.parent.mkdir(parents=True, exist_ok=True)
    _USAGE_FILE.write_text(json.dumps(data, indent=2))


def record_request() -> int:
    """Вызывается ПОСЛЕ каждого реального (не кэшированного) запроса к CoinAPI. Возвращает новый счётчик за сегодня."""
    data = _load()
    data["count"] += 1
    _save(data)
    return data["count"]


def get_usage_today() -> int:
    """Сколько реальных запросов к CoinAPI уже сделано сегодня."""
    return _load()["count"]