"""
Хранение CoinAPI-ключа на диске -- симметрично зашифрован (Fernet), чтобы
ключ не лежал в виде голого текста в файле, который может случайно попасть
в скриншот, бэкап или (по недосмотру) в git.

ЧЕСТНО про уровень защиты: ключ шифрования лежит РЯДОМ, в соседнем файле
той же папки data/. Это защищает от:
- случайного раскрытия при беглом просмотре файла (coinapi_key.enc сам по
  себе выглядит как бессмысленный набор байт, а не читаемый ключ);
- случайного попадания голого ключа в git (оба файла уже в .gitignore).

Это НЕ защита от человека, у которого есть полный доступ к файловой
системе именно этого компьютера -- тот получит оба файла и расшифрует.
Для защиты от такой угрозы нужен внешний секрет-менеджер (переменные
окружения ОС, Vault и т.п.) -- это уже за рамками локального pet-проекта.

Ключ также МОЖНО не сохранять на диск вообще -- просто вставлять в поле
ввода на время текущей сессии браузера (см. ui/analysis_sidebar.py). Эти
функции нужны только для того, кто хочет не вводить его заново при каждом
перезапуске streamlit-процесса.
"""
import logging
from pathlib import Path
from typing import Optional

from cryptography.fernet import Fernet, InvalidToken

logger = logging.getLogger(__name__)

_DATA_DIR = Path(__file__).resolve().parent.parent / "data"
_SECRET_FILE = _DATA_DIR / ".coinapi_secret"   # ключ шифрования (Fernet), генерируется локально
_KEY_FILE = _DATA_DIR / "coinapi_key.enc"      # сам CoinAPI-ключ, зашифрованный этим секретом


def _ensure_secret() -> bytes:
    """Гарантирует наличие локального ключа шифрования, создаёт при первом обращении."""
    _DATA_DIR.mkdir(parents=True, exist_ok=True)
    if not _SECRET_FILE.exists():
        _SECRET_FILE.write_bytes(Fernet.generate_key())
        try:
            _SECRET_FILE.chmod(0o600)  # читать может только владелец файла (не поддерживается на Windows -- тогда просто игнорируется)
        except Exception:
            pass
    return _SECRET_FILE.read_bytes()


def save_api_key(api_key: str) -> None:
    """Шифрует и сохраняет CoinAPI-ключ на диск -- переживает перезапуск приложения."""
    secret = _ensure_secret()
    fernet = Fernet(secret)
    token = fernet.encrypt(api_key.strip().encode("utf-8"))
    _KEY_FILE.write_bytes(token)
    try:
        _KEY_FILE.chmod(0o600)
    except Exception:
        pass


def load_api_key() -> Optional[str]:
    """Читает и расшифровывает сохранённый ключ. None, если ключ не сохранён или файл повреждён/от другого секрета."""
    if not _KEY_FILE.exists() or not _SECRET_FILE.exists():
        return None
    try:
        secret = _SECRET_FILE.read_bytes()
        fernet = Fernet(secret)
        return fernet.decrypt(_KEY_FILE.read_bytes()).decode("utf-8")
    except (InvalidToken, ValueError) as e:
        logger.warning(f"Не удалось расшифровать сохранённый CoinAPI-ключ: {e}")
        return None


def delete_api_key() -> None:
    """Удаляет сохранённый ключ с диска (и файл-секрет тоже, чтобы не плодить мусор)."""
    for f in (_KEY_FILE, _SECRET_FILE):
        try:
            f.unlink(missing_ok=True)
        except Exception:
            pass


def has_saved_key() -> bool:
    return _KEY_FILE.exists()