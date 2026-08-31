"""HTTP-уровень: авторизация ingest, роли, отключение /sync на сервере.

До этого набора HTTP-тестов в проекте не было вовсе, и обе проверки авторизации
оставались непокрытыми.
"""

import gzip
import importlib
import json

import pytest
from fastapi.testclient import TestClient

import bootstrap
from conftest import make_bundle

TOKENS = "mac-a:secret-a,mac-b:secret-b"
ADMIN = ("archivist", "very-long-password")


@pytest.fixture
def make_client(monkeypatch, tmp_path):
    """Клиент над свежеимпортированным приложением.

    app.py решает при импорте, регистрировать ли ingest и /sync, поэтому
    окружение выставляется до перезагрузки модуля.
    """
    def _make(*, role="server", admin=ADMIN, tokens=TOKENS):
        root = tmp_path / "hub"
        root.mkdir(exist_ok=True)
        bootstrap.ensure_schema(root / "database.sqlite")

        monkeypatch.setenv("MEET_ARCHIVE_ROLE", role)
        monkeypatch.setenv("SUMMIT_ARCHIVE_ROOT", str(root))
        monkeypatch.setenv("INGEST_TOKENS", tokens)
        for name, value in zip(("ADMIN_USER", "ADMIN_PASSWORD"), admin or ()):
            monkeypatch.setenv(name, value)
        if not admin:
            monkeypatch.delenv("ADMIN_USER", raising=False)
            monkeypatch.delenv("ADMIN_PASSWORD", raising=False)

        import app as app_mod
        importlib.reload(app_mod)
        return TestClient(app_mod.app), root

    return _make


def _post(client, payload, token="secret-a", gzipped=False):
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    if gzipped:
        body = gzip.compress(body)
        headers["Content-Encoding"] = "gzip"
    return client.post("/api/ingest/meeting", content=body, headers=headers)


# --- авторизация ------------------------------------------------------------


def test_ingest_requires_bearer_token(make_client):
    client, _ = make_client()
    assert client.post("/api/ingest/meeting", json=make_bundle("DOC-A")).status_code == 401
    assert _post(client, make_bundle("DOC-A"), token="wrong").status_code == 401
    assert _post(client, make_bundle("DOC-A")).status_code == 201


def test_ingest_is_not_blocked_by_basic_auth(make_client):
    """Зависимость Basic Auth висит на всём приложении.

    Без короткого замыкания по /api/ запрос с заголовком Bearer получал бы 401
    просто потому, что он не Basic, — и токены выглядели бы нерабочими.
    """
    client, _ = make_client()
    assert client.get("/", headers={"Authorization": "Bearer secret-a"}).status_code == 401
    assert _post(client, make_bundle("DOC-A")).status_code == 201


def test_browser_routes_require_basic_auth_in_server_role(make_client):
    client, _ = make_client()
    assert client.get("/").status_code == 401
    assert client.get("/", auth=ADMIN).status_code == 200


def test_server_role_does_not_fail_open_without_credentials(make_client):
    """Самая опасная строка проекта: пустые учётки не должны пускать всех."""
    client, _ = make_client(admin=None)
    assert client.get("/").status_code == 401


def test_device_id_comes_from_token_not_body(make_client):
    client, root = make_client()
    payload = make_bundle("DOC-A")
    payload["device_id"] = "mac-b"          # попытка представиться другим
    payload["device_label"] = "подделка"
    assert _post(client, payload, token="secret-a").status_code == 201

    conn = bootstrap.connect_rw(root / "database.sqlite")
    try:
        row = conn.execute(
            "SELECT device_id FROM ingest_documents WHERE document_id = 'DOC-A'"
        ).fetchone()
    finally:
        conn.close()
    assert row["device_id"] == "mac-a"


# --- роли -------------------------------------------------------------------


def test_sync_routes_absent_in_server_role(make_client):
    client, _ = make_client()
    assert client.get("/sync", auth=ADMIN).status_code == 404
    assert client.post("/sync/run", auth=ADMIN, data={"mode": "sync"}).status_code == 404


