"""DS-B 专属 GPU Manager 回归：状态机、游戏卸载、后台重载、并发与降级。

全部使用进程内替身和临时状态目录，不访问真实 Ollama、nvidia-smi 或游戏进程。
运行：``python -m pytest tests/test_gpu_regression_dsh.py -q``

回归专用（regression-only）：本文件与 ``test_gpu_manager.py``、
``test_gpu_game_holds.py``、``test_gpu_races.py`` 有场景重叠；重复的 fake 只服务
本文件的完整链路，新增用例优先补到既有套件，避免继续扩大维护面。
"""

import asyncio
import json
from uuid import uuid4

import pytest

from nova.chat.registry import stable_policies
from nova.errors import ApplicationError
from nova.gpu.backend import BackendError, StreamControl
from nova.gpu.detection import GameDetector
from nova.gpu.manager import GpuManager

MODEL = "qwen3.5:4b"
MESSAGES = [{"role": "user", "content": "DS-B 合成输入"}]
GAME_INFO = {
    "enabled": True,
    "ready": True,
    "state": "game_detected",
    "matched_processes": ["probe-game.exe"],
    "error": None,
    "last_scan_at": "2026-11-01T00:00:00+00:00",
    "clear_for_seconds": 0,
    "exit_grace_seconds": 60,
}
CLEAR_INFO = {**GAME_INFO, "state": "monitoring", "matched_processes": []}


class Clock:
    def __init__(self, value=100.0):
        self.value = value

    def __call__(self):
        return self.value


class ProcessSource:
    def __init__(self, names=()):
        self.names = set(names)

    async def __call__(self):
        return set(self.names)


class FakeBackend:
    """可控的异步 Ollama/GPU 替身，记录调用但不写真实资源。"""

    model = MODEL

    def __init__(self):
        self.base_url = "ds-b-regression-" + str(uuid4())
        self.models = []
        self.calls = []
        self.memory = {
            "available": True,
            "gpus": [{"index": 0, "total_mib": 16311, "used_mib": 2000}],
        }
        self.generate_started = asyncio.Event()
        self.generate_release = None
        self.unload_started = asyncio.Event()
        self.unload_release = None
        self.stream_started = asyncio.Event()
        self.stream_release = None
        self.status_error = False
        self.generate_error = False
        self.wake_error = False
        self.unload_error = False
        self.unload_calls = 0

    async def get_model_status(self):
        self.calls.append(("status",))
        if self.status_error:
            raise BackendError("synthetic unavailable")
        return [dict(item) for item in self.models]

    async def get_gpu_memory_usage(self):
        self.calls.append(("memory",))
        return {
            "available": self.memory["available"],
            "gpus": [dict(item) for item in self.memory["gpus"]],
        }

    async def unload_model(self, name):
        self.calls.append(("unload", name))
        self.unload_calls += 1
        self.unload_started.set()
        if self.unload_release is not None:
            await self.unload_release.wait()
        if self.unload_error:
            raise BackendError("synthetic unload failure")
        self.models = [item for item in self.models if item["name"] != name]
        return {"done": True}

    async def wake_model(self, *, runtime=None):
        self.calls.append(("wake", runtime))
        if self.wake_error:
            raise BackendError("synthetic wake failure")
        self.models = [{"name": self.model, "size_vram": 123}]
        return {"done": True, "model": self.model}

    async def generate(self, prompt, *, runtime=None):
        self.calls.append(("generate", prompt))
        if self.generate_error:
            raise BackendError("synthetic generate failure")
        self.models = [{"name": self.model, "size_vram": 123}]
        self.generate_started.set()
        if self.generate_release is not None:
            await self.generate_release.wait()
        return {"done": True, "model": self.model, "response": "完成"}

    async def ensure_local_model(self, model):
        self.calls.append(("ensure", model))

    async def stream_chat(self, model, messages, emit, *, runtime=None, control=None):
        self.calls.append(("stream", model))
        self.models = [{"name": model, "size_vram": 123}]
        emit("部分回复")
        self.stream_started.set()
        if self.stream_release is not None:
            await self.stream_release.wait()
        return {"done": True, "model": model, "response": "部分回复", "stats": {}}


