"""使用可控进程快照和单调时钟验证游戏检测，不启动真实游戏或模型。"""

import asyncio

import pytest

from nova.errors import ApplicationError
from nova.gpu.detection import GameDetector
from nova.gpu.manager import GpuManager
from nova.gpu.processes import ProcessReadError


class Clock:
    def __init__(self):
        self.value = 100.0

    def __call__(self):
        return self.value


class ProcessSource:
    def __init__(self, names=()):
        self.names = set(names)
        self.failure = False
        self.entered = asyncio.Event()
        self.release = None
        self.calls = 0

    async def __call__(self):
        self.calls += 1
        self.entered.set()
        if self.release is not None:
            await self.release.wait()
        if self.failure:
            raise ProcessReadError("synthetic process scan failure")
        return set(self.names)


class FakeBackend:
    model = "practice:tiny"

    def __init__(self):
        self.models = [{"name": self.model, "size_vram": 123}]
        self.unloads = 0
        self.generations = 0
        self.wakes = 0
        self.fail_unload = False

    async def get_model_status(self):
        return [dict(model) for model in self.models]

    async def get_gpu_memory_usage(self):
        return {"available": True, "gpus": [{"index": 0, "total_mib": 16311, "used_mib": 1000}]}

    async def unload_model(self, name):
        self.unloads += 1
        if self.fail_unload:
            from nova.gpu.backend import BackendError

            raise BackendError("synthetic unload failure")
        self.models = [model for model in self.models if model["name"] != name]
        return {"done": True}

    async def generate(self, prompt):
        self.generations += 1
        return {"done": True, "response": "合成回复"}

    async def wake_model(self):
        self.wakes += 1
        return {"done": True}


@pytest.fixture
def prepare(practice_policy):
    def make(tmp_path, names=()):
        source, clock, backend = ProcessSource(names), Clock(), FakeBackend()
        manager = GpuManager(
            backend,
            tmp_path / "gpu-mode.json",
            detection_enabled=True,
            model_policies=practice_policy(),
        )
        detector = GameDetector(
            manager, ["practice-game.exe"], scan=source, clock=clock, interval=5, exit_grace=60
        )
        return source, clock, backend, manager, detector

    return make


def test_initial_scan_blocks_requests_until_healthy_snapshot_arrives(tmp_path, prepare):
    async def scenario():
        source, _, backend, manager, detector = prepare(tmp_path)
        source.release = asyncio.Event()
        await manager.start()
        task = asyncio.create_task(detector.poll_once())
        try:
            await asyncio.wait_for(source.entered.wait(), timeout=1)
            assert manager.blocked
            with pytest.raises(ApplicationError) as caught:
                await manager.generate("首扫未完成不能进入GPU")
            assert caught.value.status_code == 423
            assert backend.generations == 0
            source.release.set()
            await task
            state = await manager.status()
            assert state["game_detection"]["ready"]
            assert state["mode"] == "AUTO"
            assert not state["local_requests_blocked"]
        finally:
            source.release.set()
            await asyncio.gather(task, return_exceptions=True)
            await detector.stop()
            await manager.close()

    asyncio.run(scenario())


def test_match_unloads_once_and_never_uses_gpu_activity_as_game_signal(tmp_path, prepare):
    async def scenario():
        source, _, backend, manager, detector = prepare(tmp_path, ["practice-game.exe"])
        await manager.start()
        try:
            await detector.poll_once()
            state = await manager.status()
            assert state["mode"] == "GAMING"
            assert state["game_detection"]["matched_processes"] == ["practice-game.exe"]
            assert manager.detected_gaming
            assert not manager.manual_gaming
            assert backend.unloads == 1
            assert backend.models == []
            stamp = manager.last_unload_at
            for _ in range(3):
                await detector.poll_once()
            assert manager.last_unload_at == stamp
            assert backend.unloads == 1
            assert backend.generations == 0
            assert backend.wakes == 0
            # 只有名单中的名称触发检测；不读取或推断游戏的 GPU 利用率。
            source.names = {"unrelated-renderer.exe"}
            await detector.poll_once()
            assert manager.blocked
        finally:
            await detector.stop()
            await manager.close()

    asyncio.run(scenario())


