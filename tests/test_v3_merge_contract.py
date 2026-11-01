"""合并阻断修复回归：共享27B策略、只读重放和模型缺失错误契约。"""

import asyncio
import json
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from nova.config import QWEN38_MODEL, Settings
from nova.errors import ApplicationError
from nova.gpu.backend import ModelNotInstalledError
from nova.gpu.manager import GpuManager
from nova.main import create_app

TOKEN = "SYNTHETIC_MERGE_CONTRACT_TOKEN_01234567890123456789"
AUTH = {"Authorization": "Bearer " + TOKEN}
MESSAGES = [{"role": "user", "content": "合成的合并阻断测试"}]
QUANT = "qwen3.8:27b-q3_k_xl"


def experimental_policy(*, enabled=True):
    return {
        "model": QWEN38_MODEL,
        "enabled": enabled,
        "required_mib": 4096,
        "min_total_ram_mib": 28672,
        "min_available_ram_mib": 18432,
        "runtime": {
            "num_ctx": 4096,
            "num_predict": 256,
            "keep_alive": 180,
            "temperature": 0.7,
        },
    }


def enough_memory():
    return {"available": True, "total_mib": 32768, "available_mib": 24576}


def low_ram():
    return {"available": True, "total_mib": 15477, "available_mib": 9000}


class PolicyBackend:
    model = QUANT

    def __init__(self):
        self.base_url = "synthetic-merge-" + str(uuid4())
        self.models = []
        self.calls = []
        self.memory = {
            "available": True,
            "gpus": [{"index": 0, "total_mib": 16311, "used_mib": 1000}],
        }

    async def list_installed_models(self):
        return [self.model]

    async def get_model_status(self):
        self.calls.append(("status",))
        return [dict(item) for item in self.models]

    async def get_gpu_memory_usage(self):
        return {
            "available": self.memory["available"],
            "gpus": [dict(item) for item in self.memory["gpus"]],
        }

    async def ensure_local_model(self, model):
        self.calls.append(("ensure", model))

    async def unload_model(self, name):
        self.calls.append(("unload", name))
        self.models = [item for item in self.models if item["name"] != name]
        return {"done": True}

    async def wake_model(self, *, runtime=None):
        self.calls.append(("wake", runtime))
        self.models = [{"name": self.model, "size_vram": 123}]
        return {"done": True, "model": self.model}

    async def generate(self, prompt, *, runtime=None):
        self.calls.append(("generate", prompt, runtime))
        self.models = [{"name": self.model, "size_vram": 123}]
        return {"done": True, "model": self.model, "response": "合成回复"}

    async def stream_chat(self, model, messages, emit, *, runtime=None, control=None):
        self.calls.append(("stream", model, runtime))
        self.models = [{"name": model, "size_vram": 123}]
        emit("合成回复")
        return {"done": True, "model": model, "response": "合成回复", "stats": {}}


def manager_for(tmp_path, backend, **kwargs):
    return GpuManager(
        backend,
        tmp_path / "gpu-mode.json",
        drain_timeout=1.0,
        unload_timeout=0.1,
        **kwargs,
    )


def test_wake_and_generate_share_27b_template_and_fail_before_backend(tmp_path, monkeypatch):
    monkeypatch.setattr("nova.gpu.resources.get_system_memory", low_ram)

    async def scenario():
        backend = PolicyBackend()
        manager = manager_for(tmp_path, backend, experimental_policy=experimental_policy())
        await manager.start()
        try:
            for operation in (manager.wake, lambda: manager.generate("合成问题")):
                with pytest.raises(ApplicationError) as caught:
                    await operation()
                assert caught.value.code == "MODEL_RAM_INSUFFICIENT"
            assert backend.calls == []
            assert not manager._active
        finally:
            await manager.close()

    asyncio.run(scenario())


