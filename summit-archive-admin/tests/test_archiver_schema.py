"""Регрессии адаптации локального архива к миграциям Summit."""

import importlib.util
import sqlite3
import subprocess
import sys
from pathlib import Path


def load_archiver():
    script = Path(__file__).resolve().parents[2] / "archive_summit_meetings.py"
    spec = importlib.util.spec_from_file_location("archiver_schema_test", script)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    try:
        spec.loader.exec_module(module)
    finally:
        sys.modules.pop(spec.name, None)
    return module


def connection() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    return conn


def test_sync_reports_a_busy_archive_instead_of_hanging(tmp_path):
    archiver = load_archiver()
    source = tmp_path / "source"
    source.mkdir()
    (source / "database.sqlite").touch()
    archive = tmp_path / "archive"
    cmd = [
        sys.executable, archiver.__file__, "--source-root", str(source),
        "--archive-root", str(archive), "--mode", "sync",
    ]

    with archiver.archive_writer_lock(archive):
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=5)
        assert result.returncode != 0
        assert "Архив занят другим процессом" in result.stderr
        assert not (archive / "database.sqlite").exists()

    with archiver.archive_writer_lock(archive):
        assert not (archive / ".sync.lock").exists()


def test_reconcile_adds_new_columns_tables_indexes_and_migrations():
    archiver = load_archiver()
    source = connection()
    archive = connection()

    source.executescript(
        """
        CREATE TABLE grdb_migrations (identifier TEXT NOT NULL PRIMARY KEY);
        INSERT INTO grdb_migrations VALUES ('v1');
        INSERT INTO grdb_migrations VALUES ('v2_new_layout');
        CREATE TABLE transcription_segments (
            id TEXT PRIMARY KEY,
            starts_new_block INTEGER NOT NULL DEFAULT 0
        );
        CREATE TABLE split_documents (
            id TEXT PRIMARY KEY,
            split_at DOUBLE NOT NULL,
            part_count INTEGER NOT NULL
        );
        CREATE INDEX idx_split_documents_date ON split_documents(split_at);
        """
    )
    archive.executescript(
        """
        CREATE TABLE grdb_migrations (identifier TEXT NOT NULL PRIMARY KEY);
        INSERT INTO grdb_migrations VALUES ('v1');
        CREATE TABLE transcription_segments (id TEXT PRIMARY KEY);
        """
    )

    archiver.reconcile_archive_schema(source, archive, dry_run=False)

    columns = {
        row["name"] for row in archive.execute("PRAGMA table_info(transcription_segments)")
    }
    assert "starts_new_block" in columns
    assert archive.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='split_documents'"
    ).fetchone()
    assert archive.execute(
        "SELECT 1 FROM sqlite_master WHERE type='index' AND name='idx_split_documents_date'"
    ).fetchone()
    assert [
        row[0] for row in archive.execute("SELECT identifier FROM grdb_migrations ORDER BY rowid")
    ] == ["v1", "v2_new_layout"]


def test_sync_preserves_other_meetings_links_for_shared_lookups():
    archiver = load_archiver()
    source = connection()
    archive = connection()
    schema_path = Path(__file__).resolve().parent.parent / "schema.sql"
    for conn in (source, archive):
        conn.executescript(schema_path.read_text())
        conn.execute("INSERT INTO tags (id, name, created_date) VALUES ('tag', 'Project', 0)")
        conn.execute("INSERT INTO speakers (id, name, created_date) VALUES ('speaker', 'Alex', 0)")

    for conn, meeting_id in ((source, "new"), (archive, "old")):
        conn.execute(
            "INSERT INTO documents (id, title, created_date, modified_date) VALUES (?, ?, 0, 0)",
            (meeting_id, meeting_id),
        )
        conn.execute("INSERT INTO document_tags VALUES (?, 'tag', 0)", (meeting_id,))
        conn.execute("INSERT INTO document_speakers VALUES (?, 'speaker', 0)", (meeting_id,))
        conn.execute(
            "INSERT INTO calendar_document_links VALUES ('calendar', ?, ?, 0)",
            (f"event-{meeting_id}", meeting_id),
        )

    source.execute("UPDATE speakers SET company = 'Example' WHERE id = 'speaker'")
    source.execute(
        """INSERT INTO person_email_links (id, speaker_id, email, status, created_date, updated_date)
           VALUES ('link', 'speaker', 'alex@example.test', 'confirmed', 0, 0)"""
    )
    for _ in range(2):
        archiver.copy_document_to_archive(source, archive, "new")

    for table in ("document_tags", "document_speakers", "calendar_document_links"):
        assert [row[0] for row in archive.execute(
            f"SELECT document_id FROM {table} ORDER BY document_id"
        )] == ["new", "old"]
    assert archive.execute("SELECT company FROM speakers").fetchone()[0] == "Example"
    assert archive.execute("SELECT speaker_id FROM person_email_links").fetchone()[0] == "speaker"
