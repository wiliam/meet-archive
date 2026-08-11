"""Дельта синхронизации и запуск archive_summit_meetings.py из админки.

Живая и архивная базы читаются read-only; изменения делает только сам скрипт
переноса, запускаемый как подпроцесс.
"""

import json
import os
import sqlite3
import subprocess
import sys
import threading
from pathlib import Path

import db

_run_lock = threading.Lock()


def source_root() -> Path:
    default = db.archive_root().parent / "summit"
    return Path(os.environ.get("SUMMIT_SOURCE_ROOT", default))


def script_path() -> Path:
    """Скрипт переноса лежит в корне репозитория, рядом с summit-archive-admin.

    Fallback на source_root оставлен для старых установок, где скрипт положили
    прямо в папку живой базы Summit.
    """
    repo_copy = Path(__file__).resolve().parent.parent / "archive_summit_meetings.py"
    if repo_copy.exists():
        return repo_copy
    return source_root() / "archive_summit_meetings.py"


def load_state() -> dict:
    state_file = db.archive_root() / "archive-state.json"
    if not state_file.exists():
        return {}
    return json.loads(state_file.read_text(encoding="utf-8"))


def _connect_ro(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def compute_delta(keep_active: int) -> dict:
    """Повторяет селекторы скрипта переноса (read-only) для предпросмотра."""
    src_db = source_root() / "database.sqlite"
    if not src_db.exists():
        return {"error": f"Живая база не найдена: {src_db}"}

    state = load_state()
    watermark = state.get("last_successful_sync_at")

    src = _connect_ro(src_db)
    arc = db.connect()
    try:
        archive_ids = {
            r["id"] for r in arc.execute("SELECT id FROM documents").fetchall()
        }

        active = src.execute(
            """SELECT id, title, created_date, modified_date
               FROM documents WHERE deleted_at IS NULL
               ORDER BY modified_date DESC, created_date DESC, id DESC"""
        ).fetchall()

        to_sync = []
        for row in active:
            is_new = row["id"] not in archive_ids
            is_changed = watermark is None or float(row["modified_date"]) > float(watermark)
            if is_new or is_changed:
                to_sync.append(
                    {
                        "id": row["id"],
                        "title": row["title"],
                        "created_date": row["created_date"],
                        "status": "новая" if is_new else "изменена",
                    }
                )

        # prune: самые старые сверх keep_active; считаем, что to_sync окажется
        # в архиве после синка (режим sync-and-prune синкает перед prune)
        will_be_archived = archive_ids | {d["id"] for d in to_sync}
        overflow = max(0, len(active) - keep_active)
        to_prune, unsafe = [], []
        if overflow:
            oldest = src.execute(
                """SELECT id, title, created_date FROM documents
                   WHERE deleted_at IS NULL ORDER BY created_date ASC, id ASC"""
            ).fetchall()
            for row in oldest:
                if len(to_prune) >= overflow:
                    break
                item = {
                    "id": row["id"],
                    "title": row["title"],
                    "created_date": row["created_date"],
                }
                (to_prune if row["id"] in will_be_archived else unsafe).append(item)

        return {
            "error": None,
            "active_count": len(active),
            "archive_count": len(archive_ids),
            "watermark": watermark,
            "to_sync": to_sync,
            "to_prune": to_prune,
            "unsafe": unsafe,
            "overflow": overflow,
        }
    finally:
        src.close()
        arc.close()


def run_script(mode: str, keep_active: int | None, dry_run: bool) -> dict:
    if mode not in ("sync", "prune", "sync-and-prune"):
        return {"ok": False, "output": f"Неизвестный режим: {mode}"}
    if mode != "sync" and (not keep_active or keep_active < 1):
        return {"ok": False, "output": "Для prune нужен keep-active ≥ 1"}
    if not script_path().exists():
        return {"ok": False, "output": f"Скрипт не найден: {script_path()}"}

    cmd = [
        sys.executable,
        str(script_path()),
        "--source-root",
        str(source_root()),
        "--archive-root",
        str(db.archive_root()),
        "--mode",
        mode,
    ]
    if mode != "sync":
        cmd += ["--keep-active", str(keep_active)]
    if dry_run:
        cmd.append("--dry-run")

    if not _run_lock.acquire(blocking=False):
        return {"ok": False, "output": "Синхронизация уже запущена, подождите."}
    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True, timeout=900
        )
        output = (proc.stdout + ("\n" + proc.stderr if proc.stderr else "")).strip()
        return {
            "ok": proc.returncode == 0,
            "output": output or "(скрипт не вывел ничего)",
            "cmd": " ".join(cmd),
        }
    except subprocess.TimeoutExpired:
        return {"ok": False, "output": "Скрипт не завершился за 15 минут — прерван."}
    finally:
        _run_lock.release()
