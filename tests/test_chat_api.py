"""V2真实路由/生命周期/GPU控制器集成；模型与云端全部为进程内替身。"""

import asyncio
import json
from concurrent.futures import ThreadPoolExecutor
from threading import Event
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from nova.config import Settings
from nova.gpu.backend import BackendError
from nova.main import create_app

TOKEN = "SYNTHETIC_V2_TEST_TOKEN_ONLY_01234567890123456789"
AUTH = {"Authorization": "Bearer " + TOKEN}


class ChatBackend:
    model = "qwen3.5:4b"

    def __init__(self):
        self.base_url = "synthetic-chat-" + str(uuid4())
        self.installed = ["qwen3.5:9b", "qwen3.5:4b"]
        self.loaded = []
        self.stream_calls = []
        self.unload_calls = []
        self.fail_stream = False
        self.available = True
        self.free_mib = 15311
        self.entered = Event()
        self.release = Event()
        self.wait_for_release = False

    async def list_installed_models(self):
        if not self.available:
            raise BackendError("SYNTHETIC_PRIVATE_SERVICE_ERROR")
        return list(self.installed)

    async def ensure_local_model(self, model):
        if model not in self.installed:
            raise BackendError("SYNTHETIC_PRIVATE_MISSING_MODEL")

    async def get_model_status(self):
        return [dict(item) for item in self.loaded]

    async def get_gpu_memory_usage(self):
        return {
            "available": True,
            "gpus": [{"index": 0, "total_mib": 16311, "used_mib": 16311 - self.free_mib}],
        }

    async def unload_model(self, model):
        self.unload_calls.append(model)
        self.loaded = [item for item in self.loaded if item["name"] != model]
        return {"done": True}

    async def stream_chat(self, model, messages, on_delta):
        self.stream_calls.append({"model": model, "messages": [dict(item) for item in messages]})
        self.loaded = [{"name": model, "size_vram": 6000 * 1024 * 1024}]
        on_delta("你好")
        self.entered.set()
        if self.wait_for_release:
            if not await asyncio.to_thread(self.release.wait, 10):
                raise BackendError("SYNTHETIC_TEST_RELEASE_TIMEOUT")
        await asyncio.sleep(0)
        if self.fail_stream:
            raise BackendError("SYNTHETIC_PRIVATE_UPSTREAM_ERROR")
        on_delta("，Nova。")
        return {
            "model": model,
            "response": "你好，Nova。",
            "done": True,
            "stats": {"load_duration": 42},
        }


@pytest.fixture
def app_factory(monkeypatch, tmp_path):
    backend = ChatBackend()
    monkeypatch.setattr("nova.main.OllamaBackend", lambda *args, **kwargs: backend)

    def forbid_cloud(*args, **kwargs):
        raise AssertionError("离线测试不允许发出收费或外部网络请求")

    monkeypatch.setattr("nova.chat.cloud.http.client.HTTPSConnection", forbid_cloud)

    def create(**overrides):
        values = {
            "_env_file": None,
            "environment": "test",
            "chat_enabled": True,
            "gpu_enabled": True,
            "gpu_token": SecretStr(TOKEN),
            "data_dir": tmp_path / "state",
            "database_path": tmp_path / "data" / "nova.sqlite3",
        }
        values.update(overrides)
        return create_app(Settings(**values))

    return create, backend


def new_thread(client, **payload):
    response = client.post("/api/v1/threads", json=payload, headers=AUTH)
    assert response.status_code == 201
    return response.json()


def chat_payload(identity, **values):
    result = {
        "thread_id": identity,
        "model": "qwen3.5:9b",
        "content": "合成的中文入门问题",
        "request_id": str(uuid4()),
    }
    result.update(values)
    return result


def events(response):
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert response.headers["cache-control"] == "no-cache, no-store"
    assert response.headers["x-accel-buffering"] == "no"
    return [
        json.loads(line[5:].strip()) for line in response.iter_lines() if line.startswith("data:")
    ]


