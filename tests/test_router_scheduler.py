"""独立调度回归：训练租约、取消和游戏竞争不释放尚未确认的GPU占用。"""

import asyncio
import json
from uuid import uuid4

import pytest

from nova.chat.registry import experimental_policy, stable_policies
from nova.config import Settings
from nova.errors import ApplicationError
from nova.gpu import resources
from nova.gpu.backend import BackendError, CancelledBackendError, StreamControl
from nova.gpu.manager import GpuManager

MODEL = "qwen3.5:4b"
MESSAGES = [{"role": "user", "content": "合成调度输入"}]


class SchedulerBackend:
    model = MODEL
    supports_stream_control = True

    def __init__(self):
        self.base_url = "synthetic-scheduler-" + str(uuid4())
        self.models = []
        self.calls = []
        self.used_mib = 1000
        self.memory_available = True
        self.ensure_started = asyncio.Event()
        self.ensure_release = None
        self.stream_started = asyncio.Event()
        self.stream_release = None
        self.unload_started = asyncio.Event()
        self.unload_release = None
        self.unload_failed = False
        self.complete_after_cancel = False

    async def ensure_local_model(self, model):
        self.calls.append(("ensure", model))
        self.ensure_started.set()
        if self.ensure_release:
            await self.ensure_release.wait()

    async def get_model_status(self):
        return [dict(item) for item in self.models]

    async def get_gpu_memory_usage(self):
        return {
            "available": self.memory_available,
            "gpus": [{"index": 0, "total_mib": 16311, "used_mib": self.used_mib}],
        }

    async def unload_model(self, name):
        self.calls.append(("unload", name))
        self.unload_started.set()
        if self.unload_release:
            await self.unload_release.wait()
        if self.unload_failed:
            raise BackendError("SYNTHETIC_PRIVATE_UNLOAD_ERROR")
        self.models = [model for model in self.models if model["name"] != name]

    async def stream_chat(self, model, messages, on_delta, *, runtime=None, control=None):
        self.calls.append(("stream", model))
        self.models = [{"name": model, "size": 4096, "size_vram": 4096}]
        on_delta("可保留的部分回复")
        self.stream_started.set()
        if self.stream_release:
            await self.stream_release.wait()
        if control is not None and control.requested and not self.complete_after_cancel:
            raise CancelledBackendError()
        return {
            "model": model,
            "done": True,
            "response": "可保留的部分回复",
            "stats": {"load_duration": 1},
        }

    async def wake_model(self, **kwargs):
        self.calls.append(("wake", self.model))
        return {"model": self.model, "done": True, "response": ""}

    async def generate(self, prompt, **kwargs):
        self.calls.append(("generate", self.model))
        return {"model": self.model, "done": True, "response": "合成回复"}


def manager_for(tmp_path, backend, **kwargs):
    kwargs.setdefault("model_policies", stable_policies())
    return GpuManager(
        backend, tmp_path / "gpu-mode.json", drain_timeout=2, unload_timeout=0.1, **kwargs
    )


async def complete(task):
    return await asyncio.wait_for(asyncio.shield(task), timeout=3)


async def cleanup(manager, backend, *tasks):
    for gate in (backend.ensure_release, backend.stream_release, backend.unload_release):
        if gate is not None:
            gate.set()
    if tasks:
        await asyncio.wait_for(asyncio.gather(*tasks, return_exceptions=True), timeout=3)
    await manager.close()


@pytest.mark.parametrize("action", ["chat", "wake", "generate"])
def test_reserved_training_blocks_all_local_inference_entrypoints(tmp_path, action):
    async def scenario():
        backend = SchedulerBackend()
        manager = manager_for(tmp_path, backend)
        await manager.start()
        try:
            status = await manager.claim_training(str(uuid4()), 8000)
            lease = status["training"]["lease"]
            assert status["training"]["state"] == "reserved"
            assert status["local_requests_blocked"] is True
            backend.calls.clear()
            with pytest.raises(ApplicationError) as caught:
                if action == "chat":
                    await manager.open_chat_stream(MODEL, MESSAGES, 5500)
                elif action == "wake":
                    await manager.wake()
                else:
                    await manager.generate("合成输入")
            assert (caught.value.code, caught.value.status_code) == ("GPU_TRAINING_ACTIVE", 409)
            assert backend.calls == []
            await manager.release_training(lease["id"], stopped=True)
            assert not manager.blocked
        finally:
            await cleanup(manager, backend)

    asyncio.run(scenario())


