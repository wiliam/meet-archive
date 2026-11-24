"""CLI interface, including safe defaults for destructive sync operations."""

import json
import sqlite3
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import meet_archive_cli as cli
from test_reader import reader_db  # noqa: F401


def test_cli_search_json(reader_db, monkeypatch, capsys):
    path, _conn = reader_db
    monkeypatch.setenv("SUMMIT_ARCHIVE_ROOT", str(path.parent))

    assert cli.main(["search", "биллинга", "--format", "json", "--archive-only"]) == 0

    payload = json.loads(capsys.readouterr().out)
    assert payload[0]["id"] == "D1"


def test_latest_refreshes_archive_before_reading(tmp_path, monkeypatch, capsys):
    source = tmp_path / "source"
    archive = tmp_path / "archive"
    schema = (Path(__file__).resolve().parent.parent / "schema.sql").read_text()
    for root, doc_id, created in ((source, "latest", 200), (archive, "archived", 100)):
        root.mkdir()
        with sqlite3.connect(root / "database.sqlite") as conn:
            conn.executescript(schema)
            conn.execute(
                "INSERT INTO documents (id, title, created_date, modified_date) VALUES (?, ?, ?, ?)",
                (doc_id, doc_id, created, created),
            )
            conn.execute(
                """INSERT INTO transcription_segments (id, document_id, text, start_time, end_time, source)
                   VALUES (?, ?, 'Review the launch plan', 0, 1, 'microphone')""",
                (f"segment-{doc_id}", doc_id),
            )
    monkeypatch.setenv("SUMMIT_ARCHIVE_ROOT", str(archive))
    monkeypatch.setenv("SUMMIT_SOURCE_ROOT", str(source))
    monkeypatch.setenv("SUMMIT_SOURCE_DB", str(source / "database.sqlite"))

    assert cli.main(["show", "--latest", "--include-transcript", "--format", "json"]) == 0
    result = capsys.readouterr()
    payload = json.loads(result.out)
    assert payload["id"] == "latest"
    assert payload["transcript"][0]["text"] == "Review the launch plan"
    assert result.err == ""
    with sqlite3.connect(archive / "database.sqlite") as conn:
        assert conn.execute("SELECT id FROM documents ORDER BY id").fetchall() == [("archived",), ("latest",)]
    with sqlite3.connect(source / "database.sqlite") as conn:
        assert conn.execute("SELECT id FROM documents").fetchall() == [("latest",)]

    index = archive / "archive-index.md"
    updated = index.stat().st_mtime_ns
    assert cli.main(["show", "--latest"]) == 0
    assert cli.main(["sync"]) == 0
    assert index.stat().st_mtime_ns == updated
    assert not (archive / ".sync.lock").exists()


def test_failed_refresh_only_reads_archive_when_requested(reader_db, monkeypatch, capsys):
    path, _conn = reader_db
    monkeypatch.setenv("SUMMIT_ARCHIVE_ROOT", str(path.parent))
    monkeypatch.setenv("SUMMIT_SOURCE_DB", str(path.parent / "private-source.sqlite"))

    assert cli.main(["show", "--latest"]) == 1
    result = capsys.readouterr()
    assert not result.out
    assert "--archive-only" in result.err
    assert "private-source.sqlite" not in result.err

    assert cli.main(["show", "--latest", "--archive-only", "--format", "json"]) == 0
    assert json.loads(capsys.readouterr().out)["id"] == "D1"


def test_prune_is_dry_run_without_apply(monkeypatch, capsys):
    calls = []

    def fake_run(mode, keep_active, dry_run):
        calls.append((mode, keep_active, dry_run))
        return 0

    monkeypatch.setattr(cli, "_run_sync", fake_run)

    assert cli.main(["prune", "--keep-active", "5"]) == 0
    assert cli.main(["prune", "--keep-active", "5", "--apply"]) == 0

    assert calls == [("prune", 5, True), ("prune", 5, False)]
    assert "только dry-run" in capsys.readouterr().err


def test_cli_wrapper_works_through_symlink(tmp_path):
    wrapper = Path(__file__).resolve().parents[2] / "meet-archive"
    link = tmp_path / "meet-archive"
    link.symlink_to(wrapper)

    result = subprocess.run(
        [str(link), "--help"], capture_output=True, text=True, timeout=10
    )

    assert result.returncode == 0
    assert "context" in result.stdout


def test_sync_and_push_passes_the_devices_own_settings(monkeypatch):
    calls = []
    monkeypatch.setattr(cli, "_run_sync", lambda *args: 0)

    def fake_run(command):
        calls.append(command)
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(cli.subprocess, "run", fake_run)
    assert cli.main([
        "sync-and-push", "--dry-run", "--hub", "https://private.example.org",
        "--token-file", "/tmp/mac-b-token", "--device-label", "Mac B",
    ]) == 0
    assert calls[0][1:] == [
        "push", "--dry-run", "--hub", "https://private.example.org",
        "--token-file", "/tmp/mac-b-token", "--device-label", "Mac B",
    ]
