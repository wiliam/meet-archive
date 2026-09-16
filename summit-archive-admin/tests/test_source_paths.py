"""Integration tests for legacy and split Summit source layouts."""

import os
import plistlib
import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path

import bootstrap
import sync as sync_mod


SCRIPT = Path(__file__).resolve().parents[2] / "archive_summit_meetings.py"
WRAPPER = Path(__file__).resolve().parents[2] / "summit_archive.sh"
SETUP = Path(__file__).resolve().parents[2] / "setup.sh"


def make_live_database(path: Path, document_id: str) -> None:
    bootstrap.ensure_schema(path)
    conn = sqlite3.connect(path)
    try:
        conn.execute(
            "INSERT INTO documents (id, title, created_date, modified_date) "
            "VALUES (?, ?, ?, ?)",
            (document_id, "Synthetic meeting", 100.0, 200.0),
        )
        conn.commit()
    finally:
        conn.close()


def test_cli_syncs_database_and_meeting_files_from_separate_roots(tmp_path: Path):
    """A separate DB path must not change where meeting files are read from."""
    meetings_root = tmp_path / "meetings"
    database_root = tmp_path / "database"
    archive_root = tmp_path / "archive"
    meetings_root.mkdir()
    database_root.mkdir()

    document_id = "11111111-1111-1111-1111-111111111111"
    source_db = database_root / "database.sqlite"
    make_live_database(source_db, document_id)
    meeting_dir = meetings_root / document_id
    meeting_dir.mkdir()
    (meeting_dir / "microphone.m4a").write_bytes(b"synthetic audio")

    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--source-root",
            str(meetings_root),
            "--source-db",
            str(source_db),
            "--archive-root",
            str(archive_root),
            "--mode",
            "sync",
        ],
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    archived = sqlite3.connect(archive_root / "database.sqlite")
    try:
        assert archived.execute("SELECT id FROM documents").fetchall() == [
            (document_id,)
        ]
    finally:
        archived.close()
    assert (archive_root / document_id / "microphone.m4a").read_bytes() == b"synthetic audio"


def test_cli_keeps_legacy_database_inside_source_root_compatible(tmp_path: Path):
    source_root = tmp_path / "summit"
    archive_root = tmp_path / "archive"
    source_root.mkdir()
    document_id = "66666666-6666-6666-6666-666666666666"
    make_live_database(source_root / "database.sqlite", document_id)

    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--source-root",
            str(source_root),
            "--archive-root",
            str(archive_root),
            "--mode",
            "sync",
        ],
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    archived = sqlite3.connect(archive_root / "database.sqlite")
    try:
        assert archived.execute("SELECT id FROM documents").fetchall() == [
            (document_id,)
        ]
    finally:
        archived.close()


def test_cli_prunes_split_layout_and_backs_up_next_to_database(tmp_path: Path):
    meetings_root = tmp_path / "meetings"
    database_root = tmp_path / "database"
    archive_root = tmp_path / "archive"
    meetings_root.mkdir()
    database_root.mkdir()

    document_id = "55555555-5555-5555-5555-555555555555"
    source_db = database_root / "database.sqlite"
    make_live_database(source_db, document_id)
    meeting_dir = meetings_root / document_id
    meeting_dir.mkdir()
    (meeting_dir / "system.m4a").write_bytes(b"synthetic system audio")
    common_args = [
        sys.executable,
        str(SCRIPT),
        "--source-root",
        str(meetings_root),
        "--source-db",
        str(source_db),
        "--archive-root",
        str(archive_root),
    ]

    synced = subprocess.run(
        [*common_args, "--mode", "sync"],
        capture_output=True,
        text=True,
    )
    assert synced.returncode == 0, synced.stdout + synced.stderr

    pruned = subprocess.run(
        [*common_args, "--mode", "prune", "--keep-active", "0"],
        capture_output=True,
        text=True,
    )

    assert pruned.returncode == 0, pruned.stdout + pruned.stderr
    source = sqlite3.connect(source_db)
    try:
        assert source.execute("SELECT count(*) FROM documents").fetchone()[0] == 0
    finally:
        source.close()
    assert not meeting_dir.exists()
    assert (archive_root / document_id / "system.m4a").read_bytes() == b"synthetic system audio"
    assert len(list(database_root.glob("database-backup-*-before-archive.sqlite"))) == 1
    assert list(meetings_root.glob("database-backup-*-before-archive.sqlite")) == []


