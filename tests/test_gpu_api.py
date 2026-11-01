"""受保护 GPU 接口离线验收，不调用真实模型或收费服务。"""

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr, ValidationError

from nova.config import Settings
from nova.gpu.backend import BackendError
from nova.main import create_app

TOKEN = "synthetic_gpu_token_for_offline_testing_only"
AUTH = {"Authorization": f"Bearer {TOKEN}"}


class FakeBackend:
    model = "qwen3.5:4b"

    def __init__(self):
        self.models = [{"name": self.model, "size_vram": 123}]
        self.calls = []
        self.unavailable = False

    async def get_model_status(self):
        if self.unavailable:
            raise BackendError("test service unavailable")
        return [dict(model) for model in self.models]

    async def get_gpu_memory_usage(self):
        return {"available": True, "gpus": [{"total_mib": 100, "used_mib": 10}]}

    async def unload_model(self, name):
        self.calls.append("unload")
        self.models = []
        return {"done": True}

    async def wake_model(self):
        self.calls.append("wake")
        self.models = [{"name": self.model, "size_vram": 123}]
        return {"done": True, "model": self.model, "load_duration": 42}

    async def generate(self, prompt):
        self.calls.append("generate")
        self.models = [{"name": self.model, "size_vram": 123}]
        return {"done": True, "model": self.model, "response": "合成测试回复"}


@pytest.fixture
def gpu_app(monkeypatch, tmp_path):
    backend = FakeBackend()
    monkeypatch.setattr("nova.main.OllamaBackend", lambda *args, **kwargs: backend)
    settings = Settings(
        _env_file=None,
        environment="test",
        gpu_enabled=True,
        gpu_token=SecretStr(TOKEN),
        data_dir=tmp_path,
        database_path=tmp_path / "nova.sqlite3",
    )
    return create_app(settings), backend


@pytest.mark.parametrize(
    ("method", "path", "payload"),
    [
        ("GET", "/api/gpu/status", None),
        ("POST", "/api/gpu/mode", {"mode": "GAMING"}),
        ("POST", "/api/gpu/unload", None),
        ("POST", "/api/gpu/wake", None),
        ("POST", "/api/gpu/generate", {"prompt": "合成问题"}),
    ],
)
def test_every_gpu_endpoint_requires_token(gpu_app, method, path, payload):
    app, backend = gpu_app
    with TestClient(app) as client:
        for headers in ({}, {"Authorization": "Bearer invalid_synthetic_token"}):
            response = client.request(method, path, json=payload, headers=headers)
            assert response.status_code == 401
            assert response.json()["error"]["code"] == "GPU_UNAUTHORIZED"
            assert TOKEN not in response.text
            assert "request_id" in response.json()["error"]
        assert backend.calls == []
        assert client.get("/health").status_code == 200


def test_gaming_blocks_generation_and_wake_but_keeps_database_healthy(gpu_app):
    app, backend = gpu_app
    with TestClient(app) as client:
        initial = client.get("/api/gpu/status", headers=AUTH)
        assert initial.status_code == 200
        assert initial.json()["training"]["state"] == "idle"
        assert initial.json()["game_detection"]["enabled"] is False
        assert initial.json()["gpu_memory"]["available"] is True
        gaming = client.post("/api/gpu/mode", json={"mode": "GAMING"}, headers=AUTH)
        assert gaming.status_code == 200
        assert gaming.json()["unload_verified"]
        assert backend.calls == ["unload"]
        for path, payload in (
            ("/api/gpu/generate", {"prompt": "游戏期间的合成问题"}),
            ("/api/gpu/wake", None),
        ):
            blocked = client.post(path, json=payload, headers=AUTH)
            assert blocked.status_code == 423
            assert blocked.json()["error"]["code"] == "GPU_GAMING"
        assert backend.calls == ["unload"]
        assert client.get("/health").json() == {"status": "ok", "database": "ok"}
        assert app.state.database.is_ready
        repeated = client.post("/api/gpu/mode", json={"mode": "GAMING"}, headers=AUTH)
        assert repeated.status_code == 200
        assert backend.calls == ["unload"]
        restored = client.post("/api/gpu/mode", json={"mode": "AUTO"}, headers=AUTH)
        assert restored.status_code == 200
        assert backend.calls == ["unload"]
        response = client.post("/api/gpu/generate", json={"prompt": "恢复后问题"}, headers=AUTH)
        assert response.status_code == 200
        assert response.json()["response"] == "合成测试回复"
        assert backend.calls == ["unload", "generate"]


