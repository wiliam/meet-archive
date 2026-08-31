"""Панель хаба в локальной админке: конфигурация, дельта, запуск отправки."""

import importlib
import sqlite3

import pytest

import bootstrap
import hub
import sync as sync_mod


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for name in ("MEET_ARCHIVE_HUB_URL", "MEET_ARCHIVE_HUB_TOKEN", "MEET_ARCHIVE_ROLE"):
        monkeypatch.delenv(name, raising=False)


@pytest.fixture
def config_file(tmp_path, monkeypatch):
    """Подменяем путь к файлу конфигурации, чтобы не трогать настоящий ~/.config."""
    path = tmp_path / "hub.env"
    monkeypatch.setattr(hub, "CONFIG_FILE", path)
    return path


# --- конфигурация -----------------------------------------------------------


def test_missing_config_file_is_not_an_error(config_file):
    """Страница обязана открываться на машине, где хаб не настроен."""
    cfg = hub.hub_config()
    assert cfg["configured"] is False
    assert cfg["url"] == "" and cfg["token"] == ""
    assert str(config_file) in cfg["config_file"]


def test_config_is_read_from_file(config_file):
    config_file.write_text(
        "# комментарий\n"
        "MEET_ARCHIVE_DOMAIN=meet.example.com\n"
        "MEET_ARCHIVE_HUB_URL=https://meet.example.com/\n"
        "INGEST_TOKENS=mbp-main:secret-a\n",
        encoding="utf-8",
    )
    cfg = hub.hub_config()
    assert cfg["configured"] is True
    assert cfg["url"] == "https://meet.example.com"      # хвостовой слэш срезан
    assert cfg["token"] == "secret-a"


def test_first_pair_is_taken_from_a_multi_device_list(config_file):
    """В файле может лежать серверная форма со всеми устройствами."""
    config_file.write_text(
        "MEET_ARCHIVE_HUB_URL=https://h\nINGEST_TOKENS=mbp:aaa,mini:bbb\n",
        encoding="utf-8",
    )
    assert hub.hub_config()["token"] == "aaa"


def test_url_is_derived_from_the_domain(config_file):
    """Тот же файл описывает окружение сервера, где адрес задан доменом."""
    config_file.write_text(
        "MEET_ARCHIVE_DOMAIN=meet.example.com\nINGEST_TOKENS=d:t\n",
        encoding="utf-8")
    cfg = hub.hub_config()
    assert cfg["url"] == "https://meet.example.com"
    assert cfg["configured"] is True


def test_explicit_url_wins_over_the_domain(config_file):
    config_file.write_text(
        "MEET_ARCHIVE_DOMAIN=meet.example.com\n"
        "MEET_ARCHIVE_HUB_URL=http://localhost:8377\n"
        "INGEST_TOKENS=d:t\n",
        encoding="utf-8")
    assert hub.hub_config()["url"] == "http://localhost:8377"


