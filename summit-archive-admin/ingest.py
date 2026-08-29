"""Слияние присланной встречи в консолидированную базу хаба.

Почему это не тот же код, что в архиваторе
------------------------------------------
`archive_summit_meetings.insert_rows` вставляет справочники через
`INSERT OR REPLACE`. Для одной машины это безопасно: id совпадают. Для двух —
разрушительно. `folders.name`, `tags.name` и `summarization_templates.name`
объявлены UNIQUE, а REPLACE разрешает конфликт по UNIQUE удалением старой строки.
`document_tags.tag_id` объявлен `ON DELETE CASCADE`, поэтому вставка тега «work»
со второй машины (другой UUID, то же имя) молча уносит все связи тегов первой.

Отсюда инвариант модуля: **ни одного `INSERT OR REPLACE` в таблицу, у которой
есть UNIQUE по тексту.** Справочники сводятся по имени до вставки, ссылающиеся
строки переписываются. Инвариант закреплён тестом, читающим этот файл.
"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

import bundle as bundle_mod
from bootstrap import connect_rw, writer_lock

# Куда ведёт каждая ссылающаяся колонка. UUID встреч, сегментов, тем и чанков
# здесь отсутствуют намеренно: они не переписываются никогда, и именно это
# сохраняет валидность transcription_chunks.segment_ids_json — мягкой ссылки
# на сегменты без constraint, которую переписывание ключей сломало бы молча.
FK_REWRITES = {
    ("documents", "template_id"): "summarization_templates",
    ("documents", "folder_id"): "folders",
    ("folders", "template_id"): "summarization_templates",
    ("folders", "parent_folder_id"): "folders",
    ("document_tags", "tag_id"): "tags",
    ("document_speakers", "speaker_id"): "speakers",
    ("transcription_segments", "speaker_id"): "speakers",
    ("speakers", "parent_speaker_id"): "speakers",
}

_process_lock = threading.Lock()


class BundleError(ValueError):
    """Бандл не соответствует контракту -> 400."""


class SchemaAhead(BundleError):
    """Схема устройства новее серверной -> 409."""

    def __init__(self, message: str, unknown: list[str]):
        super().__init__(message)
        self.unknown = unknown


class Busy(RuntimeError):
    """База занята другим писателем -> 503."""


@dataclass
class IngestResult:
    document_id: str
    created: bool = False
    unchanged: bool = False
    counts: dict[str, int] = field(default_factory=dict)
    remapped: dict[str, dict[str, str]] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)


# --- вспомогательное --------------------------------------------------------


def _table_columns(conn: sqlite3.Connection, table: str) -> list[str]:
    return [r[1] for r in conn.execute(f'PRAGMA table_info("{table}")')]


def _insert_row(
    conn: sqlite3.Connection, table: str, row: dict, *, or_ignore: bool = False
) -> None:
    """Обычный INSERT. Никогда OR REPLACE — см. инвариант модуля.

    Колонки пересекаются со схемой сервера: устройство на более старой версии
    Summit просто не пришлёт часть колонок, и SQLite подставит DEFAULT/NULL.
    """
    known = set(_table_columns(conn, table))
    columns = [c for c in bundle_mod.allowed_columns(table, row) if c in known]
    if not columns:
        raise BundleError(f"Нет ни одной известной колонки для таблицы {table}")
    values = [bundle_mod.decode_value(row[c]) for c in columns]
    quoted = ", ".join(f'"{c}"' for c in columns)
    placeholders = ", ".join("?" for _ in columns)
    verb = "INSERT OR IGNORE INTO" if or_ignore else "INSERT INTO"
    conn.execute(f'{verb} "{table}" ({quoted}) VALUES ({placeholders})', values)


def _sort_by_parent(rows: list[dict], parent_key: str) -> list[dict]:
    """Родители раньше детей: speakers.parent_speaker_id — настоящий self-FK."""
    by_id = {r.get("id"): r for r in rows}
    ordered: list[dict] = []
    seen: set = set()

    def visit(row: dict, path: set) -> None:
        rid = row.get("id")
        if rid in seen:
            return
        parent_id = row.get(parent_key)
        if parent_id and parent_id in by_id and parent_id not in path:
            visit(by_id[parent_id], path | {rid})
        if rid not in seen:
            seen.add(rid)
            ordered.append(row)

    for row in rows:
        visit(row, {row.get("id")})
    return ordered


# --- валидация --------------------------------------------------------------


def validate_bundle(payload: dict) -> None:
    if not isinstance(payload, dict):
        raise BundleError("Бандл должен быть JSON-объектом")
    version = payload.get("bundle_version")
    if version != bundle_mod.BUNDLE_VERSION:
        raise BundleError(
            f"Версия бандла {version!r}, сервер понимает {bundle_mod.BUNDLE_VERSION}"
        )

    doc_id = payload.get("document_id")
    if not isinstance(doc_id, str) or not doc_id:
        raise BundleError("Не указан document_id")

    tables = payload.get("tables")
    if not isinstance(tables, dict):
        raise BundleError("Поле tables должно быть объектом")

    for name in tables:
        if bundle_mod.is_denied_table(name):
            raise BundleError(f"Таблица {name!r} не принимается")
        if name not in bundle_mod.BUNDLE_TABLES:
            raise BundleError(f"Неизвестная таблица {name!r}")

    documents = tables.get(bundle_mod.DOCUMENT_TABLE) or []
    if len(documents) != 1:
        raise BundleError("В бандле должна быть ровно одна встреча")
    if documents[0].get("id") != doc_id:
        raise BundleError("documents.id не совпадает с document_id")

    # Иначе бандл одной встречи мог бы протащить строки, привязанные к чужой.
    for table in bundle_mod.CHILD_TABLES:
        for row in tables.get(table) or []:
            if row.get("document_id") != doc_id:
                raise BundleError(
                    f"Строка таблицы {table} принадлежит другой встрече"
                )


def check_schema_compat(conn: sqlite3.Connection, payload: dict) -> list[str]:
    """Сверка миграций Summit.

    Авто-ALTER здесь недопустим: у архиватора есть живая база как эталон, у
    сервера эталона нет, и молчаливый DDL на общей базе хуже громкого отказа.
    """
    theirs = list((payload.get("source_schema") or {}).get("migrations") or [])
    if not theirs:
        return []
    ours = {r[0] for r in conn.execute("SELECT identifier FROM grdb_migrations")}
    unknown = [m for m in theirs if m not in ours]
    if unknown:
        raise SchemaAhead(
            "Схема устройства новее серверной; перегенерируйте schema.sql",
            unknown,
        )
    missing = sorted(ours - set(theirs))
    if missing:
        return [
            "Устройство на более старой версии Summit; отсутствуют миграции: "
            + ", ".join(missing)
        ]
    return []


# --- разводка справочников --------------------------------------------------


def _mapped_id(
    conn: sqlite3.Connection, device_id: str, table: str, source_id: str
) -> str | None:
    row = conn.execute(
        "SELECT target_id FROM ingest_id_map"
        " WHERE device_id = ? AND table_name = ? AND source_id = ?",
        (device_id, table, source_id),
    ).fetchone()
    return row["target_id"] if row else None


def _remember_mapping(
    conn: sqlite3.Connection, device_id: str, table: str, source_id: str, target_id: str
) -> None:
    conn.execute(
        "INSERT OR REPLACE INTO ingest_id_map"
        " (device_id, table_name, source_id, target_id, created_at)"
        " VALUES (?, ?, ?, ?, ?)",
        (device_id, table, source_id, target_id, time.time()),
    )


def _resolve_named(
    conn: sqlite3.Connection,
    device_id: str,
    table: str,
    rows: list[dict],
    warnings: list[str],
) -> dict[str, str]:
    """Свести строки справочника с UNIQUE по имени к целевым id."""
    mapping: dict[str, str] = {}
    for row in rows:
        source_id = row.get("id")
        name = row.get("name")
        if not source_id:
            continue

        known = _mapped_id(conn, device_id, table, source_id)
        if known and conn.execute(
            f'SELECT 1 FROM "{table}" WHERE id = ?', (known,)
        ).fetchone():
            mapping[source_id] = known
            continue

        same_id = conn.execute(
            f'SELECT name FROM "{table}" WHERE id = ?', (source_id,)
        ).fetchone()
        if same_id is not None:
            # Тот же UUID — та же сущность. Переименование на устройстве не
            # переносим: побеждает первый записавший, иначе строки болтались бы
            # туда-сюда при каждой отправке с разных машин.
            if name and same_id["name"] != name:
                warnings.append(
                    f'{table}: имя "{name}" отличается от сохранённого'
                    f' "{same_id["name"]}", оставлено сохранённое'
                )
            target = source_id
        else:
            by_name = conn.execute(
                f'SELECT id FROM "{table}" WHERE name = ?', (name,)
            ).fetchone()
            if by_name is not None:
                # Вот здесь и обходится ловушка: одноимённая строка другого
                # устройства переиспользуется, а не затирается через REPLACE.
                target = by_name["id"]
            else:
                _insert_row(conn, table, row)
                target = source_id

        mapping[source_id] = target
        _remember_mapping(conn, device_id, table, source_id, target)
    return mapping


def _resolve_speakers(
    conn: sqlite3.Connection, device_id: str, rows: list[dict]
) -> dict[str, str]:
    """У speakers UNIQUE по имени нет, ловушки нет — но нужна политика склейки.

    Склеиваем по непустому email и по совпадению UUID. По имени — никогда:
    в живом архиве у 95 спикеров ноль email и имена вида «Speaker-90ca3f»,
    так что совпадение имён было бы случайностью, а для настоящих имён склеило
    бы разных людей.
    """
    mapping: dict[str, str] = {}
    for row in _sort_by_parent(rows, "parent_speaker_id"):
        source_id = row.get("id")
        if not source_id:
            continue

        known = _mapped_id(conn, device_id, "speakers", source_id)
        if known and conn.execute(
            "SELECT 1 FROM speakers WHERE id = ?", (known,)
        ).fetchone():
            mapping[source_id] = known
            continue

        email = (row.get("email") or "").strip().lower()
        target = None
        if email:
            hit = conn.execute(
                "SELECT id FROM speakers WHERE lower(trim(email)) = ?", (email,)
            ).fetchone()
            if hit is not None:
                target = hit["id"]

        if target is None:
            if conn.execute(
                "SELECT 1 FROM speakers WHERE id = ?", (source_id,)
            ).fetchone():
                target = source_id
            else:
                staged = dict(row)
                parent = staged.get("parent_speaker_id")
                if parent:
                    staged["parent_speaker_id"] = mapping.get(parent, parent)
                _insert_row(conn, "speakers", staged)
                target = source_id

        mapping[source_id] = target
        _remember_mapping(conn, device_id, "speakers", source_id, target)
    return mapping


def resolve_lookups(
    conn: sqlite3.Connection, device_id: str, payload: dict, warnings: list[str]
) -> dict[str, dict[str, str]]:
    tables = payload["tables"]
    idmap: dict[str, dict[str, str]] = {}
    for table in bundle_mod.NAME_UNIQUE_TABLES:
        idmap[table] = _resolve_named(
            conn, device_id, table, tables.get(table) or [], warnings
        )
    idmap["speakers"] = _resolve_speakers(conn, device_id, tables.get("speakers") or [])
    return idmap


def rewrite_fks(payload: dict, idmap: dict[str, dict[str, str]]) -> None:
    """Переписать ссылки на справочники в присланных строках, на месте."""
    for table, rows in payload["tables"].items():
        for row in rows:
            for column, target_table in (
                (col, tgt)
                for (tbl, col), tgt in FK_REWRITES.items()
                if tbl == table
            ):
                value = row.get(column)
                if value:
                    row[column] = idmap.get(target_table, {}).get(value, value)


# --- применение -------------------------------------------------------------


def apply_bundle(conn: sqlite3.Connection, device_id: str, payload: dict) -> IngestResult:
    """Слить одну встречу. Вызывается внутри уже открытой транзакции."""
    validate_bundle(payload)
    warnings = check_schema_compat(conn, payload)

    doc_id = payload["document_id"]
    tables = payload["tables"]

    idmap = resolve_lookups(conn, device_id, payload, warnings)
    rewrite_fks(payload, idmap)

    existed = conn.execute(
        "SELECT 1 FROM documents WHERE id = ?", (doc_id,)
    ).fetchone() is not None

    # Каскад уносит детей и заставляет сработать *_fts_delete триггеры —
    # одна строка, которая делает повторную отправку идемпотентной.
    conn.execute("DELETE FROM documents WHERE id = ?", (doc_id,))

    _insert_row(conn, "documents", tables["documents"][0])

    counts: dict[str, int] = {}
    for table in bundle_mod.CHILD_TABLES:
        rows = tables.get(table) or []
        for row in rows:
            _insert_row(
                conn, table, row, or_ignore=table in bundle_mod.JUNCTION_TABLES
            )
        if rows:
            counts[table] = len(rows)

    now = time.time()
    conn.execute(
        "INSERT INTO ingest_devices (device_id, label, first_seen, last_seen, bundle_count)"
        " VALUES (?, ?, ?, ?, 1)"
        " ON CONFLICT(device_id) DO UPDATE SET"
        "   label = coalesce(excluded.label, ingest_devices.label),"
        "   last_seen = excluded.last_seen,"
        "   bundle_count = ingest_devices.bundle_count + 1",
        (device_id, payload.get("device_label"), now, now),
    )
    conn.execute(
        "INSERT OR REPLACE INTO ingest_documents"
        " (document_id, device_id, modified_date, content_hash, ingested_at, audio_files)"
        " VALUES (?, ?, ?, ?, ?, ?)",
        (
            doc_id,
            device_id,
            float(payload.get("modified_date") or 0.0),
            payload.get("content_hash"),
            now,
            json.dumps(payload.get("audio_files") or [], ensure_ascii=False),
        ),
    )

    return IngestResult(
        document_id=doc_id,
        created=not existed,
        counts=counts,
        remapped={t: m for t, m in idmap.items() if m},
        warnings=warnings,
    )


def _is_unchanged(conn: sqlite3.Connection, payload: dict) -> bool:
    incoming = payload.get("content_hash")
    if not incoming:
        return False
    row = conn.execute(
        "SELECT content_hash FROM ingest_documents WHERE document_id = ?",
        (payload.get("document_id"),),
    ).fetchone()
    return row is not None and row["content_hash"] == incoming


def ingest_bundle(db_path: Path, device_id: str, payload: dict) -> IngestResult:
    """Точка входа: блокировки, транзакция, откат при любой ошибке."""
    db_path = Path(db_path)
    if not _process_lock.acquire(blocking=False):
        raise Busy("Слияние уже выполняется")
    try:
        try:
            lock = writer_lock(db_path.parent)
            lock.__enter__()
        except TimeoutError as exc:
            raise Busy(str(exc)) from exc
        try:
            conn = connect_rw(db_path)
            try:
                if _is_unchanged(conn, payload):
                    return IngestResult(
                        document_id=payload["document_id"], unchanged=True
                    )
                # IMMEDIATE, а не обычный BEGIN: блокировка записи берётся сразу,
                # конкурент падает на входе, а не на её апгрейде посреди слияния.
                conn.execute("BEGIN IMMEDIATE")
                try:
                    result = apply_bundle(conn, device_id, payload)
                except Exception:
                    conn.execute("ROLLBACK")
                    raise
                conn.execute("COMMIT")
                return result
            finally:
                conn.close()
        finally:
            lock.__exit__(None, None, None)
    finally:
        _process_lock.release()


def ingest_state(conn: sqlite3.Connection) -> dict:
    """Что у сервера уже есть — заменяет собой wall-clock watermark клиента."""
    documents = {
        row["document_id"]: {
            "modified_date": row["modified_date"],
            "content_hash": row["content_hash"],
            "device_id": row["device_id"],
        }
        for row in conn.execute(
            "SELECT document_id, modified_date, content_hash, device_id"
            " FROM ingest_documents"
        )
    }
    migrations = [r[0] for r in conn.execute("SELECT identifier FROM grdb_migrations")]
    return {
        "bundle_version": bundle_mod.BUNDLE_VERSION,
        "server_time": time.time(),
        "schema": {"migrations": migrations},
        "count": len(documents),
        "documents": documents,
    }
