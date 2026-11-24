"""Тесты предпросмотра дельты синхронизации."""

import json
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import sync as sync_mod

DOCS = "CREATE TABLE documents (id TEXT PRIMARY KEY, title TEXT, created_date DOUBLE, modified_date DOUBLE, deleted_at DOUBLE)"


def make_db(path, rows):
    c = sqlite3.connect(path)
    c.execute(DOCS)
    c.executemany("INSERT INTO documents VALUES (?,?,?,?,?)", rows)
    c.commit()
    c.close()


@pytest.fixture()
def roots(tmp_path, monkeypatch):
    live = tmp_path / "summit"
    archive = tmp_path / "summit-archive"
    live.mkdir()
    archive.mkdir()
    monkeypatch.delenv("SUMMIT_SOURCE_DB", raising=False)
    monkeypatch.setenv("SUMMIT_SOURCE_ROOT", str(live))
    monkeypatch.setenv("SUMMIT_ARCHIVE_ROOT", str(archive))
    # живая: A (старая, в архиве), B (изменена после watermark), C (новая)
    make_db(
        live / "database.sqlite",
        [
            ("A", "Старая", 100.0, 150.0, None),
            ("B", "Изменённая", 200.0, 900.0, None),
            ("C", "Новая", 300.0, 300.0, None),
            ("D", "Удалённая", 400.0, 400.0, 999.0),
        ],
    )
    make_db(
        archive / "database.sqlite",
        [("A", "Старая", 100.0, 150.0, None), ("B", "Изменённая", 200.0, 500.0, None)],
    )
    (archive / "archive-state.json").write_text(
        json.dumps({"last_successful_sync_at": 600.0, "last_prune_keep_active": 2})
    )
    return live, archive


def test_delta_sync_selection(roots):
    d = sync_mod.compute_delta(keep_active=10)
    assert d["error"] is None
    assert d["active_count"] == 3 and d["archive_count"] == 2
    statuses = {x["id"]: x["status"] for x in d["to_sync"]}
    # C новая, B изменена после watermark 600; A не тронута; D удалена — не считается
    assert statuses == {"C": "новая", "B": "изменена"}
    assert d["overflow"] == 0 and d["to_prune"] == []


def test_delta_prune_preview(roots):
    d = sync_mod.compute_delta(keep_active=1)
    # overflow 2: самые старые A (в архиве) и B; C будет в архиве после синка
    assert d["overflow"] == 2
    pruned = [x["id"] for x in d["to_prune"]]
    assert pruned == ["A", "B"]
    assert d["unsafe"] == []


def test_delta_missing_live_db(roots, monkeypatch, tmp_path):
    monkeypatch.setenv("SUMMIT_SOURCE_ROOT", str(tmp_path / "nope"))
    d = sync_mod.compute_delta(keep_active=10)
    assert d["error"]


def test_delta_reads_explicit_source_database(roots, monkeypatch, tmp_path):
    database_root = tmp_path / "database"
    database_root.mkdir()
    source_db = database_root / "database.sqlite"
    make_db(
        source_db,
        [("SPLIT", "Раздельная база", 500.0, 500.0, None)],
    )
    monkeypatch.setenv("SUMMIT_SOURCE_DB", str(source_db))

    d = sync_mod.compute_delta(keep_active=10)

    assert d["error"] is None
    assert d["active_count"] == 1
    assert [item["id"] for item in d["to_sync"]] == ["SPLIT"]


