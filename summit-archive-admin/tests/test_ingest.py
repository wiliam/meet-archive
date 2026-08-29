"""Слияние встреч с нескольких устройств.

Главное здесь — первые три теста. Они воспроизводят сценарий, в котором наивное
слияние через INSERT OR REPLACE молча уничтожает данные первого устройства.
"""

import sqlite3

import pytest

import bundle as bundle_mod
import ingest
from conftest import make_bundle


def _ingest(hub_db, device_id, payload):
    return ingest.ingest_bundle(hub_db, device_id, payload)


# --- ловушка UNIQUE + CASCADE ----------------------------------------------


def test_same_tag_name_different_uuid_preserves_other_device_tags(hub_db, hub_conn):
    """Тег «work» с обеих машин: связи первой машины обязаны уцелеть.

    Через INSERT OR REPLACE здесь происходит следующее: REPLACE разрешает
    конфликт по tags.name удалением строки устройства A, а
    document_tags.tag_id ON DELETE CASCADE уносит её связи. Проверено на живой
    схеме: document_tags падает с 1 строки до 0.
    """
    _ingest(hub_db, "mac-a", make_bundle("DOC-A", tags=[("TAG-A", "work")]))
    _ingest(hub_db, "mac-b", make_bundle("DOC-B", tags=[("TAG-B", "work")]))

    assert hub_conn.execute(
        "SELECT count(*) FROM tags WHERE name = 'work'"
    ).fetchone()[0] == 1

    links = hub_conn.execute(
        "SELECT document_id FROM document_tags ORDER BY document_id"
    ).fetchall()
    assert [r["document_id"] for r in links] == ["DOC-A", "DOC-B"]

    # Самое важное: встреча первого устройства всё ещё помечена тегом.
    tagged = hub_conn.execute(
        "SELECT t.name FROM document_tags dt JOIN tags t ON t.id = dt.tag_id"
        " WHERE dt.document_id = 'DOC-A'"
    ).fetchall()
    assert [r["name"] for r in tagged] == ["work"]


def test_same_folder_name_different_uuid_leaves_no_dangling_folder_id(hub_db, hub_conn):
    """У documents.folder_id нет FK, поэтому висячая ссылка не ловится движком.

    PRAGMA foreign_key_check на ней возвращает чисто — проверять приходится
    явным LEFT JOIN.
    """
    _ingest(hub_db, "mac-a", make_bundle("DOC-A", folder=("FOL-A", "Работа")))
    _ingest(hub_db, "mac-b", make_bundle("DOC-B", folder=("FOL-B", "Работа")))

    assert hub_conn.execute("SELECT count(*) FROM folders").fetchone()[0] == 1

    dangling = hub_conn.execute(
        "SELECT count(*) FROM documents d"
        " LEFT JOIN folders f ON f.id = d.folder_id"
        " WHERE d.folder_id IS NOT NULL AND f.id IS NULL"
    ).fetchone()[0]
    assert dangling == 0

    named = hub_conn.execute(
        "SELECT d.id, f.name FROM documents d JOIN folders f ON f.id = d.folder_id"
        " ORDER BY d.id"
    ).fetchall()
    assert [(r["id"], r["name"]) for r in named] == [
        ("DOC-A", "Работа"), ("DOC-B", "Работа"),
    ]


def test_same_template_name_different_uuid_keeps_template_id(hub_db, hub_conn):
    """documents.template_id объявлен ON DELETE SET NULL — REPLACE обнулил бы его."""
    _ingest(hub_db, "mac-a", make_bundle("DOC-A", template=("TPL-A", "Стандартный")))
    _ingest(hub_db, "mac-b", make_bundle("DOC-B", template=("TPL-B", "Стандартный")))

    assert hub_conn.execute(
        "SELECT count(*) FROM summarization_templates"
    ).fetchone()[0] == 1

    nulled = hub_conn.execute(
        "SELECT count(*) FROM documents WHERE template_id IS NULL"
    ).fetchone()[0]
    assert nulled == 0


def test_foreign_key_check_clean_after_two_devices(hub_db, hub_conn):
    _ingest(hub_db, "mac-a", make_bundle(
        "DOC-A", folder=("FOL-A", "Работа"), tags=[("TAG-A", "work")],
        speakers=[{"id": "SPK-A", "name": "Иван", "created_date": 1.0}],
    ))
    _ingest(hub_db, "mac-b", make_bundle(
        "DOC-B", folder=("FOL-B", "Работа"), tags=[("TAG-B", "work")],
        speakers=[{"id": "SPK-B", "name": "Иван", "created_date": 1.0}],
    ))
    assert hub_conn.execute("PRAGMA foreign_key_check").fetchall() == []