def test_active_inference_rejects_training_and_parallel_inference(tmp_path):
    async def scenario():
        backend = SchedulerBackend()
        backend.stream_release = asyncio.Event()
        manager = manager_for(tmp_path, backend)
        await manager.start()
        worker = None
        try:
            _, worker = await manager.open_chat_stream(MODEL, MESSAGES, 5500)
            await asyncio.wait_for(backend.stream_started.wait(), timeout=3)
            with pytest.raises(ApplicationError) as caught:
                await manager.claim_training(str(uuid4()), 8000)
            assert caught.value.code == "GPU_BUSY"
            with pytest.raises(ApplicationError) as caught:
                await manager.open_chat_stream(MODEL, MESSAGES, 5500)
            assert caught.value.code == "GPU_BUSY"
            assert manager.training_lease is None
            assert backend.calls.count(("stream", MODEL)) == 1
            backend.stream_release.set()
            assert (await complete(worker))["done"] is True
        finally:
            await cleanup(manager, backend, *([worker] if worker else []))

    asyncio.run(scenario())


def test_training_claim_unloads_resident_model_before_granting_lease(tmp_path):
    async def scenario():
        backend = SchedulerBackend()
        backend.models = [{"name": MODEL, "size_vram": 3000}]
        manager = manager_for(tmp_path, backend)
        await manager.start()
        try:
            status = await manager.claim_training(str(uuid4()), 8000)
            assert ("unload", MODEL) in backend.calls
            assert status["backend"]["models"] == []
            assert status["unload_verified"] is True
            assert status["last_operation"]["reason"] == "training_reservation"
            assert status["mode"] == "AUTO"
            assert status["training"]["safe_pause_supported"] is False
            assert status["training"]["scope"] == "cooperative_registered_jobs_only"
        finally:
            await cleanup(manager, backend)

    asyncio.run(scenario())


def test_preparing_training_lease_cannot_be_released_or_double_allocated(tmp_path):
    async def scenario():
        backend = SchedulerBackend()
        backend.models = [{"name": MODEL, "size_vram": 3000}]
        backend.unload_release = asyncio.Event()
        manager = manager_for(tmp_path, backend)
        await manager.start()
        claim = asyncio.create_task(manager.claim_training(str(uuid4()), 8000))
        try:
            await asyncio.wait_for(backend.unload_started.wait(), timeout=3)
            lease = dict(manager.training_lease)
            assert lease["state"] == "preparing"
            with pytest.raises(ApplicationError) as caught:
                await manager.release_training(lease["id"], stopped=True)
            assert caught.value.code == "TRAINING_PREPARING"
            with pytest.raises(ApplicationError):
                await manager.claim_training(str(uuid4()), 8000)
            with pytest.raises(ApplicationError):
                await manager.open_chat_stream(MODEL, MESSAGES, 5500)
            assert manager.training_lease["id"] == lease["id"]
            backend.unload_release.set()
            assert (await complete(claim))["training"]["state"] == "reserved"
        finally:
            await cleanup(manager, backend, claim)

    asyncio.run(scenario())


def test_cancelled_claim_can_recover_same_reservation_without_releasing_it(tmp_path):
    async def scenario():
        backend = SchedulerBackend()
        backend.models = [{"name": MODEL, "size_vram": 3000}]
        backend.unload_release = asyncio.Event()
        manager = manager_for(tmp_path, backend)
        await manager.start()
        job = str(uuid4())
        claim = asyncio.create_task(manager.claim_training(job, 8000))
        try:
            await asyncio.wait_for(backend.unload_started.wait(), timeout=3)
            lease_id = manager.training_lease["id"]
            claim.cancel()
            with pytest.raises(asyncio.CancelledError):
                await claim
            with pytest.raises(ApplicationError) as caught:
                await manager.claim_training(job, 8000)
            assert caught.value.code == "TRAINING_PREPARING"
            backend.unload_release.set()
            await asyncio.wait_for(asyncio.gather(*tuple(manager._controls)), timeout=3)
            recovered = await manager.claim_training(job, 8000)
            assert recovered["training"]["lease"]["id"] == lease_id
            assert recovered["training"]["state"] == "reserved"
            with pytest.raises(ApplicationError) as caught:
                await manager.claim_training(job, 7000)
            assert caught.value.code == "TRAINING_JOB_CONFLICT"
            await manager.set_mode("GAMING")
            with pytest.raises(ApplicationError) as caught:
                await manager.claim_training(job, 8000)
            assert caught.value.code == "GPU_GAMING"
            assert manager.training_lease["id"] == lease_id
            await manager.release_training(lease_id, stopped=True)
        finally:
            await cleanup(manager, backend)

    asyncio.run(scenario())


