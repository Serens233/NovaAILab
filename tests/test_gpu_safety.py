"""记录/状态保存失败时锁定入口，GPU 清理失败也不得泄漏数据库资源。"""

import asyncio
import json

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from nova.config import Settings
from nova.errors import ApplicationError
from nova.gpu.manager import GpuManager
from nova.main import create_app
from nova.storage.database import DatabaseError


class FakeBackend:
    model = "practice:tiny"

    def __init__(self):
        self.generated = 0

    async def get_model_status(self):
        return []

    async def get_gpu_memory_usage(self):
        return {"available": True, "gpus": [{"index": 0, "total_mib": 16311, "used_mib": 1000}]}

    async def generate(self, prompt):
        self.generated += 1
        return {"done": True, "response": "SYNTHETIC_PRIVATE_RESPONSE", "load_duration": 42}


def test_audit_save_failure_locks_gpu_without_recording_prompt(tmp_path, practice_policy):
    async def scenario():
        prompt = "SYNTHETIC_PRIVATE_PROMPT_DO_NOT_RECORD"
        audit_path = tmp_path / "gpu-events.jsonl"
        audit_path.mkdir()
        backend = FakeBackend()
        manager = GpuManager(backend, tmp_path / "gpu-mode.json", model_policies=practice_policy())
        await manager.start()
        try:
            with pytest.raises(ApplicationError) as caught:
                await manager.generate(prompt)
            assert caught.value.code == "GPU_AUDIT_FAILED"
            assert caught.value.status_code == 503
            assert backend.generated == 1
            status = await manager.status()
            assert status["mode"] == "GAMING"
            assert status["local_requests_blocked"]
            assert status["fault"] == "OPERATION_RECORD_UNAVAILABLE"
            assert status["active_requests"] == 0
            public_and_state = json.dumps(status) + manager.state_path.read_text(encoding="utf-8")
            assert prompt not in public_and_state
            assert "SYNTHETIC_PRIVATE_RESPONSE" not in public_and_state
            assert audit_path.is_dir()
            assert not list(audit_path.iterdir())
            with pytest.raises(ApplicationError) as blocked:
                await manager.generate("新的合成问题")
            assert blocked.value.code == "GPU_GAMING"
            assert backend.generated == 1
        finally:
            await manager.close()

    asyncio.run(scenario())


@pytest.mark.parametrize("operation", ["gaming", "generate"])
def test_invalid_state_parent_locks_before_model_request(tmp_path, operation, practice_policy):
    async def scenario():
        parent_file = tmp_path / "blocked-parent"
        parent_file.write_text("preserve-original-content", encoding="utf-8")
        backend = FakeBackend()
        manager = GpuManager(
            backend, parent_file / "gpu-mode.json", model_policies=practice_policy()
        )
        try:
            with pytest.raises(ApplicationError) as caught:
                if operation == "gaming":
                    await manager.set_mode("GAMING")
                else:
                    await manager.generate("状态无法保存时不能开始推理")
            assert caught.value.code == "GPU_STATE_FAILED"
            assert caught.value.status_code == 503
            assert manager.mode == "GAMING"
            assert manager.blocked
            assert manager.fault == "STATE_WRITE_FAILED"
            assert backend.generated == 0
            assert parent_file.read_text(encoding="utf-8") == "preserve-original-content"
        finally:
            await manager.close()

    asyncio.run(scenario())


def test_database_closes_even_when_gpu_cleanup_raises(monkeypatch, tmp_path):
    monkeypatch.setattr("nova.main.OllamaBackend", lambda *args, **kwargs: FakeBackend())
    settings = Settings(
        _env_file=None,
        environment="test",
        gpu_enabled=True,
        gpu_token=SecretStr("synthetic_gpu_safety_test_token_only"),
        data_dir=tmp_path,
        database_path=tmp_path / "nova.sqlite3",
    )
    app = create_app(settings)
    original_close = app.state.gpu_manager.close

    async def fail_after_gpu_close():
        # 释放测试持有的实例锁，再模拟上层 GPU 清理报告失败。
        await original_close()
        raise RuntimeError("synthetic GPU cleanup failure")

    monkeypatch.setattr(app.state.gpu_manager, "close", fail_after_gpu_close)
    with pytest.raises(RuntimeError, match="synthetic GPU cleanup failure"):
        with TestClient(app) as client:
            assert app.state.database.is_ready
            assert client.get("/health").status_code == 200
    assert not app.state.ready
    assert not app.state.database.is_ready
    with pytest.raises(DatabaseError), app.state.database.session():
        pytest.fail("GPU 清理失败后数据库也必须关闭")
