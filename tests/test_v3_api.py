"""V3路由集成：全部RAM、GPU与上游为替身，不实际加载27B或访问收费API。"""

import asyncio
import json
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from nova.chat.service import SYSTEM_MESSAGE
from nova.config import QWEN38_MODEL, Settings
from nova.gpu.backend import BackendError
from nova.main import create_app

TOKEN = "SYNTHETIC_V3_API_TOKEN_ONLY_01234567890123456789"
AUTH = {"Authorization": "Bearer " + TOKEN}


class V3Backend:
    model = "qwen3.5:4b"

    def __init__(self):
        self.base_url = "synthetic-v3-api-" + str(uuid4())
        self.installed = ["qwen3.5:9b", "qwen3.5:4b", QWEN38_MODEL]
        self.loaded = []
        self.ensured = []
        self.list_calls = 0
        self.status_calls = 0
        self.stream_calls = []
        self.unload_calls = []
        self.fail_stream = False
        self.free_mib = 15000

    async def list_installed_models(self):
        self.list_calls += 1
        return list(self.installed)

    async def ensure_local_model(self, model):
        self.ensured.append(model)
        if model not in self.installed:
            raise BackendError("SYNTHETIC_PRIVATE_MISSING_MODEL")

    async def get_model_status(self):
        self.status_calls += 1
        return [dict(model) for model in self.loaded]

    async def get_gpu_memory_usage(self):
        return {
            "available": True,
            "gpus": [{"index": 0, "total_mib": 16311, "used_mib": 16311 - self.free_mib}],
        }

    async def unload_model(self, model):
        self.unload_calls.append(model)
        self.loaded = [entry for entry in self.loaded if entry["name"] != model]
        return {"done": True}

    async def stream_chat(self, model, messages, on_delta, *, runtime=None):
        self.stream_calls.append(
            {
                "model": model,
                "messages": [dict(message) for message in messages],
                "runtime": None if runtime is None else dict(runtime),
            }
        )
        self.loaded = [{"name": model, "size_vram": 10000 * 1024 * 1024}]
        on_delta("你好")
        await asyncio.sleep(0)
        if self.fail_stream:
            raise BackendError("SYNTHETIC_PRIVATE_MODEL_LOAD_ERROR")
        on_delta("，我是Nova。")
        return {
            "model": model,
            "response": "你好，我是Nova。",
            "done": True,
            "stats": {"load_duration": 7, "eval_duration": 9, "tokens_per_second": 3.5},
        }


@pytest.fixture
def factory(monkeypatch, tmp_path):
    backend = V3Backend()
    constructed = []

    def make_backend(*args, **kwargs):
        constructed.append((args, kwargs))
        return backend

    def forbid_network(*args, **kwargs):
        raise AssertionError("V3测试不得调用真实模型服务或收费API")

    monkeypatch.setattr("nova.main.OllamaBackend", make_backend)
    monkeypatch.setattr("nova.chat.cloud.http.client.HTTPSConnection", forbid_network)
    memory = {"available": True, "total_mib": 32768, "available_mib": 24576}
    monkeypatch.setattr("nova.gpu.resources.get_system_memory", lambda: dict(memory))

    def make(**overrides):
        values = {
            "_env_file": None,
            "environment": "test",
            "chat_enabled": True,
            "gpu_enabled": True,
            "gpu_token": TOKEN,
            "qwen38_enabled": True,
            "data_dir": tmp_path / "state",
            "database_path": tmp_path / "data" / "nova.sqlite3",
        }
        values.update(overrides)
        return create_app(Settings(**values))

    return make, backend, memory, constructed


def thread(client):
    result = client.post("/api/v1/threads", json={}, headers=AUTH)
    assert result.status_code == 201
    return result.json()["id"]


def payload(thread_id, **overrides):
    result = {
        "thread_id": thread_id,
        "model": QWEN38_MODEL,
        "content": "请用中文介绍自己",
        "request_id": str(uuid4()),
    }
    result.update(overrides)
    return result


def events(response):
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    return [
        json.loads(line[5:].strip()) for line in response.iter_lines() if line.startswith("data:")
    ]


def test_experiment_disabled_preserves_stable_model_and_backend(factory):
    make, backend, _, _ = factory
    with TestClient(make(qwen38_enabled=False)) as client:
        identity = thread(client)
        denied = client.post("/api/v1/chat", json=payload(identity), headers=AUTH)
        assert denied.status_code == 503
        assert denied.json()["error"]["code"] == "MODEL_EXPERIMENT_DISABLED"
        assert backend.ensured == backend.stream_calls == backend.unload_calls == []
        assert client.get("/health").status_code == 200
        stable = client.post(
            "/api/v1/chat", json=payload(identity, model="qwen3.5:9b", stream=False), headers=AUTH
        )
        assert stable.status_code == 200
        assert stable.json()["message"]["model"] == "qwen3.5:9b"