def test_chat_actual_quant_alias_uses_template_before_ollama(tmp_path, monkeypatch):
    monkeypatch.setattr("nova.gpu.resources.get_system_memory", low_ram)

    async def scenario():
        backend = PolicyBackend()
        manager = manager_for(tmp_path, backend, experimental_policy=experimental_policy())
        await manager.start()
        try:
            with pytest.raises(ApplicationError) as caught:
                await manager.open_chat_stream(QUANT, MESSAGES, 1)
            assert caught.value.code == "MODEL_RAM_INSUFFICIENT"
            assert backend.calls == []
        finally:
            await manager.close()

    asyncio.run(scenario())


def test_27b_unmatched_label_and_missing_template_fail_closed(tmp_path, monkeypatch):
    monkeypatch.setattr("nova.gpu.resources.get_system_memory", enough_memory)

    async def scenario():
        backend = PolicyBackend()
        backend.model = "qwen3.8:27b_q3_k_xl"
        manager = manager_for(tmp_path, backend, experimental_policy=experimental_policy())
        await manager.start()
        try:
            with pytest.raises(ApplicationError) as caught:
                await manager.wake()
            assert caught.value.code == "MODEL_POLICY_INVALID"
            assert backend.calls == []

            # 连模板都没有时，任何近似27B标签也不能因查不到策略而绕过。
            other = PolicyBackend()
            other.model = QUANT
            bare = manager_for(tmp_path / "bare", other)
            await bare.start()
            try:
                with pytest.raises(ApplicationError) as caught:
                    await bare.generate("合成问题")
                assert caught.value.code == "MODEL_POLICY_INVALID"
                assert other.calls == []
            finally:
                await bare.close()
        finally:
            await manager.close()

    asyncio.run(scenario())


def test_disabled_experiment_and_vram_gate_apply_to_all_entrypoints(tmp_path, monkeypatch):
    monkeypatch.setattr("nova.gpu.resources.get_system_memory", enough_memory)

    async def scenario():
        backend = PolicyBackend()
        manager = manager_for(
            tmp_path, backend, experimental_policy=experimental_policy(enabled=False)
        )
        await manager.start()
        try:
            for operation in (manager.wake, lambda: manager.generate("合成问题")):
                with pytest.raises(ApplicationError) as caught:
                    await operation()
                assert caught.value.code == "MODEL_EXPERIMENT_DISABLED"
            assert backend.calls == []
        finally:
            await manager.close()

        limited = PolicyBackend()
        limited.memory["gpus"][0]["used_mib"] = 15000
        gated = manager_for(tmp_path / "vram", limited, experimental_policy=experimental_policy())
        await gated.start()
        try:
            with pytest.raises(ApplicationError) as caught:
                await gated.wake()
            assert caught.value.code == "GPU_MEMORY_INSUFFICIENT"
            assert not any(call[0] in {"wake", "generate", "stream"} for call in limited.calls)
        finally:
            await gated.close()

    asyncio.run(scenario())


def test_policy_runtime_is_reused_by_wake_and_generate(tmp_path, monkeypatch):
    monkeypatch.setattr("nova.gpu.resources.get_system_memory", enough_memory)

    async def scenario():
        backend = PolicyBackend()
        manager = manager_for(tmp_path, backend, experimental_policy=experimental_policy())
        await manager.start()
        try:
            await manager.wake()
            await manager.unload()
            assert (await manager.set_mode("AUTO"))["mode"] == "AUTO"
            await manager.generate("合成问题")
            runtime = experimental_policy()["runtime"]
            assert backend.calls[-1] == ("generate", "合成问题", runtime)
            assert any(call[0] == "wake" and call[1] == runtime for call in backend.calls)
        finally:
            await manager.close()

    asyncio.run(scenario())


class MissingBackend(PolicyBackend):
    model = "missing:tiny"

    async def ensure_local_model(self, model):
        self.calls.append(("ensure", model))
        raise ModelNotInstalledError("synthetic missing model")

    async def wake_model(self, *, runtime=None):
        self.calls.append(("wake", runtime))
        raise ModelNotInstalledError("synthetic missing model")

    async def generate(self, prompt, *, runtime=None):
        self.calls.append(("generate", prompt, runtime))
        raise ModelNotInstalledError("synthetic missing model")