def test_explicit_unload_then_auto_and_wake(gpu_app):
    app, backend = gpu_app
    with TestClient(app) as client:
        assert client.post("/api/gpu/unload", headers=AUTH).status_code == 200
        assert client.post("/api/gpu/wake", headers=AUTH).status_code == 423
        assert client.post("/api/gpu/mode", json={"mode": "AUTO"}, headers=AUTH).status_code == 200
        wake = client.post("/api/gpu/wake", headers=AUTH)
        assert wake.status_code == 200
        assert backend.calls == ["unload", "wake"]


def test_ollama_unavailable_is_unknown_not_empty_or_success(gpu_app):
    app, backend = gpu_app
    backend.unavailable = True
    with TestClient(app) as client:
        status = client.get("/api/gpu/status", headers=AUTH)
        assert status.status_code == 200
        assert status.json()["backend"]["available"] is False
        assert status.json()["backend"]["models"] is None
        gaming = client.post("/api/gpu/mode", json={"mode": "GAMING"}, headers=AUTH)
        assert gaming.status_code == 503
        assert client.get("/api/gpu/status", headers=AUTH).json()["local_requests_blocked"]
        assert client.get("/health").status_code == 200


@pytest.mark.parametrize("origin", ["https://example.invalid", "http://127.0.0.1:8000"])
def test_browser_origins_are_rejected_even_with_valid_token(gpu_app, origin):
    app, backend = gpu_app
    with TestClient(app) as client:
        response = client.post("/api/gpu/wake", headers={**AUTH, "Origin": origin})
        assert response.status_code == 403
        assert backend.calls == []


def test_remote_client_is_rejected_even_with_valid_token(gpu_app):
    app, backend = gpu_app
    with TestClient(app, client=("198.51.100.7", 50000)) as client:
        assert client.post("/api/gpu/wake", headers=AUTH).status_code == 403
        assert backend.calls == []


@pytest.mark.parametrize(
    ("path", "payload"),
    [
        ("/api/gpu/mode", {"mode": "AI_FOCUS"}),
        ("/api/gpu/mode", {"mode": "GAMING", "force": True}),
        ("/api/gpu/generate", {"prompt": "   "}),
        ("/api/gpu/generate", {"prompt": "x" * 8001}),
        ("/api/gpu/generate", {"prompt": "test", "model": "unapproved:large"}),
    ],
)
def test_invalid_controls_or_unapproved_models_never_reach_backend(gpu_app, path, payload):
    app, backend = gpu_app
    with TestClient(app) as client:
        assert client.post(path, json=payload, headers=AUTH).status_code == 422
        assert backend.calls == []


def test_disabled_manager_never_constructs_backend(monkeypatch, settings):
    def unexpected_backend(*args, **kwargs):
        pytest.fail("默认关闭 GPU 管理时不应构造模型客户端")

    monkeypatch.setattr("nova.main.OllamaBackend", unexpected_backend)
    with TestClient(create_app(settings)) as client:
        assert client.get("/api/gpu/status", headers=AUTH).status_code == 503
        assert client.get("/health").status_code == 200


@pytest.mark.parametrize(
    "changes",
    [
        {"gpu_token": SecretStr("")},
        {"gpu_token": SecretStr("short")},
        {"host": "0.0.0.0"},
        {"gpu_ollama_url": "https://example.invalid:443"},
        {"gpu_ollama_url": "http://127.0.0.1:11434/private"},
        {"gpu_ollama_url": "http://user:password@127.0.0.1:11434"},
    ],
)
def test_unsafe_gpu_configuration_is_rejected(changes):
    values = {"gpu_enabled": True, "gpu_token": SecretStr(TOKEN), **changes}
    with pytest.raises(ValidationError):
        Settings(_env_file=None, **values)


def test_gpu_token_is_excluded_from_dumps_and_is_available_for_redaction():
    settings = Settings(_env_file=None, gpu_enabled=True, gpu_token=SecretStr(TOKEN))
    assert TOKEN not in repr(settings)
    assert TOKEN not in settings.model_dump_json()
    assert "gpu_token" not in settings.model_dump()
    assert TOKEN in settings.secret_values()
