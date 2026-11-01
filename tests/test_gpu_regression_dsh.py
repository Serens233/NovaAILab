"""DS-B 专属 GPU Manager 回归：状态机、游戏卸载、后台重载、并发与降级。

全部使用进程内替身和临时状态目录，不访问真实 Ollama、nvidia-smi 或游戏进程。
运行：``python -m pytest tests/test_gpu_regression_dsh.py -q``
"""

import asyncio
import json
from uuid import uuid4

import pytest

from nova.errors import ApplicationError
from nova.gpu.backend import BackendError
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
        if self.status_error:
            raise BackendError("synthetic unavailable")
        return [dict(item) for item in self.models]

    async def get_gpu_memory_usage(self):
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


@pytest.mark.xfail(
    strict=True,
    reason=(
        "BUG-DSB-01: 游戏在推理期间被检测到且 drain 超时后，管理器写入 "
        "UNLOAD_NOT_VERIFIED 并只尝试一次卸载；推理结束后不会重试，模型继续占用显存。"
        "见 src/nova/gpu/manager.py:602-658（_unload 的 drain 超时）与 "
        "src/nova/gpu/manager.py:729-756 + detection.py:121-123（仅首次动作触发卸载）。"
    ),
)
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
            # 推理已经结束，游戏仍在；保护逻辑应当再次卸载残留模型。
            await detector.poll_once()
            assert manager.detected_gaming is True
            assert backend.models == []
            assert backend.unload_calls >= 1
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
            assert manager.fault == "UNLOAD_NOT_VERIFIED"
            assert backend.models

            backend.generate_release.set()
            await work
            # 目前只能由人工再次进入 GAMING 触发重试；这是 Bug-DSB-01 的临时恢复路径。
            result = await manager.set_mode("GAMING")
            assert result["unload_verified"] is True
            assert result["backend"]["models"] == []
            assert manager.fault is None
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
            before = list(backend.calls)
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
            assert backend.calls == before
            assert backend.models == []

            # 延迟重试也不能绕过门禁。
            await asyncio.sleep(0.02)
            with pytest.raises(ApplicationError) as retry:
                await manager.wake()
            assert retry.value.code == "GPU_GAMING"
            assert backend.calls == before
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


@pytest.mark.xfail(
    strict=True,
    reason=(
        "BUG-DSB-02: claim_training 在写入租约状态失败时已经设置内存中的 "
        "training_lease(preparing)，但没有清理；调用方拿不到 lease id，"
        "release_training 又以 TRAINING_PREPARING 拒绝，GPU 入口只能靠重启恢复。"
        "见 src/nova/gpu/manager.py:794-810。"
    ),
)
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
