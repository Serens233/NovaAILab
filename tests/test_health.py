"""健康状态与外部可见信息边界。"""

from fastapi.testclient import TestClient

from nova.main import create_app
from nova.storage.database import DatabaseError


def test_health_ready(settings):
    with TestClient(create_app(settings)) as client:
        response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "database": "ok"}
    assert len(response.headers["X-Request-ID"]) == 32
    assert str(settings.database_path) not in response.text


def test_health_before_lifespan_does_not_create_database(settings):
    client = TestClient(create_app(settings), raise_server_exceptions=False)
    try:
        response = client.get("/health")
    finally:
        client.close()
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "SERVICE_UNAVAILABLE"
    assert not settings.database_path.exists()


def test_health_database_failure_is_safe(settings, monkeypatch):
    app = create_app(settings)

    def fail():
        raise DatabaseError("private database diagnostic")

    with TestClient(app) as client:
        monkeypatch.setattr(app.state.database, "probe", fail)
        response = client.get("/health")
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "DATABASE_UNAVAILABLE"
    assert "private database diagnostic" not in response.text


def test_request_ids_are_generated_and_not_trusted(settings):
    with TestClient(create_app(settings)) as client:
        first = client.get("/health", headers={"X-Request-ID": "untrusted"})
        second = client.get("/health")
    assert first.headers["X-Request-ID"] != "untrusted"
    assert first.headers["X-Request-ID"] != second.headers["X-Request-ID"]
