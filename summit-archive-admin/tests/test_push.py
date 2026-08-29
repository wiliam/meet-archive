"""Клиент отправки: отбор встреч, форма бандла, защита от живого хранилища."""

import importlib.util
import json
import sqlite3
import sys
from pathlib import Path

import pytest

import bootstrap
import bundle as bundle_mod

REPO_ROOT = Path(__file__).resolve().parents[2]


def _load_push():
    spec = importlib.util.spec_from_file_location(
        "push_to_hub", REPO_ROOT / "push_to_hub.py"
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules["push_to_hub"] = module
    try:
        spec.loader.exec_module(module)
    finally:
        sys.modules.pop("push_to_hub", None)
    return module


push = _load_push()


@pytest.fixture
def archive_root(tmp_path):
    """Каталог, неотличимый от настоящего архива, но без реальных встреч."""
    root = tmp_path / "summit-archive"
    root.mkdir()
    bootstrap.ensure_schema(root / "database.sqlite")
    (root / "archive-state.json").write_text("{}", encoding="utf-8")

    conn = bootstrap.connect_rw(root / "database.sqlite")
    try:
        conn.execute(
            "INSERT INTO folders (id,name,created_date,modified_date) VALUES (?,?,?,?)",
            ("FOL", "Работа", 1.0, 1.0))
        conn.execute(
            "INSERT INTO tags (id,name,created_date) VALUES (?,?,?)", ("TAG", "work", 1.0))
        conn.execute(
            "INSERT INTO speakers (id,name,created_date,embedding) VALUES (?,?,?,?)",
            ("SPK", "Иван", 1.0, b"\x01\x02"))
        conn.execute(
            "INSERT INTO documents (id,title,created_date,modified_date,folder_id)"
            " VALUES (?,?,?,?,?)", ("DOC-A", "Планёрка", 1.0, 100.0, "FOL"))
        conn.execute(
            "INSERT INTO documents (id,title,created_date,modified_date)"
            " VALUES (?,?,?,?)", ("DOC-B", "Ретро", 2.0, 200.0))
        conn.execute(
            "INSERT INTO document_tags (document_id,tag_id,created_date)"
            " VALUES (?,?,?)", ("DOC-A", "TAG", 1.0))
        conn.execute(
            "INSERT INTO document_speakers (document_id,speaker_id,created_date)"
            " VALUES (?,?,?)", ("DOC-A", "SPK", 1.0))
        conn.execute(
            "INSERT INTO transcription_segments"
            " (id,document_id,text,start_time,end_time,source,word_confidences_json)"
            " VALUES (?,?,?,?,?,?,?)",
            ("SEG", "DOC-A", "обсудили бюджет", 0.0, 5.0, "microphone", "[0.9,0.8]"))
    finally:
        conn.close()
    return root


@pytest.fixture
def archive_conn(archive_root):
    conn = push.open_archive_ro(archive_root)
    try:
        yield conn
    finally:
        conn.close()


# --- отбор ------------------------------------------------------------------


def test_select_documents_uses_server_state(archive_conn):
    """Единственный источник истины о том, что уже отправлено, — ответ сервера."""
    assert push.select_documents(archive_conn, {"documents": {}}) == ["DOC-A", "DOC-B"]

    state = {"documents": {"DOC-A": {"modified_date": 100.0}}}
    assert push.select_documents(archive_conn, state) == ["DOC-B"]

    stale = {"documents": {"DOC-A": {"modified_date": 50.0},
                           "DOC-B": {"modified_date": 200.0}}}
    assert push.select_documents(archive_conn, stale) == ["DOC-A"]


def test_select_documents_respects_limit_and_filter(archive_conn):
    assert push.select_documents(archive_conn, {"documents": {}}, limit=1) == ["DOC-A"]
    assert push.select_documents(
        archive_conn, {"documents": {}}, only={"DOC-B"}) == ["DOC-B"]


def test_deleted_meetings_are_never_pushed(archive_root, archive_conn):
    conn = bootstrap.connect_rw(archive_root / "database.sqlite")
    try:
        conn.execute("UPDATE documents SET deleted_at = 1 WHERE id = 'DOC-B'")
    finally:
        conn.close()
    assert push.select_documents(archive_conn, {"documents": {}}) == ["DOC-A"]


# --- форма бандла -----------------------------------------------------------


def test_build_bundle_shape(archive_conn):
    payload = push.build_bundle(archive_conn, "DOC-A", device_label="mac-test")

    assert payload["bundle_version"] == bundle_mod.BUNDLE_VERSION
    assert payload["document_id"] == "DOC-A"
    assert payload["content_hash"].startswith("sha256:")
    # device_id в теле отсутствует: сервер выводит его из токена
    assert "device_id" not in payload

    tables = payload["tables"]
    assert set(tables) <= bundle_mod.BUNDLE_TABLES
    assert not any(bundle_mod.is_denied_table(t) for t in tables)
    assert tables["documents"][0]["id"] == "DOC-A"
    assert tables["folders"][0]["name"] == "Работа"
    assert tables["tags"][0]["name"] == "work"


def test_heavy_columns_are_stripped_client_side(archive_conn):
    """Отсекается на устройстве, а не на сервере: это экономия трафика.

    word_confidences_json — 39.9 МБ из 66 МБ живой базы; speakers.embedding —
    голосовой отпечаток, которому на публичном сервере делать нечего.
    """
    payload = push.build_bundle(archive_conn, "DOC-A", device_label="mac-test")
    assert "word_confidences_json" not in payload["tables"]["transcription_segments"][0]
    assert "embedding" not in payload["tables"]["speakers"][0]


def test_bundle_is_json_serialisable(archive_conn):
    payload = push.build_bundle(archive_conn, "DOC-A", device_label="mac-test")
    json.dumps(payload, ensure_ascii=False)


def test_speaker_parents_travel_with_children(archive_root, archive_conn):
    conn = bootstrap.connect_rw(archive_root / "database.sqlite")
    try:
        conn.execute(
            "INSERT INTO speakers (id,name,created_date) VALUES ('PARENT','Иван',1.0)")
        conn.execute("UPDATE speakers SET parent_speaker_id='PARENT' WHERE id='SPK'")
    finally:
        conn.close()
    payload = push.build_bundle(archive_conn, "DOC-A", device_label="mac-test")
    assert {s["id"] for s in payload["tables"]["speakers"]} == {"SPK", "PARENT"}


# --- защита -----------------------------------------------------------------


def test_push_refuses_a_directory_that_is_not_an_archive(tmp_path):
    """Живое хранилище Summit читать нельзя: там работает приложение."""
    live = tmp_path / "summit"
    live.mkdir()
    (live / "database.sqlite").write_bytes(b"")
    with pytest.raises(push.ConfigError, match="не похож на архив"):
        push.assert_is_archive(live, force=False)


def test_push_refuses_when_root_equals_live_summit(archive_root, monkeypatch):
    monkeypatch.setenv("SUMMIT_SOURCE_ROOT", str(archive_root))
    with pytest.raises(push.ConfigError, match="живой каталог"):
        push.assert_is_archive(archive_root, force=False)


def test_force_bypasses_the_guard(tmp_path):
    push.assert_is_archive(tmp_path, force=True)


def test_missing_hub_url_exits_with_config_code(monkeypatch, archive_root):
    monkeypatch.delenv("MEET_ARCHIVE_HUB_URL", raising=False)
    monkeypatch.setenv("MEET_ARCHIVE_HUB_TOKEN", "x")
    code = push.main(["--archive-root", str(archive_root)])
    assert code == push.EXIT_CONFIG


def test_missing_token_exits_with_config_code(monkeypatch, archive_root):
    monkeypatch.delenv("MEET_ARCHIVE_HUB_TOKEN", raising=False)
    code = push.main(["--archive-root", str(archive_root), "--hub", "http://example"])
    assert code == push.EXIT_CONFIG
