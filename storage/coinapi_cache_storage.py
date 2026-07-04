"""
Дисковый кэш результатов CoinAPI (Order Book консенсус + Asset overview)
по каждой монете отдельно -- переживает перезапуск streamlit-процесса
(в отличие от кэша в памяти): кликнул на BTC днём, вечером перезапустил
приложение -- данные всё ещё на месте, если не прошло больше
MAX_AGE_SECONDS.

Ключевая договорённость с пользователем: запрос к CoinAPI уходит ТОЛЬКО
по явному нажатию кнопки в ui/analysis_sidebar.py -- никогда автоматически,
даже при повторном клике на ту же монету. Если с момента последнего
запроса прошло больше MAX_AGE_SECONDS (4 часа) -- данные считаются
устаревшими и просто не показываются (пусто), а НЕ подгружаются повторно
сами по себе. Это осознанный выбор пользователя: он готов увидеть "нет
данных" и нажать кнопку заново, лишь бы приложение никогда не тратило
дневную квоту CoinAPI без явного клика.
"""
import json
import logging
import time
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

_CACHE_FILE = Path(__file__).resolve().parent.parent / "data" / "coinapi_cache.json"
MAX_AGE_SECONDS = 4 * 60 * 60  # 4 часа


def _load_all() -> dict:
    if not _CACHE_FILE.exists():
        return {}
    try:
        return json.loads(_CACHE_FILE.read_text())
    except Exception as e:
        logger.warning(f"Не удалось прочитать {_CACHE_FILE}: {e}")
        return {}


def _save_all(data: dict) -> None:
    _CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
    _CACHE_FILE.write_text(json.dumps(data, indent=2, ensure_ascii=False))


def save_result(ticker: str, coinapi_overview: Optional[dict], orderbook: Optional[dict]) -> None:
    """Сохраняет результат запроса CoinAPI по монете на диск вместе с текущим временем (для проверки возраста при чтении)."""
    data = _load_all()
    data[ticker] = {
        "fetched_at": time.time(),
        "coinapi_overview": coinapi_overview,
        "orderbook": orderbook,
    }
    _save_all(data)


def load_result(ticker: str) -> Optional[dict]:
    """
    Возвращает {"coinapi_overview": .., "orderbook": ..}, если для этой
    монеты есть сохранённый результат МОЛОЖЕ MAX_AGE_SECONDS.

    Если запись устарела (>4ч) -- возвращает None И удаляет её из файла
    (чтобы файл не рос вечно устаревшими записями), не пытаясь запросить
    свежие данные сама -- решение о новом запросе принимает только
    пользователь кнопкой в UI.
    """
    data = _load_all()
    entry = data.get(ticker)
    if entry is None:
        return None
    age = time.time() - entry.get("fetched_at", 0)
    if age > MAX_AGE_SECONDS:
        data.pop(ticker, None)
        _save_all(data)
        return None
    return {"coinapi_overview": entry.get("coinapi_overview"), "orderbook": entry.get("orderbook")}


def age_seconds(ticker: str) -> Optional[float]:
    """Сколько секунд прошло с последнего сохранённого запроса по этой монете (для подписи в UI), или None, если записи нет."""
    data = _load_all()
    entry = data.get(ticker)
    if entry is None:
        return None
    return time.time() - entry.get("fetched_at", 0)