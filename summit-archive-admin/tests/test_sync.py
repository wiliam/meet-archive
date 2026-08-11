"""Тесты предпросмотра дельты синхронизации."""

import json
import sqlite3
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


def test_run_script_validation(roots):
    r = sync_mod.run_script("prune", None, dry_run=True)
    assert not r["ok"] and "keep-active" in r["output"]
    r = sync_mod.run_script("bad-mode", 5, dry_run=True)
    assert not r["ok"]