def manager_for(tmp_path, backend, **kwargs):
    kwargs.setdefault("model_policies", stable_policies())
    kwargs.setdefault("drain_timeout", 1.0)
    kwargs.setdefault("unload_timeout", 1.0)
    return GpuManager(backend, tmp_path / "gpu-mode.json", **kwargs)


def detector_for(manager, source, clock):
    return GameDetector(
        manager,
        ["probe-game.exe"],
        scan=source,
        clock=clock,
        interval=5,
        exit_grace=60,
    )


async def wait_until(predicate, timeout=2.0):
    deadline = asyncio.get_running_loop().time() + timeout
    while not predicate():
        if asyncio.get_running_loop().time() >= deadline:
            raise AssertionError("condition not reached before timeout")
        await asyncio.sleep(0.01)


_MUTATING_CALLS = {"wake", "generate", "stream", "ensure", "unload"}


def mutation_calls(backend):
    """Only loading/unloading calls count as backend mutations; status reads may happen."""
    return [call for call in backend.calls if call[0] in _MUTATING_CALLS]


def test_observable_idle_inference_gaming_and_recovery(tmp_path):
    async def scenario():
        backend = FakeBackend()
        manager = manager_for(tmp_path, backend, drain_timeout=2.0)
        await manager.start()
        transition = None
        work = None
        try:
            # IDLE：AUTO、无活动请求、无驻留模型；AUTO 本身不预载。
            idle = await manager.status()
            assert idle["mode"] == "AUTO"
            assert idle["mode_source"] == "auto"
            assert idle["active_requests"] == 0
            assert idle["backend"]["models"] == []
            assert idle["local_requests_blocked"] is False
            assert idle["unload_verified"] is False
            assert idle["training"]["state"] == "idle"

            backend.generate_release = asyncio.Event()
            work = asyncio.create_task(manager.generate("慢请求"))
            await asyncio.wait_for(backend.generate_started.wait(), timeout=1)

            # 推理中：请求被跟踪，第二个本地请求必须失败而不是并行加载。
            inference = await manager.status()
            assert inference["mode"] == "AUTO"
            assert inference["active_requests"] == 1
            assert inference["backend"]["models"] == [{"name": MODEL, "size_vram": 123}]
            assert json.loads(manager.state_path.read_text(encoding="utf-8"))["uncertain"] is True
            with pytest.raises(ApplicationError) as busy:
                await manager.generate("并发请求")
            assert (busy.value.code, busy.value.status_code) == ("GPU_BUSY", 409)
            assert backend.calls.count(("generate", "慢请求")) == 1

            # 游戏到达：先关闭入口，已接纳请求结束前不能卸载。
            transition = asyncio.create_task(manager.set_mode("GAMING"))
            await wait_until(lambda: manager.mode == "GAMING")
            during = await manager.status()
            assert during["local_requests_blocked"] is True
            assert during["active_requests"] == 1
            assert backend.unload_calls == 0
            with pytest.raises(ApplicationError) as blocked:
                await manager.wake()
            assert blocked.value.code == "GPU_GAMING"

            backend.generate_release.set()
            await work
            gaming = await transition
            assert gaming["mode"] == "GAMING"
            assert gaming["unload_verified"] is True
            assert gaming["backend"]["models"] == []
            assert gaming["active_requests"] == 0
            assert backend.unload_calls == 1
            assert json.loads(manager.state_path.read_text(encoding="utf-8"))["uncertain"] is False

            # 游戏期间所有加载入口保持关闭，AUTO 不预载。
            for action in (manager.wake, lambda: manager.generate("游戏中不能加载")):
                with pytest.raises(ApplicationError) as caught:
                    await action()
                assert caught.value.status_code == 423
            assert backend.calls.count(("wake", None)) == 0
            assert backend.calls.count(("generate", "游戏中不能加载")) == 0

            restored = await manager.set_mode("AUTO")
            assert restored["mode"] == "AUTO"
            assert restored["local_requests_blocked"] is False
            assert restored["backend"]["models"] == []
            assert backend.unload_calls == 1

            # 恢复后只有显式请求才重新加载，且再次卸载可复核。
            assert (await manager.generate("恢复后请求"))["done"] is True
            assert backend.models == [{"name": MODEL, "size_vram": 123}]
            assert (await manager.status())["unload_verified"] is False
            final = await manager.unload()
            assert final["unload_verified"] is True
            assert backend.models == []
            assert backend.unload_calls == 2
        finally:
            backend.generate_release.set()
            if work is not None:
                await asyncio.gather(work, return_exceptions=True)
            if transition is not None:
                await asyncio.gather(transition, return_exceptions=True)
            await manager.close()

    asyncio.run(scenario())


