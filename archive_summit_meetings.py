#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import shutil
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence

DIRECT_DOCUMENT_TABLES = [
    ("action_items", "document_id"),
    ("attachments", "document_id"),
    ("bookmarks", "document_id"),
    ("document_analytics", "document_id"),
    ("document_speakers", "document_id"),
    ("document_tags", "document_id"),
    ("document_versions", "document_id"),
    ("topics", "document_id"),
    ("transcription_chunks", "document_id"),
    ("transcription_segments", "document_id"),
]

CLEAR_TABLES_ON_INIT = [
    "chat_messages",
    "chat_sessions",
    "calendar_events",
    "documents",
    "action_items",
    "attachments",
    "bookmarks",
    "document_analytics",
    "document_speakers",
    "document_tags",
    "document_versions",
    "topics",
    "transcription_chunks",
    "transcription_segments",
    "search_history",
]


@dataclass
class DocumentSummary:
    doc_id: str
    title: str
    created_date: float
    modified_date: float
    recording_duration: float
    folder_name: str | None
    tags: list[str]


def connect(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def utc_now_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")


def utc_now_ts() -> float:
    return datetime.now(timezone.utc).timestamp()


def format_ts(value: float | None) -> str:
    if value is None:
        return "-"
    try:
        return datetime.fromtimestamp(float(value)).astimezone().strftime("%Y-%m-%d %H:%M:%S %Z")
    except Exception:
        return str(value)


def sqlite_backup_to_path(src: sqlite3.Connection, dest_path: Path) -> None:
    dest_path.parent.mkdir(parents=True, exist_ok=True)
    dest = sqlite3.connect(dest_path)
    try:
        src.backup(dest)
        dest.commit()
    finally:
        dest.close()


def create_source_backup(src: sqlite3.Connection, source_db: Path) -> Path:
    stamp = utc_now_stamp()
    backup = source_db.with_name(f"database-backup-{stamp}-before-archive.sqlite")
    sqlite_backup_to_path(src, backup)
    return backup


def insert_rows(dest: sqlite3.Connection, table: str, rows: Sequence[sqlite3.Row]) -> None:
    if not rows:
        return
    columns = list(rows[0].keys())
    quoted = ", ".join(f'"{c}"' for c in columns)
    placeholders = ", ".join("?" for _ in columns)
    sql = f"INSERT OR REPLACE INTO {table} ({quoted}) VALUES ({placeholders})"
    dest.executemany(sql, [tuple(row[c] for c in columns) for row in rows])


def copy_query_rows(src: sqlite3.Connection, dest: sqlite3.Connection, table: str, where_sql: str, params: Sequence[object]) -> int:
    rows = src.execute(f"SELECT * FROM {table} WHERE {where_sql}", params).fetchall()
    insert_rows(dest, table, rows)
    return len(rows)


def ensure_archive_db(src: sqlite3.Connection, archive_db: Path) -> None:
    if archive_db.exists():
        return
    sqlite_backup_to_path(src, archive_db)
    conn = connect(archive_db)
    try:
        conn.execute("BEGIN")
        for table in ("chat_messages", "chat_sessions", "calendar_events"):
            conn.execute(f"DELETE FROM {table}")
        conn.execute("DELETE FROM documents")
        for table in CLEAR_TABLES_ON_INIT:
            if table not in {"chat_messages", "chat_sessions", "calendar_events", "documents"}:
                conn.execute(f"DELETE FROM {table}")
        conn.commit()
    finally:
        conn.close()


def _table_columns(conn: sqlite3.Connection, table: str) -> dict[str, sqlite3.Row]:
    return {row["name"]: row for row in conn.execute(f'PRAGMA table_info("{table}")')}


def reconcile_archive_schema(
    src: sqlite3.Connection, archive_conn: sqlite3.Connection, dry_run: bool
) -> None:
    """Догоняет схему архива после миграций приложения: ADD COLUMN для колонок,
    появившихся в живой базе. Новые таблицы не создаёт (скрипт их всё равно не
    копирует) — только предупреждает, чтобы дрейф не остался незамеченным."""
    src_tables = [
        r["name"]
        for r in src.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
        )
    ]
    archive_tables = {
        r["name"]
        for r in archive_conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
        )
    }

    alters: list[str] = []
    for table in src_tables:
        if table not in archive_tables:
            print(f"WARNING: table '{table}' exists in source but not in archive (not copied by this script)")
            continue
        archive_cols = _table_columns(archive_conn, table)
        for name, col in _table_columns(src, table).items():
            if name in archive_cols:
                continue
            ddl = f'ALTER TABLE "{table}" ADD COLUMN "{name}" {col["type"]}'
            if col["notnull"]:
                if col["dflt_value"] is None:
                    print(
                        f"WARNING: cannot add NOT NULL column {table}.{name} without default; skipping"
                    )
                    continue
                ddl += f" NOT NULL DEFAULT {col['dflt_value']}"
            elif col["dflt_value"] is not None:
                ddl += f" DEFAULT {col['dflt_value']}"
            alters.append(ddl)

    if not alters:
        return
    if dry_run:
        print("Schema reconciliation needed (dry-run, not applied):")
        for ddl in alters:
            print(f"  {ddl}")
        return
    archive_conn.execute("BEGIN")
    try:
        for ddl in alters:
            archive_conn.execute(ddl)
        archive_conn.commit()
    except Exception:
        archive_conn.rollback()
        raise
    print("Schema reconciled with source:")
    for ddl in alters:
        print(f"  {ddl}")