@pytest.mark.parametrize(
    "memory_override", [{"total_mib": 16384, "available_mib": 8192}, {"available_mib": 4096}]
)
def test_low_ram_rejects_before_tags_loading_or_eviction_and_9b_remains_usable(
    factory, memory_override
):
    make, backend, memory, _ = factory
    memory.update(memory_override)
    backend.loaded = [{"name": "qwen3.5:9b", "size_vram": 6000 * 1024 * 1024}]
    with TestClient(make()) as client:
        identity = thread(client)
        denied = client.post("/api/v1/chat", json=payload(identity), headers=AUTH)
        assert denied.status_code == 409
        assert denied.json()["error"]["code"] == "MODEL_RAM_INSUFFICIENT"
        assert backend.ensured == backend.stream_calls == backend.unload_calls == []
        assert backend.list_calls == backend.status_calls == 0
        assert [model["name"] for model in backend.loaded] == ["qwen3.5:9b"]
        assert client.get("/health").status_code == 200
        stable = client.post(
            "/api/v1/chat", json=payload(identity, model="qwen3.5:9b", stream=False), headers=AUTH
        )
        assert stable.status_code == 200
        assert backend.stream_calls[-1]["model"] == "qwen3.5:9b"
        assert backend.stream_calls[-1]["runtime"] is None


def test_unknown_ram_is_unavailable_not_a_permission_to_load(factory):
    make, backend, memory, _ = factory
    memory.update(available=False, total_mib=0, available_mib=0)
    with TestClient(make()) as client:
        identity = thread(client)
        denied = client.post("/api/v1/chat", json=payload(identity), headers=AUTH)
        assert denied.status_code == 503
        assert denied.json()["error"]["code"] == "TELEMETRY_UNAVAILABLE"
        assert backend.ensured == backend.stream_calls == backend.unload_calls == []
        assert client.get("/health").status_code == 200


def test_experimental_json_then_multiturn_sse_preserves_persona_history_and_runtime(factory):
    make, backend, _, constructed = factory
    settings = {
        "gpu_ollama_url": "http://127.0.0.1:12345",
        "qwen38_context": 2048,
        "qwen38_num_predict": 96,
        "qwen38_keep_alive": 20,
        "qwen38_num_gpu": 24,
        "qwen38_temperature": 0.4,
    }
    app = make(**settings)
    with TestClient(app) as client:
        identity = thread(client)
        first = client.post("/api/v1/chat", json=payload(identity, stream=False), headers=AUTH)
        assert first.status_code == 200
        assert first.headers["content-type"].startswith("application/json")
        message = first.json()["message"]
        assert first.json()["thread_id"] == identity
        assert message["content"] == "你好，我是Nova。"
        assert message["status"] == "complete"
        second = client.post(
            "/api/v1/chat", json=payload(identity, content="继续上一轮问题"), headers=AUTH
        )
        stream = events(second)
        assert [item["type"] for item in stream] == ["meta", "delta", "delta", "done"]
        assert stream[-1]["message"]["status"] == "complete"
        assert all(call["model"] == QWEN38_MODEL for call in backend.stream_calls)
        assert all(
            call["messages"][0] == {"role": "system", "content": SYSTEM_MESSAGE}
            for call in backend.stream_calls
        )
        assert backend.stream_calls[1]["messages"][-3:] == [
            {"role": "user", "content": "请用中文介绍自己"},
            {"role": "assistant", "content": "你好，我是Nova。"},
            {"role": "user", "content": "继续上一轮问题"},
        ]
        expected = {
            "num_ctx": 2048,
            "num_predict": 96,
            "keep_alive": 20,
            "temperature": 0.4,
            "num_gpu": 24,
        }
        assert all(call["runtime"] == expected for call in backend.stream_calls)
        saved = client.get("/api/v1/threads/" + identity, headers=AUTH).json()
        assert len(saved["messages"]) == 4
        assert saved["messages"][1] == message
        assert saved["thread"]["model"] == QWEN38_MODEL
        assert app.state.gpu_manager.last_operation["tokens_per_second"] == 3.5
        assert app.state.gpu_manager.manual_gaming is False
        assert constructed[0][0][0] == "http://127.0.0.1:12345"


@pytest.mark.parametrize("source", ["manual", "detected"])
def test_gaming_hold_rejects_before_experiment_memory_or_load(factory, monkeypatch, source):
    make, backend, _, _ = factory
    app = make()
    with TestClient(app) as client:
        identity = thread(client)
        if source == "manual":
            assert (
                client.post("/api/gpu/mode", json={"mode": "GAMING"}, headers=AUTH).status_code
                == 200
            )
        else:
            # 模拟检测器已经锁定的状态，不读取真实游戏进程。
            app.state.gpu_manager.detected_gaming = True
            app.state.gpu_manager.mode = "GAMING"

        def forbid_memory():
            raise AssertionError("游戏门禁必须先于实验资源准备")

        monkeypatch.setattr("nova.gpu.resources.get_system_memory", forbid_memory)
        denied = client.post("/api/v1/chat", json=payload(identity), headers=AUTH)
        assert denied.status_code == 423
        assert denied.json()["error"]["code"] == "GPU_GAMING"
        assert backend.ensured == backend.stream_calls == []
        assert backend.loaded == []
        assert client.get("/health").status_code == 200


