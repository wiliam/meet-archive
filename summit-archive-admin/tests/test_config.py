"""Проверки при старте и разбор токенов устройств."""

import pytest

import auth
import config


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for name in ("MEET_ARCHIVE_ROLE", "ADMIN_USER", "ADMIN_PASSWORD",
                 "INGEST_TOKENS", "SUMMIT_ARCHIVE_ROOT"):
        monkeypatch.delenv(name, raising=False)


def _server(monkeypatch, **overrides):
    env = {
        "MEET_ARCHIVE_ROLE": "server",
        "ADMIN_USER": "archivist",
        "ADMIN_PASSWORD": "very-long-password",
        "INGEST_TOKENS": "mac-a:secret-a",
        "SUMMIT_ARCHIVE_ROOT": "/data",
    }
    env.update(overrides)
    for key, value in env.items():
        if value is None:
            monkeypatch.delenv(key, raising=False)
        else:
            monkeypatch.setenv(key, value)


def test_local_role_needs_no_configuration():
    """Локальный запуск обязан остаться нулевой конфигурацией."""
    assert config.role() == "local"
    assert config.is_server() is False
    config.check_startup()


def test_server_role_is_fully_configured(monkeypatch):
    _server(monkeypatch)
    config.check_startup()


@pytest.mark.parametrize("missing", ["ADMIN_USER", "ADMIN_PASSWORD",
                                     "INGEST_TOKENS", "SUMMIT_ARCHIVE_ROOT"])
def test_server_role_refuses_to_boot_without(monkeypatch, missing):
    _server(monkeypatch, **{missing: None})
    with pytest.raises(RuntimeError) as exc:
        config.check_startup()
    assert missing in str(exc.value)


def test_server_role_rejects_short_password(monkeypatch):
    _server(monkeypatch, ADMIN_PASSWORD="short")
    with pytest.raises(RuntimeError, match="ADMIN_PASSWORD"):
        config.check_startup()


def test_server_role_rejects_unparseable_tokens(monkeypatch):
    _server(monkeypatch, INGEST_TOKENS="без-двоеточия")
    with pytest.raises(RuntimeError, match="INGEST_TOKENS"):
        config.check_startup()


def test_tokens_are_parsed_into_device_ids(monkeypatch):
    monkeypatch.setenv("INGEST_TOKENS", " mac-a:secret-a , mac-b:secret-b ,, bad ")
    assert sorted(auth.load_device_tokens().values()) == ["mac-a", "mac-b"]


def test_tokens_are_stored_hashed(monkeypatch):
    """В памяти лежит хэш, а не сам секрет."""
    monkeypatch.setenv("INGEST_TOKENS", "mac-a:secret-a")
    assert "secret-a" not in "".join(auth.load_device_tokens())
