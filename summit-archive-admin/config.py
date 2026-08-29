"""Роль экземпляра и проверки при старте.

Локально админка живёт на 127.0.0.1 и настраивается нулём переменных. На хостинге
тот же код смотрит в публичный интернет и держит чужие личные встречи, поэтому
часть послаблений там должна быть недоступна — не по договорённости, а по коду.
"""

import os

ROLE_LOCAL = "local"
ROLE_SERVER = "server"

MIN_PASSWORD_LENGTH = 12


def role() -> str:
    return os.environ.get("MEET_ARCHIVE_ROLE", ROLE_LOCAL).strip().lower()


def is_server() -> bool:
    return role() == ROLE_SERVER


def ingest_tokens_raw() -> str:
    return os.environ.get("INGEST_TOKENS", "").strip()


def ingest_enabled() -> bool:
    return bool(ingest_tokens_raw())


def max_bundle_bytes() -> int:
    return int(os.environ.get("INGEST_MAX_BYTES", 32 * 1024 * 1024))


def check_startup() -> None:
    """Отказаться подниматься в серверной роли без защиты.

    Исключение здесь роняет uvicorn с ненулевым кодом, и Dokploy показывает
    неудавшийся деплой. Это осознанно предпочтительнее: иначе потеря переменной
    окружения молча выкладывает архив личных встреч в открытый доступ.
    """
    if not is_server():
        return

    import auth

    problems = []
    user = os.environ.get("ADMIN_USER", "")
    password = os.environ.get("ADMIN_PASSWORD", "")
    if not user:
        problems.append("ADMIN_USER не задан")
    if not password:
        problems.append("ADMIN_PASSWORD не задан")
    elif len(password) < MIN_PASSWORD_LENGTH:
        problems.append(
            f"ADMIN_PASSWORD короче {MIN_PASSWORD_LENGTH} символов"
        )
    if not auth.load_device_tokens():
        problems.append("INGEST_TOKENS пуст или не разбирается")
    if not os.environ.get("SUMMIT_ARCHIVE_ROOT"):
        problems.append("SUMMIT_ARCHIVE_ROOT не задан")

    if problems:
        raise RuntimeError(
            "Серверная роль требует полной конфигурации: " + "; ".join(problems)
        )