@pytest.mark.parametrize("stream", [False, True])
def test_missing_experiment_rejects_without_evicting_stable_model(factory, stream):
    make, backend, _, _ = factory
    backend.installed.remove(QWEN38_MODEL)
    backend.loaded = [{"name": "qwen3.5:9b", "size_vram": 6000 * 1024 * 1024}]
    with TestClient(make()) as client:
        identity = thread(client)
        denied = client.post("/api/v1/chat", json=payload(identity, stream=stream), headers=AUTH)
        assert denied.status_code == 503
        assert denied.json()["error"]["code"] == "GPU_PREFLIGHT_FAILED"
        assert "SYNTHETIC_PRIVATE" not in denied.text
        assert backend.stream_calls == backend.unload_calls == []
        assert [model["name"] for model in backend.loaded] == ["qwen3.5:9b"]
        assert client.get("/health").status_code == 200


@pytest.mark.parametrize("stream", [False, True])
def test_upstream_failure_keeps_backend_alive_and_can_safely_fall_back(factory, stream):
    make, backend, _, _ = factory
    backend.fail_stream = True
    with TestClient(make()) as client:
        identity = thread(client)
        failed = client.post("/api/v1/chat", json=payload(identity, stream=stream), headers=AUTH)
        if stream:
            items = events(failed)
            assert items[-1]["type"] == "error"
            assert not any(item["type"] == "done" for item in items)
        else:
            assert failed.status_code == 503
            assert failed.json()["error"]["code"] == "GPU_UPSTREAM_FAILED"
        assert "SYNTHETIC_PRIVATE" not in failed.text
        assert client.get("/health").status_code == 200
        saved = client.get("/api/v1/threads/" + identity, headers=AUTH).json()
        assert saved["messages"][-1]["status"] == "error"
        assert saved["messages"][-1]["content"] == "你好"
        assert client.post("/api/gpu/unload", json={}, headers=AUTH).status_code == 200
        assert client.post("/api/gpu/mode", json={"mode": "AUTO"}, headers=AUTH).status_code == 200
        backend.fail_stream = False
        fallback = client.post(
            "/api/v1/chat", json=payload(identity, model="qwen3.5:9b", stream=False), headers=AUTH
        )
        assert fallback.status_code == 200
        assert fallback.json()["message"]["model"] == "qwen3.5:9b"


def test_successful_experiment_can_switch_back_to_both_stable_models(factory):
    make, backend, _, _ = factory
    with TestClient(make()) as client:
        identity = thread(client)
        for model in (QWEN38_MODEL, "qwen3.5:9b", "qwen3.5:4b"):
            reply = client.post(
                "/api/v1/chat", json=payload(identity, model=model, stream=False), headers=AUTH
            )
            assert reply.status_code == 200
            assert reply.json()["message"]["model"] == model
            assert [item["name"] for item in backend.loaded] == [model]
        assert [call["model"] for call in backend.stream_calls] == [
            QWEN38_MODEL,
            "qwen3.5:9b",
            "qwen3.5:4b",
        ]
        assert backend.unload_calls == [QWEN38_MODEL, "qwen3.5:9b"]
        assert all(call["runtime"] is None for call in backend.stream_calls[1:])


def test_saved_experiment_reply_replays_without_reloading_under_low_ram(factory):
    make, backend, memory, _ = factory
    with TestClient(make()) as client:
        identity = thread(client)
        body = payload(identity, stream=False)
        first = client.post("/api/v1/chat", json=body, headers=AUTH)
        assert first.status_code == 200
        memory.update(total_mib=16384, available_mib=4096)
        second = client.post("/api/v1/chat", json=body, headers=AUTH)
        assert second.status_code == 200
        assert second.json() == first.json()
        assert len(backend.stream_calls) == 1


def test_model_registry_does_not_replace_default_or_load_models(factory):
    make, backend, _, _ = factory
    with TestClient(make()) as client:
        listed = client.get("/api/v1/models", headers=AUTH)
        assert listed.status_code == 200
        assert listed.json()["default_model"] == "qwen3.5:9b"
        entry = next(item for item in listed.json()["models"] if item["id"] == QWEN38_MODEL)
        assert entry["available"] is True
        assert entry["installed"] is True
        assert entry["experimental"] is True
        assert backend.stream_calls == backend.ensured == backend.unload_calls == []
        assert TOKEN not in listed.text
