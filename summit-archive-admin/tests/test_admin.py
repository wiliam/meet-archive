"""Тесты слоя данных и экспорта на фикстурной мини-базе."""

import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import db
from export import build_markdown, fmt_ts

SCHEMA = """
CREATE TABLE documents (
  id TEXT PRIMARY KEY, title TEXT, created_date DOUBLE, modified_date DOUBLE,
  recording_duration DOUBLE, word_count INTEGER, has_transcription BOOLEAN,
  notes TEXT, folder_id TEXT, deleted_at DOUBLE
);
CREATE TABLE folders (id TEXT PRIMARY KEY, name TEXT, color TEXT, icon TEXT, sort_order INTEGER);
CREATE TABLE tags (id TEXT PRIMARY KEY, name TEXT, color TEXT);
CREATE TABLE document_tags (document_id TEXT, tag_id TEXT, PRIMARY KEY (document_id, tag_id));
CREATE TABLE speakers (id TEXT PRIMARY KEY, name TEXT, company TEXT, position TEXT, parent_speaker_id TEXT);
CREATE TABLE document_speakers (document_id TEXT, speaker_id TEXT, PRIMARY KEY (document_id, speaker_id));
CREATE TABLE topics (id TEXT PRIMARY KEY, document_id TEXT, content TEXT, sort_order INTEGER);
CREATE TABLE action_items (
  id TEXT PRIMARY KEY, document_id TEXT, content TEXT, sort_order INTEGER,
  completed BOOLEAN, assignee TEXT, due_date DOUBLE
);
CREATE TABLE transcription_segments (
  id TEXT PRIMARY KEY, document_id TEXT, text TEXT, start_time DOUBLE,
  end_time DOUBLE, source TEXT, is_irrelevant BOOLEAN, speaker_id TEXT
);
CREATE VIRTUAL TABLE documents_fts USING fts5(title, notes, tokenize='unicode61');
CREATE VIRTUAL TABLE transcription_fts USING fts5(text, document_id UNINDEXED, tokenize='unicode61');
CREATE VIRTUAL TABLE topics_fts USING fts5(content, document_id UNINDEXED, tokenize='unicode61');
"""


@pytest.fixture()
def conn(tmp_path):
    path = tmp_path / "archive.sqlite"
    c = sqlite3.connect(path)
    c.executescript(SCHEMA)
    c.execute("INSERT INTO folders VALUES ('F1', 'work', '#ff0000', NULL, 1)")
    c.execute("INSERT INTO tags VALUES ('T1', 'важное', '#00ff00')")
    c.execute(
        "INSERT INTO documents VALUES ('D1', 'Планирование Q2', 1750000000, 1750000000, 1549.0, 120, 1, NULL, 'F1', NULL)"
    )
    c.execute(
        "INSERT INTO documents VALUES ('D2', 'Ретро команды', 1750100000, 1750100000, 900.0, 80, 1, NULL, NULL, NULL)"
    )
    c.execute(
        "INSERT INTO documents VALUES ('D3', 'Удалённая', 1750200000, 1750200000, 0, 0, 0, NULL, NULL, 1750300000)"
    )
    c.execute("INSERT INTO document_tags VALUES ('D1', 'T1')")
    c.execute("INSERT INTO speakers VALUES ('S1', 'Марина', NULL, NULL, NULL)")
    c.execute("INSERT INTO speakers VALUES ('S2', 'Speaker-abc', NULL, NULL, 'S1')")
    c.execute("INSERT INTO document_speakers VALUES ('D1', 'S2')")
    c.execute("INSERT INTO topics VALUES ('TP1', 'D1', 'Метрики квартала', 0)")
    c.execute(
        "INSERT INTO action_items VALUES ('A1', 'D1', 'Подготовить отчёт', 0, 0, 'Илья', NULL)"
    )
    c.execute(
        "INSERT INTO transcription_segments VALUES ('TS1', 'D1', 'Начнём с метрик', 0.0, 4.0, 'microphone', 0, 'S2')"
    )
    c.execute(
        "INSERT INTO transcription_segments VALUES ('TS2', 'D1', 'и планов на квартал', 4.0, 8.0, 'microphone', 0, 'S2')"
    )
    c.execute(
        "INSERT INTO transcription_segments VALUES ('TS3', 'D1', 'Согласен', 8.0, 9.0, 'systemAudio', 0, NULL)"
    )
    # FTS вручную (в проде их наполняют триггеры)
    c.execute("INSERT INTO documents_fts(rowid, title, notes) SELECT rowid, title, '' FROM documents")
    c.execute("INSERT INTO transcription_fts(rowid, text, document_id) SELECT rowid, text, document_id FROM transcription_segments")
    c.execute("INSERT INTO topics_fts(rowid, content, document_id) SELECT rowid, content, document_id FROM topics")
    c.commit()
    c.row_factory = sqlite3.Row
    yield c
    c.close()


def test_list_meetings_excludes_deleted(conn):
    rows = db.list_meetings(conn)
    ids = [r["id"] for r in rows]
    assert ids == ["D2", "D1"]  # новые сверху, D3 (deleted) исключена