def test_exit_requires_continuous_sixty_seconds_and_does_not_reload(tmp_path, prepare):
    async def scenario():
        source, clock, backend, manager, detector = prepare(tmp_path, ["practice-game.exe"])
        await manager.start()
        try:
            await detector.poll_once()
            source.names.clear()
            clock.value = 110
            await detector.poll_once()
            clock.value = 169.9
            await detector.poll_once()
            assert manager.mode == "GAMING"
            assert manager.blocked
            clock.value = 170
            await detector.poll_once()
            assert manager.mode == "AUTO"
            assert not manager.blocked
            assert not manager.detected_gaming
            assert backend.models == []
            assert backend.generations == 0
            assert backend.wakes == 0
            assert backend.unloads == 1
        finally:
            await detector.stop()
            await manager.close()

    asyncio.run(scenario())


def test_game_reappearing_resets_exit_grace(tmp_path, prepare):
    async def scenario():
        source, clock, _, manager, detector = prepare(tmp_path, ["practice-game.exe"])
        await manager.start()
        try:
            await detector.poll_once()
            source.names.clear()
            clock.value = 101
            await detector.poll_once()
            clock.value = 160
            await detector.poll_once()
            assert manager.blocked
            source.names.add("practice-game.exe")
            clock.value = 160.5
            await detector.poll_once()
            source.names.clear()
            clock.value = 161
            await detector.poll_once()
            clock.value = 220.9
            await detector.poll_once()
            assert manager.blocked
            clock.value = 221
            await detector.poll_once()
            assert manager.mode == "AUTO"
            assert not manager.blocked
        finally:
            await detector.stop()
            await manager.close()

    asyncio.run(scenario())


def test_scan_failure_locks_once_and_resets_healthy_exit_grace(tmp_path, prepare):
    async def scenario():
        source, clock, backend, manager, detector = prepare(tmp_path)
        await manager.start()
        try:
            source.failure = True
            await detector.poll_once()
            assert manager.blocked
            assert manager.detected_gaming
            first_stamp = manager.last_unload_at
            assert (await manager.status())["game_detection"]["error"]
            for _ in range(3):
                await detector.poll_once()
            assert manager.last_unload_at == first_stamp
            assert backend.unloads == 1
            source.failure = False
            clock.value = 110
            await detector.poll_once()
            clock.value = 169
            source.failure = True
            await detector.poll_once()
            source.failure = False
            clock.value = 170
            await detector.poll_once()
            clock.value = 229.9
            await detector.poll_once()
            assert manager.blocked
            clock.value = 230
            await detector.poll_once()
            assert manager.mode == "AUTO"
            assert not manager.blocked
            assert backend.wakes == 0
        finally:
            await detector.stop()
            await manager.close()

    asyncio.run(scenario())


def test_saved_automatic_hold_requires_new_full_exit_grace_after_restart(tmp_path, prepare):
    async def scenario():
        source, _, _, manager, detector = prepare(tmp_path, ["practice-game.exe"])
        await manager.start()
        await detector.poll_once()
        await detector.stop()
        await manager.close()
        source, clock, backend, restarted, new_detector = prepare(tmp_path)
        await restarted.start()
        try:
            assert restarted.detected_gaming
            assert not restarted.manual_gaming
            clock.value = 1000
            await new_detector.poll_once()
            assert restarted.blocked
            clock.value = 1059.9
            await new_detector.poll_once()
            assert restarted.blocked
            clock.value = 1060
            await new_detector.poll_once()
            assert restarted.mode == "AUTO"
            assert not restarted.blocked
            assert backend.wakes == 0
            assert backend.generations == 0
        finally:
            await new_detector.stop()
            await restarted.close()

    asyncio.run(scenario())


