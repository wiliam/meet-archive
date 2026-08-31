#!/usr/bin/env python3
"""Отправка заархивированных встреч на хаб.

Читает **архивную** базу, а не живую базу Summit. Это принципиально: модель
безопасности прунинга в archive_summit_meetings целиком опирается на «встреча
подтверждённо есть в архиве», и второй процесс, лезущий в живую базу, — ровно то,
от чего предостерегает CLAUDE.md. Побочный полезный эффект: на хаб физически не
может попасть встреча, не прошедшая архивацию.

Состояние синхронизации не хранится локально. Что уже есть на сервере, спрашивается
у сервера — поэтому modified_date всегда сравнивается только с копией, записанной
этой же машиной, и часы разных машин не сравниваются никогда.

Только stdlib: на Mac не должно появляться ни одной новой зависимости.
"""

from __future__ import annotations

import argparse
import gzip
import json
import os
import sqlite3
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(REPO_ROOT / "summit-archive-admin"))

import bundle as bundle_mod  # noqa: E402

USER_AGENT = "meet-archive-push/1"
RETRIES = 3

EXIT_OK = 0
EXIT_PUSH_ERRORS = 1
EXIT_CONFIG = 2
EXIT_SCHEMA_AHEAD = 3


class ConfigError(RuntimeError):
    pass


class SchemaAheadError(RuntimeError):
    pass


# --- доступ к архиву --------------------------------------------------------


def open_archive_ro(archive_root: Path) -> sqlite3.Connection:
    db_path = archive_root / "database.sqlite"
    if not db_path.exists():
        raise ConfigError(f"Архивная база не найдена: {db_path}")
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def assert_is_archive(archive_root: Path, force: bool) -> None:
    """Не дать случайно отправить содержимое живого хранилища Summit."""
    if force:
        return
    source_root = os.environ.get("SUMMIT_SOURCE_ROOT")
    if source_root and Path(source_root).resolve() == archive_root.resolve():
        raise ConfigError(
            "Указан живой каталог Summit, а не архив. Push читает только архив."
        )
    markers = ("archive-index.md", "archive-state.json")
    if not any((archive_root / m).exists() for m in markers):
        raise ConfigError(
            f"{archive_root} не похож на архив (нет {' или '.join(markers)}). "
            "Сначала запустите ./summit_archive.sh sync, либо передайте --force."
        )


# --- сборка бандла ----------------------------------------------------------


def _rows(conn, table: str, where: str, params) -> list[dict]:
    cursor = conn.execute(f'SELECT * FROM "{table}" WHERE {where}', params)
    return [bundle_mod.encode_row(table, row) for row in cursor]


def _one(conn, table: str, row_id) -> list[dict]:
    if not row_id:
        return []
    return _rows(conn, table, "id = ?", (row_id,))


def _collect_speakers(conn, doc_id: str) -> list[dict]:
    """Все спикеры встречи, включая родительские записи.

    parent_speaker_id — настоящий self-FK, поэтому родитель должен уехать вместе
    с ребёнком, иначе сервер не сможет вставить строку.
    """
    ids: set[str] = set()
    for query in (
        "SELECT speaker_id AS id FROM document_speakers WHERE document_id = ?",
        "SELECT DISTINCT speaker_id AS id FROM transcription_segments"
        " WHERE document_id = ? AND speaker_id IS NOT NULL",
    ):
        ids.update(r["id"] for r in conn.execute(query, (doc_id,)) if r["id"])

    collected: dict[str, dict] = {}
    pending = list(ids)
    while pending:
        speaker_id = pending.pop()
        if speaker_id in collected:
            continue
        rows = _rows(conn, "speakers", "id = ?", (speaker_id,))
        if not rows:
            continue
        collected[speaker_id] = rows[0]
        parent = rows[0].get("parent_speaker_id")
        if parent and parent not in collected:
            pending.append(parent)
    return list(collected.values())


