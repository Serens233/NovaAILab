"""只用替身验证请求门闩与竞态；不访问本机 Ollama 或 GPU。"""

import asyncio
import json

import pytest

from nova.errors import ApplicationError
from nova.gpu.backend import BackendError
from nova.gpu.manager import GpuManager


class FakeBackend:
    model = "practice:tiny"

    def __init__(self):
        self.models = [{"name": self.model, "size_vram": 123}]
        self.calls = []
        self.status_error = False
        self.unload_error = False
        self.generate_error = False
        self.retain_after_unload = False
        self.generate_started = asyncio.Event()
        self.generate_release = None
        self.unload_started = asyncio.Event()
        self.unload_release = None

    async def get_model_status(self):
        if self.status_error:
            raise BackendError("test unavailable")
        return [dict(model) for model in self.models]

    async def get_gpu_memory_usage(self):
        return {"available": True, "gpus": [{"total_mib": 100, "used_mib": 10}]}

    async def unload_model(self, name):
        self.calls.append(("unload", name))
        self.unload_started.set()
        if self.unload_error:
            raise BackendError("test unload failure")
        if self.unload_release is not None:
            await self.unload_release.wait()
        if not self.retain_after_unload:
            self.models = [model for model in self.models if model["name"] != name]
        return {"done": True}

    async def wake_model(self):
        self.calls.append(("wake", self.model))
        self.models = [{"name": self.model, "size_vram": 123}]
        return {"done": True, "load_duration": 42}

    async def generate(self, prompt):
        self.calls.append(("generate", prompt))
        self.generate_started.set()
        if self.generate_release is not None:
            await self.generate_release.wait()
        if self.generate_error:
            raise BackendError("test generation uncertain")
        self.models = [{"name": self.model, "size_vram": 123}]
        return {"done": True, "response": "合成回复", "load_duration": 42}


def manager_for(tmp_path, backend=None, **kwargs):
    return GpuManager(
        backend or FakeBackend(), tmp_path / "gpu-mode.json", unload_timeout=0.1, **kwargs
    )


def test_gaming_unloads_every_model_and_auto_does_not_reload(tmp_path):
    async def scenario():
        backend = FakeBackend()
        backend.models.append({"name": "another:tiny", "size_vram": 456})
        manager = manager_for(tmp_path, backend)
        await manager.start()
        try:
            gaming = await manager.set_mode("GAMING")
            assert gaming["mode"] == "GAMING"
            assert gaming["local_requests_blocked"]
            assert gaming["unload_verified"]
            assert gaming["backend"]["models"] == []
            assert gaming["last_unload_at"]
            assert backend.calls == [("unload", "practice:tiny"), ("unload", "another:tiny")]
            for operation in (manager.wake, lambda: manager.generate("练习问题")):
                with pytest.raises(ApplicationError) as caught:
                    await operation()
                assert caught.value.status_code == 423
            assert backend.calls == [("unload", "practice:tiny"), ("unload", "another:tiny")]
            await manager.set_mode("GAMING")
            assert len(backend.calls) == 2
            restored = await manager.set_mode("AUTO")
            assert restored["mode"] == "AUTO"
            assert not restored["local_requests_blocked"]
            assert backend.models == []
            assert (await manager.generate("练习问题"))["response"] == "合成回复"
            assert backend.calls[-1][0] == "generate"
            assert (await manager.status())["last_operation"]["load_duration_ns"] == 42
        finally:
            await manager.close()

    asyncio.run(scenario())


def test_gate_closes_before_drain_and_auto_cannot_overtake_unload(tmp_path):
    async def scenario():
        backend = FakeBackend()
        backend.generate_release = asyncio.Event()
        manager = manager_for(tmp_path, backend, drain_timeout=1)
        await manager.start()
        try:
            generation = asyncio.create_task(manager.generate("已接纳请求"))
            await backend.generate_started.wait()
            transition = asyncio.create_task(manager.set_mode("GAMING"))
            await asyncio.sleep(0)
            status = await manager.status()
            assert status["mode"] == "GAMING"
            assert status["active_requests"] == 1
            assert status["local_requests_blocked"]
            assert not backend.unload_started.is_set()
            with pytest.raises(ApplicationError) as caught:
                await manager.generate("新请求不能进入")
            assert caught.value.status_code == 423
            with pytest.raises(ApplicationError) as caught:
                await manager.set_mode("AUTO")
            assert caught.value.status_code == 409
            backend.generate_release.set()
            await generation
            assert (await transition)["unload_verified"]
            assert backend.calls == [("generate", "已接纳请求"), ("unload", "practice:tiny")]
        finally:
            backend.generate_release.set()
            await manager.close()

    asyncio.run(scenario())


