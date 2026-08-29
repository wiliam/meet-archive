"""Общие фикстуры.

База собирается из закоммиченных schema.sql и ingest_schema.sql, то есть из
настоящей production-схемы с настоящими FTS-триггерами. Данных встреч при этом
ноль — требование CLAUDE.md о том, что тесты не должны зависеть от реального
архива, соблюдено.
"""

import sys
from pathlib import Path

import pytest

ADMIN_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ADMIN_DIR))

import bootstrap  # noqa: E402


@pytest.fixture
def hub_root(tmp_path: Path) -> Path:
    """Каталог архива хаба с созданной пустой базой."""
    root = tmp_path / "hub"
    root.mkdir()
    bootstrap.ensure_schema(root / "database.sqlite")
    return root


@pytest.fixture
def hub_db(hub_root: Path) -> Path:
    return hub_root / "database.sqlite"


@pytest.fixture
def hub_conn(hub_db: Path):
    conn = bootstrap.connect_rw(hub_db)
    try:
        yield conn
    finally:
        conn.close()


import bundle as bundle_mod  # noqa: E402


def make_bundle(
    doc_id: str,
    *,
    title: str = "Встреча",
    modified_date: float = 100.0,
    folder: tuple[str, str] | None = None,
    template: tuple[str, str] | None = None,
    tags: list[tuple[str, str]] | None = None,
    speakers: list[dict] | None = None,
    segments: list[tuple[str, str]] | None = None,
    topics: list[tuple[str, str]] | None = None,
    chunks: list[dict] | None = None,
    migrations: list[str] | None = None,
    device_label: str = "test-mac",
    audio_files: list[str] | None = None,
) -> dict:
    """Собрать бандл одной встречи.

    Умышленно строится вручную, а не выгрузкой из настоящего архива: фикстуры
    не должны зависеть от реальных встреч.
    """
    document = {
        "id": doc_id,
        "title": title,
        "created_date": 1.0,
        "modified_date": modified_date,
    }
    tables: dict[str, list[dict]] = {}

    if template is not None:
        tid, tname = template
        document["template_id"] = tid
        tables["summarization_templates"] = [{
            "id": tid, "name": tname, "topics_prompt": "t",
            "action_items_prompt": "a", "created_date": 1.0, "modified_date": 1.0,
        }]
    if folder is not None:
        fid, fname = folder
        document["folder_id"] = fid
        tables["folders"] = [{
            "id": fid, "name": fname, "created_date": 1.0, "modified_date": 1.0,
        }]
    if tags:
        tables["tags"] = [
            {"id": tid, "name": name, "created_date": 1.0} for tid, name in tags
        ]
        tables["document_tags"] = [
            {"document_id": doc_id, "tag_id": tid, "created_date": 1.0}
            for tid, _ in tags
        ]
    if speakers:
        tables["speakers"] = [dict(s) for s in speakers]
        tables["document_speakers"] = [
            {"document_id": doc_id, "speaker_id": s["id"], "created_date": 1.0}
            for s in speakers
        ]
    if segments:
        tables["transcription_segments"] = [
            {
                "id": sid, "document_id": doc_id, "text": text,
                "start_time": float(i), "end_time": float(i) + 1.0,
                "source": "microphone",
            }
            for i, (sid, text) in enumerate(segments)
        ]
    if topics:
        tables["topics"] = [
            {"id": tid, "document_id": doc_id, "content": content, "created_date": 1.0}
            for tid, content in topics
        ]
    if chunks:
        tables["transcription_chunks"] = [dict(c) for c in chunks]

    tables["documents"] = [document]

    payload = {
        "bundle_version": bundle_mod.BUNDLE_VERSION,
        "device_label": device_label,
        "generated_at": 1.0,
        "document_id": doc_id,
        "modified_date": modified_date,
        "source_schema": {"migrations": migrations if migrations is not None else []},
        "audio_files": audio_files or [],
        "tables": tables,
    }
    payload["content_hash"] = bundle_mod.content_hash(tables, None)
    return payload


@pytest.fixture
def bundle_factory():
    return make_bundle
