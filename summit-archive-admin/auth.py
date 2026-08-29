"""Токены устройств для ingest.

Отдельно от браузерного Basic Auth: у них разные субъекты (человек с паролем
против машины с ключом) и разный жизненный цикл. Общий у них только транспорт.
"""

import hashlib
import os

from fastapi import Header, HTTPException


def load_device_tokens() -> dict[str, str]:
    """`INGEST_TOKENS="mac-a:hex,mac-b:hex"` -> {sha256(токена): device_id}.

    Хранится хэш, а сравнение — поиском по словарю: постоянное время независимо
    от числа устройств, без линейного перебора с compare_digest.
    """
    tokens: dict[str, str] = {}
    for entry in os.environ.get("INGEST_TOKENS", "").split(","):
        entry = entry.strip()
        if not entry or ":" not in entry:
            continue
        device_id, _, secret = entry.partition(":")
        device_id, secret = device_id.strip(), secret.strip()
        if not device_id or not secret:
            continue
        tokens[hashlib.sha256(secret.encode("utf-8")).hexdigest()] = device_id
    return tokens


def require_device(authorization: str | None = Header(None)) -> str:
    """Проверить `Authorization: Bearer …` и вернуть device_id.

    Именно возврат device_id, а не доверие полю в теле бандла, не даёт одному
    устройству писать записи разводки справочников от имени другого.
    """
    tokens = load_device_tokens()
    unauthorized = HTTPException(
        status_code=401,
        detail="Требуется токен устройства",
        headers={"WWW-Authenticate": "Bearer"},
    )
    if not tokens or not authorization:
        raise unauthorized

    scheme, _, secret = authorization.partition(" ")
    if scheme.lower() != "bearer" or not secret.strip():
        raise unauthorized

    digest = hashlib.sha256(secret.strip().encode("utf-8")).hexdigest()
    device_id = tokens.get(digest)
    if device_id is None:
        raise unauthorized
    return device_id