def test_retry_after_restart_does_not_resume_unknown_training(tmp_path):
    async def scenario():
        backend = SchedulerBackend()
        first = manager_for(tmp_path, backend)
        job = str(uuid4())
        await first.start()
        status = await first.claim_training(job, 8000)
        lease_id = status["training"]["lease"]["id"]
        await first.close()
        second = manager_for(tmp_path, backend)
        await second.start()
        try:
            with pytest.raises(ApplicationError) as caught:
                await second.claim_training(job, 8000)
            assert caught.value.code == "TRAINING_STATE_UNKNOWN"
            assert second.training_lease["id"] == lease_id
            await second.release_training(lease_id, stopped=True)
        finally:
            await cleanup(second, backend)

    asyncio.run(scenario())


def test_claim_retry_rechecks_gaming_after_status_await(tmp_path):
    async def scenario():
        backend = SchedulerBackend()
        manager = manager_for(tmp_path, backend)
        await manager.start()
        job = str(uuid4())
        leased = await manager.claim_training(job, 8000)
        lease_id = leased["training"]["lease"]["id"]
        entered, proceed = asyncio.Event(), asyncio.Event()
        original = backend.get_model_status

        async def delayed_status():
            entered.set()
            await proceed.wait()
            return await original()

        backend.get_model_status = delayed_status
        retry = asyncio.create_task(manager.claim_training(job, 8000))
        try:
            await asyncio.wait_for(entered.wait(), timeout=3)
            manager.manual_gaming = True
            manager.mode = "GAMING"
            proceed.set()
            with pytest.raises(ApplicationError) as caught:
                await retry
            assert caught.value.code == "GPU_GAMING"
            assert manager.training_lease["id"] == lease_id
            await manager.release_training(lease_id, stopped=True)
        finally:
            proceed.set()
            await cleanup(manager, backend, retry)

    asyncio.run(scenario())


@pytest.mark.parametrize("unavailable", [False, True])
def test_failed_training_memory_preflight_removes_undelivered_reservation(tmp_path, unavailable):
    async def scenario():
        backend = SchedulerBackend()
        backend.used_mib = 15000
        backend.memory_available = not unavailable
        manager = manager_for(tmp_path, backend)
        await manager.start()
        try:
            with pytest.raises(ApplicationError) as caught:
                await manager.claim_training(str(uuid4()), 8000)
            assert caught.value.code == (
                "GPU_MEMORY_UNAVAILABLE" if unavailable else "GPU_MEMORY_INSUFFICIENT"
            )
            assert manager.training_lease is None
            persisted = json.loads(manager.state_path.read_text(encoding="utf-8"))
            assert persisted["training_lease"] is None
            assert not manager.blocked
        finally:
            await cleanup(manager, backend)

    asyncio.run(scenario())


def test_training_unload_failure_keeps_safety_lock_but_not_undelivered_lease(tmp_path):
    async def scenario():
        backend = SchedulerBackend()
        backend.models = [{"name": MODEL, "size_vram": 3000}]
        backend.unload_failed = True
        manager = manager_for(tmp_path, backend)
        await manager.start()
        try:
            with pytest.raises(ApplicationError) as caught:
                await manager.claim_training(str(uuid4()), 8000)
            assert caught.value.code == "GPU_UNLOAD_FAILED"
            assert manager.training_lease is None
            assert manager.blocked
            assert manager.fault == "UNLOAD_NOT_VERIFIED"
            assert backend.models
        finally:
            await cleanup(manager, backend)

    asyncio.run(scenario())


def test_gaming_keeps_training_checkpoint_and_lease_until_explicit_safe_release(tmp_path):
    async def scenario():
        backend = SchedulerBackend()
        manager = manager_for(tmp_path, backend)
        checkpoint = tmp_path / "synthetic-checkpoint.json"
        checkpoint.write_text(
            json.dumps({"weights": [1], "optimizer": {"step": 7}, "scheduler": {"epoch": 2}}),
            encoding="utf-8",
        )
        checkpoint_before = checkpoint.read_bytes()
        await manager.start()
        try:
            leased = await manager.claim_training(str(uuid4()), 8000)
            lease_id = leased["training"]["lease"]["id"]
            gaming = await manager.set_mode("GAMING")
            assert gaming["training"]["lease"]["id"] == lease_id
            assert gaming["training"]["game_conflict"] is True
            assert gaming["training"]["safe_pause_supported"] is False
            with pytest.raises(ApplicationError) as caught:
                await manager.wake()
            assert (caught.value.code, caught.value.status_code) == ("GPU_GAMING", 423)
            with pytest.raises(ApplicationError) as caught:
                await manager.claim_training(str(uuid4()), 5000)
            assert caught.value.code == "GPU_GAMING"
            auto = await manager.set_mode("AUTO")
            assert auto["training"]["lease"]["id"] == lease_id
            assert auto["training"]["game_conflict"] is False
            assert auto["local_requests_blocked"] is True
            assert checkpoint.read_bytes() == checkpoint_before
            with pytest.raises(ApplicationError) as caught:
                await manager.release_training(lease_id, stopped=False)
            assert caught.value.code == "TRAINING_STOP_CONFIRMATION"
            with pytest.raises(ApplicationError) as caught:
                await manager.release_training(str(uuid4()), stopped=True)
            assert caught.value.code == "TRAINING_LEASE_MISMATCH"
            assert manager.training_lease["id"] == lease_id
            released = await manager.release_training(lease_id, stopped=True)
            assert released["training"]["state"] == "idle"
            assert released["backend"]["models"] == []
            assert not any(call[0] in {"wake", "generate", "stream"} for call in backend.calls)
            assert checkpoint.read_bytes() == checkpoint_before
        finally:
            await cleanup(manager, backend)

    asyncio.run(scenario())


