"""
Хранение watchlist (списка тикеров) в простом JSON-файле.

Намеренно БЕЗ @st.cache_data -- в v1 кэш на load_coins() приводил к тому,
что после remove_coin() список на экране не обновлялся (кэш не знал,
что файл на диске изменился). Файл маленький (десятки тикеров), читать
его с диска на каждый вызов -- дёшево, а бага с "не удаляется" быть не может.
"""
import json
import logging
from pathlib import Path
from typing import List, Tuple

logger = logging.getLogger(__name__)

DATA_FILE = Path(__file__).resolve().parent.parent / "data" / "coins.json"
DEFAULT_COINS = ["BTCUSDT", "ETHUSDT", "SOLUSDT"]


def _ensure_file() -> None:
    DATA_FILE.parent.mkdir(parents=True, exist_ok=True)
    if not DATA_FILE.exists():
        DATA_FILE.write_text(json.dumps(DEFAULT_COINS, indent=2, ensure_ascii=False))


def load_coins() -> List[str]:
    """Читает список тикеров с диска. При первом запуске создаёт файл с дефолтами."""
    _ensure_file()
    try:
        return json.loads(DATA_FILE.read_text())
    except Exception as e:
        logger.error(f"Не удалось прочитать {DATA_FILE}: {e}")
        return list(DEFAULT_COINS)


def _save(coins: List[str]) -> None:
    DATA_FILE.write_text(json.dumps(coins, indent=2, ensure_ascii=False))


def add_coin(ticker: str) -> Tuple[bool, str]:
    """Добавляет тикер в watchlist. Возвращает (успех, сообщение)."""
    coins = load_coins()
    if ticker in coins:
        return False, "уже есть в списке"
    coins.append(ticker)
    _save(coins)
    return True, "добавлена в watchlist"


def remove_coin(ticker: str) -> bool:
    """Удаляет тикер из watchlist. Возвращает True, если реально был удалён."""
    coins = load_coins()
    if ticker not in coins:
        return False
    coins.remove(ticker)
    _save(coins)
    return True