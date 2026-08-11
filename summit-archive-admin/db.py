"""Слой доступа к архивной базе Summit. Все соединения строго read-only."""

import os
import sqlite3
from pathlib import Path


def archive_root() -> Path:
    default = Path(__file__).resolve().parent.parent / "summit-archive"
    return Path(os.environ.get("SUMMIT_ARCHIVE_ROOT", default))


def db_path() -> Path:
    return archive_root() / "database.sqlite"


def connect(path: Path | None = None) -> sqlite3.Connection:
    path = Path(path) if path else db_path()
    if not path.exists():
        raise FileNotFoundError(
            f"Архивная база не найдена: {path}. "
            "Укажите путь через переменную окружения SUMMIT_ARCHIVE_ROOT."
        )
    # check_same_thread=False: FastAPI открывает и закрывает sync-зависимости
    # в разных потоках пула; сам sqlite3 собран в serialized-режиме, это безопасно
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def fts_match_expr(q: str) -> str:
    """Пользовательский запрос -> безопасное MATCH-выражение (каждое слово — фраза)."""
    tokens = [t for t in q.split() if t]
    return " ".join('"' + t.replace('"', '""') + '"' for t in tokens)


def search_documents(conn: sqlite3.Connection, q: str) -> dict[str, dict]:
    """Полнотекстовый поиск по заголовкам, транскриптам и темам.

    Возвращает {document_id: {"snippet": str | None, "where": set[str]}}.
    """
    match = fts_match_expr(q)
    if not match:
        return {}
    hits: dict[str, dict] = {}

    def hit(doc_id: str, where: str, snippet: str | None = None):
        entry = hits.setdefault(doc_id, {"snippet": None, "where": set()})
        entry["where"].add(where)
        if snippet and not entry["snippet"]:
            entry["snippet"] = snippet

    rows = conn.execute(
        """SELECT d.id FROM documents_fts f
           JOIN documents d ON d.rowid = f.rowid
           WHERE documents_fts MATCH ? AND d.deleted_at IS NULL""",
        (match,),
    )
    for r in rows:
        hit(r["id"], "title")

    rows = conn.execute(
        """SELECT document_id,
                  snippet(transcription_fts, 0, '⟦', '⟧', '…', 14) AS snip
           FROM transcription_fts WHERE transcription_fts MATCH ?
           ORDER BY bm25(transcription_fts)""",
        (match,),
    )
    for r in rows:
        hit(r["document_id"], "transcript", r["snip"])

    rows = conn.execute(
        """SELECT document_id,
                  snippet(topics_fts, 0, '⟦', '⟧', '…', 14) AS snip
           FROM topics_fts WHERE topics_fts MATCH ?""",
        (match,),
    )
    for r in rows:
        hit(r["document_id"], "topics", r["snip"])

    return hits


def list_meetings(
    conn: sqlite3.Connection,
    folder_id: str | None = None,
    tag_id: str | None = None,
    ids: list[str] | None = None,
) -> list[sqlite3.Row]:
    sql = """
        SELECT d.id, d.title, d.created_date, d.recording_duration, d.word_count,
               d.has_transcription,
               f.id AS folder_id, f.name AS folder_name, f.color AS folder_color,
               (SELECT group_concat(t.name, ', ')
                  FROM document_tags dt JOIN tags t ON t.id = dt.tag_id
                 WHERE dt.document_id = d.id) AS tag_names,
               (SELECT count(*) FROM document_speakers ds
                 WHERE ds.document_id = d.id) AS speaker_count
        FROM documents d
        LEFT JOIN folders f ON f.id = d.folder_id
        WHERE d.deleted_at IS NULL
    """
    params: list = []
    if folder_id:
        sql += " AND d.folder_id = ?"
        params.append(folder_id)
    if tag_id:
        sql += """ AND EXISTS (SELECT 1 FROM document_tags dt
                               WHERE dt.document_id = d.id AND dt.tag_id = ?)"""
        params.append(tag_id)
    if ids is not None:
        if not ids:
            return []
        sql += f" AND d.id IN ({','.join('?' * len(ids))})"
        params.extend(ids)
    sql += " ORDER BY d.created_date DESC"
    return conn.execute(sql, params).fetchall()