def test_sync_routes_present_in_local_role(make_client):
    client, _ = make_client(role="local")
    assert client.get("/sync").status_code != 404


def test_push_route_absent_in_server_role(make_client):
    """Кнопка отправки — локальная: на хабе отправлять некуда и нечем."""
    client, _ = make_client()
    assert client.post("/sync/push", auth=ADMIN, data={}).status_code == 404


def test_push_route_present_in_local_role(make_client):
    client, _ = make_client(role="local")
    assert client.post("/sync/push", data={}).status_code != 404


def test_hub_state_route_follows_the_same_role_rule(make_client):
    assert make_client()[0].get("/sync/hub.json", auth=ADMIN).status_code == 404
    assert make_client(role="local")[0].get("/sync/hub.json").status_code != 404


def test_health_needs_no_auth(make_client):
    client, _ = make_client()
    response = client.get("/api/ingest/health")
    assert response.status_code == 200
    assert response.json()["role"] == "server"


# --- приём ------------------------------------------------------------------


def test_state_reflects_ingested_documents(make_client):
    client, _ = make_client()
    _post(client, make_bundle("DOC-A", modified_date=150.0))
    state = client.get("/api/ingest/state", headers={"Authorization": "Bearer secret-a"})
    assert state.status_code == 200
    body = state.json()
    assert body["count"] == 1
    assert body["documents"]["DOC-A"]["modified_date"] == 150.0
    assert body["device_id"] == "mac-a"


def test_gzip_body_is_accepted(make_client):
    client, _ = make_client()
    assert _post(client, make_bundle("DOC-A"), gzipped=True).status_code == 201


def test_repush_reports_unchanged(make_client):
    client, _ = make_client()
    assert _post(client, make_bundle("DOC-A")).status_code == 201
    again = _post(client, make_bundle("DOC-A"))
    assert again.status_code == 200
    assert again.json()["status"] == "unchanged"


def test_schema_ahead_returns_409(make_client):
    client, _ = make_client()
    response = _post(client, make_bundle("DOC-A", migrations=["v99_future"]))
    assert response.status_code == 409
    assert response.json()["detail"]["error"] == "schema_ahead"


def test_denied_table_returns_400(make_client):
    client, _ = make_client()
    payload = make_bundle("DOC-A")
    payload["tables"]["documents_fts"] = []
    assert _post(client, payload).status_code == 400


def test_oversized_body_rejected(make_client, monkeypatch):
    monkeypatch.setenv("INGEST_MAX_BYTES", "500")
    client, _ = make_client()
    payload = make_bundle("DOC-A", segments=[(f"S{i}", "текст" * 20) for i in range(50)])
    assert _post(client, payload).status_code == 413


# --- markdown ---------------------------------------------------------------


UUID_A = "07DAE794-DCB7-4467-93C8-59AF69D170A8"


def test_document_md_is_written(make_client):
    client, root = make_client()
    _post(client, make_bundle(UUID_A))
    response = client.put(
        f"/api/ingest/meeting/{UUID_A}/document.md",
        content="# Заметки".encode("utf-8"),
        headers={"Authorization": "Bearer secret-a"},
    )
    assert response.status_code == 200
    assert (root / UUID_A / "document.md").read_text(encoding="utf-8") == "# Заметки"


def test_document_md_rejects_path_traversal(make_client):
    client, _ = make_client()
    response = client.put(
        "/api/ingest/meeting/..%2F..%2Fetc/document.md",
        content=b"x",
        headers={"Authorization": "Bearer secret-a"},
    )
    assert response.status_code in (400, 404)


def test_document_md_requires_known_meeting(make_client):
    client, _ = make_client()
    response = client.put(
        f"/api/ingest/meeting/{UUID_A}/document.md",
        content=b"x",
        headers={"Authorization": "Bearer secret-a"},
    )
    assert response.status_code == 404