def test_game_detection_waits_for_admitted_inference_then_unloads(tmp_path):
    async def scenario():
        backend = FakeBackend()
        source, clock = ProcessSource(), Clock()
        manager = manager_for(tmp_path, backend, detection_enabled=True, drain_timeout=2.0)
        detector = detector_for(manager, source, clock)
        await manager.start()
        work = None
        try:
            # 首扫无游戏，解除启动时的保守门禁。
            await detector.poll_once()
            assert manager.mode == "AUTO" and not manager.blocked

            backend.generate_release = asyncio.Event()
            work = asyncio.create_task(manager.generate("游戏到达前的请求"))
            await asyncio.wait_for(backend.generate_started.wait(), timeout=1)

            source.names = {"probe-game.exe"}
            poll = asyncio.create_task(detector.poll_once())
            await wait_until(lambda: manager.mode == "GAMING")
            assert manager.detected_gaming is True
            assert backend.unload_calls == 0  # 先排空，不中断已接纳请求。

            backend.generate_release.set()
            await work
            snapshot = await poll
            assert snapshot["matched_processes"] == ["probe-game.exe"]
            assert snapshot["last_action_error"] is None
            assert manager.unload_verified is True
            assert backend.unload_calls == 1
            assert backend.models == []
            assert backend.calls.count(("wake", None)) == 0

            # 游戏退出需要连续健康快照；恢复 AUTO 也不预载模型。
            source.names.clear()
            clock.value = 110.0
            await detector.poll_once()
            assert manager.mode == "GAMING"
            clock.value = 170.0
            await detector.poll_once()
            assert manager.mode == "AUTO" and not manager.blocked
            assert backend.models == []
            assert backend.unload_calls == 1
            assert backend.calls.count(("wake", None)) == 0

            assert (await manager.generate("退出后按需请求"))["done"] is True
            assert backend.models == [{"name": MODEL, "size_vram": 123}]
        finally:
            backend.generate_release.set()
            if work is not None:
                await asyncio.gather(work, return_exceptions=True)
            await detector.stop()
            await manager.close()

    asyncio.run(scenario())


def test_game_detection_drain_timeout_retries_after_inference_finishes(tmp_path):
    async def scenario():
        backend = FakeBackend()
        source, clock = ProcessSource(), Clock()
        manager = manager_for(tmp_path, backend, detection_enabled=True, drain_timeout=0.01)
        detector = detector_for(manager, source, clock)
        await manager.start()
        work = None
        try:
            await detector.poll_once()
            backend.generate_release = asyncio.Event()
            work = asyncio.create_task(manager.generate("超过排空窗口的请求"))
            await asyncio.wait_for(backend.generate_started.wait(), timeout=1)

            source.names = {"probe-game.exe"}
            snapshot = await detector.poll_once()
            assert snapshot["last_action_error"] == "GPU_DRAIN_TIMEOUT"
            assert manager.mode == "GAMING"
            assert manager.fault == "UNLOAD_NOT_VERIFIED"
            assert backend.unload_calls == 0
            assert manager.last_unload_at is None

            backend.generate_release.set()
            await work
            # 推理已经结束，游戏仍在；补偿任务应复用原卸载流程并复核 /api/ps。
            await wait_until(lambda: manager.unload_verified)
            await detector.poll_once()
            assert manager.detected_gaming is True
            assert manager.fault is None
            assert backend.models == []
            assert backend.unload_calls >= 1
            assert manager.last_operation["reason"] == "drain_timeout"
            assert manager._reclaims == {}
        finally:
            backend.generate_release.set()
            if work is not None:
                await asyncio.gather(work, return_exceptions=True)
            await detector.stop()
            await manager.close()

    asyncio.run(scenario())


