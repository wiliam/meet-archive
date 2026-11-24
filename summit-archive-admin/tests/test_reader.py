"""Agent-oriented read interface over the meeting archive."""

import sqlite3
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from reader import MeetingQuery, MeetingReader, parse_since


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
CREATE TABLE attachments (
  id TEXT PRIMARY KEY, document_id TEXT, file_name TEXT, file_size INTEGER,
  is_image BOOLEAN, sort_order INTEGER, recording_timestamp DOUBLE
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
def reader_db(tmp_path):
    path = tmp_path / "database.sqlite"
    conn = sqlite3.connect(path)
    conn.executescript(SCHEMA)
    conn.execute("INSERT INTO folders VALUES ('F1','Billing',NULL,NULL,0)")
    conn.execute("INSERT INTO tags VALUES ('T1','project-x',NULL)")
    conn.execute(
        "INSERT INTO documents VALUES (?,?,?,?,?,?,?,?,?,NULL)",
        ("D1", "Billing redesign", 2000.0, 2100.0, 600.0, 100, 1, "Решили менять API", "F1"),
    )
    conn.execute(
        "INSERT INTO documents VALUES (?,?,?,?,?,?,?,?,?,NULL)",
        ("D2", "Old retro", 1000.0, 1000.0, 300.0, 50, 1, None, None),
    )
    conn.execute("INSERT INTO document_tags VALUES ('D1','T1')")
    conn.execute("INSERT INTO speakers VALUES ('S1','Марина',NULL,NULL,NULL)")
    conn.execute("INSERT INTO document_speakers VALUES ('D1','S1')")
    conn.execute("INSERT INTO topics VALUES ('TP1','D1','Метрики биллинга',0)")
    conn.execute(
        "INSERT INTO action_items VALUES ('A1','D1','Подготовить RFC',0,0,'Илья',NULL)"
    )
    conn.execute(
        "INSERT INTO transcription_segments VALUES ('TS1','D1','Обсудили метрики биллинга',0,4,'microphone',0,'S1')"
    )
    conn.execute("INSERT INTO documents_fts(rowid,title,notes) SELECT rowid,title,coalesce(notes,'') FROM documents")
    conn.execute("INSERT INTO transcription_fts(rowid,text,document_id) SELECT rowid,text,document_id FROM transcription_segments")
    conn.execute("INSERT INTO topics_fts(rowid,content,document_id) SELECT rowid,content,document_id FROM topics")
    conn.commit()
    conn.row_factory = sqlite3.Row
    yield path, conn
    conn.close()


def test_search_filters_and_returns_agent_metadata(reader_db):
    _path, conn = reader_db
    rows = MeetingReader(conn).search(
        MeetingQuery(text="биллинга", since=1500, folder="billing", tag="project-x")
    )

    assert [row["id"] for row in rows] == ["D1"]
    assert rows[0]["matched_in"] == ["topics", "transcript"]
    assert "**биллинга**" in rows[0]["snippet"]


def test_get_summary_hides_transcript_until_requested(reader_db):
    _path, conn = reader_db
    reader = MeetingReader(conn)

    summary = reader.get("D1")
    full = reader.get("D1", include_transcript=True)

    assert summary["notes"] == "Решили менять API"
    assert summary["topics"] == ["Метрики биллинга"]
    assert summary["transcript"] == []
    assert full["transcript"][0]["text"] == "Обсудили метрики биллинга"


def test_context_is_bounded_and_cites_meeting_id(reader_db):
    _path, conn = reader_db
    text = MeetingReader(conn).context(
        MeetingQuery(text="биллинга", limit=5), max_chars=1_000
    )

    assert "`D1`" in text
    assert "Подготовить RFC" in text
    assert "### Транскрипт" not in text
    assert len(text) <= 1_000


@pytest.mark.parametrize("query", ["биллинга", "no matches"])
def test_context_honors_small_output_budget(reader_db, query):
    _path, conn = reader_db
    reader = MeetingReader(conn)

    assert len(reader.context(MeetingQuery(text=query), max_chars=10)) <= 10


def test_parse_since_relative_and_iso():
    assert parse_since("2d", now=200_000) == 27_200
    assert parse_since("1970-01-02T00:00:00+00:00") == 86_400
    with pytest.raises(ValueError):
        parse_since("yesterday")


def test_http_agent_adapter(reader_db, monkeypatch):
    path, _conn = reader_db
    monkeypatch.setenv("SUMMIT_ARCHIVE_ROOT", str(path.parent))
    import app

    with TestClient(app.app) as client:
        response = client.get("/agent/v1/meetings", params={"q": "биллинга"})
        assert response.status_code == 200
        assert response.json()[0]["id"] == "D1"

        context = client.get("/agent/v1/context", params={"q": "биллинга"})
        assert context.status_code == 200
        assert "Подготовить RFC" in context.text