def test_restart_does_not_implicitly_release_or_resume_training(tmp_path):
    async def scenario():
        backend = SchedulerBackend()
        first = manager_for(tmp_path, backend)
        await first.start()
        leased = await first.claim_training(str(uuid4()), 8000)
        lease_id = leased["training"]["lease"]["id"]
        await first.close()
        resumed = manager_for(tmp_path, backend)
        await resumed.start()
        try:
            status = await resumed.status()
            assert status["training"]["state"] == "unknown_after_restart"
            assert status["training"]["lease"]["id"] == lease_id
            with pytest.raises(ApplicationError) as caught:
                await resumed.wake()
            assert caught.value.code == "GPU_TRAINING_ACTIVE"
            await resumed.release_training(lease_id, stopped=True)
            assert not resumed.blocked
            assert not any(call[0] in {"wake", "generate", "stream"} for call in backend.calls)
        finally:
            await cleanup(resumed, backend)

    asyncio.run(scenario())


def test_cancel_keeps_admission_closed_until_transport_and_unload_are_verified(tmp_path):
    async def scenario():
        backend = SchedulerBackend()
        backend.stream_release = asyncio.Event()
        backend.unload_release = asyncio.Event()
        control = StreamControl()
        manager = manager_for(tmp_path, backend)
        await manager.start()
        worker = None
        try:
            queue, worker = await manager.open_chat_stream(MODEL, MESSAGES, 5500, control=control)
            await asyncio.wait_for(backend.stream_started.wait(), timeout=3)
            control.cancel()
            with pytest.raises(ApplicationError) as caught:
                await manager.claim_training(str(uuid4()), 8000)
            assert caught.value.code == "GPU_BUSY"
            assert len(manager._active) == 1
            backend.stream_release.set()
            await asyncio.wait_for(backend.unload_started.wait(), timeout=3)
            assert not worker.done()
            assert backend.models
            with pytest.raises(ApplicationError):
                await manager.wake()
            backend.unload_release.set()
            with pytest.raises(ApplicationError) as caught:
                await complete(worker)
            assert caught.value.code == "CHAT_CANCELLED"
            status = await manager.status()
            assert status["backend"]["models"] == []
            assert status["unload_verified"] is True
            assert status["active_requests"] == 0
            assert status["fault"] is None
            assert status["last_operation"]["reason"] == "task_cancelled"
            events = [queue.get_nowait() for _ in range(queue.qsize())]
            assert events[-1]["type"] == "error"
            assert events[-1]["code"] == "CHAT_CANCELLED"
            assert not any(event["type"] == "done" for event in events)
        finally:
            await cleanup(manager, backend, *([worker] if worker else []))

    asyncio.run(scenario())


def test_cancel_requested_just_after_backend_completion_is_not_reported_as_system_failure(tmp_path):
    async def scenario():
        backend = SchedulerBackend()
        backend.stream_release = asyncio.Event()
        backend.complete_after_cancel = True
        manager = manager_for(tmp_path, backend)
        control = StreamControl()
        await manager.start()
        worker = None
        try:
            _, worker = await manager.open_chat_stream(MODEL, MESSAGES, 5500, control=control)
            await asyncio.wait_for(backend.stream_started.wait(), timeout=3)
            control.cancel()
            backend.stream_release.set()
            with pytest.raises(ApplicationError) as caught:
                await complete(worker)
            assert caught.value.code == "CHAT_CANCELLED"
            assert manager.unload_verified
            assert not manager.fault
            assert not backend.models
        finally:
            await cleanup(manager, backend, *([worker] if worker else []))

    asyncio.run(scenario())