def test_manual_gaming_retry_recovers_after_drain_timeout(tmp_path):
    async def scenario():
        backend = FakeBackend()
        source, clock = ProcessSource(), Clock()
        manager = manager_for(tmp_path, backend, detection_enabled=True, drain_timeout=0.01)
        detector = detector_for(manager, source, clock)
        await manager.start()
        work = None
        try:
            await detector.poll_once()
            backend.generate_release = asyncio.Event()
            work = asyncio.create_task(manager.generate("慢请求"))
            await asyncio.wait_for(backend.generate_started.wait(), timeout=1)
            source.names = {"probe-game.exe"}
            await detector.poll_once()
            assert manager.fault is not None
            assert manager.blocked is True
            assert backend.models

            backend.generate_release.set()
            await work
            # 补偿卸载自动执行，但不解除检测来源，也不自动恢复 AUTO。
            await wait_until(lambda: manager.unload_verified)
            assert manager.fault is None
            assert manager.mode == "GAMING"
            assert manager.detected_gaming is True

            # 手动 GAMING 仍可显式进入并复核；随后才允许退出保护。
            result = await manager.set_mode("GAMING")
            assert result["unload_verified"] is True
            assert result["backend"]["models"] == []
            assert manager.manual_gaming is True

            await manager.set_detected_gaming(False, CLEAR_INFO)
            assert (await manager.set_mode("AUTO"))["mode"] == "AUTO"
            assert (await manager.wake())["done"] is True
        finally:
            backend.generate_release.set()
            if work is not None:
                await asyncio.gather(work, return_exceptions=True)
            await detector.stop()
            await manager.close()

    asyncio.run(scenario())


def test_game_mode_blocks_every_background_loading_path(tmp_path):
    async def scenario():
        backend = FakeBackend()
        manager = manager_for(tmp_path, backend)
        await manager.start()
        try:
            gaming = await manager.set_mode("GAMING")
            assert gaming["unload_verified"] is True
            before = mutation_calls(backend)
            lease_id = str(uuid4())

            with pytest.raises(ApplicationError) as wake:
                await manager.wake()
            with pytest.raises(ApplicationError) as generate:
                await manager.generate("后台任务偷偷加载")
            with pytest.raises(ApplicationError) as chat:
                await manager.open_chat_stream(MODEL, MESSAGES, 4096)
            with pytest.raises(ApplicationError) as training:
                await manager.claim_training(lease_id, 4096)

            assert {wake.value.code, generate.value.code, chat.value.code, training.value.code} == {
                "GPU_GAMING"
            }
            for error in (wake.value, generate.value, chat.value, training.value):
                assert error.status_code == 423
            assert manager.training_lease is None
            assert await manager.idle_tick(manager._last_request_finished + 1000) == {
                "unloaded": False
            }
            assert mutation_calls(backend) == before
            assert backend.models == []

            # 延迟重试也不能绕过门禁。
            await asyncio.sleep(0.02)
            with pytest.raises(ApplicationError) as retry:
                await manager.wake()
            assert retry.value.code == "GPU_GAMING"
            assert mutation_calls(backend) == before
        finally:
            await manager.close()

    asyncio.run(scenario())


def test_cancelled_idle_caller_still_completes_unload_and_releases_control(tmp_path):
    async def scenario():
        backend = FakeBackend()
        backend.models = [{"name": MODEL, "size_vram": 123}]
        backend.unload_release = asyncio.Event()
        manager = manager_for(tmp_path, backend, unload_timeout=1.0)
        await manager.start()
        idle = None
        try:
            manager._last_request_finished = 1000.0
            idle = asyncio.create_task(manager.idle_tick(1180.0))
            await asyncio.wait_for(backend.unload_started.wait(), timeout=1)
            idle.cancel()
            with pytest.raises(asyncio.CancelledError):
                await idle
            assert manager.unload_verified is False
            assert backend.models

            backend.unload_release.set()
            await wait_until(lambda: manager.unload_verified)
            assert backend.models == []
            assert manager.fault is None
            assert manager.mode == "AUTO"
            assert manager.manual_gaming is False
            assert manager._pending_controls == 0
            assert (await manager.wake())["done"] is True
        finally:
            backend.unload_release.set()
            if idle is not None:
                await asyncio.gather(idle, return_exceptions=True)
            await manager.close()

    asyncio.run(scenario())