def test_no_insert_or_replace_into_tables_with_text_unique():
    """Инвариант модуля, закреплённый на уровне исходника.

    Грубо, но ровно там, где его сломает будущий рефакторинг: любое OR REPLACE
    в ingest.py должно быть нацелено только в наши собственные ingest_* таблицы.
    """
    import inspect
    import re

    source = inspect.getsource(ingest)
    targets = re.findall(r"INSERT OR REPLACE INTO\s+(\w+)", source)
    assert targets, "проверка потеряла смысл: OR REPLACE больше не встречается"
    assert all(t.startswith("ingest_") for t in targets), targets


# --- разводка справочников --------------------------------------------------


def test_id_map_is_stable_across_pushes(hub_db, hub_conn):
    """Повторная отправка не должна уводить тег на другую строку."""
    _ingest(hub_db, "mac-a", make_bundle("DOC-A", tags=[("TAG-A", "work")]))
    _ingest(hub_db, "mac-b", make_bundle("DOC-B", tags=[("TAG-B", "work")]))
    mapped = hub_conn.execute(
        "SELECT target_id FROM ingest_id_map"
        " WHERE device_id = 'mac-b' AND table_name = 'tags' AND source_id = 'TAG-B'"
    ).fetchone()["target_id"]
    assert mapped == "TAG-A"

    _ingest(hub_db, "mac-b", make_bundle(
        "DOC-B", tags=[("TAG-B", "work")], modified_date=200.0,
    ))
    again = hub_conn.execute(
        "SELECT target_id FROM ingest_id_map"
        " WHERE device_id = 'mac-b' AND table_name = 'tags' AND source_id = 'TAG-B'"
    ).fetchone()["target_id"]
    assert again == mapped


def test_speakers_are_not_deduplicated_by_name(hub_db, hub_conn):
    """Одинаковые имена без email — не повод склеивать разных людей."""
    _ingest(hub_db, "mac-a", make_bundle(
        "DOC-A", speakers=[{"id": "SPK-A", "name": "Иван", "created_date": 1.0}]))
    _ingest(hub_db, "mac-b", make_bundle(
        "DOC-B", speakers=[{"id": "SPK-B", "name": "Иван", "created_date": 1.0}]))
    assert hub_conn.execute("SELECT count(*) FROM speakers").fetchone()[0] == 2


def test_speakers_are_deduplicated_by_email(hub_db, hub_conn):
    _ingest(hub_db, "mac-a", make_bundle("DOC-A", speakers=[
        {"id": "SPK-A", "name": "Иван", "email": "ivan@example.com", "created_date": 1.0}]))
    _ingest(hub_db, "mac-b", make_bundle("DOC-B", speakers=[
        {"id": "SPK-B", "name": "Ivan P", "email": "IVAN@example.com ", "created_date": 1.0}]))
    assert hub_conn.execute("SELECT count(*) FROM speakers").fetchone()[0] == 1
    links = hub_conn.execute(
        "SELECT speaker_id FROM document_speakers ORDER BY document_id"
    ).fetchall()
    assert {r["speaker_id"] for r in links} == {"SPK-A"}


def test_speaker_parent_inserted_before_child(hub_db, hub_conn):
    """parent_speaker_id — настоящий self-FK, порядок вставки значим."""
    _ingest(hub_db, "mac-a", make_bundle("DOC-A", speakers=[
        {"id": "CHILD", "name": "Ваня", "created_date": 1.0, "parent_speaker_id": "PARENT"},
        {"id": "PARENT", "name": "Иван", "created_date": 1.0},
    ]))
    assert hub_conn.execute("PRAGMA foreign_key_check").fetchall() == []
    row = hub_conn.execute(
        "SELECT parent_speaker_id FROM speakers WHERE id = 'CHILD'"
    ).fetchone()
    assert row["parent_speaker_id"] == "PARENT"


# --- идемпотентность --------------------------------------------------------


def _counts(conn):
    return {
        t: conn.execute(f"SELECT count(*) FROM {t}").fetchone()[0]
        for t in ("documents", "tags", "document_tags", "topics",
                  "transcription_segments", "folders")
    }


def test_repush_identical_bundle_is_noop(hub_db, hub_conn):
    payload = make_bundle(
        "DOC-A", tags=[("TAG-A", "work")], segments=[("S1", "первый"), ("S2", "второй")])
    _ingest(hub_db, "mac-a", payload)
    before = _counts(hub_conn)

    result = _ingest(hub_db, "mac-a", make_bundle(
        "DOC-A", tags=[("TAG-A", "work")], segments=[("S1", "первый"), ("S2", "второй")]))

    assert result.unchanged is True
    assert _counts(hub_conn) == before