def load_state(state_path: Path) -> dict[str, Any]:
    if not state_path.exists():
        return {
            "last_successful_sync_at": None,
            "last_sync_mode": None,
            "last_prune_keep_active": None,
            "last_synced_count": 0,
            "last_pruned_count": 0,
        }
    return json.loads(state_path.read_text(encoding="utf-8"))


def save_state(state_path: Path, state: dict[str, Any]) -> None:
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def get_document(src: sqlite3.Connection, document_id: str) -> sqlite3.Row:
    row = src.execute("SELECT * FROM documents WHERE id = ?", (document_id,)).fetchone()
    if row is None:
        raise SystemExit(f"Document not found: {document_id}")
    return row


def copy_lookup_rows(src: sqlite3.Connection, dest: sqlite3.Connection, doc: sqlite3.Row, document_id: str) -> None:
    template_ids: set[str] = set()
    folder_ids: set[str] = set()
    tag_ids: set[str] = set()
    speaker_ids: set[str] = set()

    if doc["template_id"]:
        template_ids.add(doc["template_id"])
    if doc["folder_id"]:
        folder_ids.add(doc["folder_id"])

    for row in src.execute("SELECT tag_id FROM document_tags WHERE document_id = ?", (document_id,)):
        if row[0]:
            tag_ids.add(row[0])

    for row in src.execute("SELECT speaker_id FROM document_speakers WHERE document_id = ?", (document_id,)):
        if row[0]:
            speaker_ids.add(row[0])

    for row in src.execute("SELECT DISTINCT speaker_id FROM transcription_segments WHERE document_id = ? AND speaker_id IS NOT NULL", (document_id,)):
        if row[0]:
            speaker_ids.add(row[0])

    if folder_ids:
        placeholders = ", ".join("?" for _ in folder_ids)
        folder_rows = src.execute(f"SELECT * FROM folders WHERE id IN ({placeholders})", tuple(folder_ids)).fetchall()
        insert_rows(dest, "folders", folder_rows)
        for row in folder_rows:
            if row["template_id"]:
                template_ids.add(row["template_id"])

    if template_ids:
        placeholders = ", ".join("?" for _ in template_ids)
        rows = src.execute(f"SELECT * FROM summarization_templates WHERE id IN ({placeholders})", tuple(template_ids)).fetchall()
        insert_rows(dest, "summarization_templates", rows)

    if tag_ids:
        placeholders = ", ".join("?" for _ in tag_ids)
        rows = src.execute(f"SELECT * FROM tags WHERE id IN ({placeholders})", tuple(tag_ids)).fetchall()
        insert_rows(dest, "tags", rows)

    pending = set(speaker_ids)
    seen: set[str] = set()
    while pending:
        speaker_id = pending.pop()
        if speaker_id in seen:
            continue
        seen.add(speaker_id)
        row = src.execute("SELECT * FROM speakers WHERE id = ?", (speaker_id,)).fetchone()
        if row is None:
            continue
        insert_rows(dest, "speakers", [row])
        parent_id = row["parent_speaker_id"]
        if parent_id and parent_id not in seen:
            pending.add(parent_id)


def delete_document_from_archive(dest: sqlite3.Connection, document_id: str) -> None:
    session_ids = [
        row[0]
        for row in dest.execute("SELECT id FROM chat_sessions WHERE document_id = ?", (document_id,)).fetchall()
    ]
    if session_ids:
        placeholders = ", ".join("?" for _ in session_ids)
        dest.execute(f"DELETE FROM chat_messages WHERE session_id IN ({placeholders})", tuple(session_ids))
    dest.execute("DELETE FROM chat_sessions WHERE document_id = ?", (document_id,))
    dest.execute("DELETE FROM calendar_events WHERE linked_document_id = ?", (document_id,))
    dest.execute("DELETE FROM documents WHERE id = ?", (document_id,))