def test_drain_timeout_preserves_gaming_without_cancelling_generation(tmp_path):
    async def scenario():
        backend = FakeBackend()
        backend.generate_release = asyncio.Event()
        manager = manager_for(tmp_path, backend, drain_timeout=0.01)
        await manager.start()
        try:
            generation = asyncio.create_task(manager.generate("需要时间的请求"))
            await backend.generate_started.wait()
            with pytest.raises(ApplicationError) as caught:
                await manager.set_mode("GAMING")
            assert caught.value.code == "GPU_DRAIN_TIMEOUT"
            assert not generation.done()
            assert (await manager.status())["mode"] == "GAMING"
            assert not (await manager.status())["unload_verified"]
            assert not backend.unload_started.is_set()
            backend.generate_release.set()
            await generation
            # 推理结束后由显式补偿任务复用原卸载流程，不需要空闲调度或人工重试。
            for _ in range(100):
                if (await manager.status())["unload_verified"]:
                    break
                await asyncio.sleep(0.01)
            status = await manager.status()
            assert status["unload_verified"]
            assert status["backend"]["models"] == []
            assert status["mode"] == "GAMING"
            assert status["manual_gaming"] is True
            assert status["pending_unloads"] == 0
            assert backend.unload_started.is_set()
            assert (await manager.set_mode("AUTO"))["mode"] == "AUTO"
        finally:
            backend.generate_release.set()
            await manager.close()

    asyncio.run(scenario())


def test_cancelled_caller_keeps_worker_active_and_blocks_competing_request(tmp_path):
    async def scenario():
        backend = FakeBackend()
        backend.generate_release = asyncio.Event()
        manager = manager_for(tmp_path, backend, drain_timeout=1)
        await manager.start()
        try:
            caller = asyncio.create_task(manager.generate("调用者将断开"))
            await backend.generate_started.wait()
            caller.cancel()
            with pytest.raises(asyncio.CancelledError):
                await caller
            assert (await manager.status())["active_requests"] == 1
            with pytest.raises(ApplicationError) as caught:
                await manager.generate("不能与后台线程争抢")
            assert caught.value.code == "GPU_BUSY"
            transition = asyncio.create_task(manager.set_mode("GAMING"))
            await asyncio.sleep(0)
            assert not transition.done()
            backend.generate_release.set()
            assert (await transition)["unload_verified"]
            assert (await manager.status())["active_requests"] == 0
        finally:
            backend.generate_release.set()
            await manager.close()

    asyncio.run(scenario())


@pytest.mark.parametrize("failure", ["unload_error", "retain_after_unload", "status_error"])
def test_failed_or_unverified_unload_remains_locked(tmp_path, failure):
    async def scenario():
        backend = FakeBackend()
        setattr(backend, failure, True)
        manager = manager_for(tmp_path, backend)
        await manager.start()
        try:
            with pytest.raises(ApplicationError) as caught:
                await manager.set_mode("GAMING")
            assert caught.value.status_code == 503
            status = await manager.status()
            assert status["mode"] == "GAMING"
            assert status["local_requests_blocked"]
            assert not status["unload_verified"]
            if failure == "status_error":
                assert status["backend"]["available"] is False
                assert status["backend"]["models"] is None
            with pytest.raises(ApplicationError):
                await manager.set_mode("AUTO")
            with pytest.raises(ApplicationError):
                await manager.wake()
            setattr(backend, failure, False)
            assert (await manager.unload())["unload_verified"]
            assert (await manager.set_mode("AUTO"))["fault"] is None
        finally:
            await manager.close()

    asyncio.run(scenario())