def build_bundle(conn, doc_id: str, *, device_label: str) -> dict:
    document_rows = _rows(conn, "documents", "id = ?", (doc_id,))
    if not document_rows:
        raise ConfigError(f"Встреча {doc_id} не найдена в архиве")
    document = document_rows[0]

    tables: dict[str, list[dict]] = {}
    for table, value in (
        ("summarization_templates", document.get("template_id")),
        ("folders", document.get("folder_id")),
    ):
        rows = _one(conn, table, value)
        if rows:
            tables[table] = rows

    tag_rows = [
        bundle_mod.encode_row("tags", row)
        for row in conn.execute(
            "SELECT t.* FROM tags t JOIN document_tags dt ON dt.tag_id = t.id"
            " WHERE dt.document_id = ?",
            (doc_id,),
        )
    ]
    if tag_rows:
        tables["tags"] = tag_rows

    speakers = _collect_speakers(conn, doc_id)
    if speakers:
        tables["speakers"] = speakers

    for table in bundle_mod.CHILD_TABLES:
        rows = _rows(conn, table, "document_id = ?", (doc_id,))
        if rows:
            tables[table] = rows

    tables["documents"] = [document]

    meeting_dir = _archive_root_of(conn) / doc_id
    audio_files = sorted(
        name
        for name in ("microphone.m4a", "system.m4a")
        if (meeting_dir / name).exists()
    )

    md_path = _document_md_path(conn, doc_id)
    document_md = md_path.read_text(encoding="utf-8") if md_path else None

    return {
        "bundle_version": bundle_mod.BUNDLE_VERSION,
        "device_label": device_label,
        "generated_at": time.time(),
        "document_id": doc_id,
        "modified_date": float(document.get("modified_date") or 0.0),
        "content_hash": bundle_mod.content_hash(tables, document_md),
        "source_schema": {
            "migrations": [
                r[0] for r in conn.execute("SELECT identifier FROM grdb_migrations")
            ]
        },
        "audio_files": audio_files,
        "tables": tables,
    }


def _archive_root_of(conn) -> Path:
    return Path(conn.execute("PRAGMA database_list").fetchone()[2]).parent


def _document_md_path(conn, doc_id: str) -> Path | None:
    path = _archive_root_of(conn) / doc_id / "document.md"
    return path if path.exists() else None


# --- обмен с сервером -------------------------------------------------------


def _request(url: str, token: str, *, method="GET", body=None, headers=None) -> dict:
    request = urllib.request.Request(url, data=body, method=method)
    request.add_header("Authorization", f"Bearer {token}")
    request.add_header("User-Agent", USER_AGENT)
    for key, value in (headers or {}).items():
        request.add_header(key, value)

    last_error = None
    for attempt in range(RETRIES):
        try:
            with urllib.request.urlopen(request, timeout=120) as response:
                payload = response.read()
            return json.loads(payload) if payload else {}
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")
            if exc.code == 409:
                raise SchemaAheadError(detail) from exc
            # 4xx — это баг, а не сбой связи: повторять бессмысленно.
            if 400 <= exc.code < 500:
                raise RuntimeError(f"HTTP {exc.code}: {detail}") from exc
            last_error = RuntimeError(f"HTTP {exc.code}: {detail}")
        except urllib.error.URLError as exc:
            last_error = RuntimeError(f"Сеть недоступна: {exc.reason}")
        if attempt < RETRIES - 1:
            time.sleep(2 ** attempt)
    raise last_error


def fetch_state(hub: str, token: str) -> dict:
    return _request(f"{hub.rstrip('/')}/api/ingest/state", token)


def push_bundle(hub: str, token: str, payload: dict) -> dict:
    body = gzip.compress(
        json.dumps(payload, ensure_ascii=False).encode("utf-8")
    )
    return _request(
        f"{hub.rstrip('/')}/api/ingest/meeting",
        token,
        method="POST",
        body=body,
        headers={"Content-Type": "application/json", "Content-Encoding": "gzip"},
    )


def push_document_md(hub: str, token: str, doc_id: str, text: str) -> None:
    _request(
        f"{hub.rstrip('/')}/api/ingest/meeting/{doc_id}/document.md",
        token,
        method="PUT",
        body=text.encode("utf-8"),
        headers={"Content-Type": "text/markdown; charset=utf-8"},
    )


# --- отбор ------------------------------------------------------------------