def copy_document_to_archive(src: sqlite3.Connection, dest: sqlite3.Connection, document_id: str) -> DocumentSummary:
    doc = get_document(src, document_id)
    delete_document_from_archive(dest, document_id)
    copy_lookup_rows(src, dest, doc, document_id)
    insert_rows(dest, "documents", [doc])

    for table, column in DIRECT_DOCUMENT_TABLES:
        copy_query_rows(src, dest, table, f"{column} = ?", (document_id,))

    copy_query_rows(src, dest, "calendar_events", "linked_document_id = ?", (document_id,))

    session_rows = src.execute("SELECT * FROM chat_sessions WHERE document_id = ?", (document_id,)).fetchall()
    insert_rows(dest, "chat_sessions", session_rows)
    session_ids = [row["id"] for row in session_rows]
    if session_ids:
        placeholders = ", ".join("?" for _ in session_ids)
        message_rows = src.execute(
            f"SELECT * FROM chat_messages WHERE session_id IN ({placeholders})",
            tuple(session_ids),
        ).fetchall()
        insert_rows(dest, "chat_messages", message_rows)

    folder_name = None
    if doc["folder_id"]:
        folder_row = src.execute("SELECT name FROM folders WHERE id = ?", (doc["folder_id"],)).fetchone()
        folder_name = folder_row[0] if folder_row else None

    tag_rows = src.execute(
        """
        SELECT tags.name
        FROM tags
        JOIN document_tags ON document_tags.tag_id = tags.id
        WHERE document_tags.document_id = ?
        ORDER BY tags.name COLLATE NOCASE
        """,
        (document_id,),
    ).fetchall()

    return DocumentSummary(
        doc_id=doc["id"],
        title=doc["title"],
        created_date=doc["created_date"],
        modified_date=doc["modified_date"],
        recording_duration=doc["recording_duration"] or 0.0,
        folder_name=folder_name,
        tags=[row[0] for row in tag_rows],
    )


def copy_meeting_folder_if_missing(source_root: Path, archive_root: Path, document_id: str) -> None:
    src_dir = source_root / document_id
    if not src_dir.exists():
        return
    dest_dir = archive_root / document_id
    if dest_dir.exists():
        return
    shutil.copytree(src_dir, dest_dir)


def move_meeting_folder_to_archive(source_root: Path, archive_root: Path, document_id: str) -> None:
    src_dir = source_root / document_id
    if not src_dir.exists():
        return
    dest_dir = archive_root / document_id
    if dest_dir.exists():
        shutil.rmtree(dest_dir)
    shutil.move(str(src_dir), str(dest_dir))


def delete_document_from_source(src: sqlite3.Connection, document_id: str) -> None:
    src.execute("BEGIN")
    try:
        session_ids = [
            row[0]
            for row in src.execute("SELECT id FROM chat_sessions WHERE document_id = ?", (document_id,)).fetchall()
        ]
        if session_ids:
            placeholders = ", ".join("?" for _ in session_ids)
            src.execute(f"DELETE FROM chat_messages WHERE session_id IN ({placeholders})", tuple(session_ids))
        src.execute("DELETE FROM chat_sessions WHERE document_id = ?", (document_id,))
        src.execute("DELETE FROM documents WHERE id = ?", (document_id,))
        src.commit()
    except Exception:
        src.rollback()
        raise


