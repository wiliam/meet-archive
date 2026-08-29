"""Контракт бандла: что именно уезжает с устройства на хаб.

Общий для клиента (`push_to_hub.py`) и сервера (`ingest.py`), чтобы стороны не
разъехались. Модуль намеренно не зависит ни от FastAPI, ни от archive_summit_meetings:
в образ попадает только каталог админки, а совпадение списка дочерних таблиц со
списком архиватора закреплено тестом.
"""

from __future__ import annotations

import base64
import hashlib
import json
import sqlite3

BUNDLE_VERSION = 1

# --- что едет ---------------------------------------------------------------

DOCUMENT_TABLE = "documents"

# Справочники с UNIQUE по имени. Порядок значим: folders.template_id ссылается на
# summarization_templates, поэтому шаблоны разводятся первыми.
NAME_UNIQUE_TABLES = ("summarization_templates", "folders", "tags")
LOOKUP_TABLES = NAME_UNIQUE_TABLES + ("speakers",)

# Дочерние таблицы встречи. Совпадает с DIRECT_DOCUMENT_TABLES архиватора —
# сторожится тестом test_child_tables_match_archiver.
CHILD_TABLES = (
    "document_tags",
    "document_speakers",
    "topics",
    "action_items",
    "transcription_segments",
    "transcription_chunks",
    "bookmarks",
    "attachments",
    "document_analytics",
    "document_versions",
)

# Составной первичный ключ: после разводки два разных исходных тега могут
# схлопнуться в один целевой и столкнуться. Вставляются через INSERT OR IGNORE.
JUNCTION_TABLES = frozenset({"document_tags", "document_speakers"})

MERGE_ORDER = LOOKUP_TABLES + (DOCUMENT_TABLE,) + CHILD_TABLES
BUNDLE_TABLES = frozenset(MERGE_ORDER)

# chat_sessions/chat_messages — 14 строк, UI нет, явно «вне скоупа» в
# docs/admin-design.md. calendar_events — 0 строк, не читается нигде и несёт
# второй UNIQUE-капкан (provider, external_id). Обе константы оставлены явными,
# чтобы включение обратно было правкой в одну строку.
INCLUDE_CHAT = False
INCLUDE_CALENDAR = False

# --- что не едет ------------------------------------------------------------

# word_confidences_json — 39.9 МБ из 66 МБ базы при 1.6 МБ собственно текста;
# админка её не читает (db.py:214). speakers.embedding — голосовой отпечаток,
# на сервере бесполезен и нежелателен при публичном домене.
EXCLUDED_COLUMNS = {
    "transcription_segments": frozenset({"word_confidences_json"}),
    "speakers": frozenset({"embedding", "embedding_model"}),
}

_DENIED_EXACT = frozenset({"grdb_migrations", "search_history"})
_DENIED_PREFIXES = ("sqlite_", "ingest_")


def is_denied_table(name: str) -> bool:
    """Таблицы, которые не имеет права называть клиент.

    Отдельно перечислены FTS: их содержимое поддерживают триггеры, и прямая
    запись в shadow-таблицы рассогласовала бы поиск с базой.
    """
    if name in _DENIED_EXACT or "_fts" in name:
        return True
    return name.startswith(_DENIED_PREFIXES)


def allowed_columns(table: str, columns) -> list[str]:
    excluded = EXCLUDED_COLUMNS.get(table, frozenset())
    return [c for c in columns if c not in excluded]


# --- кодирование значений ---------------------------------------------------


def encode_value(value):
    """BLOB -> {"$b64": ...}; остальное — обычные скаляры JSON.

    Сегодня единственный BLOB в схеме (`speakers.embedding`) вырезается, так что
    base64 на практике не возникает. Кодировщик всё равно общий: будущая миграция
    Summit, добавившая BLOB, не должна ломать формат.
    """
    if isinstance(value, (bytes, bytearray, memoryview)):
        return {"$b64": base64.b64encode(bytes(value)).decode("ascii")}
    return value


def decode_value(value):
    if isinstance(value, dict) and set(value) == {"$b64"}:
        return base64.b64decode(value["$b64"])
    return value


def encode_row(table: str, row: sqlite3.Row) -> dict:
    return {
        key: encode_value(row[key])
        for key in allowed_columns(table, row.keys())
    }


def decode_row(row: dict) -> dict:
    return {key: decode_value(value) for key, value in row.items()}


# --- отпечаток --------------------------------------------------------------


def content_hash(tables: dict, document_md: str | None) -> str:
    """Отпечаток содержимого — дешёвая проверка «ничего не изменилось».

    Считается одинаково на обеих сторонах, поэтому сериализация должна быть
    канонической: сортировка ключей, без пробелов, без экранирования юникода.
    """
    payload = json.dumps(
        tables, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")
    digest = hashlib.sha256(payload)
    if document_md is not None:
        digest.update(b"\x00")
        digest.update(document_md.encode("utf-8"))
    return "sha256:" + digest.hexdigest()
