"""自动检测只能解除自己的锁定，不能覆盖手动意图或既有故障。"""

import asyncio
import json

import pytest

from nova.errors import ApplicationError
from nova.gpu.manager import GpuManager


def practice_policy():
    return {
        "practice:tiny": {
            "model": "practice:tiny",
            "enabled": True,
            "required_mib": 1,
            "min_total_ram_mib": 0,
            "min_available_ram_mib": 0,
            "runtime": {},
        }
    }


class FakeBackend:
    model = "practice:tiny"

    def __init__(self):
        self.models = [{"name": self.model, "size_vram": 123}]
        self.unloads = 0
        self.generated = 0
        self.unload_entered = asyncio.Event()
        self.unload_release = None

    async def get_model_status(self):
        return [dict(model) for model in self.models]

    async def get_gpu_memory_usage(self):
        return {"available": True, "gpus": [{"index": 0, "total_mib": 16311, "used_mib": 1000}]}

    async def unload_model(self, name):
        self.unloads += 1
        self.unload_entered.set()
        if self.unload_release is not None:
            await self.unload_release.wait()
        self.models = [model for model in self.models if model["name"] != name]
        return {"done": True}

    async def generate(self, prompt):
        self.generated += 1
        return {"done": True, "response": "合成回复"}


def info(hold):
    return {
        "enabled": True,
        "ready": True,
        "state": "gaming" if hold else "monitoring",
        "matched_processes": ["practice-game.exe"] if hold else [],
        "error": None,
        "last_scan_at": "2026-11-01T00:00:00+00:00",
        "clear_for_seconds": 0 if hold else 60,
        "exit_grace_seconds": 60,
    }


def manager_for(tmp_path, backend):
    return GpuManager(
        backend,
        tmp_path / "gpu-mode.json",
        detection_enabled=True,
        model_policies=practice_policy(),
    )


def test_manual_gaming_survives_game_exit_and_requires_explicit_auto(tmp_path):
    async def scenario():
        backend = FakeBackend()
        manager = manager_for(tmp_path, backend)
        await manager.start()
        try:
            await manager.set_detected_gaming(False, info(False))
            await manager.set_mode("GAMING")
            assert manager.manual_gaming
            await manager.set_detected_gaming(True, info(True))
            await manager.set_detected_gaming(False, info(False))
            assert manager.mode == "GAMING"
            assert manager.manual_gaming
            assert not manager.detected_gaming
            assert manager.blocked
            with pytest.raises(ApplicationError) as caught:
                await manager.generate("手动模式不能因退出游戏解除")
            assert caught.value.status_code == 423
            assert backend.generated == 0
            state = json.loads(manager.state_path.read_text(encoding="utf-8"))
            assert state["manual_gaming"] is True
            assert state["detected_gaming"] is False
            await manager.set_mode("AUTO")
            assert manager.mode == "AUTO"
            assert not manager.manual_gaming
            assert not manager.blocked
        finally:
            await manager.close()

    asyncio.run(scenario())


def test_manual_auto_cannot_override_detected_game_or_discard_manual_hold(tmp_path):
    async def scenario():
        backend = FakeBackend()
        manager = manager_for(tmp_path, backend)
        await manager.start()
        try:
            await manager.set_detected_gaming(False, info(False))
            await manager.set_mode("GAMING")
            await manager.set_detected_gaming(True, info(True))
            with pytest.raises(ApplicationError) as caught:
                await manager.set_mode("AUTO")
            assert caught.value.status_code == 409
            assert manager.manual_gaming
            assert manager.detected_gaming
            assert manager.blocked
            await manager.set_detected_gaming(False, info(False))
            assert manager.manual_gaming
            assert manager.mode == "GAMING"
            assert backend.generated == 0
        finally:
            await manager.close()

    asyncio.run(scenario())


def test_explicit_unload_is_a_manual_hold_not_a_temporary_detector_hold(tmp_path):
    async def scenario():
        backend = FakeBackend()
        manager = manager_for(tmp_path, backend)
        await manager.start()
        try:
            await manager.set_detected_gaming(False, info(False))
            await manager.unload()
            assert manager.manual_gaming
            await manager.set_detected_gaming(True, info(True))
            await manager.set_detected_gaming(False, info(False))
            assert manager.manual_gaming
            assert manager.mode == "GAMING"
            assert backend.generated == 0
        finally:
            await manager.close()

    asyncio.run(scenario())


def test_legacy_gaming_state_is_conservatively_migrated_to_manual_hold(tmp_path):
    async def scenario():
        state_path = tmp_path / "gpu-mode.json"
        state_path.write_text(json.dumps({"mode": "GAMING", "uncertain": False}), encoding="utf-8")
        backend = FakeBackend()
        manager = manager_for(tmp_path, backend)
        await manager.start()
        try:
            assert manager.manual_gaming
            await manager.set_detected_gaming(False, info(False))
            assert manager.mode == "GAMING"
            assert manager.blocked
            assert backend.unloads == 0
            state = json.loads(state_path.read_text(encoding="utf-8"))
            assert state["manual_gaming"] is True
            assert state["detected_gaming"] is False
        finally:
            await manager.close()

    asyncio.run(scenario())


def test_automatic_hold_is_persisted_without_becoming_manual(tmp_path):
    async def scenario():
        backend = FakeBackend()
        manager = manager_for(tmp_path, backend)
        await manager.start()
        await manager.set_detected_gaming(True, info(True))
        state = json.loads(manager.state_path.read_text(encoding="utf-8"))
        assert state["mode"] == "GAMING"
        assert state["manual_gaming"] is False
        assert state["detected_gaming"] is True
        await manager.close()
        restarted = manager_for(tmp_path, backend)
        await restarted.start()
        try:
            assert not restarted.manual_gaming
            assert restarted.detected_gaming
            assert restarted.mode == "GAMING"
            assert restarted.blocked
        finally:
            await restarted.close()

    asyncio.run(scenario())


def test_manual_hold_added_during_auto_unload_survives_automatic_release(tmp_path):
    async def scenario():
        backend = FakeBackend()
        backend.unload_release = asyncio.Event()
        manager = manager_for(tmp_path, backend)
        await manager.start()
        tasks = []
        try:
            detected = asyncio.create_task(manager.set_detected_gaming(True, info(True)))
            tasks.append(detected)
            await asyncio.wait_for(backend.unload_entered.wait(), timeout=1)
            manual = asyncio.create_task(manager.set_mode("GAMING"))
            tasks.append(manual)
            await asyncio.sleep(0)
            assert manager.manual_gaming
            backend.unload_release.set()
            await asyncio.wait_for(asyncio.gather(*tasks), timeout=2)
            await manager.set_detected_gaming(False, info(False))
            assert manager.manual_gaming
            assert manager.mode == "GAMING"
            assert manager.blocked
            assert backend.generated == 0
        finally:
            backend.unload_release.set()
            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)
            await manager.close()

    asyncio.run(scenario())