def rebuild_archive_index(archive_db: Path, archive_root: Path) -> Path:
    conn = connect(archive_db)
    try:
        docs = conn.execute(
            """
            SELECT d.id, d.title, d.created_date, d.modified_date, d.recording_duration, f.name AS folder_name
            FROM documents d
            LEFT JOIN folders f ON f.id = d.folder_id
            ORDER BY d.created_date DESC, d.id DESC
            """
        ).fetchall()

        lines = [
            "# Summit Archive Index",
            "",
            f"Generated: {datetime.now().astimezone().strftime('%Y-%m-%d %H:%M:%S %Z')}",
            f"Archive DB: `{archive_db}`",
            f"Meetings archived: `{len(docs)}`",
            "",
        ]

        current_day = None
        for doc in docs:
            day = datetime.fromtimestamp(doc["created_date"]).astimezone().strftime("%Y-%m-%d")
            if day != current_day:
                current_day = day
                lines.extend([f"## {day}", ""])

            tag_rows = conn.execute(
                """
                SELECT tags.name
                FROM tags
                JOIN document_tags ON document_tags.tag_id = tags.id
                WHERE document_tags.document_id = ?
                ORDER BY tags.name COLLATE NOCASE
                """,
                (doc["id"],),
            ).fetchall()
            tag_text = ", ".join(row[0] for row in tag_rows) if tag_rows else "-"
            doc_dir = archive_root / doc["id"]
            md_path = doc_dir / "document.md"
            audio_files = [name for name in ("microphone.m4a", "system.m4a") if (doc_dir / name).exists()]
            audio_text = ", ".join(audio_files) if audio_files else "-"

            lines.extend(
                [
                    f"### {doc['title']}",
                    f"- document_id: `{doc['id']}`",
                    f"- folder: `{doc['folder_name'] or '-'}`",
                    f"- tags: `{tag_text}`",
                    f"- created: `{format_ts(doc['created_date'])}`",
                    f"- modified: `{format_ts(doc['modified_date'])}`",
                    f"- duration_seconds: `{int(doc['recording_duration'] or 0)}`",
                    f"- markdown: `{md_path}`" if md_path.exists() else "- markdown: `missing`",
                    f"- audio: `{audio_text}`",
                    "",
                ]
            )
    finally:
        conn.close()

    index_path = archive_root / "archive-index.md"
    index_path.write_text("\n".join(lines), encoding="utf-8")
    return index_path


def archive_has_document(archive_conn: sqlite3.Connection, document_id: str) -> bool:
    row = archive_conn.execute("SELECT 1 FROM documents WHERE id = ?", (document_id,)).fetchone()
    return row is not None


def select_documents_for_sync(
    src: sqlite3.Connection,
    archive_conn: sqlite3.Connection,
    explicit_ids: Sequence[str],
    watermark: float | None,
) -> list[str]:
    if explicit_ids:
        return list(dict.fromkeys(explicit_ids))

    rows = src.execute(
        """
        SELECT id, modified_date
        FROM documents
        WHERE deleted_at IS NULL
        ORDER BY modified_date DESC, created_date DESC, id DESC
        """
    ).fetchall()

    selected: list[str] = []
    for row in rows:
        doc_id = row["id"]
        is_new_to_archive = not archive_has_document(archive_conn, doc_id)
        is_changed = watermark is None or float(row["modified_date"]) > float(watermark)
        if is_new_to_archive or is_changed:
            selected.append(doc_id)
    return selected


def select_documents_for_prune(src: sqlite3.Connection, archive_conn: sqlite3.Connection, keep_active: int) -> list[str]:
    active_count = src.execute("SELECT COUNT(*) FROM documents WHERE deleted_at IS NULL").fetchone()[0]
    overflow = active_count - keep_active
    if overflow <= 0:
        return []

    rows = src.execute(
        """
        SELECT id
        FROM documents
        WHERE deleted_at IS NULL
        ORDER BY created_date ASC, id ASC
        """
    ).fetchall()

    selected: list[str] = []
    for row in rows:
        if len(selected) >= overflow:
            break
        if archive_has_document(archive_conn, row["id"]):
            selected.append(row["id"])

    if len(selected) < overflow:
        missing = overflow - len(selected)
        raise SystemExit(
            f"Cannot prune safely: {missing} oldest active meeting(s) are not confirmed in archive. "
            "Run sync first and retry."
        )
    return selected


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Sync Summit meetings into an archive DB, then optionally prune old active meetings.")
    parser.add_argument("--source-root", default=".", help="Path to active Summit storage directory")
    parser.add_argument("--archive-root", default=None, help="Path to archive directory (default: sibling summit-archive)")
    parser.add_argument(
        "--mode",
        choices=["sync", "prune", "sync-and-prune"],
        default="sync-and-prune",
        help="sync changed meetings, prune overflow, or do both",
    )
    parser.add_argument("--document-id", action="append", default=[], help="Sync one explicit document ID; can be passed multiple times")
    parser.add_argument("--keep-active", type=int, help="When pruning, keep only this many active meetings")
    parser.add_argument("--dry-run", action="store_true", help="Show what would be synced/pruned without changing anything")
    parser.add_argument("--skip-backup", action="store_true", help="Do not create a source DB backup before source-side deletion")
    return parser.parse_args()


def print_documents(src: sqlite3.Connection, heading: str, doc_ids: Sequence[str]) -> None:
    print(heading)
    if not doc_ids:
        print("- none")
        return
    for doc_id in doc_ids:
        row = src.execute("SELECT title, created_date, modified_date FROM documents WHERE id = ?", (doc_id,)).fetchone()
        if row:
            print(f"- {doc_id} | {row['title']} | created {format_ts(row['created_date'])} | modified {format_ts(row['modified_date'])}")
        else:
            print(f"- {doc_id} | missing in source DB")