def select_documents(conn, state: dict, *, limit=None, only=None) -> list[str]:
    known = state.get("documents") or {}
    selected = []
    for row in conn.execute(
        "SELECT id, modified_date FROM documents"
        " WHERE deleted_at IS NULL ORDER BY created_date"
    ):
        doc_id = row["id"]
        if only and doc_id not in only:
            continue
        remote = known.get(doc_id)
        if remote is None or float(row["modified_date"] or 0) > float(
            remote.get("modified_date") or 0
        ):
            selected.append(doc_id)
    return selected[:limit] if limit else selected


# --- CLI --------------------------------------------------------------------


def _read_token(args) -> str:
    if args.token_file:
        path = Path(args.token_file).expanduser()
        try:
            token = path.read_text(encoding="utf-8").strip()
        except OSError as exc:
            raise ConfigError(f"Не читается файл токена {path}: {exc.strerror}") from exc
        if not token:
            raise ConfigError(f"Файл токена пуст: {path}")
        return token
    token = os.environ.get("MEET_ARCHIVE_HUB_TOKEN", "").strip()
    if not token:
        raise ConfigError(
            "Нужен токен: MEET_ARCHIVE_HUB_TOKEN или --token-file. "
            "Флагом в командной строке — нельзя, argv виден в ps."
        )
    return token


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description="Отправка встреч из архива на хаб")
    parser.add_argument("--archive-root", default=os.environ.get(
        "SUMMIT_ARCHIVE_ROOT", str(REPO_ROOT / "summit-archive")))
    parser.add_argument("--hub", default=os.environ.get("MEET_ARCHIVE_HUB_URL", ""))
    parser.add_argument("--token-file")
    parser.add_argument("--device-label", default=os.uname().nodename)
    parser.add_argument("--document-id", action="append", default=[])
    parser.add_argument("--limit", type=int)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--force", action="store_true",
                        help="пропустить проверку, что каталог — это архив")
    return parser.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)
    try:
        if not args.hub:
            raise ConfigError("Не задан адрес хаба: MEET_ARCHIVE_HUB_URL или --hub")
        archive_root = Path(args.archive_root).expanduser().resolve()
        assert_is_archive(archive_root, args.force)
        token = _read_token(args)
    except ConfigError as exc:
        print(f"ОШИБКА: {exc}", file=sys.stderr)
        return EXIT_CONFIG

    conn = open_archive_ro(archive_root)
    try:
        try:
            state = fetch_state(args.hub, token)
        except SchemaAheadError as exc:
            print(f"Схема сервера устарела: {exc}", file=sys.stderr)
            return EXIT_SCHEMA_AHEAD
        except RuntimeError as exc:
            print(f"ОШИБКА: хаб недоступен — {exc}", file=sys.stderr)
            return EXIT_PUSH_ERRORS

        only = set(args.document_id) or None
        doc_ids = select_documents(conn, state, limit=args.limit, only=only)

        if not doc_ids:
            print("Нечего отправлять — хаб в актуальном состоянии.")
            return EXIT_OK

        print(f"К отправке: {len(doc_ids)}")
        if args.dry_run:
            for doc_id in doc_ids:
                title = conn.execute(
                    "SELECT title FROM documents WHERE id = ?", (doc_id,)
                ).fetchone()["title"]
                print(f"- {doc_id} | {title} | будет отправлена")
            return EXIT_OK

        failures = 0
        for doc_id in doc_ids:
            payload = build_bundle(conn, doc_id, device_label=args.device_label)
            title = payload["tables"]["documents"][0].get("title") or ""
            try:
                result = push_bundle(args.hub, token, payload)
                md_path = _document_md_path(conn, doc_id)
                if md_path and result.get("status") != "unchanged":
                    push_document_md(
                        args.hub, token, doc_id,
                        md_path.read_text(encoding="utf-8"))
                print(f"- {doc_id} | {title} | {result.get('status', 'ok')}")
                for warning in result.get("warnings") or []:
                    print(f"    ! {warning}")
            except SchemaAheadError as exc:
                print(f"- {doc_id} | {title} | схема сервера устарела", file=sys.stderr)
                print(str(exc), file=sys.stderr)
                return EXIT_SCHEMA_AHEAD
            except RuntimeError as exc:
                failures += 1
                print(f"- {doc_id} | {title} | ОШИБКА: {exc}", file=sys.stderr)

        print(f"Готово: {len(doc_ids) - failures} из {len(doc_ids)}")
        return EXIT_PUSH_ERRORS if failures else EXIT_OK
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