def test_environment_wins_over_the_file(config_file, monkeypatch):
    config_file.write_text(
        "MEET_ARCHIVE_HUB_URL=https://from-file\nINGEST_TOKENS=d:from-file\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("MEET_ARCHIVE_HUB_URL", "https://from-env")
    monkeypatch.setenv("MEET_ARCHIVE_HUB_TOKEN", "from-env")
    cfg = hub.hub_config()
    assert cfg["url"] == "https://from-env" and cfg["token"] == "from-env"


def test_url_without_token_is_not_configured(config_file):
    config_file.write_text("MEET_ARCHIVE_HUB_URL=https://h\n", encoding="utf-8")
    assert hub.hub_config()["configured"] is False


# --- состояние хаба ---------------------------------------------------------


def test_unreachable_hub_returns_error_instead_of_raising(config_file):
    """Недоступный хаб не должен ронять страницу /sync."""
    config_file.write_text(
        "MEET_ARCHIVE_HUB_URL=http://127.0.0.1:9\nINGEST_TOKENS=d:t\n", encoding="utf-8"
    )
    state = hub.hub_state()
    assert "error" in state and "недоступен" in state["error"]


def test_unconfigured_hub_state_is_an_error_not_a_crash(config_file):
    assert hub.hub_state()["error"] == "Хаб не настроен"


# --- дельта -----------------------------------------------------------------


@pytest.fixture
def archive(tmp_path, monkeypatch):
    root = tmp_path / "archive"
    root.mkdir()
    bootstrap.ensure_schema(root / "database.sqlite")
    monkeypatch.setenv("SUMMIT_ARCHIVE_ROOT", str(root))
    conn = bootstrap.connect_rw(root / "database.sqlite")
    try:
        for doc_id, title, modified in (
            ("D1", "Уже на хабе", 100.0),
            ("D2", "Изменена после отправки", 300.0),
            ("D3", "Новая", 100.0),
            ("D4", "Удалённая", 100.0),
        ):
            conn.execute(
                "INSERT INTO documents (id,title,created_date,modified_date)"
                " VALUES (?,?,?,?)", (doc_id, title, 1.0, modified))
        conn.execute("UPDATE documents SET deleted_at = 1 WHERE id = 'D4'")
    finally:
        conn.close()
    return root


def test_delta_lists_new_and_changed_meetings(archive, config_file, monkeypatch):
    config_file.write_text(
        "MEET_ARCHIVE_HUB_URL=https://h\nINGEST_TOKENS=d:t\n", encoding="utf-8")
    monkeypatch.setattr(hub, "hub_state", lambda config=None: {
        "count": 2,
        "documents": {
            "D1": {"modified_date": 100.0},
            "D2": {"modified_date": 200.0},
        },
    })
    delta = hub.compute_delta()
    assert delta["archive_count"] == 3          # удалённая не считается
    assert delta["hub_count"] == 2
    by_id = {d["id"]: d["status"] for d in delta["to_push"]}
    assert by_id == {"D2": "изменена", "D3": "новая"}


def test_delta_reports_error_without_touching_the_archive(config_file):
    delta = hub.compute_delta()
    assert delta["error"] == "Хаб не настроен"
    assert delta["to_push"] == [] and delta["archive_count"] is None


# --- запуск отправки --------------------------------------------------------


def test_push_refuses_when_hub_is_not_configured(config_file):
    result = hub.run_push(dry_run=True)
    assert result["ok"] is False
    assert "не настроен" in result["output"]


def test_push_refuses_while_the_archiver_holds_the_lock(archive, config_file):
    """Push читает архив в mode=ro; параллельно с пишущим архиватором нельзя."""
    config_file.write_text(
        "MEET_ARCHIVE_HUB_URL=https://h\nINGEST_TOKENS=d:t\n", encoding="utf-8")
    assert sync_mod.RUN_LOCK.acquire(blocking=False)
    try:
        result = hub.run_push(dry_run=True)
    finally:
        sync_mod.RUN_LOCK.release()
    assert result["ok"] is False
    assert "уже запущена" in result["output"]


def test_push_never_puts_the_token_on_the_command_line(archive, config_file):
    """argv виден всей системе через ps — токен уходит только окружением."""
    config_file.write_text(
        "MEET_ARCHIVE_HUB_URL=http://127.0.0.1:9\nINGEST_TOKENS=d:top-secret\n",
        encoding="utf-8")
    result = hub.run_push(dry_run=True)
    assert "top-secret" not in result.get("cmd", "")
    assert "top-secret" not in result.get("output", "")


def test_lock_is_released_after_push(archive, config_file):
    config_file.write_text(
        "MEET_ARCHIVE_HUB_URL=http://127.0.0.1:9\nINGEST_TOKENS=d:t\n", encoding="utf-8")
    hub.run_push(dry_run=True)
    assert sync_mod.RUN_LOCK.acquire(blocking=False)
    sync_mod.RUN_LOCK.release()
