"""Схема хаба: воспроизводимость дампа и отсутствие в нём данных встреч."""

import sqlite3

import bootstrap

FTS_SHADOW_SUFFIXES = ("_fts_data", "_fts_idx", "_fts_content", "_fts_docsize", "_fts_config")


def test_schema_sql_replays_into_empty_db():
    """Дамп должен накатываться без ошибок.

    Сырой вывод `.schema` содержит CREATE для shadow-таблиц FTS5, чьи имена
    SQLite считает зарезервированными; он проходит лишь потому, что они идут
    следом за своей CREATE VIRTUAL TABLE и вырождаются в no-op. Генератор их
    отфильтровывает, и этот тест сторожит фильтр.
    """
    schema, _ = bootstrap.schema_paths()
    conn = sqlite3.connect(":memory:")
    conn.executescript(schema.read_text(encoding="utf-8"))
    tables = [r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
    )]
    assert "documents" in tables
    assert "transcription_segments" in tables


def test_schema_sql_has_no_shadow_table_ddl():
    """Ни одного CREATE для shadow-таблиц FTS5 — их создаёт CREATE VIRTUAL TABLE."""
    schema, _ = bootstrap.schema_paths()
    offending = [
        line
        for line in schema.read_text(encoding="utf-8").splitlines()
        if line.startswith("CREATE TABLE")
        and any(suffix in line for suffix in FTS_SHADOW_SUFFIXES)
    ]
    assert offending == []


def test_schema_sql_contains_no_meeting_data(hub_conn):
    """Единственные данные в дампе — идентификаторы миграций."""
    tables = [r[0] for r in hub_conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
    )]
    nonempty = {}
    for table in tables:
        if table.endswith(FTS_SHADOW_SUFFIXES) or table.startswith("ingest_"):
            continue
        try:
            count = hub_conn.execute(f'SELECT count(*) FROM "{table}"').fetchone()[0]
        except sqlite3.OperationalError:
            continue
        if count:
            nonempty[table] = count
    assert set(nonempty) == {"grdb_migrations"}, nonempty
    assert nonempty["grdb_migrations"] > 0


def test_fts_tables_and_triggers_present(hub_conn):
    fts = [r[0] for r in hub_conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name LIKE '%_fts'"
    )]
    assert sorted(fts) == [
        "action_items_fts", "documents_fts", "topics_fts", "transcription_fts",
    ]
    triggers = hub_conn.execute(
        "SELECT count(*) FROM sqlite_master WHERE type='trigger'"
    ).fetchone()[0]
    assert triggers == 12


def test_fts_triggers_fire_on_plain_insert(hub_conn):
    """Слияние вставляет обычным INSERT и рассчитывает на триггеры.

    Здесь же проверяется джойн по rowid — ровно тот, что использует db.py:55.
    """
    hub_conn.execute(
        "INSERT INTO documents (id,title,created_date,modified_date) VALUES (?,?,?,?)",
        ("D1", "Планёрка по бюджету", 1.0, 1.0),
    )
    hub_conn.execute(
        "INSERT INTO transcription_segments (id,document_id,text,start_time,end_time,source)"
        " VALUES (?,?,?,?,?,?)",
        ("S1", "D1", "обсудили квартальный бюджет", 0.0, 5.0, "microphone"),
    )
    found = hub_conn.execute(
        "SELECT d.id FROM documents_fts f JOIN documents d ON d.rowid = f.rowid"
        " WHERE documents_fts MATCH ?",
        ('"Планёрка"',),
    ).fetchall()
    assert [r["id"] for r in found] == ["D1"]

    snippet = hub_conn.execute(
        "SELECT snippet(transcription_fts,0,'[',']','…',8) AS s"
        " FROM transcription_fts WHERE transcription_fts MATCH ?",
        ('"бюджет"',),
    ).fetchone()
    assert "[бюджет]" in snippet["s"]


def test_ingest_tables_present(hub_conn):
    tables = {r[0] for r in hub_conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'ingest_%'"
    )}
    assert tables == {"ingest_devices", "ingest_id_map", "ingest_documents"}


def test_ensure_schema_is_idempotent(hub_db, hub_conn):
    before = hub_conn.execute(
        "SELECT count(*) FROM sqlite_master WHERE type='table'"
    ).fetchone()[0]
    assert bootstrap.ensure_schema(hub_db) is False
    after = hub_conn.execute(
        "SELECT count(*) FROM sqlite_master WHERE type='table'"
    ).fetchone()[0]
    assert before == after


def test_database_is_in_wal_mode(hub_conn):
    """WAL: читающая админка не должна блокироваться на время слияния."""
    mode = hub_conn.execute("PRAGMA journal_mode").fetchone()[0]
    assert mode.lower() == "wal"
