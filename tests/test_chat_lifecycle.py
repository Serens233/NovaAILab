"""断开预检、空闲采集失败和关闭失败不应遗失聊天状态或泄漏资源。"""

import asyncio
from threading import Event
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from nova.chat.service import ChatService, idle_monitor
from nova.config import Settings
from nova.gpu.backend import BackendError
from nova.gpu.manager import GpuManager
from nova.main import create_app
from nova.storage.chat import ChatStore
from nova.storage.database import Database, DatabaseError


class PreparationBackend:
    model = "qwen3.5:9b"

    def __init__(self):
        self.base_url = "synthetic-lifecycle-" + str(uuid4())
        self.preparing = asyncio.Event()
        self.release = asyncio.Event()
        self.generations = 0

    async def list_installed_models(self):
        return ["qwen3.5:9b", "qwen3.5:4b"]

    async def ensure_local_model(self, model):
        self.preparing.set()
        await self.release.wait()

    async def get_model_status(self):
        return []

    async def get_gpu_memory_usage(self):
        return {
            "available": True,
            "gpus": [{"index": 0, "total_mib": 16311, "used_mib": 1000}],
        }

    async def stream_chat(self, model, messages, on_delta):
        self.generations += 1
        on_delta("取消消费端后仍然保存")
        return {
            "model": model,
            "response": "取消消费端后仍然保存",
            "done": True,
            "stats": {},
        }


def settings_for(tmp_path):
    return Settings(
        _env_file=None,
        environment="test",
        chat_enabled=True,
        gpu_enabled=True,
        gpu_token=SecretStr("SYNTHETIC_CHAT_LIFECYCLE_TOKEN_ONLY_0123456789"),
        data_dir=tmp_path / "state",
        database_path=tmp_path / "data" / "nova.sqlite3",
    )


def test_cancelled_open_during_preflight_still_finishes_and_persists(tmp_path):
    async def scenario():
        settings = settings_for(tmp_path)
        database = Database(settings.database_path)
        await asyncio.to_thread(database.initialize)
        store = ChatStore(database)
        await asyncio.to_thread(store.initialize)
        thread = await asyncio.to_thread(store.create_thread)
        backend = PreparationBackend()
        manager = GpuManager(backend, settings.data_dir / "gpu-mode.json")
        service = ChatService(store, manager, settings)
        await manager.start()
        caller = asyncio.create_task(
            service.open(thread["id"], "qwen3.5:9b", "断开连接的合成请求", str(uuid4()))
        )
        try:
            await asyncio.wait_for(backend.preparing.wait(), timeout=3)
            assert (await manager.status())["active_requests"] == 1
            caller.cancel()
            with pytest.raises(asyncio.CancelledError):
                await caller
            # 消费者已离开，但模型预检仍受追踪，不能伪装为空闲。
            assert (await manager.status())["active_requests"] == 1
            messages = await asyncio.to_thread(store.get_messages, thread["id"])
            assert messages[1]["status"] == "pending"
            assert backend.generations == 0

            backend.release.set()
            # close须等待独立preparing任务及其后续保存任务，而非丢弃回复。
            await asyncio.wait_for(service.close(), timeout=3)
            messages = await asyncio.to_thread(store.get_messages, thread["id"])
            assert len(messages) == 2
            assert [message["status"] for message in messages] == ["complete", "complete"]
            assert messages[1]["content"] == "取消消费端后仍然保存"
            assert backend.generations == 1
            assert (await manager.status())["active_requests"] == 0
            assert not service._preparing
            assert not service._runs
            with database.session() as connection:
                assert (
                    connection.execute(
                        "SELECT COUNT(*) FROM nova_chat_runs WHERE status = 'pending'"
                    ).fetchone()[0]
                    == 0
                )
        finally:
            backend.release.set()
            if not caller.done():
                caller.cancel()
                await asyncio.gather(caller, return_exceptions=True)
            await asyncio.wait_for(service.close(), timeout=3)
            await manager.close()
            await asyncio.to_thread(database.close)

    asyncio.run(scenario())


def test_idle_monitor_survives_backend_error_and_keeps_polling():
    async def scenario():
        class FlakyIdleManager:
            def __init__(self):
                self.calls = 0
                self.retried = asyncio.Event()

            async def idle_tick(self, *, idle_seconds):
                self.calls += 1
                if self.calls == 1:
                    raise BackendError("SYNTHETIC_PRIVATE_COUNTER_FAILURE")
                self.retried.set()

        manager = FlakyIdleManager()
        # 仅缩短测试轮询等待，不替换业务异常或采集逻辑。
        task = asyncio.create_task(idle_monitor(manager, 0.001))
        try:
            await asyncio.wait_for(manager.retried.wait(), timeout=2)
            assert manager.calls >= 2
            assert not task.done()
        finally:
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task

    asyncio.run(scenario())


@pytest.mark.parametrize("failure_source", ["idle", "chat"])
def test_shutdown_releases_gpu_and_database_despite_new_cleanup_failure(
    monkeypatch, tmp_path, failure_source
):
    backend = PreparationBackend()
    monkeypatch.setattr("nova.main.OllamaBackend", lambda *args, **kwargs: backend)
    idle_failed = Event()
    expected = f"synthetic {failure_source} shutdown failure"
    if failure_source == "idle":

        async def failed_idle(manager, idle_seconds):
            idle_failed.set()
            raise RuntimeError(expected)

        monkeypatch.setattr("nova.main.idle_monitor", failed_idle)
    app = create_app(settings_for(tmp_path))
    if failure_source == "chat":

        async def failed_chat_close():
            raise RuntimeError(expected)

        monkeypatch.setattr(app.state.chat_service, "close", failed_chat_close)

    with pytest.raises(RuntimeError, match=expected):
        with TestClient(app) as client:
            assert app.state.database.is_ready
            assert client.get("/health").status_code == 200
            if failure_source == "idle":
                assert idle_failed.wait(timeout=3)
    assert app.state.ready is False
    assert app.state.database.is_ready is False
    assert app.state.gpu_manager._closing is True
    assert app.state.gpu_manager._lease.file is None
    with pytest.raises(DatabaseError), app.state.database.session():
        pytest.fail("聊天清理异常后数据库仍应被关闭")