def test_successful_scans_cannot_clear_a_failed_unload_fault(tmp_path, prepare):
    async def scenario():
        source, clock, backend, manager, detector = prepare(tmp_path, ["practice-game.exe"])
        backend.fail_unload = True
        await manager.start()
        try:
            await detector.poll_once()
            original_fault = manager.fault
            assert original_fault
            source.names.clear()
            clock.value = 110
            await detector.poll_once()
            clock.value = 170
            await detector.poll_once()
            state = await manager.status()
            assert manager.fault == original_fault
            assert state["mode"] == "GAMING"
            assert state["local_requests_blocked"]
            assert not state["unload_verified"]
            assert backend.unloads == 1
            assert backend.generations == 0
            assert backend.wakes == 0
        finally:
            await detector.stop()
            await manager.close()

    asyncio.run(scenario())


def test_stopping_detector_does_not_unlock_game_mode(tmp_path, prepare):
    async def scenario():
        _, _, backend, manager, detector = prepare(tmp_path, ["practice-game.exe"])
        await manager.start()
        try:
            await detector.start()
            assert manager.blocked
            await detector.stop()
            assert manager.blocked
            assert manager.mode == "GAMING"
            assert backend.generations == 0
            assert backend.wakes == 0
            with pytest.raises(ApplicationError):
                await manager.generate("检测停止不能解除游戏门禁")
        finally:
            await detector.stop()
            await manager.close()

    asyncio.run(scenario())


def test_scan_finishing_after_stop_cannot_apply_stale_auto_recovery(tmp_path, prepare):
    async def scenario():
        source, clock, backend, manager, detector = prepare(tmp_path, ["practice-game.exe"])
        await manager.start()
        task = None
        try:
            await detector.poll_once()
            source.names.clear()
            clock.value = 110
            await detector.poll_once()
            clock.value = 170
            source.entered.clear()
            source.release = asyncio.Event()
            task = asyncio.create_task(detector.poll_once())
            await asyncio.wait_for(source.entered.wait(), timeout=1)
            await detector.stop()
            source.release.set()
            await asyncio.wait_for(task, timeout=1)
            assert manager.detected_gaming
            assert manager.mode == "GAMING"
            assert manager.blocked
            assert backend.unloads == 1
            assert backend.generations == 0
            assert backend.wakes == 0
        finally:
            if source.release is not None:
                source.release.set()
            if task is not None:
                await asyncio.gather(task, return_exceptions=True)
            await detector.stop()
            await manager.close()

    asyncio.run(scenario())


def test_overlapping_polls_serialize_snapshots_instead_of_releasing_a_new_game(tmp_path, prepare):
    async def scenario():
        source, clock, backend, manager, detector = prepare(tmp_path, ["practice-game.exe"])
        entered, release = asyncio.Event(), asyncio.Event()
        scans = 0
        tasks = []

        async def snapshot():
            nonlocal scans
            scans += 1
            if scans == 1:
                entered.set()
                await release.wait()
                return set()
            return {"practice-game.exe"}

        await manager.start()
        try:
            await detector.poll_once()
            source.names.clear()
            clock.value = 110
            await detector.poll_once()
            clock.value = 170
            detector.scan = snapshot
            tasks.append(asyncio.create_task(detector.poll_once()))
            await asyncio.wait_for(entered.wait(), timeout=1)
            tasks.append(asyncio.create_task(detector.poll_once()))
            await asyncio.sleep(0)
            assert scans == 1
            release.set()
            await asyncio.wait_for(asyncio.gather(*tasks), timeout=2)
            assert scans == 2
            assert manager.detected_gaming
            assert manager.mode == "GAMING"
            assert manager.blocked
            assert (await manager.status())["game_detection"]["matched_processes"] == [
                "practice-game.exe"
            ]
            assert backend.generations == 0
            assert backend.wakes == 0
        finally:
            release.set()
            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)
            await detector.stop()
            await manager.close()

    asyncio.run(scenario())
