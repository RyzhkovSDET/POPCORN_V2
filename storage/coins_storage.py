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
# Новое имя файла (не coin_colors.json) -- специально, чтобы не читать
# старые данные в формате string ("gray"/"teal"), оставшиеся с прошлой
# версии схемы, где хранился цвет, а не число кликов.
COLORS_FILE = Path(__file__).resolve().parent.parent / "data" / "coin_click_counts.json"
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


# ---------------------------------------------------------------------------
# Счётчик кликов по монете в watchlist -- по нему считается цвет фона кнопки
# (цикл: прозрачный -> бирюзовый -> серый -> прозрачный). Чисто визуальная
# персональная разметка, не влияет ни на какие расчёты. Храним отдельно от
# coins.json, чтобы не трогать формат основного списка тикеров.
# ---------------------------------------------------------------------------


def _ensure_colors_file() -> None:
    COLORS_FILE.parent.mkdir(parents=True, exist_ok=True)
    if not COLORS_FILE.exists():
        COLORS_FILE.write_text(json.dumps({}, indent=2, ensure_ascii=False))


def load_coin_click_counts() -> dict:
    """Читает {ticker: click_count} с диска. Отсутствующий тикер = 0 (прозрачный)."""
    _ensure_colors_file()
    try:
        raw = json.loads(COLORS_FILE.read_text())
        # Защита от битых/чужеродных значений (например, если файл был
        # создан вручную или повреждён) -- нечисловое значение просто
        # пропускаем, чтобы не падать при вычитании дальше по коду.
        return {k: v for k, v in raw.items() if isinstance(v, int)}
    except Exception as e:
        logger.error(f"Не удалось прочитать {COLORS_FILE}: {e}")
        return {}


def set_coin_click_count(ticker: str, count: int) -> None:
    """Сохраняет число кликов по монете (0 -- удаляет запись, чтобы файл не рос вечно)."""
    counts = load_coin_click_counts()
    if count <= 0:
        counts.pop(ticker, None)
    else:
        counts[ticker] = count
    COLORS_FILE.write_text(json.dumps(counts, indent=2, ensure_ascii=False))