def test_uncertain_generation_requires_explicit_unload_before_recovery(tmp_path):
    async def scenario():
        backend = FakeBackend()
        backend.generate_error = True
        manager = manager_for(tmp_path, backend)
        await manager.start()
        try:
            with pytest.raises(ApplicationError) as caught:
                await manager.generate("合成失败")
            assert caught.value.code == "GPU_UPSTREAM_FAILED"
            assert (await manager.status())["mode"] == "GAMING"
            with pytest.raises(ApplicationError):
                await manager.set_mode("AUTO")
            backend.generate_error = False
            await manager.unload()
            await manager.set_mode("AUTO")
            assert (await manager.generate("恢复后请求"))["done"]
        finally:
            await manager.close()

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "saved",
    [
        {"mode": "GAMING", "uncertain": False},
        {"mode": "AUTO", "uncertain": True},
        {"mode": "AUTO", "fault": "UNLOAD_NOT_VERIFIED"},
        "not-json",
    ],
)
def test_restarting_never_loses_gaming_or_uncertain_gate(tmp_path, saved):
    async def scenario():
        state_path = tmp_path / "gpu-mode.json"
        original = saved if isinstance(saved, str) else json.dumps(saved)
        state_path.write_text(original, encoding="utf-8")
        backend = FakeBackend()
        manager = manager_for(tmp_path, backend)
        await manager.start()
        try:
            status = await manager.status()
            assert status["mode"] == "GAMING"
            assert status["local_requests_blocked"]
            assert backend.calls == []
            with pytest.raises(ApplicationError):
                await manager.generate("重启后也不得加载")
            if isinstance(saved, str):
                assert state_path.read_text(encoding="utf-8") == original
        finally:
            await manager.close()

    asyncio.run(scenario())


def test_cancelled_control_caller_does_not_cancel_unload(tmp_path):
    async def scenario():
        backend = FakeBackend()
        backend.unload_release = asyncio.Event()
        manager = manager_for(tmp_path, backend)
        await manager.start()
        try:
            caller = asyncio.create_task(manager.set_mode("GAMING"))
            await backend.unload_started.wait()
            caller.cancel()
            with pytest.raises(asyncio.CancelledError):
                await caller
            with pytest.raises(ApplicationError):
                await manager.set_mode("AUTO")
            backend.unload_release.set()
            for _ in range(30):
                if (await manager.status())["unload_verified"]:
                    break
                await asyncio.sleep(0.01)
            assert (await manager.status())["unload_verified"]
            assert backend.models == []
            assert (await manager.status())["mode"] == "GAMING"
        finally:
            backend.unload_release.set()
            await manager.close()

    asyncio.run(scenario())


def test_existing_other_model_is_not_evicted_by_wake(tmp_path):
    async def scenario():
        backend = FakeBackend()
        backend.models = [{"name": "other:large", "size_vram": 456}]
        manager = manager_for(tmp_path, backend)
        await manager.start()
        try:
            with pytest.raises(ApplicationError) as caught:
                await manager.wake()
            assert caught.value.code == "GPU_MODEL_CONFLICT"
            assert backend.calls == []
            assert backend.models == [{"name": "other:large", "size_vram": 456}]
        finally:
            await manager.close()

    asyncio.run(scenario())


def test_shutdown_keeps_lease_while_cancelled_control_still_runs(tmp_path):
    async def scenario():
        backend = FakeBackend()
        backend.unload_release = asyncio.Event()
        manager = manager_for(tmp_path, backend, drain_timeout=0.01)
        competing = manager_for(tmp_path, backend, drain_timeout=0.01)
        await manager.start()
        try:
            caller = asyncio.create_task(manager.set_mode("GAMING"))
            await backend.unload_started.wait()
            caller.cancel()
            with pytest.raises(asyncio.CancelledError):
                await caller
            await manager.close()
            # 卸载线程仍在操作同一 Ollama，不能让第二个调度实例接管并重新加载。
            with pytest.raises(RuntimeError, match="调度实例|单 worker"):
                await competing.start()
        finally:
            await competing.close()
            backend.unload_release.set()
            for _ in range(30):
                if (await manager.status())["unload_verified"]:
                    break
                await asyncio.sleep(0.01)
            await manager.close()

    asyncio.run(scenario())