def sync_documents(
    src: sqlite3.Connection,
    archive_conn: sqlite3.Connection,
    source_root: Path,
    archive_root: Path,
    doc_ids: Sequence[str],
) -> list[DocumentSummary]:
    synced: list[DocumentSummary] = []
    for doc_id in doc_ids:
        archive_conn.execute("BEGIN")
        try:
            summary = copy_document_to_archive(src, archive_conn, doc_id)
            archive_conn.commit()
        except Exception:
            archive_conn.rollback()
            raise
        # Keep archive files available for already-pruned meetings, but avoid
        # duplicating active-meeting folders on the same disk during normal sync.
        copy_meeting_folder_if_missing(source_root, archive_root, doc_id)
        synced.append(summary)
    return synced


def prune_documents(
    src: sqlite3.Connection,
    source_root: Path,
    archive_root: Path,
    doc_ids: Sequence[str],
) -> list[str]:
    pruned: list[str] = []
    for doc_id in doc_ids:
        move_meeting_folder_to_archive(source_root, archive_root, doc_id)
        delete_document_from_source(src, doc_id)
        pruned.append(doc_id)
    return pruned


def main() -> None:
    args = parse_args()
    source_root = Path(args.source_root).expanduser().resolve()
    archive_root = Path(args.archive_root).expanduser().resolve() if args.archive_root else source_root.with_name(source_root.name + "-archive")
    source_db = source_root / "database.sqlite"
    archive_db = archive_root / "database.sqlite"
    state_path = archive_root / "archive-state.json"

    if not source_db.exists():
        raise SystemExit(f"Source DB not found: {source_db}")

    src = connect(source_db)
    try:
        ensure_archive_db(src, archive_db)
        archive_conn = connect(archive_db)
        try:
            reconcile_archive_schema(src, archive_conn, args.dry_run)
            state = load_state(state_path)
            watermark = state.get("last_successful_sync_at")
            sync_ids = (
                select_documents_for_sync(src, archive_conn, args.document_id, watermark)
                if args.mode in {"sync", "sync-and-prune"}
                else []
            )
            prune_preview_ids = (
                select_documents_for_prune(src, archive_conn, args.keep_active)
                if args.mode == "prune" and args.keep_active is not None
                else []
            )

            if args.mode in {"prune", "sync-and-prune"} and args.keep_active is None:
                raise SystemExit("--keep-active is required for prune and sync-and-prune modes")

            print_documents(src, "Meetings selected for sync:", sync_ids)
            if args.mode == "prune":
                print_documents(src, "Meetings selected for prune:", prune_preview_ids)
            elif args.mode == "sync-and-prune":
                print("Meetings selected for prune:")
                print("- will be recalculated after sync against the refreshed archive")
            else:
                print("Meetings selected for prune:")
                print("- none")

            if args.dry_run:
                print("Dry run only; no changes made.")
                return

            backup_path = None
            if not args.skip_backup and args.mode in {"prune", "sync-and-prune"}:
                backup_path = create_source_backup(src, source_db)
                print(f"Backup created: {backup_path}")

            synced: list[DocumentSummary] = []
            pruned: list[str] = []

            if sync_ids:
                synced = sync_documents(src, archive_conn, source_root, archive_root, sync_ids)
                state["last_successful_sync_at"] = utc_now_ts()
                state["last_sync_mode"] = args.mode
                state["last_synced_count"] = len(synced)

            prune_ids = (
                select_documents_for_prune(src, archive_conn, args.keep_active)
                if args.mode in {"prune", "sync-and-prune"} and args.keep_active is not None
                else []
            )
            if prune_ids:
                pruned = prune_documents(src, source_root, archive_root, prune_ids)
                state["last_prune_keep_active"] = args.keep_active
                state["last_pruned_count"] = len(pruned)

            index_path = rebuild_archive_index(archive_db, archive_root)
            save_state(state_path, state)

            print(f"Archive index updated: {index_path}")
            print(f"Archive state updated: {state_path}")
            print(f"Synced meetings: {len(synced)}")
            for item in synced:
                print(f"- {item.doc_id} | {item.title}")
            print(f"Pruned meetings: {len(pruned)}")
            for doc_id in pruned:
                print(f"- {doc_id}")
            if backup_path:
                print(f"Source backup: {backup_path}")
        finally:
            archive_conn.close()
    finally:
        src.close()


if __name__ == "__main__":
    main()