def test_admin_sync_passes_separate_database_to_archiver(tmp_path: Path, monkeypatch):
    meetings_root = tmp_path / "meetings"
    database_root = tmp_path / "database"
    archive_root = tmp_path / "archive"
    meetings_root.mkdir()
    database_root.mkdir()

    document_id = "22222222-2222-2222-2222-222222222222"
    source_db = database_root / "database.sqlite"
    make_live_database(source_db, document_id)
    (meetings_root / document_id).mkdir()

    monkeypatch.setenv("SUMMIT_SOURCE_ROOT", str(meetings_root))
    monkeypatch.setenv("SUMMIT_SOURCE_DB", str(source_db))
    monkeypatch.setenv("SUMMIT_ARCHIVE_ROOT", str(archive_root))

    result = sync_mod.run_script("sync", None, dry_run=False)

    assert result["ok"], result["output"]
    archived = sqlite3.connect(archive_root / "database.sqlite")
    try:
        assert archived.execute("SELECT id FROM documents").fetchall() == [
            (document_id,)
        ]
    finally:
        archived.close()


def test_shell_wrapper_passes_source_database_environment_variable(tmp_path: Path):
    meetings_root = tmp_path / "meetings"
    database_root = tmp_path / "database"
    archive_root = tmp_path / "archive"
    meetings_root.mkdir()
    database_root.mkdir()
    source_db = database_root / "database.sqlite"
    make_live_database(source_db, "33333333-3333-3333-3333-333333333333")

    env = os.environ.copy()
    env.update(
        {
            "SUMMIT_SOURCE_ROOT": str(meetings_root),
            "SUMMIT_SOURCE_DB": str(source_db),
            "SUMMIT_ARCHIVE_ROOT": str(archive_root),
        }
    )
    result = subprocess.run(
        [str(WRAPPER), "dry-run"],
        capture_output=True,
        text=True,
        env=env,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert "Dry run only; no changes made." in result.stdout


def test_shell_wrapper_discovers_sibling_summit_db_directory(tmp_path: Path):
    repo_root = tmp_path / "repo"
    meetings_root = repo_root / "summit"
    database_root = repo_root / "summit-db"
    repo_root.mkdir()
    meetings_root.mkdir()
    database_root.mkdir()
    shutil.copy2(WRAPPER, repo_root / "summit_archive.sh")
    shutil.copy2(SCRIPT, repo_root / "archive_summit_meetings.py")
    make_live_database(
        database_root / "database.sqlite",
        "44444444-4444-4444-4444-444444444444",
    )

    env = os.environ.copy()
    env.pop("SUMMIT_SOURCE_ROOT", None)
    env.pop("SUMMIT_SOURCE_DB", None)
    env.pop("SUMMIT_ARCHIVE_ROOT", None)
    result = subprocess.run(
        [str(repo_root / "summit_archive.sh"), "dry-run"],
        capture_output=True,
        text=True,
        env=env,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert "Dry run only; no changes made." in result.stdout


def test_shell_wrapper_prefers_legacy_source_db_over_stale_summit_db(tmp_path: Path):
    repo_root = tmp_path / "repo"
    source_root = repo_root / "summit"
    split_db_root = repo_root / "summit-db"
    repo_root.mkdir()
    source_root.mkdir()
    split_db_root.mkdir()
    shutil.copy2(WRAPPER, repo_root / "summit_archive.sh")
    shutil.copy2(SCRIPT, repo_root / "archive_summit_meetings.py")
    make_live_database(
        source_root / "database.sqlite",
        "77777777-7777-7777-7777-777777777777",
    )
    make_live_database(
        split_db_root / "database.sqlite",
        "88888888-8888-8888-8888-888888888888",
    )

    env = os.environ.copy()
    env.pop("SUMMIT_SOURCE_ROOT", None)
    env.pop("SUMMIT_SOURCE_DB", None)
    env.pop("SUMMIT_ARCHIVE_ROOT", None)
    result = subprocess.run(
        [str(repo_root / "summit_archive.sh"), "dry-run"],
        capture_output=True,
        text=True,
        env=env,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert "77777777-7777-7777-7777-777777777777" in result.stdout
    assert "88888888-8888-8888-8888-888888888888" not in result.stdout


def test_setup_links_separate_meetings_and_database_directories(tmp_path: Path):
    repo_root = tmp_path / "repo"
    meetings_root = tmp_path / "meetings"
    database_root = tmp_path / "database"
    fake_app = tmp_path / "Summit AI Notes.app"
    bin_dir = repo_root / "summit-archive-admin" / ".venv" / "bin"
    repo_root.mkdir()
    meetings_root.mkdir()
    database_root.mkdir()
    fake_app.mkdir()
    bin_dir.mkdir(parents=True)
    shutil.copy2(SETUP, repo_root / "setup.sh")
    (database_root / "database.sqlite").write_bytes(b"test database")
    (repo_root / "summit-archive-admin" / "requirements.txt").write_text("")
    (repo_root / "summit_archive.sh").write_text("#!/bin/bash\nexit 0\n")
    (repo_root / "summit_archive.sh").chmod(0o755)
    (bin_dir / "pip").write_text("#!/bin/bash\nexit 0\n")
    (bin_dir / "pip").chmod(0o755)
    (bin_dir / "python").write_text("#!/bin/bash\necho 'fastapi test'\n")
    (bin_dir / "python").chmod(0o755)

    env = os.environ.copy()
    env["MEET_ARCHIVE_APP_PATH"] = str(fake_app)
    result = subprocess.run(
        [
            str(repo_root / "setup.sh"),
            "--source",
            str(meetings_root),
            "--source-db",
            str(database_root / "database.sqlite"),
        ],
        capture_output=True,
        text=True,
        env=env,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert (repo_root / "summit").resolve() == meetings_root.resolve()
    assert (repo_root / "summit-db").resolve() == database_root.resolve()


def test_setup_removes_stale_summit_db_symlink_for_explicit_legacy_source(
    tmp_path: Path,
):
    repo_root = tmp_path / "repo"
    source_root = tmp_path / "legacy-summit"
    stale_db_root = tmp_path / "stale-database"
    fake_app = tmp_path / "Summit AI Notes.app"
    bin_dir = repo_root / "summit-archive-admin" / ".venv" / "bin"
    repo_root.mkdir()
    source_root.mkdir()
    stale_db_root.mkdir()
    fake_app.mkdir()
    bin_dir.mkdir(parents=True)
    shutil.copy2(SETUP, repo_root / "setup.sh")
    (source_root / "database.sqlite").write_bytes(b"legacy database")
    (stale_db_root / "database.sqlite").write_bytes(b"stale database")
    (repo_root / "summit-db").symlink_to(stale_db_root, target_is_directory=True)
    (repo_root / "summit-archive-admin" / "requirements.txt").write_text("")
    (repo_root / "summit_archive.sh").write_text("#!/bin/bash\nexit 0\n")
    (repo_root / "summit_archive.sh").chmod(0o755)
    (bin_dir / "pip").write_text("#!/bin/bash\nexit 0\n")
    (bin_dir / "pip").chmod(0o755)
    (bin_dir / "python").write_text("#!/bin/bash\necho 'fastapi test'\n")
    (bin_dir / "python").chmod(0o755)

    env = os.environ.copy()
    env["MEET_ARCHIVE_APP_PATH"] = str(fake_app)
    result = subprocess.run(
        [str(repo_root / "setup.sh"), "--source", str(source_root)],
        capture_output=True,
        text=True,
        env=env,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert (repo_root / "summit").resolve() == source_root.resolve()
    assert not (repo_root / "summit-db").exists()
    assert not (repo_root / "summit-db").is_symlink()


def test_setup_discovers_modern_summit_layout(tmp_path: Path):
    repo_root = tmp_path / "repo"
    meetings_root = tmp_path / "meetings"
    database_root = tmp_path / "database"
    fake_app = tmp_path / "Summit AI Notes.app"
    preferences = tmp_path / "app.summitnotes.plist"
    bin_dir = repo_root / "summit-archive-admin" / ".venv" / "bin"
    repo_root.mkdir()
    meetings_root.mkdir()
    database_root.mkdir()
    fake_app.mkdir()
    bin_dir.mkdir(parents=True)
    shutil.copy2(SETUP, repo_root / "setup.sh")
    (database_root / "database.sqlite").write_bytes(b"test database")
    with preferences.open("wb") as handle:
        plistlib.dump({"audioStorageBookmarkPath": str(meetings_root)}, handle)
    (repo_root / "summit-archive-admin" / "requirements.txt").write_text("")
    (repo_root / "summit_archive.sh").write_text("#!/bin/bash\nexit 0\n")
    (repo_root / "summit_archive.sh").chmod(0o755)
    (bin_dir / "pip").write_text("#!/bin/bash\nexit 0\n")
    (bin_dir / "pip").chmod(0o755)
    (bin_dir / "python").write_text("#!/bin/bash\necho 'fastapi test'\n")
    (bin_dir / "python").chmod(0o755)

    env = os.environ.copy()
    env.update(
        {
            "MEET_ARCHIVE_APP_PATH": str(fake_app),
            "MEET_ARCHIVE_DEFAULT_STORAGE": str(tmp_path / "missing-legacy-storage"),
            "MEET_ARCHIVE_DEFAULT_DATABASE": str(database_root / "database.sqlite"),
            "MEET_ARCHIVE_PREFERENCES": str(preferences),
        }
    )
    result = subprocess.run(
        [str(repo_root / "setup.sh")],
        capture_output=True,
        text=True,
        env=env,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert (repo_root / "summit").resolve() == meetings_root.resolve()
    assert (repo_root / "summit-db").resolve() == database_root.resolve()
