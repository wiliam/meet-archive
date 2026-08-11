"""Summit Archive Admin — read-only веб-админка над архивом встреч Summit AI.

Запуск:  uvicorn app:app --reload
Конфигурация через переменные окружения:
  SUMMIT_ARCHIVE_ROOT  путь к папке архива (по умолчанию ../summit-archive)
  ADMIN_USER / ADMIN_PASSWORD  включают HTTP Basic Auth (для хостинга)
"""

import os
import secrets
from pathlib import Path
from urllib.parse import quote

from fastapi import Depends, FastAPI, Form, HTTPException, Request
from fastapi.responses import FileResponse, Response
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

import db
import sync as sync_mod
from export import build_markdown, export_filename, fmt_date, fmt_duration, fmt_ts

BASE_DIR = Path(__file__).resolve().parent
MEETING_FILES = {"document.md", "microphone.m4a", "system.m4a"}

security = HTTPBasic(auto_error=False)


def require_auth(credentials: HTTPBasicCredentials | None = Depends(security)):
    user = os.environ.get("ADMIN_USER")
    password = os.environ.get("ADMIN_PASSWORD")
    if not user or not password:
        return  # auth выключен (локальный режим)
    ok = (
        credentials is not None
        and secrets.compare_digest(credentials.username, user)
        and secrets.compare_digest(credentials.password, password)
    )
    if not ok:
        raise HTTPException(
            status_code=401, headers={"WWW-Authenticate": "Basic realm=summit-archive"}
        )


app = FastAPI(title="Summit Archive", dependencies=[Depends(require_auth)])
app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")

templates = Jinja2Templates(directory=BASE_DIR / "templates")
templates.env.filters["fmt_date"] = fmt_date
templates.env.filters["fmt_duration"] = fmt_duration
templates.env.filters["fmt_ts"] = fmt_ts


def highlight(snippet: str):
    """Экранирует текст сниппета и превращает маркеры ⟦…⟧ в <mark>."""
    from markupsafe import Markup, escape

    return Markup(str(escape(snippet)).replace("⟦", "<mark>").replace("⟧", "</mark>"))


templates.env.filters["highlight"] = highlight


def get_conn():
    conn = db.connect()
    try:
        yield conn
    finally:
        conn.close()


@app.get("/")
def index(
    request: Request,
    folder: str | None = None,
    tag: str | None = None,
    q: str | None = None,
    conn=Depends(get_conn),
):
    hits = db.search_documents(conn, q) if q else None
    meetings = db.list_meetings(
        conn,
        folder_id=folder,
        tag_id=tag,
        ids=list(hits.keys()) if hits is not None else None,
    )
    return templates.TemplateResponse(
        request,
        "index.html",
        {
            "meetings": meetings,
            "hits": hits or {},
            "folders": db.folders_with_counts(conn),
            "tags": db.tags_with_counts(conn),
            "active_folder": folder,
            "active_tag": tag,
            "q": q or "",
        },
    )


def _load_meeting(conn, doc_id: str):
    meeting = db.get_meeting(conn, doc_id)
    if meeting is None:
        raise HTTPException(status_code=404, detail="Встреча не найдена")
    return meeting


@app.get("/meetings/{doc_id}")
def meeting_page(request: Request, doc_id: str, conn=Depends(get_conn)):
    meeting = _load_meeting(conn, doc_id)
    blocks = db.group_transcript(db.meeting_transcript(conn, doc_id))
    meeting_dir = db.archive_root() / doc_id
    files_on_disk = sorted(
        f for f in MEETING_FILES if (meeting_dir / f).exists()
    )
    attachments = [
        a
        for a in db.meeting_attachments(conn, doc_id)
        if (meeting_dir / "Attachments" / a["file_name"]).exists()
    ]
    timeline = db.build_timeline(blocks, attachments)
    other_attachments = [
        a for a in attachments if not (a["is_image"] and a["recording_timestamp"] is not None)
    ]
    return templates.TemplateResponse(
        request,
        "meeting.html",
        {
            "m": meeting,
            "speakers": db.meeting_speakers(conn, doc_id),
            "tags": db.meeting_tags(conn, doc_id),
            "topics": db.meeting_topics(conn, doc_id),
            "action_items": db.meeting_action_items(conn, doc_id),
            "timeline": timeline,
            "has_transcript_blocks": bool(blocks),
            "other_attachments": other_attachments,
            "files_on_disk": files_on_disk,
        },
    )


@app.get("/meetings/{doc_id}/export.md")
def export_md(doc_id: str, conn=Depends(get_conn)):
    meeting = _load_meeting(conn, doc_id)
    markdown = build_markdown(
        meeting,
        db.meeting_speakers(conn, doc_id),
        db.meeting_tags(conn, doc_id),
        db.meeting_topics(conn, doc_id),
        db.meeting_action_items(conn, doc_id),
        db.group_transcript(db.meeting_transcript(conn, doc_id)),
        db.meeting_attachments(conn, doc_id),
    )
    filename = quote(export_filename(meeting))
    return Response(
        markdown,
        media_type="text/markdown; charset=utf-8",
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{filename}"},
    )


def _render_sync(request, keep_active: int, result: dict | None = None):
    delta = sync_mod.compute_delta(keep_active)
    return templates.TemplateResponse(
        request,
        "sync.html",
        {
            "delta": delta,
            "state": sync_mod.load_state(),
            "keep_active": keep_active,
            "result": result,
            "source_root": str(sync_mod.source_root()),
        },
    )


def _default_keep() -> int:
    return int(sync_mod.load_state().get("last_prune_keep_active") or 10)


@app.get("/sync")
def sync_page(request: Request, keep: int | None = None):
    return _render_sync(request, keep or _default_keep())


@app.post("/sync/run")
def sync_run(
    request: Request,
    mode: str = Form("sync-and-prune"),
    keep_active: int = Form(10),
    dry_run: bool = Form(False),
):
    result = sync_mod.run_script(mode, keep_active, dry_run)
    return _render_sync(request, keep_active, result)


@app.get("/meetings/{doc_id}/files/{name}")
def meeting_file(doc_id: str, name: str, conn=Depends(get_conn)):
    _load_meeting(conn, doc_id)  # 404 для несуществующих встреч
    if name not in MEETING_FILES:
        raise HTTPException(status_code=404)
    path = db.archive_root() / doc_id / name
    if not path.exists():
        raise HTTPException(status_code=404)
    return FileResponse(path)


@app.get("/meetings/{doc_id}/attachments/{name}")
def meeting_attachment(doc_id: str, name: str, conn=Depends(get_conn)):
    _load_meeting(conn, doc_id)
    # имя должно существовать в таблице attachments — это и есть whitelist
    known = {a["file_name"] for a in db.meeting_attachments(conn, doc_id)}
    if name not in known or Path(name).name != name:
        raise HTTPException(status_code=404)
    path = db.archive_root() / doc_id / "Attachments" / name
    if not path.exists():
        raise HTTPException(status_code=404)
    return FileResponse(path)