def test_stream_deltas_persist_two_messages_and_expose_metadata(app_factory):
    create, backend = app_factory
    with TestClient(create()) as client:
        thread = new_thread(client)
        response = client.post("/api/v1/chat", json=chat_payload(thread["id"]), headers=AUTH)
        stream = events(response)
        assert [item["type"] for item in stream] == ["meta", "delta", "delta", "done"]
        assert [item["text"] for item in stream if item["type"] == "delta"] == ["你好", "，Nova。"]
        assert stream[0]["thread_id"] == thread["id"]
        assert stream[0]["user_message_id"] != stream[0]["assistant_message_id"]
        assert stream[-1]["message"]["content"] == "你好，Nova。"
        history = client.get("/api/v1/threads/" + thread["id"], headers=AUTH).json()
        assert len(history["messages"]) == 2
        assert [message["status"] for message in history["messages"]] == ["complete", "complete"]
        assert history["messages"][1] == stream[-1]["message"]
        assert history["thread"]["title"] == "合成的中文入门问题"
        assert len(backend.stream_calls) == 1
        assert backend.stream_calls[0]["model"] == "qwen3.5:9b"
        assert backend.stream_calls[0]["messages"][-1] == {
            "role": "user",
            "content": "合成的中文入门问题",
        }


def test_history_survives_app_restart_and_same_request_replays_without_inference(app_factory):
    create, backend = app_factory
    with TestClient(create()) as client:
        thread = new_thread(client, title="保留的会话")
        payload = chat_payload(thread["id"])
        original = events(client.post("/api/v1/chat", json=payload, headers=AUTH))[-1]["message"]
    with TestClient(create()) as client:
        listed = client.get("/api/v1/threads", headers=AUTH).json()["threads"]
        assert listed[0]["id"] == thread["id"]
        assert listed[0]["title"] == "保留的会话"
        replay = events(client.post("/api/v1/chat", json=payload, headers=AUTH))
        assert replay[-1]["message"] == original
        assert len(backend.stream_calls) == 1
        assert (
            len(client.get("/api/v1/threads/" + thread["id"], headers=AUTH).json()["messages"]) == 2
        )


def test_model_switch_uses_same_gpu_manager_and_preserves_prior_context(app_factory):
    create, backend = app_factory
    app = create()
    with TestClient(app) as client:
        thread = new_thread(client)
        events(client.post("/api/v1/chat", json=chat_payload(thread["id"]), headers=AUTH))
        second = chat_payload(thread["id"], model="qwen3.5:4b", content="换小模型继续")
        events(client.post("/api/v1/chat", json=second, headers=AUTH))
        assert [call["model"] for call in backend.stream_calls] == ["qwen3.5:9b", "qwen3.5:4b"]
        assert backend.unload_calls == ["qwen3.5:9b"]
        assert app.state.gpu_manager.manual_gaming is False
        assert app.state.gpu_manager.mode == "AUTO"
        assert backend.stream_calls[1]["messages"][-3:] == [
            {"role": "user", "content": "合成的中文入门问题"},
            {"role": "assistant", "content": "你好，Nova。"},
            {"role": "user", "content": "换小模型继续"},
        ]
        history = client.get("/api/v1/threads/" + thread["id"], headers=AUTH).json()
        assert len(history["messages"]) == 4
        assert history["thread"]["model"] == "qwen3.5:4b"


def test_gaming_rejects_chat_without_loading_and_auto_recovers_on_demand(app_factory):
    create, backend = app_factory
    with TestClient(create()) as client:
        thread = new_thread(client)
        assert (
            client.post("/api/gpu/mode", json={"mode": "GAMING"}, headers=AUTH).status_code == 200
        )
        blocked = client.post("/api/v1/chat", json=chat_payload(thread["id"]), headers=AUTH)
        assert blocked.status_code == 423
        assert blocked.json()["error"]["code"] == "GPU_GAMING"
        assert backend.stream_calls == []
        assert backend.loaded == []
        assert client.get("/health").status_code == 200
        assert client.post("/api/gpu/mode", json={"mode": "AUTO"}, headers=AUTH).status_code == 200
        assert backend.loaded == []
        events(client.post("/api/v1/chat", json=chat_payload(thread["id"]), headers=AUTH))
        assert len(backend.stream_calls) == 1