def test_concurrent_unload_calls_are_serialized_and_idempotent(tmp_path):
    async def scenario():
        backend = FakeBackend()
        backend.models = [{"name": "other:tiny", "size_vram": 456}]
        manager = manager_for(tmp_path, backend)
        await manager.start()
        try:
            results = await asyncio.gather(
                manager.unload(), manager.unload(), return_exceptions=True
            )
            assert not any(isinstance(item, BaseException) for item in results)
            for result in results:
                assert result["mode"] == "GAMING"
                assert result["unload_verified"] is True
                assert result["backend"]["models"] == []
            assert backend.unload_calls == 1
            assert backend.models == []
            assert manager.fault is None

            # 重复卸载不能悄悄重新加载，也不能把 AUTO 变成可绕过卸载的状态。
            await asyncio.sleep(0.02)
            assert backend.calls.count(("wake", None)) == 0
            assert (await manager.set_mode("AUTO"))["local_requests_blocked"] is False
            assert (await manager.wake())["done"] is True
        finally:
            await manager.close()

    asyncio.run(scenario())


def test_idle_unload_then_wake_recovers_without_manual_hold(tmp_path):
    async def scenario():
        backend = FakeBackend()
        manager = manager_for(tmp_path, backend)
        await manager.start()
        try:
            manager._last_request_finished = 1000.0
            backend.models = [{"name": MODEL, "size_vram": 123}]
            assert (await manager.idle_tick(1180.0))["unloaded"] is True
            assert backend.models == []
            assert manager.unload_verified is True
            assert manager.mode == "AUTO"
            assert manager.manual_gaming is False
            assert manager.blocked is False

            assert (await manager.wake())["done"] is True
            assert backend.models == [{"name": MODEL, "size_vram": 123}]
            assert manager.unload_verified is False
            assert manager.mode == "AUTO"
            assert manager.blocked is False
            assert (await manager.generate("空闲恢复后请求"))["done"] is True
        finally:
            await manager.close()

    asyncio.run(scenario())


def test_ollama_outage_degrades_and_recovers_without_stale_success(tmp_path):
    async def scenario():
        backend = FakeBackend()
        backend.status_error = True
        manager = manager_for(tmp_path, backend)
        await manager.start()
        try:
            manager._last_request_finished = 1000.0
            with pytest.raises(BackendError):
                await manager.idle_tick(1180.0)
            assert manager.fault is None
            assert manager.mode == "AUTO"
            assert manager.blocked is False

            with pytest.raises(ApplicationError) as generate:
                await manager.generate("Ollama 不可用")
            assert (generate.value.code, generate.value.status_code) == (
                "GPU_UPSTREAM_FAILED",
                503,
            )
            assert manager.fault == "UPSTREAM_OPERATION_UNCERTAIN"
            assert manager.blocked is True

            status = await manager.status()
            assert status["backend"]["available"] is False
            assert status["backend"]["models"] is None
            assert status["unload_verified"] is False

            with pytest.raises(ApplicationError) as unload:
                await manager.unload()
            assert (unload.value.code, unload.value.status_code) == ("GPU_UNLOAD_FAILED", 503)
            assert manager.fault == "UNLOAD_NOT_VERIFIED"
            assert backend.models == []

            backend.status_error = False
            recovered = await manager.unload()
            assert recovered["unload_verified"] is True
            assert manager.fault is None
            assert (await manager.set_mode("AUTO"))["mode"] == "AUTO"
            assert (await manager.generate("恢复后"))["done"] is True
        finally:
            await manager.close()

    asyncio.run(scenario())