def test_missing_model_contract_does_not_create_safety_fault(tmp_path, monkeypatch):
    monkeypatch.setattr("nova.gpu.resources.get_system_memory", enough_memory)

    async def scenario():
        backend = MissingBackend()
        manager = manager_for(tmp_path, backend)
        await manager.start()
        try:
            for operation in (manager.wake, lambda: manager.generate("合成问题")):
                with pytest.raises(ApplicationError) as caught:
                    await operation()
                assert (caught.value.code, caught.value.status_code) == (
                    "MODEL_NOT_INSTALLED",
                    503,
                )
                assert manager.fault is None
                assert manager.mode == "AUTO"
                assert manager.blocked is False
            with pytest.raises(ApplicationError) as caught:
                await manager.open_chat_stream("missing:tiny", MESSAGES, 5500)
            assert caught.value.code == "MODEL_NOT_INSTALLED"
            assert manager.fault is None
            assert not manager._active
            assert not any(call[0] == "stream" for call in backend.calls)
        finally:
            await manager.close()

    asyncio.run(scenario())


class ReplayBackend:
    model = "qwen3.5:4b"
    supports_stream_control = False

    def __init__(self):
        self.base_url = "synthetic-replay-" + str(uuid4())
        self.installed = ["qwen3.5:9b", "qwen3.5:4b"]
        self.loaded = []
        self.list_calls = 0
        self.status_calls = 0
        self.ensured = []
        self.stream_calls = []
        self.unload_calls = []

    async def list_installed_models(self):
        self.list_calls += 1
        return list(self.installed)

    async def ensure_local_model(self, model):
        self.ensured.append(model)

    async def get_model_status(self):
        self.status_calls += 1
        return [dict(item) for item in self.loaded]

    async def get_gpu_memory_usage(self):
        return {
            "available": True,
            "gpus": [{"index": 0, "total_mib": 16311, "used_mib": 1000}],
        }

    async def unload_model(self, name):
        self.unload_calls.append(name)
        self.loaded = [item for item in self.loaded if item["name"] != name]
        return {"done": True}

    async def stream_chat(self, model, messages, emit):
        self.stream_calls.append({"model": model, "messages": messages})
        self.loaded = [{"name": model, "size_vram": 123}]
        emit("合成回复")
        return {
            "done": True,
            "model": model,
            "response": "合成回复",
            "stats": {},
        }


@pytest.fixture
def replay_app(monkeypatch, tmp_path):
    backend = ReplayBackend()
    monkeypatch.setattr("nova.main.OllamaBackend", lambda *args, **kwargs: backend)
    monkeypatch.setattr("nova.gpu.resources.get_system_memory", enough_memory)
    app = create_app(
        Settings(
            _env_file=None,
            environment="test",
            chat_enabled=True,
            gpu_enabled=True,
            gpu_token=SecretStr(TOKEN),
            data_dir=tmp_path / "state",
            database_path=tmp_path / "data" / "nova.sqlite3",
        )
    )
    return app, backend


def _thread(client):
    response = client.post("/api/v1/threads", json={}, headers=AUTH)
    assert response.status_code == 201
    return response.json()["id"]


def _payload(thread_id, **overrides):
    body = {
        "thread_id": thread_id,
        "model": "qwen3.5:9b",
        "content": "合成的重放问题",
        "request_id": str(uuid4()),
        "stream": False,
    }
    body.update(overrides)
    return body


