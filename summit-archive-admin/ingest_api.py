"""HTTP-приём встреч с устройств."""

import gzip
import json
import os
import re
import tempfile
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request, Response

import config
import db
import ingest
from auth import require_device

router = APIRouter(prefix="/api/ingest", tags=["ingest"])

# Имя каталога встречи — только UUID. Проверяется до построения пути, иначе
# `..` в идентификаторе увёл бы запись за пределы каталога архива.
UUID_RE = re.compile(r"\A[0-9A-Fa-f]{8}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}"
                     r"-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{12}\Z")

MAX_MARKDOWN_BYTES = 4 * 1024 * 1024


def _safe_meeting_dir(doc_id: str) -> Path:
    if not UUID_RE.match(doc_id):
        raise HTTPException(status_code=400, detail="Некорректный идентификатор встречи")
    return db.archive_root() / doc_id


async def _read_body(request: Request, limit: int) -> bytes:
    """Прочитать тело с ограничением до разбора.

    Starlette не распаковывает Content-Encoding сама, поэтому gzip снимаем здесь.
    Лимит проверяется по мере чтения — иначе тело произвольного размера успевало
    бы оказаться в памяти целиком.
    """
    chunks: list[bytes] = []
    size = 0
    async for chunk in request.stream():
        size += len(chunk)
        if size > limit:
            raise HTTPException(status_code=413, detail="Бандл слишком велик")
        chunks.append(chunk)
    body = b"".join(chunks)

    if request.headers.get("content-encoding", "").lower() == "gzip":
        try:
            body = gzip.decompress(body)
        except OSError as exc:
            raise HTTPException(status_code=400, detail="Тело не распаковывается") from exc
        if len(body) > limit * 8:
            raise HTTPException(status_code=413, detail="Бандл слишком велик")
    return body


@router.get("/health")
def health() -> dict:
    return {"status": "ok", "role": config.role()}


@router.get("/state")
def state(device_id: str = Depends(require_device)) -> dict:
    """Что у сервера уже есть — по всем устройствам, не только по спрашивающему.

    Именно полнота ответа делает состояние авторитетным: клиенту не нужен
    локальный watermark, а значит и часы разных машин не сравниваются.
    """
    conn = db.connect()
    try:
        payload = ingest.ingest_state(conn)
    finally:
        conn.close()
    payload["device_id"] = device_id
    return payload


@router.post("/meeting")
async def ingest_meeting(request: Request, device_id: str = Depends(require_device)):
    body = await _read_body(request, config.max_bundle_bytes())
    try:
        payload = json.loads(body)
    except (ValueError, UnicodeDecodeError) as exc:
        raise HTTPException(status_code=400, detail=f"Некорректный JSON: {exc}") from exc

    try:
        result = ingest.ingest_bundle(db.db_path(), device_id, payload)
    except ingest.SchemaAhead as exc:
        raise HTTPException(
            status_code=409,
            detail={"error": "schema_ahead", "message": str(exc), "unknown": exc.unknown},
        ) from exc
    except ingest.BundleError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except ingest.Busy as exc:
        raise HTTPException(
            status_code=503, detail=str(exc), headers={"Retry-After": "10"}
        ) from exc

    status = "unchanged" if result.unchanged else ("created" if result.created else "updated")
    return Response(
        content=json.dumps(
            {
                "status": status,
                "document_id": result.document_id,
                "counts": result.counts,
                "warnings": result.warnings,
            },
            ensure_ascii=False,
        ),
        media_type="application/json",
        status_code=201 if result.created else 200,
    )


@router.put("/meeting/{doc_id}/document.md")
async def put_document_md(
    doc_id: str, request: Request, device_id: str = Depends(require_device)
):
    """Markdown отдельным запросом.

    Запись файла всё равно не может быть атомарной вместе с транзакцией SQLite,
    а раздельная отправка означает, что повтор пересылает только маленькую часть.
    Здесь же — шов для второй фазы: обобщить путь до /files/{name}, и за ним
    можно поменять хранилище на объектное, не трогая схему.
    """
    meeting_dir = _safe_meeting_dir(doc_id)

    conn = db.connect()
    try:
        known = conn.execute(
            "SELECT 1 FROM documents WHERE id = ?", (doc_id,)
        ).fetchone()
    finally:
        conn.close()
    if known is None:
        raise HTTPException(status_code=404, detail="Встреча ещё не принята")

    body = await _read_body(request, MAX_MARKDOWN_BYTES)
    meeting_dir.mkdir(parents=True, exist_ok=True)
    target = meeting_dir / "document.md"

    fd, tmp_name = tempfile.mkstemp(dir=meeting_dir, prefix=".document.md.")
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(body)
        os.replace(tmp_name, target)
    except BaseException:
        Path(tmp_name).unlink(missing_ok=True)
        raise

    return {"status": "ok", "bytes": len(body), "device_id": device_id}