def test_repush_changed_bundle_replaces_children(hub_db, hub_conn):
    _ingest(hub_db, "mac-a", make_bundle("DOC-A", segments=[
        ("S1", "первый"), ("S2", "второй"), ("S3", "квартальный бюджет")]))
    assert hub_conn.execute(
        "SELECT count(*) FROM transcription_segments").fetchone()[0] == 3

    _ingest(hub_db, "mac-a", make_bundle(
        "DOC-A", modified_date=200.0, segments=[("S1", "первый"), ("S2", "второй")]))

    assert hub_conn.execute(
        "SELECT count(*) FROM transcription_segments").fetchone()[0] == 2
    # Удалённый текст не должен остаться в поисковом индексе.
    stale = hub_conn.execute(
        "SELECT count(*) FROM transcription_fts WHERE transcription_fts MATCH ?",
        ('"бюджет"',),
    ).fetchone()[0]
    assert stale == 0


def test_repush_does_not_duplicate_junction_rows(hub_db, hub_conn):
    for modified in (100.0, 200.0, 300.0):
        _ingest(hub_db, "mac-a", make_bundle(
            "DOC-A", tags=[("TAG-A", "work")], modified_date=modified))
    assert hub_conn.execute("SELECT count(*) FROM document_tags").fetchone()[0] == 1


def test_two_source_tags_collapsing_onto_one_target_do_not_collide(hub_db, hub_conn):
    """После разводки два исходных тега могут схлопнуться в один целевой."""
    _ingest(hub_db, "mac-a", make_bundle("DOC-A", tags=[("TAG-X", "work")]))
    payload = make_bundle("DOC-B", tags=[("TAG-Y", "work")])
    payload["tables"]["tags"].append({"id": "TAG-Z", "name": "work", "created_date": 1.0})
    payload["tables"]["document_tags"].append(
        {"document_id": "DOC-B", "tag_id": "TAG-Z", "created_date": 1.0})

    _ingest(hub_db, "mac-b", payload)

    assert hub_conn.execute(
        "SELECT count(*) FROM document_tags WHERE document_id = 'DOC-B'"
    ).fetchone()[0] == 1


def test_failed_bundle_rolls_back(hub_db, hub_conn):
    _ingest(hub_db, "mac-a", make_bundle("DOC-A", tags=[("TAG-A", "work")]))
    before = _counts(hub_conn)

    broken = make_bundle("DOC-B", tags=[("TAG-B", "новый")])
    broken["tables"]["documents"][0]["title"] = None  # NOT NULL

    with pytest.raises(sqlite3.IntegrityError):
        _ingest(hub_db, "mac-b", broken)

    assert _counts(hub_conn) == before
    assert hub_conn.execute(
        "SELECT count(*) FROM documents WHERE id = 'DOC-B'").fetchone()[0] == 0


# --- поиск после слияния ----------------------------------------------------


def test_search_finds_meetings_from_both_devices(hub_db, hub_conn):
    _ingest(hub_db, "mac-a", make_bundle(
        "DOC-A", title="Планёрка", segments=[("S1", "обсудили квартальный бюджет")]))
    _ingest(hub_db, "mac-b", make_bundle(
        "DOC-B", title="Ретро", segments=[("S2", "бюджет на следующий квартал")]))

    found = hub_conn.execute(
        "SELECT DISTINCT ts.document_id FROM transcription_fts f"
        " JOIN transcription_segments ts ON ts.rowid = f.rowid"
        " WHERE transcription_fts MATCH ? ORDER BY ts.document_id",
        ('"бюджет"',),
    ).fetchall()
    assert [r["document_id"] for r in found] == ["DOC-A", "DOC-B"]


def test_documents_fts_rowid_join_stays_consistent(hub_db, hub_conn):
    """Ровно тот джойн, что использует db.py:55."""
    for i in range(3):
        _ingest(hub_db, "mac-a", make_bundle(f"DOC-{i}", title=f"Встреча {i}"))
    total = hub_conn.execute("SELECT count(*) FROM documents").fetchone()[0]
    joined = hub_conn.execute(
        "SELECT count(*) FROM documents d JOIN documents_fts f ON d.rowid = f.rowid"
    ).fetchone()[0]
    assert joined == total == 3