def test_delta_discovers_sibling_summit_db_directory(monkeypatch, tmp_path):
    repo_root = tmp_path / "repo"
    source_root = repo_root / "summit"
    source_db_root = repo_root / "summit-db"
    archive_root = repo_root / "summit-archive"
    source_root.mkdir(parents=True)
    source_db_root.mkdir()
    archive_root.mkdir()
    make_db(
        source_db_root / "database.sqlite",
        [("SPLIT", "Раздельная база", 500.0, 500.0, None)],
    )
    make_db(archive_root / "database.sqlite", [])
    monkeypatch.delenv("SUMMIT_SOURCE_ROOT", raising=False)
    monkeypatch.delenv("SUMMIT_SOURCE_DB", raising=False)
    monkeypatch.setenv("SUMMIT_ARCHIVE_ROOT", str(archive_root))

    d = sync_mod.compute_delta(keep_active=10)

    assert d["error"] is None
    assert [item["id"] for item in d["to_sync"]] == ["SPLIT"]


def test_delta_prefers_legacy_source_db_over_stale_summit_db(monkeypatch, tmp_path):
    repo_root = tmp_path / "repo"
    source_root = repo_root / "summit"
    source_db_root = repo_root / "summit-db"
    archive_root = repo_root / "summit-archive"
    source_root.mkdir(parents=True)
    source_db_root.mkdir()
    archive_root.mkdir()
    make_db(
        source_root / "database.sqlite",
        [("LEGACY", "Совместимая база", 500.0, 500.0, None)],
    )
    make_db(
        source_db_root / "database.sqlite",
        [("STALE", "Устаревшая ссылка", 400.0, 400.0, None)],
    )
    make_db(archive_root / "database.sqlite", [])
    monkeypatch.delenv("SUMMIT_SOURCE_ROOT", raising=False)
    monkeypatch.delenv("SUMMIT_SOURCE_DB", raising=False)
    monkeypatch.setenv("SUMMIT_ARCHIVE_ROOT", str(archive_root))

    d = sync_mod.compute_delta(keep_active=10)

    assert d["error"] is None
    assert [item["id"] for item in d["to_sync"]] == ["LEGACY"]


def test_first_sync_preview_leaves_missing_archive_untouched(roots, tmp_path, monkeypatch):
    archive = tmp_path / "new-archive"
    monkeypatch.setenv("SUMMIT_ARCHIVE_ROOT", str(archive))

    delta = sync_mod.compute_delta(keep_active=10)
    preview = sync_mod.run_script("sync", None, dry_run=True)

    assert delta["error"] is None
    assert delta["archive_count"] == 0
    assert {row["id"] for row in delta["to_sync"]} == {"A", "B", "C"}
    assert preview["ok"], preview["output"]
    assert "Dry run only" in preview["output"]
    assert not archive.exists()


def test_delta_timeout_stops_sync_before_running_archiver(monkeypatch):
    calls = []

    def timeout(cmd, *, capture_output, text, timeout):
        calls.append(cmd)
        raise subprocess.TimeoutExpired(cmd, timeout)

    monkeypatch.setenv("MEET_ARCHIVE_SOURCE_TIMEOUT", "0.5")
    monkeypatch.setattr(sync_mod.subprocess, "run", timeout)

    result = sync_mod.run_script("sync", None, dry_run=True)

    assert not result["ok"]
    assert "за 0.5 сек." in result["output"]
    assert "закройте Summit" in result["output"]
    assert len(calls) == 1 and calls[0][2] == "--delta-worker"


def test_delta_reads_database_separately_from_meeting_files(roots, monkeypatch, tmp_path):
    """Summit 1.13 moved SQLite under Application Support, but kept media elsewhere."""
    live, _archive = roots
    modern_db = tmp_path / "Application Support" / "Summit" / "Database" / "database.sqlite"
    modern_db.parent.mkdir(parents=True)
    (live / "database.sqlite").replace(modern_db)
    monkeypatch.setenv("SUMMIT_SOURCE_DB", str(modern_db))

    d = sync_mod.compute_delta(keep_active=10)

    assert d["error"] is None
    assert d["active_count"] == 3


def test_run_script_validation(roots):
    r = sync_mod.run_script("prune", None, dry_run=True)
    assert not r["ok"] and "keep-active" in r["output"]
    r = sync_mod.run_script("bad-mode", 5, dry_run=True)
    assert not r["ok"]