def test_models_list_is_safe_and_does_not_load_models(app_factory):
    create, backend = app_factory
    with TestClient(create()) as client:
        response = client.get("/api/v1/models", headers=AUTH)
        assert response.status_code == 200
        body = response.json()
        assert body["default_model"] == "qwen3.5:9b"
        models = {model["id"]: model for model in body["models"]}
        experimental = "hf.co/unsloth/Qwen3.8-27B-GGUF:UD-Q3_K_XL"
        assert set(models) == {"qwen3.5:9b", "qwen3.5:4b", "deepseek", experimental}
        assert models["qwen3.5:9b"]["available"] is True
        assert models["qwen3.5:4b"]["available"] is True
        assert models[experimental]["available"] is False
        assert models[experimental]["enabled"] is False
        assert "关闭" in models[experimental]["reason"]
        assert (
            next(model for model in body["models"] if model["id"] == "deepseek")["available"]
            is False
        )
        assert TOKEN not in response.text
        assert backend.stream_calls == []
        assert backend.loaded == []


def test_registry_ollama_unavailable_returns_unavailable_entries_without_fake_install(app_factory):
    create, backend = app_factory
    backend.available = False
    with TestClient(create()) as client:
        response = client.get("/api/v1/models", headers=AUTH)
        assert response.status_code == 200
        locals_ = [entry for entry in response.json()["models"] if entry["provider"] == "ollama"]
        assert all(not entry["available"] for entry in locals_)
        assert "SYNTHETIC_PRIVATE" not in response.text
        assert backend.stream_calls == []


@pytest.mark.parametrize("consent", [False, True])
def test_unconfigured_cloud_never_contacts_deepseek(app_factory, consent):
    create, backend = app_factory
    with TestClient(create()) as client:
        thread = new_thread(client)
        response = client.post(
            "/api/v1/chat",
            json=chat_payload(thread["id"], model="deepseek", cloud_consent=consent),
            headers=AUTH,
        )
        assert response.status_code == 503
        assert response.json()["error"]["code"] == "CLOUD_DISABLED"
        assert backend.stream_calls == []


def test_configured_cloud_requires_per_request_consent_without_network(app_factory):
    create, backend = app_factory
    fake_key = "SYNTHETIC_OFFLINE_DEEPSEEK_KEY_ONLY"
    with TestClient(create(cloud_enabled=True, api_key=SecretStr(fake_key))) as client:
        thread = new_thread(client)
        response = client.post(
            "/api/v1/chat", json=chat_payload(thread["id"], model="deepseek"), headers=AUTH
        )
        assert response.status_code == 403
        assert response.json()["error"]["code"] == "CLOUD_CONSENT_REQUIRED"
        assert fake_key not in response.text
        assert backend.stream_calls == []


def test_unknown_model_is_422_and_does_not_load_or_create_messages(app_factory):
    create, backend = app_factory
    with TestClient(create()) as client:
        thread = new_thread(client)
        response = client.post(
            "/api/v1/chat", json=chat_payload(thread["id"], model="unknown:private"), headers=AUTH
        )
        assert response.status_code == 422
        assert response.json()["error"]["code"] == "MODEL_UNKNOWN"
        assert backend.stream_calls == []
        assert client.get("/api/v1/threads/" + thread["id"], headers=AUTH).json()["messages"] == []


@pytest.mark.parametrize("path", ["/api/v1/models", "/api/v1/threads"])
def test_disabled_chat_is_503_and_existing_health_is_available(app_factory, path):
    create, backend = app_factory
    with TestClient(create(chat_enabled=False)) as client:
        response = client.get(path, headers=AUTH)
        assert response.status_code == 503
        assert response.json()["error"]["code"] == "CHAT_DISABLED"
        assert client.get("/health").json() == {"status": "ok", "database": "ok"}
        assert client.get("/api/gpu/status", headers=AUTH).status_code == 200
        assert backend.stream_calls == []