def test_cancellation_unload_failure_retains_fault_and_blocks_reload(tmp_path):
    async def scenario():
        backend = SchedulerBackend()
        backend.stream_release = asyncio.Event()
        backend.unload_failed = True
        manager = manager_for(tmp_path, backend)
        control = StreamControl()
        await manager.start()
        worker = None
        try:
            _, worker = await manager.open_chat_stream(MODEL, MESSAGES, 5500, control=control)
            await asyncio.wait_for(backend.stream_started.wait(), timeout=3)
            control.cancel()
            backend.stream_release.set()
            with pytest.raises(ApplicationError) as caught:
                await complete(worker)
            assert caught.value.code == "GPU_UNLOAD_FAILED"
            assert manager.fault == "UNLOAD_NOT_VERIFIED"
            assert not manager.unload_verified
            assert backend.models
            with pytest.raises(ApplicationError) as caught:
                await manager.wake()
            assert caught.value.status_code == 423
        finally:
            await cleanup(manager, backend, *([worker] if worker else []))

    asyncio.run(scenario())


def test_gaming_and_cancel_race_uses_existing_unload_and_cannot_reopen_admission(tmp_path):
    async def scenario():
        backend = SchedulerBackend()
        backend.stream_release = asyncio.Event()
        manager = manager_for(tmp_path, backend)
        control = StreamControl()
        await manager.start()
        worker = gaming = None
        try:
            _, worker = await manager.open_chat_stream(MODEL, MESSAGES, 5500, control=control)
            await asyncio.wait_for(backend.stream_started.wait(), timeout=3)
            entered = asyncio.Event()

            async def enter_game():
                entered.set()
                return await manager.set_mode("GAMING")

            gaming = asyncio.create_task(enter_game())
            await asyncio.wait_for(entered.wait(), timeout=3)
            assert manager.mode == "GAMING"
            control.cancel()
            backend.stream_release.set()
            with pytest.raises(ApplicationError) as caught:
                await complete(worker)
            assert caught.value.code == "CHAT_CANCELLED"
            status = await complete(gaming)
            assert status["mode"] == "GAMING"
            assert status["local_requests_blocked"] is True
            assert status["backend"]["models"] == []
            assert status["fault"] is None
            assert backend.calls.count(("unload", MODEL)) == 1
            with pytest.raises(ApplicationError) as caught:
                await manager.wake()
            assert caught.value.code == "GPU_GAMING"
        finally:
            await cleanup(manager, backend, *[task for task in (worker, gaming) if task])

    asyncio.run(scenario())


def test_cancel_during_preflight_does_not_start_model_or_leave_active_request(tmp_path):
    async def scenario():
        backend = SchedulerBackend()
        backend.ensure_release = asyncio.Event()
        manager = manager_for(tmp_path, backend)
        control = StreamControl()
        await manager.start()
        opening = asyncio.create_task(
            manager.open_chat_stream(MODEL, MESSAGES, 5500, control=control)
        )
        try:
            await asyncio.wait_for(backend.ensure_started.wait(), timeout=3)
            control.cancel()
            with pytest.raises(ApplicationError) as caught:
                await manager.open_chat_stream(MODEL, MESSAGES, 5500)
            assert caught.value.code == "GPU_BUSY"
            backend.ensure_release.set()
            with pytest.raises(ApplicationError) as caught:
                await complete(opening)
            assert caught.value.code == "CHAT_CANCELLED"
            assert not manager._active
            assert not manager._preparing
            assert not backend.models
            assert not backend.stream_started.is_set()
            assert not manager.fault
        finally:
            await cleanup(manager, backend, opening)

    asyncio.run(scenario())


def test_discovered_alias_policy_cannot_bypass_27b_ram_gate(tmp_path, monkeypatch):
    monkeypatch.setattr(
        resources,
        "get_system_memory",
        lambda: {"available": True, "total_mib": 15477, "available_mib": 9000},
    )

    async def scenario():
        backend = SchedulerBackend()
        manager = manager_for(tmp_path, backend)
        policy = experimental_policy(Settings(qwen38_enabled=True))
        policy["model"] = "qwen3.8:27b"
        manager.update_model_policies({policy["model"]: policy})
        await manager.start()
        try:
            with pytest.raises(ApplicationError) as caught:
                await manager.open_chat_stream("qwen3.8:27b", MESSAGES, 1)
            assert caught.value.code == "MODEL_RAM_INSUFFICIENT"
            assert backend.calls == []
            assert not manager._active
        finally:
            await cleanup(manager, backend)

    asyncio.run(scenario())