def test_list_meetings_folder_and_tag_filters(conn):
    assert [r["id"] for r in db.list_meetings(conn, folder_id="F1")] == ["D1"]
    assert [r["id"] for r in db.list_meetings(conn, tag_id="T1")] == ["D1"]
    assert db.list_meetings(conn, folder_id="nope") == []


def test_list_meetings_aggregates(conn):
    row = db.list_meetings(conn, folder_id="F1")[0]
    assert row["tag_names"] == "важное"
    assert row["speaker_count"] == 1
    assert row["folder_name"] == "work"


def test_search_by_transcript_and_topic(conn):
    hits = db.search_documents(conn, "метрик")
    assert "D1" in hits
    assert "транскрипт" not in (hits["D1"]["snippet"] or "")
    assert "⟦метрик" in hits["D1"]["snippet"] or "⟦Метрики" in hits["D1"]["snippet"]

    assert db.search_documents(conn, "несуществующееслово") == {}
    # спецсимволы не роняют MATCH
    assert db.search_documents(conn, 'кав"ычки AND (скобки)') == {}


def test_speaker_resolves_to_parent(conn):
    speakers = db.meeting_speakers(conn, "D1")
    assert [s["name"] for s in speakers] == ["Марина"]


def test_transcript_grouping(conn):
    blocks = db.group_transcript(db.meeting_transcript(conn, "D1"))
    assert len(blocks) == 2  # две реплики Марины склеились, systemAudio отдельно
    assert blocks[0]["speaker"] == "Марина"
    assert blocks[0]["texts"] == ["Начнём с метрик", "и планов на квартал"]
    assert blocks[1]["speaker"] == "Системный звук"


def test_export_markdown(conn):
    meeting = db.get_meeting(conn, "D1")
    md = build_markdown(
        meeting,
        db.meeting_speakers(conn, "D1"),
        db.meeting_tags(conn, "D1"),
        db.meeting_topics(conn, "D1"),
        db.meeting_action_items(conn, "D1"),
        db.group_transcript(db.meeting_transcript(conn, "D1")),
    )
    assert md.startswith("# Планирование Q2")
    assert "- **Участники:** Марина" in md
    assert "## Темы" in md and "- Метрики квартала" in md
    assert "- [ ] Подготовить отчёт — Илья" in md
    assert "**[00:00] Марина:**" in md
    assert "Начнём с метрик и планов на квартал" in md


def test_fmt_ts():
    assert fmt_ts(65) == "01:05"
    assert fmt_ts(3725) == "1:02:05"
    assert fmt_ts(None) == "00:00"


def test_get_meeting_missing_and_deleted(conn):
    assert db.get_meeting(conn, "nope") is None
    assert db.get_meeting(conn, "D3") is None


def test_connection_survives_cross_thread_close(tmp_path):
    """FastAPI выполняет setup/teardown sync-зависимостей в разных потоках пула —
    соединение обязано переживать закрытие из чужого потока (иначе рвутся
    стриминговые ответы, см. баг с аудиоплеерами)."""
    import threading

    path = tmp_path / "db.sqlite"
    sqlite3.connect(path).close()
    conn = db.connect(path)
    errors = []

    def close_in_other_thread():
        try:
            conn.execute("SELECT 1")
            conn.close()
        except Exception as e:  # noqa: BLE001
            errors.append(e)

    t = threading.Thread(target=close_in_other_thread)
    t.start()
    t.join()
    assert errors == []


def test_build_timeline_interleaves_shots(conn):
    conn.execute(
        """CREATE TABLE attachments (id TEXT PRIMARY KEY, document_id TEXT,
           file_name TEXT, file_size INTEGER, is_image BOOLEAN,
           sort_order INTEGER, recording_timestamp DOUBLE)"""
    )
    conn.execute("INSERT INTO attachments VALUES ('AT1','D1','shot1.png',100,1,0,5.0)")
    conn.execute("INSERT INTO attachments VALUES ('AT2','D1','doc.pdf',200,0,1,NULL)")
    blocks = db.group_transcript(db.meeting_transcript(conn, "D1"))
    shots = db.meeting_attachments(conn, "D1")
    timeline = db.build_timeline(blocks, shots)
    kinds = [(i["kind"], i["start_time"]) for i in timeline]
    assert kinds == [("block", 0.0), ("shot", 5.0), ("block", 8.0)]
    # не-картинка и без таймкода в таймлайн не попадает
    assert all(i["kind"] != "shot" or i["file_name"] == "shot1.png" for i in timeline)


def test_export_includes_attachments(conn):
    conn.execute(
        """CREATE TABLE attachments (id TEXT PRIMARY KEY, document_id TEXT,
           file_name TEXT, file_size INTEGER, is_image BOOLEAN,
           sort_order INTEGER, recording_timestamp DOUBLE)"""
    )
    conn.execute("INSERT INTO attachments VALUES ('AT1','D1','shot1.png',100,1,0,65.0)")
    meeting = db.get_meeting(conn, "D1")
    md = build_markdown(
        meeting, [], [], [], [],
        db.group_transcript(db.meeting_transcript(conn, "D1")),
        db.meeting_attachments(conn, "D1"),
    )
    assert "## Скриншоты и вложения" in md
    assert "- [01:05] shot1.png" in md