@pytest.mark.parametrize(
    ("method", "path", "payload"),
    [
        ("GET", "/api/v1/models", None),
        ("GET", "/api/v1/threads", None),
        ("POST", "/api/v1/threads", {}),
        ("GET", "/api/v1/threads/" + str(uuid4()), None),
        ("PATCH", "/api/v1/threads/" + str(uuid4()), {"title": "名字"}),
        ("DELETE", "/api/v1/threads/" + str(uuid4()), None),
        ("POST", "/api/v1/chat", chat_payload(str(uuid4()))),
    ],
)
def test_all_chat_routes_keep_token_and_origin_protection(app_factory, method, path, payload):
    create, backend = app_factory
    with TestClient(create()) as client:
        for headers, expected in (
            ({}, 401),
            ({"Authorization": "Bearer invalid-synthetic"}, 401),
            ({**AUTH, "Origin": "http://127.0.0.1:3000"}, 403),
        ):
            response = client.request(method, path, json=payload, headers=headers)
            assert response.status_code == expected
            assert TOKEN not in response.text
        assert backend.stream_calls == []


def test_midstream_failure_preserves_partial_error_without_done(app_factory):
    create, backend = app_factory
    backend.fail_stream = True
    with TestClient(create()) as client:
        thread = new_thread(client)
        response = client.post("/api/v1/chat", json=chat_payload(thread["id"]), headers=AUTH)
        stream = events(response)
        assert [item["type"] for item in stream] == ["meta", "delta", "error"]
        assert stream[1]["text"] == "你好"
        assert "SYNTHETIC_PRIVATE_UPSTREAM_ERROR" not in response.text
        history = client.get("/api/v1/threads/" + thread["id"], headers=AUTH).json()
        assert history["messages"][1]["status"] == "error"
        assert history["messages"][1]["content"] == "你好"
        status = client.get("/api/gpu/status", headers=AUTH).json()
        assert status["local_requests_blocked"] is True
        assert status["fault"] == "UPSTREAM_OPERATION_UNCERTAIN"
        assert client.get("/health").status_code == 200


def test_running_request_rejects_delete_and_rename_until_saved(app_factory):
    create, backend = app_factory
    backend.wait_for_release = True
    with TestClient(create()) as client, ThreadPoolExecutor(max_workers=1) as workers:
        thread = new_thread(client)
        future = workers.submit(
            client.post, "/api/v1/chat", json=chat_payload(thread["id"]), headers=AUTH
        )
        try:
            assert backend.entered.wait(timeout=5)
            deleted = client.delete("/api/v1/threads/" + thread["id"], headers=AUTH)
            assert deleted.status_code == 409
            renamed = client.patch(
                "/api/v1/threads/" + thread["id"], json={"title": "运行中改名"}, headers=AUTH
            )
            assert renamed.status_code == 409
        finally:
            backend.release.set()
        assert events(future.result(timeout=10))[-1]["type"] == "done"
        deleted = client.delete("/api/v1/threads/" + thread["id"], headers=AUTH)
        assert deleted.status_code == 204
        assert deleted.content == b""
        assert client.get("/api/v1/threads/" + thread["id"], headers=AUTH).status_code == 404


def test_insufficient_memory_rejects_before_backend_stream(app_factory):
    create, backend = app_factory
    backend.free_mib = 200
    with TestClient(create()) as client:
        thread = new_thread(client)
        response = client.post("/api/v1/chat", json=chat_payload(thread["id"]), headers=AUTH)
        assert response.status_code == 409
        assert response.json()["error"]["code"] == "GPU_MEMORY_INSUFFICIENT"
        assert backend.stream_calls == []


@pytest.mark.parametrize(
    "overrides",
    [
        {"thread_id": "invalid"},
        {"request_id": "invalid"},
        {"content": ""},
        {"content": "长" * 4001},
    ],
)
def test_chat_payload_validation_prevents_backend_calls(app_factory, overrides):
    create, backend = app_factory
    with TestClient(create()) as client:
        thread = new_thread(client)
        response = client.post(
            "/api/v1/chat", json=chat_payload(thread["id"], **overrides), headers=AUTH
        )
        assert response.status_code == 422
        assert response.json()["error"]["code"] == "VALIDATION_ERROR"
        assert backend.stream_calls == []
