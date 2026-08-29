"""Создание и запись консолидированной базы хаба.

Админка открывает базу строго read-only (`db.connect`) — это инвариант, который
здесь не нарушается: писать умеет только ingest, и только через соединения,
полученные из этого модуля.
"""

import errno
import fcntl
import os
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
SCHEMA_SQL = BASE_DIR / "schema.sql"
INGEST_SCHEMA_SQL = BASE_DIR / "ingest_schema.sql"

LOCK_NAME = ".ingest.lock"


def schema_paths() -> tuple[Path, Path]:
    return SCHEMA_SQL, INGEST_SCHEMA_SQL


def connect_rw(db_path: Path) -> sqlite3.Connection:
    """Соединение на запись. Единственный способ изменить базу хаба."""
    conn = sqlite3.connect(db_path, isolation_level=None, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    # Каскады из этого PRAGMA — рабочая часть слияния: удаление документа уносит
    # детей и заставляет сработать *_fts_delete триггеры, поэтому индекс поиска
    # не устаревает при повторной отправке встречи.
    conn.execute("PRAGMA busy_timeout = 10000")
    return conn


@contextmanager
def writer_lock(archive_root: Path, timeout: float = 5.0):
    """Единственный писатель на каталог архива.

    Внутрипроцессной блокировки мало: в контейнере может оказаться запущенный
    руками скрипт починки, работающий с тем же файлом.
    """
    archive_root.mkdir(parents=True, exist_ok=True)
    lock_path = archive_root / LOCK_NAME
    fd = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o644)
    deadline = time.monotonic() + timeout
    try:
        while True:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except OSError as exc:
                if exc.errno not in (errno.EACCES, errno.EAGAIN):
                    raise
                if time.monotonic() >= deadline:
                    raise TimeoutError(
                        f"Каталог архива занят другим писателем: {lock_path}"
                    ) from exc
                time.sleep(0.05)
        yield
    finally:
        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)


def _has_table(conn: sqlite3.Connection, name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (name,)
    ).fetchone()
    return row is not None


def ensure_schema(db_path: Path) -> bool:
    """Создаёт базу хаба, если её ещё нет. Возвращает True, если создал.

    Идемпотентно. Схема Summit накатывается только на пустую базу; собственные
    таблицы хаба — всегда, они объявлены через IF NOT EXISTS и догоняют базу,
    созданную предыдущей версией.
    """
    db_path = Path(db_path)
    with writer_lock(db_path.parent):
        created = False
        conn = connect_rw(db_path)
        try:
            if not _has_table(conn, "documents"):
                conn.executescript(SCHEMA_SQL.read_text(encoding="utf-8"))
                created = True
            conn.executescript(INGEST_SCHEMA_SQL.read_text(encoding="utf-8"))
            # WAL нужен, чтобы читающая админка не блокировалась на время слияния.
            conn.execute("PRAGMA journal_mode = WAL")
            conn.execute("PRAGMA synchronous = NORMAL")
        finally:
            conn.close()
        return created
