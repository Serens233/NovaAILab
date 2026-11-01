"""只在测试应用注册故障端点，正式应用不提供调试路由。"""

import socket
import threading
import time
from urllib.error import HTTPError
from urllib.request import ProxyHandler, build_opener

import pytest
import uvicorn
from fastapi import HTTPException
from fastapi.testclient import TestClient

from nova.errors import ApplicationError
from nova.main import create_app

SENTINEL = "SYNTHETIC_PRIVATE_VALUE_984132"


def test_expected_application_error(settings):
    app = create_app(settings)

    @app.get("/test-conflict")
    def conflict():
        raise ApplicationError("CONFLICT", "操作发生冲突", 409)

    with TestClient(app) as client:
        response = client.get("/test-conflict")
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "CONFLICT"
    assert response.json()["error"]["message"] == "操作发生冲突"
    assert response.json()["error"]["request_id"] == response.headers["X-Request-ID"]


@pytest.mark.parametrize(
    ("method", "path", "status", "code"),
    [("get", "/missing", 404, "NOT_FOUND"), ("post", "/health", 405, "METHOD_NOT_ALLOWED")],
)
def test_framework_errors_use_same_contract(settings, method, path, status, code):
    with TestClient(create_app(settings)) as client:
        response = getattr(client, method)(path)
    assert response.status_code == status
    assert set(response.json()["error"]) == {"code", "message", "request_id"}
    assert response.json()["error"]["code"] == code
    if status == 405:
        assert "GET" in response.headers["Allow"]


def test_request_validation_never_echoes_input(settings, capsys):
    app = create_app(settings)

    @app.get("/test-validation")
    def validate(count: int):
        return {"count": count}

    with TestClient(app) as client:
        response = client.get("/test-validation", params={"count": SENTINEL})
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"
    assert SENTINEL not in response.text
    assert SENTINEL not in capsys.readouterr().err


def test_http_exception_details_are_not_public(settings):
    app = create_app(settings)

    @app.get("/test-http")
    def fail():
        raise HTTPException(400, detail=SENTINEL)

    with TestClient(app) as client:
        response = client.get("/test-http")
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "BAD_REQUEST"
    assert SENTINEL not in response.text


def test_unexpected_error_keeps_diagnostics_without_raw_message(settings, capsys):
    app = create_app(settings)

    @app.get("/test-crash")
    def crash():
        raise RuntimeError(SENTINEL)

    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.get("/test-crash", params={"token": SENTINEL})
    output = capsys.readouterr().err
    assert response.status_code == 500
    assert response.json()["error"]["code"] == "INTERNAL_ERROR"
    assert response.json()["error"]["request_id"] == response.headers["X-Request-ID"]
    assert SENTINEL not in response.text + output
    assert "RuntimeError" in output
    assert '"frames"' in output
    assert '"request_id"' in output
    assert "Traceback" not in response.text


def test_real_uvicorn_error_log_does_not_leak_exception_or_query(settings, capsys):
    app = create_app(settings)

    @app.get("/test-server-crash")
    def crash():
        raise RuntimeError(SENTINEL)

    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    port = listener.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(app, log_config=None, access_log=False))
    thread = threading.Thread(target=server.run, kwargs={"sockets": [listener]}, daemon=True)
    thread.start()
    try:
        deadline = time.monotonic() + 10
        while not server.started and thread.is_alive() and time.monotonic() < deadline:
            thread.join(0.01)
        assert server.started
        opener = build_opener(ProxyHandler({}))
        with pytest.raises(HTTPError) as captured:
            opener.open(f"http://127.0.0.1:{port}/test-server-crash?token={SENTINEL}", timeout=5)
        try:
            assert captured.value.code == 500
            response = captured.value.read().decode("utf-8")
            assert "INTERNAL_ERROR" in response
        finally:
            captured.value.close()
    finally:
        server.should_exit = True
        thread.join(10)
        listener.close()
    assert not thread.is_alive()
    assert not app.state.ready
    output = capsys.readouterr().err
    assert SENTINEL not in output + response
    assert "Exception in ASGI application" in output
    assert "RuntimeError" in output