def folders_with_counts(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute(
        """SELECT f.id, f.name, f.color, f.icon,
                  count(d.id) AS meeting_count
           FROM folders f
           LEFT JOIN documents d ON d.folder_id = f.id AND d.deleted_at IS NULL
           GROUP BY f.id ORDER BY f.sort_order, f.name"""
    ).fetchall()


def tags_with_counts(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute(
        """SELECT t.id, t.name, t.color, count(dt.document_id) AS meeting_count
           FROM tags t
           LEFT JOIN document_tags dt ON dt.tag_id = t.id
           GROUP BY t.id HAVING meeting_count > 0
           ORDER BY t.name"""
    ).fetchall()


def get_meeting(conn: sqlite3.Connection, doc_id: str) -> sqlite3.Row | None:
    return conn.execute(
        """SELECT d.*, f.name AS folder_name, f.color AS folder_color
           FROM documents d LEFT JOIN folders f ON f.id = d.folder_id
           WHERE d.id = ? AND d.deleted_at IS NULL""",
        (doc_id,),
    ).fetchone()


def meeting_speakers(conn: sqlite3.Connection, doc_id: str) -> list[sqlite3.Row]:
    # parent_speaker_id указывает на «объединённого» спикера — предпочитаем его имя
    return conn.execute(
        """SELECT COALESCE(p.name, s.name) AS name,
                  COALESCE(p.company, s.company) AS company,
                  COALESCE(p.position, s.position) AS position
           FROM document_speakers ds
           JOIN speakers s ON s.id = ds.speaker_id
           LEFT JOIN speakers p ON p.id = s.parent_speaker_id
           WHERE ds.document_id = ?
           ORDER BY name""",
        (doc_id,),
    ).fetchall()


def meeting_tags(conn: sqlite3.Connection, doc_id: str) -> list[sqlite3.Row]:
    return conn.execute(
        """SELECT t.id, t.name, t.color FROM document_tags dt
           JOIN tags t ON t.id = dt.tag_id
           WHERE dt.document_id = ? ORDER BY t.name""",
        (doc_id,),
    ).fetchall()


def meeting_topics(conn: sqlite3.Connection, doc_id: str) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT content FROM topics WHERE document_id = ? ORDER BY sort_order",
        (doc_id,),
    ).fetchall()


def meeting_action_items(conn: sqlite3.Connection, doc_id: str) -> list[sqlite3.Row]:
    return conn.execute(
        """SELECT content, completed, assignee, due_date
           FROM action_items WHERE document_id = ? ORDER BY sort_order""",
        (doc_id,),
    ).fetchall()


def meeting_attachments(conn: sqlite3.Connection, doc_id: str) -> list[sqlite3.Row]:
    return conn.execute(
        """SELECT file_name, file_size, is_image, recording_timestamp
           FROM attachments WHERE document_id = ?
           ORDER BY recording_timestamp, sort_order""",
        (doc_id,),
    ).fetchall()


def build_timeline(blocks: list[dict], attachments) -> list[dict]:
    """Вплетает скриншоты (recording_timestamp) в блоки транскрипта по времени."""
    shots = [
        {
            "kind": "shot",
            "file_name": a["file_name"],
            "start_time": float(a["recording_timestamp"]),
        }
        for a in attachments
        if a["is_image"] and a["recording_timestamp"] is not None
    ]
    items = [{"kind": "block", **b} for b in blocks] + shots
    # при равном времени реплика идёт раньше скриншота
    items.sort(key=lambda x: (x["start_time"], x["kind"] == "shot"))
    return items


def meeting_transcript(conn: sqlite3.Connection, doc_id: str) -> list[sqlite3.Row]:
    return conn.execute(
        """SELECT ts.text, ts.start_time, ts.end_time, ts.source, ts.is_irrelevant,
                  COALESCE(p.name, s.name) AS speaker_name
           FROM transcription_segments ts
           LEFT JOIN speakers s ON s.id = ts.speaker_id
           LEFT JOIN speakers p ON p.id = s.parent_speaker_id
           WHERE ts.document_id = ?
           ORDER BY ts.start_time""",
        (doc_id,),
    ).fetchall()


def group_transcript(segments) -> list[dict]:
    """Склеивает подряд идущие реплики одного спикера в блоки для чтения."""
    blocks: list[dict] = []
    for seg in segments:
        name = seg["speaker_name"] or (
            "Системный звук" if seg["source"] == "systemAudio" else "Микрофон"
        )
        last = blocks[-1] if blocks else None
        if last and last["speaker"] == name and last["source"] == seg["source"]:
            last["texts"].append(seg["text"])
            last["end_time"] = seg["end_time"]
        else:
            blocks.append(
                {
                    "speaker": name,
                    "source": seg["source"],
                    "start_time": seg["start_time"],
                    "end_time": seg["end_time"],
                    "texts": [seg["text"]],
                }
            )
    return blocks