def test_completed_replay_runs_before_gaming_and_resource_admission(replay_app):
    app, backend = replay_app
    with TestClient(app) as client:
        thread_id = _thread(client)
        body = _payload(thread_id)
        first = client.post("/api/v1/chat", json=body, headers=AUTH)
        assert first.status_code == 200

        assert (
            client.post("/api/gpu/mode", json={"mode": "GAMING"}, headers=AUTH).status_code == 200
        )
        counts = (
            backend.list_calls,
            backend.status_calls,
            len(backend.ensured),
            len(backend.stream_calls),
            len(backend.unload_calls),
        )
        replay = client.post("/api/v1/chat", json=body, headers=AUTH)
        assert replay.status_code == 200
        assert replay.json() == first.json()
        assert counts == (
            backend.list_calls,
            backend.status_calls,
            len(backend.ensured),
            len(backend.stream_calls),
            len(backend.unload_calls),
        )
        assert app.state.gpu_manager.mode == "GAMING"
        assert app.state.gpu_manager.blocked is True

        different_content = client.post(
            "/api/v1/chat", json={**body, "content": "不同内容"}, headers=AUTH
        )
        assert different_content.status_code == 409
        assert different_content.json()["error"]["code"] == "CHAT_REQUEST_CONFLICT"
        different_model = client.post(
            "/api/v1/chat", json={**body, "model": "qwen3.5:4b"}, headers=AUTH
        )
        assert different_model.status_code == 409
        assert different_model.json()["error"]["code"] == "CHAT_REQUEST_CONFLICT"
        assert len(backend.stream_calls) == 1


class MenuBackend(ReplayBackend):
    model = QUANT

    def __init__(self):
        super().__init__()
        self.calls = []


def test_app_wires_shared_template_for_wake_and_generate(monkeypatch, tmp_path):
    backend = MenuBackend()
    monkeypatch.setattr("nova.main.OllamaBackend", lambda *args, **kwargs: backend)
    app = create_app(
        Settings(
            _env_file=None,
            environment="test",
            gpu_enabled=True,
            gpu_token=SecretStr(TOKEN),
            gpu_model=QUANT,
            qwen38_enabled=False,
            data_dir=tmp_path / "state",
            database_path=tmp_path / "data" / "nova.sqlite3",
        )
    )
    with TestClient(app) as client:
        for path, body in (("/api/gpu/wake", None), ("/api/gpu/generate", {"prompt": "合成"})):
            response = client.post(path, json=body, headers=AUTH)
            assert response.status_code == 503
            assert response.json()["error"]["code"] == "MODEL_EXPERIMENT_DISABLED"
            assert backend.calls == []


def test_app_missing_model_wake_uses_installed_contract(monkeypatch, tmp_path):
    backend = MissingBackend()
    monkeypatch.setattr("nova.main.OllamaBackend", lambda *args, **kwargs: backend)
    app = create_app(
        Settings(
            _env_file=None,
            environment="test",
            gpu_enabled=True,
            gpu_token=SecretStr(TOKEN),
            gpu_model="missing:tiny",
            data_dir=tmp_path / "state",
            database_path=tmp_path / "data" / "nova.sqlite3",
        )
    )
    with TestClient(app) as client:
        response = client.post("/api/gpu/wake", headers=AUTH)
        assert response.status_code == 503
        assert response.json()["error"]["code"] == "MODEL_NOT_INSTALLED"
        status = client.get("/api/gpu/status", headers=AUTH).json()
        assert status["fault"] is None
        assert status["mode"] == "AUTO"
        assert status["local_requests_blocked"] is False


def test_completed_replay_keeps_database_rows_unchanged(replay_app):
    app, _ = replay_app
    with TestClient(app) as client:
        thread_id = _thread(client)
        body = _payload(thread_id)
        assert client.post("/api/v1/chat", json=body, headers=AUTH).status_code == 200
        before = client.get("/api/v1/threads/" + thread_id, headers=AUTH).json()
        assert client.post("/api/v1/chat", json=body, headers=AUTH).status_code == 200
        after = client.get("/api/v1/threads/" + thread_id, headers=AUTH).json()
        assert before == after
        assert len(after["messages"]) == 2
        assert json.dumps(after, sort_keys=True) == json.dumps(before, sort_keys=True)
