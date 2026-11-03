"""压力保护不等同游戏分类，不取消推理/训练，仅处理空闲模型。"""

import asyncio
import copy
import json

import pytest

from nova.errors import ApplicationError
from nova.gpu.manager import GpuManager
from nova.gpu.pressure import MIB, MemoryPressureGuard, MemoryPressurePolicy


def sample(*, used=15500, external=2000, activity=30, vendor=0x10DE):
    return {
        "available": True,
        "processes": [
            {"pid": 10, "ppid": 1, "name": "llama-server.exe"},
            {"pid": 20, "ppid": 1, "name": "python.exe"},
        ],
        "gpu": {
            "available": True,
            "warming_up": False,
            "adapters": [
                {
                    "luid": "nv",
                    "vendor_id": vendor,
                    "total_dedicated_bytes": 16000 * MIB,
                    "dedicated_used_bytes": used * MIB,
                    "probe_budget_bytes": 14000 * MIB,
                    "budget_scope": "current_process",
                }
            ],
            "processes": {
                10: {
                    "per_adapter": {
                        "nv": {"dedicated_bytes": 12000 * MIB, "engines": {"Compute": 90}}
                    }
                },
                20: {
                    "per_adapter": {
                        "nv": {"dedicated_bytes": external * MIB, "engines": {"Compute": activity}}
                    }
                },
            },
        },
    }


def test_pressure_requires_multiple_signals_and_two_samples():
    policy = MemoryPressurePolicy(own_pid=99)
    first = policy.evaluate(sample())
    assert first["state"] == "confirming" and not first["active"]
    second = policy.evaluate(sample())
    assert second["active"] and second["state"] == "pressure"
    assert "EXTERNAL_ENGINE_ACTIVITY" in second["reasons"]
    assert second["adapters"][0]["budget_scope"] == "current_process"
    assert second["adapters"][0]["global_budget_bytes"] is None


@pytest.mark.parametrize(
    "snapshot", [sample(activity=0), sample(external=0), sample(used=10000), sample(vendor=0x1002)]
)
def test_threshold_or_idle_or_integrated_gpu_alone_never_triggers(snapshot):
    policy = MemoryPressurePolicy(own_pid=99)
    for _ in range(4):
        assert not policy.evaluate(snapshot)["active"]


def test_nova_children_are_excluded_but_external_training_demand_is_allowed():
    policy = MemoryPressurePolicy(own_pid=99)
    owned = sample()
    owned["processes"][1]["ppid"] = 99
    for _ in range(3):
        assert not policy.evaluate(owned)["active"]
    assert not policy.evaluate(sample())["active"]
    assert policy.evaluate(sample())["active"]


def test_counter_loss_keeps_previous_hold_and_resets_confirmation():
    policy = MemoryPressurePolicy(own_pid=99)
    policy.evaluate(sample())
    assert policy.evaluate(sample())["active"]
    lost = sample()
    lost["gpu"]["available"] = False
    result = policy.evaluate(lost)
    assert result["active"] and not result["available"]
    assert result["state"] == "unavailable"


def test_clear_hysteresis_reserves_room_for_model_reload():
    clock = [0.0]
    policy = MemoryPressurePolicy(clock=lambda: clock[0], own_pid=99)
    policy.evaluate(sample())
    policy.evaluate(sample())
    policy.reserve_bytes = 12000 * MIB
    insufficient = policy.evaluate(sample(used=3000))
    assert insufficient["active"] and insufficient["state"] == "reserved_headroom_required"
    policy.reserve_bytes = 3000 * MIB
    clear = sample(used=4000)
    assert policy.evaluate(clear)["active"]
    clock[0] = 19.9
    assert policy.evaluate(clear)["active"]
    clock[0] = 20
    assert not policy.evaluate(clear)["active"]


class Backend:
    model = "practice:tiny"

    def __init__(self):
        self.models = [{"name": self.model, "size_vram": 3000 * MIB}]
        self.entered = asyncio.Event()
        self.release = None
        self.unloads = 0

    async def get_model_status(self):
        return copy.deepcopy(self.models)

    async def get_gpu_memory_usage(self):
        return {"available": True, "gpus": [{"index": 0, "total_mib": 16311, "used_mib": 1000}]}

    async def unload_model(self, name):
        self.models = []
        self.unloads += 1
        return {"done": True}

    async def generate(self, prompt):
        self.entered.set()
        if self.release:
            await self.release.wait()
        return {"done": True, "response": "synthetic"}

    async def wake_model(self):
        return {"done": True}


def test_pressure_defers_unload_until_inference_idle_and_records_reason(tmp_path, practice_policy):
    async def run():
        backend = Backend()
        backend.release = asyncio.Event()
        manager = GpuManager(
            backend, tmp_path / "gpu.json", pressure_enabled=True, model_policies=practice_policy()
        )
        await manager.start()
        work = asyncio.create_task(manager.generate("synthetic"))
        try:
            await backend.entered.wait()
            await manager.set_memory_pressure(True, {"active": True}, unload_idle=True)
            assert not work.done() and backend.unloads == 0
            with pytest.raises(ApplicationError):
                await manager.generate("blocked")
            backend.release.set()
            await work
            await manager.set_memory_pressure(True, {"active": True}, unload_idle=True)
            assert backend.unloads == 1 and manager.memory_pressure_hold
            assert not manager.manual_gaming
            assert manager.last_operation["reason"] == "memory_pressure"
            assert manager.memory_pressure_reserve_bytes == 3000 * MIB
            await manager.set_memory_pressure(False, {"active": False})
            assert not manager.blocked and manager.mode == "AUTO"
        finally:
            backend.release.set()
            await asyncio.gather(work, return_exceptions=True)
            await manager.close()

    asyncio.run(run())


def test_pressure_release_never_clears_manual_game_mode_and_state_survives(
    tmp_path, practice_policy
):
    async def run():
        backend = Backend()
        path = tmp_path / "gpu.json"
        manager = GpuManager(backend, path, pressure_enabled=True, model_policies=practice_policy())
        await manager.start()
        await manager.set_memory_pressure(True, {"active": True}, unload_idle=True)
        await manager.close()
        restored = GpuManager(
            backend, path, pressure_enabled=True, model_policies=practice_policy()
        )
        await restored.start()
        try:
            assert restored.memory_pressure_hold and restored.blocked
            assert json.loads(path.read_text())["memory_pressure_reserve_bytes"] == 3000 * MIB
            await restored.set_mode("GAMING")
            await restored.set_memory_pressure(False, {"active": False})
            assert restored.manual_gaming and restored.mode == "GAMING" and restored.blocked
        finally:
            await restored.close()

    asyncio.run(run())


def test_pressure_monitor_works_without_game_detection(tmp_path, practice_policy):
    async def run():
        class Source:
            async def observe(self):
                return sample()

        backend = Backend()
        manager = GpuManager(
            backend, tmp_path / "gpu.json", pressure_enabled=True, model_policies=practice_policy()
        )
        await manager.start()
        guard = MemoryPressureGuard(manager, Source(), policy=MemoryPressurePolicy(own_pid=99))
        try:
            await guard.poll_once()
            assert backend.unloads == 0
            await guard.poll_once()
            assert backend.unloads == 1
            assert not manager.detection_enabled and manager.memory_pressure_hold
            assert not manager.manual_gaming
        finally:
            await guard.stop()
            await manager.close()

    asyncio.run(run())