def test_cancel_while_auto_status_query_holds_control_defers_explicit_unload(tmp_path):
    """AUTO 状态查询持锁时 Stop：不能假定锁持有者会卸载，必须登记回收任务。"""

    class BlockingStatusBackend(FakeBackend):
        def __init__(self):
            super().__init__()
            self.block_next_status = False
            self.status_entered = asyncio.Event()
            self.status_gate = asyncio.Event()

        async def get_model_status(self):
            if self.block_next_status:
                self.block_next_status = False
                self.status_entered.set()
                await self.status_gate.wait()
            return await super().get_model_status()

    async def scenario():
        backend = BlockingStatusBackend()
        manager = manager_for(tmp_path, backend)
        control = None
        worker = auto = None
        await manager.start()
        try:
            # 模拟 AUTO 状态查询在控制锁内等待后端返回。
            backend.block_next_status = True
            auto = asyncio.create_task(manager.set_mode("AUTO"))
            await asyncio.wait_for(backend.status_entered.wait(), timeout=1)
            assert manager._control.locked()

            control = StreamControl()
            backend.stream_release = asyncio.Event()
            _, worker = await manager.open_chat_stream(MODEL, MESSAGES, 5500, control=control)
            await asyncio.wait_for(backend.stream_started.wait(), timeout=1)
            control.cancel()
            backend.stream_release.set()
            with pytest.raises(ApplicationError) as caught:
                await worker
            assert caught.value.code == "CHAT_CANCELLED"
            # 锁被 AUTO 查询持有；卸载责任必须已登记，且不能提前宣称释放。
            assert manager._control.locked()
            assert manager.fault == "CANCELLED_AWAITING_UNLOAD"
            assert manager.unload_verified is False
            assert backend.models
            assert (await manager.status())["pending_unloads"] == 1

            backend.status_gate.set()
            await auto
            await wait_until(lambda: manager.unload_verified and not manager._reclaims)
            final = await manager.status()
            assert final["backend"]["models"] == []
            assert final["fault"] is None
            assert final["pending_unloads"] == 0
            assert final["mode"] == "AUTO"
            assert final["manual_gaming"] is False
            assert backend.unload_calls == 1
        finally:
            backend.status_gate.set()
            backend.stream_release.set()
            for task in (auto, worker):
                if task is not None:
                    await asyncio.gather(task, return_exceptions=True)
            await manager.close()

    asyncio.run(scenario())


def test_training_claim_state_write_failure_does_not_leak_unreleasable_lease(tmp_path):
    async def scenario():
        backend = FakeBackend()
        manager = manager_for(tmp_path, backend)
        await manager.start()
        try:
            # 让 manager 在已启动后遇到不可写的状态父路径，模拟磁盘/权限异常。
            blocker = tmp_path / "blocked-parent"
            blocker.write_text("preserve", encoding="utf-8")
            manager.state_path = blocker / "gpu-mode.json"

            with pytest.raises(ApplicationError) as caught:
                await manager.claim_training(str(uuid4()), 4096)
            assert (caught.value.code, caught.value.status_code) == ("GPU_STATE_FAILED", 503)
            assert manager.fault == "STATE_WRITE_FAILED"
            assert manager.mode == "GAMING"
            assert manager.blocked is True
            # 未交付给训练程序的预留必须撤销，否则调用方没有办法释放它。
            assert manager.training_lease is None
            assert blocker.read_text(encoding="utf-8") == "preserve"

            # 存储恢复后按“显式卸载复核 → AUTO → 重新申请”流程继续，无需重启。
            manager.state_path = tmp_path / "gpu-mode.json"
            recovered = await manager.unload()
            assert recovered["unload_verified"] is True
            assert manager.fault is None
            assert (await manager.set_mode("AUTO"))["mode"] == "AUTO"
            leased = await manager.claim_training(str(uuid4()), 4096)
            assert leased["training"]["state"] == "reserved"
            await manager.release_training(leased["training"]["lease"]["id"], stopped=True)
        finally:
            await manager.close()

    asyncio.run(scenario())


def test_restart_uncertain_state_and_training_lease_require_explicit_recovery(tmp_path):
    async def scenario():
        state_path = tmp_path / "gpu-mode.json"
        lease_id = str(uuid4())
        job_id = str(uuid4())
        state_path.write_text(
            json.dumps(
                {
                    "mode": "AUTO",
                    "manual_gaming": False,
                    "detected_gaming": False,
                    "uncertain": True,
                    "fault": None,
                    "last_unload_at": None,
                    "memory_pressure_hold": False,
                    "memory_pressure_reserve_bytes": 0,
                    "training_lease": {
                        "id": lease_id,
                        "job_id": job_id,
                        "required_mib": 8000,
                        "state": "reserved",
                    },
                }
            ),
            encoding="utf-8",
        )
        backend = FakeBackend()
        manager = manager_for(tmp_path, backend)
        await manager.start()
        try:
            status = await manager.status()
            assert status["mode"] == "GAMING"
            assert status["fault"] == "PREVIOUS_OPERATION_UNCERTAIN"
            assert status["local_requests_blocked"] is True
            assert status["training"]["state"] == "unknown_after_restart"
            assert status["training"]["lease"]["id"] == lease_id
            with pytest.raises(ApplicationError) as blocked:
                await manager.wake()
            assert blocked.value.code == "GPU_GAMING"

            released = await manager.release_training(lease_id, stopped=True)
            assert released["training"]["state"] == "idle"
            assert released["training"]["lease"] is None
            assert manager.blocked is True  # 未确认的恢复故障不能随租约释放。
            with pytest.raises(ApplicationError):
                await manager.wake()

            recovered = await manager.unload()
            assert recovered["unload_verified"] is True
            assert manager.fault is None
            assert (await manager.set_mode("AUTO"))["mode"] == "AUTO"
            assert (await manager.wake())["done"] is True
        finally:
            await manager.close()

    asyncio.run(scenario())