def test_chunk_segment_ids_still_resolve(hub_db, hub_conn):
    """segment_ids_json — мягкая ссылка; UUID сегментов не переписываются."""
    payload = make_bundle(
        "DOC-A",
        segments=[("SEG-1", "первый"), ("SEG-2", "второй")],
        chunks=[{
            "id": "CHK-1", "document_id": "DOC-A",
            "segment_ids_json": '["SEG-1","SEG-2"]',
            "chunk_text": "первый второй", "start_time": 0.0, "end_time": 2.0,
            "word_count": 2, "created_at": 1.0,
        }],
    )
    _ingest(hub_db, "mac-a", payload)

    import json
    raw = hub_conn.execute(
        "SELECT segment_ids_json FROM transcription_chunks").fetchone()[0]
    for seg_id in json.loads(raw):
        assert hub_conn.execute(
            "SELECT 1 FROM transcription_segments WHERE id = ?", (seg_id,)
        ).fetchone() is not None


# --- контракт бандла --------------------------------------------------------


def test_excluded_columns_never_reach_the_server(hub_db, hub_conn):
    payload = make_bundle("DOC-A", segments=[("S1", "текст")])
    payload["tables"]["transcription_segments"][0]["word_confidences_json"] = "[0.9]"
    payload["tables"]["speakers"] = [
        {"id": "SPK", "name": "Иван", "created_date": 1.0, "embedding": {"$b64": "AAAA"}}]
    payload["tables"]["document_speakers"] = [
        {"document_id": "DOC-A", "speaker_id": "SPK", "created_date": 1.0}]

    _ingest(hub_db, "mac-a", payload)

    row = hub_conn.execute(
        "SELECT word_confidences_json FROM transcription_segments").fetchone()
    assert row["word_confidences_json"] is None
    assert hub_conn.execute("SELECT embedding FROM speakers").fetchone()["embedding"] is None


@pytest.mark.parametrize("table", ["documents_fts", "grdb_migrations", "ingest_documents"])
def test_denied_tables_are_rejected(hub_db, table):
    payload = make_bundle("DOC-A")
    payload["tables"][table] = []
    with pytest.raises(ingest.BundleError):
        _ingest(hub_db, "mac-a", payload)


def test_child_rows_of_another_document_are_rejected(hub_db):
    payload = make_bundle("DOC-A", segments=[("S1", "текст")])
    payload["tables"]["transcription_segments"][0]["document_id"] = "DOC-OTHER"
    with pytest.raises(ingest.BundleError):
        _ingest(hub_db, "mac-a", payload)


def test_wrong_bundle_version_is_rejected(hub_db):
    payload = make_bundle("DOC-A")
    payload["bundle_version"] = 99
    with pytest.raises(ingest.BundleError):
        _ingest(hub_db, "mac-a", payload)


def test_child_tables_match_archiver():
    """Список дочерних таблиц не должен разъехаться с архиватором.

    Импортировать оттуда нельзя: в образ едет только каталог админки.
    """
    import importlib.util
    import sys
    from pathlib import Path

    script = Path(__file__).resolve().parents[2] / "archive_summit_meetings.py"
    spec = importlib.util.spec_from_file_location("archiver", script)
    module = importlib.util.module_from_spec(spec)
    # dataclasses резолвит аннотации через sys.modules — под
    # `from __future__ import annotations` без регистрации модуль не соберётся.
    sys.modules["archiver"] = module
    try:
        spec.loader.exec_module(module)
    finally:
        sys.modules.pop("archiver", None)

    assert set(bundle_mod.CHILD_TABLES) == {t for t, _ in module.DIRECT_DOCUMENT_TABLES}


# --- дрейф схемы ------------------------------------------------------------


def test_newer_client_schema_is_rejected(hub_db):
    payload = make_bundle("DOC-A", migrations=["v99_from_the_future"])
    with pytest.raises(ingest.SchemaAhead) as exc:
        _ingest(hub_db, "mac-a", payload)
    assert exc.value.unknown == ["v99_from_the_future"]


def test_older_client_schema_is_accepted_with_warning(hub_db, hub_conn):
    known = [r[0] for r in hub_conn.execute("SELECT identifier FROM grdb_migrations")]
    result = _ingest(hub_db, "mac-a", make_bundle("DOC-A", migrations=known[:3]))
    assert result.warnings
    assert "более старой версии" in result.warnings[0]


def test_state_reports_what_the_server_has(hub_db, hub_conn):
    _ingest(hub_db, "mac-a", make_bundle("DOC-A", modified_date=150.0))
    state = ingest.ingest_state(hub_conn)
    assert state["count"] == 1
    assert state["documents"]["DOC-A"]["modified_date"] == 150.0
    assert state["documents"]["DOC-A"]["device_id"] == "mac-a"
    assert state["schema"]["migrations"]
