"""回归控制队列与关闭时的实际竞态；仅用临时状态和进程内替身。"""

import asyncio
import json

import pytest

from nova.errors import ApplicationError
from nova.gpu.manager import GpuManager


class StatusGateBackend:
    """让第一个 AUTO 的状态查询占住控制锁，后续操作按顺序排队。"""

    def __init__(self):
        self.status_entered = asyncio.Event()
        self.status_release = asyncio.Event()
        self.status_calls = 0
        self.generation_calls = 0

    async def get_model_status(self):
        self.status_calls += 1
        if self.status_calls == 1:
            self.status_entered.set()
            await self.status_release.wait()
        return []

    async def get_gpu_memory_usage(self):
        return {"available": True, "gpus": []}

    async def generate(self, prompt):
        self.generation_calls += 1
        raise AssertionError("游戏模式中的请求不应到达推理后端")


class UnloadGateBackend:
    def __init__(self):
        self.unload_entered = asyncio.Event()
        self.unload_release = asyncio.Event()
        self.loaded = True

    async def get_model_status(self):
        return [{"name": "race:tiny", "size_vram": 123}] if self.loaded else []

    async def get_gpu_memory_usage(self):
        return {"available": True, "gpus": []}

    async def unload_model(self, name):
        self.unload_entered.set()
        await self.unload_release.wait()
        self.loaded = False
        return {"done": True}


def test_queued_auto_cannot_overwrite_later_gaming_request(tmp_path):
    async def scenario():
        backend = StatusGateBackend()
        state_path = tmp_path / "gpu-mode.json"
        manager = GpuManager(backend, state_path, drain_timeout=0.1)
        await manager.start()
        tasks = []
        try:
            first_auto = asyncio.create_task(manager.set_mode("AUTO"))
            tasks.append(first_auto)
            await asyncio.wait_for(backend.status_entered.wait(), timeout=1)
            # 第二个 AUTO 已通过锁外检查，随后等待第一个 AUTO 持有的锁。
            queued_auto = asyncio.create_task(manager.set_mode("AUTO"))
            tasks.append(queued_auto)
            await asyncio.sleep(0)
            assert not queued_auto.done()
            gaming = asyncio.create_task(manager.set_mode("GAMING"))
            tasks.append(gaming)
            await asyncio.sleep(0)
            assert manager.mode == "GAMING"
            assert manager.blocked

            backend.status_release.set()
            results = await asyncio.wait_for(
                asyncio.gather(*tasks, return_exceptions=True), timeout=2
            )
            assert not isinstance(results[0], Exception)
            assert isinstance(results[1], ApplicationError)
            assert results[1].code == "GPU_TRANSITION_BUSY"
            assert results[2]["mode"] == "GAMING"
            assert results[2]["unload_verified"]
            assert manager.mode == "GAMING"
            assert manager.blocked
            assert json.loads(state_path.read_text(encoding="utf-8"))["mode"] == "GAMING"
            with pytest.raises(ApplicationError) as caught:
                await manager.generate("游戏期间不能重新加载")
            assert caught.value.status_code == 423
            assert backend.generation_calls == 0
        finally:
            backend.status_release.set()
            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)
            await manager.close()

    asyncio.run(scenario())


def test_close_retains_instance_lease_until_control_task_finishes(tmp_path):
    async def scenario():
        backend = UnloadGateBackend()
        state_path = tmp_path / "gpu-mode.json"
        manager = GpuManager(backend, state_path, drain_timeout=0.01)
        competitor = GpuManager(backend, state_path, drain_timeout=0.01)
        await manager.start()
        control = asyncio.create_task(manager.unload())
        try:
            await asyncio.wait_for(backend.unload_entered.wait(), timeout=1)
            await asyncio.wait_for(manager.close(), timeout=1)
            assert not control.done()
            assert backend.loaded
            saved = json.loads(state_path.read_text(encoding="utf-8"))
            assert saved["mode"] == "GAMING"
            assert saved["uncertain"] is True
            assert saved["fault"] == "SHUTDOWN_OPERATION_UNCERTAIN"
            # 用真正的 OS 锁竞争证明旧卸载仍运行时新实例不能取得调度权。
            with pytest.raises(RuntimeError, match="已有 GPU 调度实例"):
                await competitor.start()

            backend.unload_release.set()
            assert (await asyncio.wait_for(control, timeout=1))["unload_verified"]
            await manager.close()
            await competitor.start()
            assert competitor.mode == "GAMING"
            assert competitor.blocked
        finally:
            backend.unload_release.set()
            await asyncio.gather(control, return_exceptions=True)
            await manager.close()
            await competitor.close()

    asyncio.run(scenario())