def test_drain_timeout_stays_locked_and_never_reloads(tmp_path):
    """BUG-DSB-01 的安全不变量：补偿卸载完成也不能自动打开门禁或重载。

    补偿任务只负责复用原卸载流程并复核 /api/ps；手动/检测 GAMING 来源和显式
    AUTO 退出仍由用户控制。
    """

    async def scenario():
        backend = FakeBackend()
        source, clock = ProcessSource(), Clock()
        manager = manager_for(tmp_path, backend, detection_enabled=True, drain_timeout=0.01)
        detector = detector_for(manager, source, clock)
        await manager.start()
        work = None
        try:
            await detector.poll_once()
            backend.generate_release = asyncio.Event()
            work = asyncio.create_task(manager.generate("超过排空窗口的请求"))
            await asyncio.wait_for(backend.generate_started.wait(), timeout=1)

            source.names = {"probe-game.exe"}
            await detector.poll_once()
            assert manager.mode == "GAMING"
            assert manager.blocked is True
            assert manager.fault is not None
            wake_calls = backend.calls.count(("wake", None))

            backend.generate_release.set()
            await work
            await detector.poll_once()

            # 保护期间不能自动重载、自动解除 GAMING 或丢弃故障。
            assert manager.mode == "GAMING"
            assert manager.blocked is True
            assert backend.calls.count(("wake", None)) == wake_calls
            status = await manager.status()
            assert status["training"]["state"] == "idle"
            assert status["training"]["lease"] is None
            # 修复后重试可能已清除故障；但只要还有故障就必须可诊断。
            if status["fault"] is not None:
                assert isinstance(status["fault"], str)
            with pytest.raises(ApplicationError) as blocked:
                await manager.wake()
            assert blocked.value.code == "GPU_GAMING"

            # 诊断后仍保留显式恢复路径。
            recovered = await manager.set_mode("GAMING")
            assert recovered["unload_verified"] is True
            await manager.set_detected_gaming(False, CLEAR_INFO)
            assert (await manager.set_mode("AUTO"))["mode"] == "AUTO"
        finally:
            backend.generate_release.set()
            if work is not None:
                await asyncio.gather(work, return_exceptions=True)
            await detector.stop()
            await manager.close()

    asyncio.run(scenario())


def test_training_state_failure_keeps_gate_locked_and_lease_visible(tmp_path):
    """BUG-DSB-02 的安全不变量：失败后入口锁定、故障和租约可诊断、不偷偷加载。"""

    async def scenario():
        backend = FakeBackend()
        manager = manager_for(tmp_path, backend)
        await manager.start()
        try:
            blocker = tmp_path / "blocked-parent"
            blocker.write_text("preserve", encoding="utf-8")
            manager.state_path = blocker / "gpu-mode.json"

            with pytest.raises(ApplicationError) as caught:
                await manager.claim_training(str(uuid4()), 4096)
            assert (caught.value.code, caught.value.status_code) == ("GPU_STATE_FAILED", 503)

            status = await manager.status()
            assert manager.mode == "GAMING"
            assert status["local_requests_blocked"] is True
            assert status["fault"] == "STATE_WRITE_FAILED"
            lease = status["training"]["lease"]
            if lease is None:
                # 修复后预留会被撤销；这也是可接受的诊断结果。
                assert status["training"]["state"] == "idle"
            else:
                assert status["training"]["state"] == "preparing"
                assert lease["state"] == "preparing"
            assert backend.calls.count(("wake", None)) == 0
            assert backend.calls.count(("generate", "训练期间偷偷加载")) == 0
            with pytest.raises(ApplicationError):
                await manager.wake()
            assert blocker.read_text(encoding="utf-8") == "preserve"
        finally:
            await manager.close()

    asyncio.run(scenario())
