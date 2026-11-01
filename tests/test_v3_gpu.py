"""实验27B的准入与参数传输回归；不访问真实模型、GPU或付费服务。"""

import asyncio
import io
import json

import pytest

from nova.errors import ApplicationError
from nova.gpu import resources
from nova.gpu.backend import BackendError, OllamaBackend
from nova.gpu.manager import GpuManager

MODEL = "hf.co/unsloth/Qwen3.8-27B-GGUF:UD-Q3_K_XL"
MESSAGES = [{"role": "system", "content": "你是Nova"}, {"role": "user", "content": "你好"}]
RUNTIME = {"num_ctx": 4096, "num_predict": 256, "keep_alive": 180, "temperature": 0.7}


def policy(**changes):
    return {
        "model": MODEL,
        "enabled": True,
        "required_mib": 4096,
        "min_total_ram_mib": 28672,
        "min_available_ram_mib": 18432,
        "runtime": dict(RUNTIME),
        **changes,
    }


@pytest.fixture
def memory(monkeypatch):
    current = {"available": True, "total_mib": 32000, "available_mib": 22000}
    monkeypatch.setattr(resources, "get_system_memory", lambda: dict(current))
    return current


class Backend:
    def __init__(self, model="qwen3.5:4b"):
        self.model = model
        self.calls = []
        self.models = []
        self.memory = {
            "available": True,
            "gpus": [{"index": 0, "total_mib": 16311, "used_mib": 2000}],
        }
        self.status_entered = asyncio.Event()
        self.status_release = None
        self.stream_entered = asyncio.Event()
        self.stream_release = None
        self.fail_stream = False

    async def ensure_local_model(self, model):
        self.calls.append(("ensure", model))

    async def get_model_status(self):
        self.calls.append(("status",))
        self.status_entered.set()
        if self.status_release:
            await self.status_release.wait()
        return [dict(item) for item in self.models]

    async def get_gpu_memory_usage(self):
        self.calls.append(("memory",))
        return self.memory

    async def unload_model(self, name):
        self.calls.append(("unload", name))
        self.models = [item for item in self.models if item["name"] != name]

    async def stream_chat(self, model, messages, emit, *, runtime=None):
        self.calls.append(("stream", model, runtime, messages))
        self.stream_entered.set()
        if self.stream_release:
            await self.stream_release.wait()
        if self.fail_stream:
            raise BackendError("synthetic private failure")
        emit("你好Nova")
        self.models = [{"name": model, "size_vram": 10 * 1024**3}]
        return {
            "model": model,
            "response": "你好Nova",
            "done": True,
            "stats": {
                "load_duration": 12,
                "eval_count": 4,
                "eval_duration": 200000000,
                "tokens_per_second": 20.0,
            },
        }

    async def wake_model(self, *, runtime=None):
        self.calls.append(("wake", runtime))
        return {"model": self.model, "done": True}

    async def generate(self, prompt, *, runtime=None):
        self.calls.append(("generate", runtime))
        return {"model": self.model, "response": "你好Nova", "done": True}


def manager(tmp_path, backend, **changes):
    return GpuManager(
        backend,
        tmp_path / "mode.json",
        drain_timeout=1,
        model_policies={MODEL: policy(**changes)},
    )


@pytest.mark.parametrize("entry", ["stream", "wake", "generate"])
@pytest.mark.parametrize("case", ["16gb", "busy32gb", "unknown", "disabled"])
def test_all_loading_entries_reject_before_backend_or_eviction(tmp_path, memory, entry, case):
    if case == "16gb":
        memory.update(total_mib=15400, available_mib=6000)
    elif case == "busy32gb":
        memory["available_mib"] = 9000
    elif case == "unknown":
        memory["available"] = False

    async def scenario():
        backend = Backend(MODEL)
        backend.models = [{"name": "qwen3.5:9b", "size_vram": 5000}]
        gpu = manager(tmp_path, backend, enabled=case != "disabled")
        await gpu.start()
        try:
            with pytest.raises(ApplicationError) as caught:
                if entry == "stream":
                    await gpu.open_chat_stream(MODEL, MESSAGES, 1)
                elif entry == "wake":
                    await gpu.wake()
                else:
                    await gpu.generate("你好")
            assert caught.value.code == (
                "MODEL_EXPERIMENT_DISABLED"
                if case == "disabled"
                else "TELEMETRY_UNAVAILABLE"
                if case == "unknown"
                else "MODEL_RAM_INSUFFICIENT"
            )
            assert backend.calls == []
            assert backend.models[0]["name"] == "qwen3.5:9b"
            assert not gpu._active and not gpu.fault and not gpu.manual_gaming
        finally:
            await gpu.close()

    asyncio.run(scenario())


def test_stream_uses_policy_runtime_and_gpu_requirement_not_caller_hint(tmp_path, memory):
    async def scenario():
        backend = Backend()
        backend.memory["gpus"][0]["used_mib"] = 14000
        gpu = manager(tmp_path, backend)
        await gpu.start()
        try:
            with pytest.raises(ApplicationError) as caught:
                await gpu.open_chat_stream(MODEL, MESSAGES, 1)
            assert caught.value.code == "GPU_MEMORY_INSUFFICIENT"
            assert not any(item[0] == "stream" for item in backend.calls)
            backend.memory["gpus"][0]["used_mib"] = 4000
            queue, task = await gpu.open_chat_stream(MODEL, MESSAGES, 65536)
            result = await task
            call = next(item for item in backend.calls if item[0] == "stream")
            assert call[2] == RUNTIME and "num_gpu" not in call[2]
            assert call[3] == MESSAGES and backend.model == "qwen3.5:4b"
            assert result["stats"]["tokens_per_second"] == 20
            assert gpu.last_operation["eval_duration_ns"] == 200000000
            assert gpu.last_operation["tokens_per_second"] == 20
            assert queue.get_nowait()["type"] == "delta"
            assert queue.get_nowait()["type"] == "done"
        finally:
            await gpu.close()

    asyncio.run(scenario())


@pytest.mark.parametrize("entry", ["wake", "generate"])
@pytest.mark.parametrize("case", ["16gb", "disabled"])
def test_lowercase_legacy_target_cannot_bypass_experimental_policy(tmp_path, memory, entry, case):
    memory.update(total_mib=15400, available_mib=6000)

    async def scenario():
        backend = Backend(MODEL.casefold())
        gpu = manager(tmp_path, backend, enabled=case != "disabled")
        await gpu.start()
        try:
            with pytest.raises(ApplicationError) as caught:
                await (gpu.wake() if entry == "wake" else gpu.generate("你好"))
            assert caught.value.code == (
                "MODEL_RAM_INSUFFICIENT" if case == "16gb" else "MODEL_EXPERIMENT_DISABLED"
            )
            assert backend.calls == [] and not gpu._active
        finally:
            await gpu.close()

    asyncio.run(scenario())


def test_policy_model_field_matches_even_when_mapping_key_is_alias(tmp_path, memory):
    memory.update(total_mib=15400, available_mib=6000)

    async def scenario():
        backend = Backend(MODEL.casefold())
        gpu = GpuManager(backend, tmp_path / "mode.json", model_policies={"experiment": policy()})
        await gpu.start()
        try:
            with pytest.raises(ApplicationError) as caught:
                await gpu.wake()
            assert caught.value.code == "MODEL_RAM_INSUFFICIENT" and backend.calls == []
        finally:
            await gpu.close()

    asyncio.run(scenario())


def test_conflicting_case_alias_policies_fail_closed(tmp_path, memory):
    async def scenario():
        backend = Backend(MODEL.casefold())
        gpu = GpuManager(
            backend,
            tmp_path / "mode.json",
            model_policies={MODEL: policy(enabled=False), MODEL.casefold(): policy()},
        )
        await gpu.start()
        try:
            with pytest.raises(ApplicationError) as caught:
                await gpu.wake()
            assert caught.value.code == "MODEL_POLICY_INVALID" and backend.calls == []
        finally:
            await gpu.close()

    asyncio.run(scenario())


def test_identical_case_alias_policies_share_same_guard(tmp_path, memory):
    memory.update(total_mib=15400, available_mib=6000)

    async def scenario():
        backend = Backend(MODEL.casefold())
        restricted = policy()
        gpu = GpuManager(
            backend,
            tmp_path / "mode.json",
            model_policies={MODEL: restricted, MODEL.casefold(): restricted},
        )
        await gpu.start()
        try:
            with pytest.raises(ApplicationError) as caught:
                await gpu.generate("你好")
            assert caught.value.code == "MODEL_RAM_INSUFFICIENT" and backend.calls == []
        finally:
            await gpu.close()

    asyncio.run(scenario())


@pytest.mark.parametrize("entry", ["wake", "generate"])
def test_legacy_entries_use_policy_runtime_when_safe(tmp_path, memory, entry):
    async def scenario():
        backend = Backend(MODEL)
        gpu = manager(tmp_path, backend, runtime={**RUNTIME, "num_gpu": 40})
        await gpu.start()
        try:
            result = await (gpu.wake() if entry == "wake" else gpu.generate("你好"))
            assert result["done"]
            assert backend.calls[-2][0] == entry  # 审计前会再采一次显存。
            call = next(item for item in backend.calls if item[0] == entry)
            assert call[1] == {**RUNTIME, "num_gpu": 40}
        finally:
            await gpu.close()

    asyncio.run(scenario())


@pytest.mark.parametrize("entry", ["stream", "wake", "generate"])
def test_gaming_has_priority_and_never_starts_experimental_load(tmp_path, memory, entry):
    async def scenario():
        backend = Backend(MODEL)
        gpu = manager(tmp_path, backend)
        await gpu.start()
        gpu.manual_gaming = True
        try:
            with pytest.raises(ApplicationError) as caught:
                if entry == "stream":
                    await gpu.open_chat_stream(MODEL, MESSAGES, 4096)
                elif entry == "wake":
                    await gpu.wake()
                else:
                    await gpu.generate("你好")
            assert caught.value.status_code == 423 and backend.calls == []
        finally:
            await gpu.close()

    asyncio.run(scenario())


def test_ram_drop_during_preflight_does_not_evict_stable_model(tmp_path, memory):
    async def scenario():
        backend = Backend()
        backend.models = [{"name": "qwen3.5:9b", "size_vram": 5000}]
        backend.status_release = asyncio.Event()
        gpu = manager(tmp_path, backend)
        await gpu.start()
        opening = asyncio.create_task(gpu.open_chat_stream(MODEL, MESSAGES, 4096))
        await backend.status_entered.wait()
        memory["available_mib"] = 4000
        backend.status_release.set()
        try:
            with pytest.raises(ApplicationError, match="内存不足"):
                await opening
            assert backend.models[0]["name"] == "qwen3.5:9b"
            assert not any(item[0] in {"unload", "stream"} for item in backend.calls)
            assert not gpu._active and not gpu.fault
        finally:
            await gpu.close()

    asyncio.run(scenario())


def test_stream_failure_locks_then_can_unload_without_ram_or_enabled_policy(tmp_path, memory):
    async def scenario():
        backend = Backend()
        backend.fail_stream = True
        gpu = manager(tmp_path, backend)
        await gpu.start()
        queue, task = await gpu.open_chat_stream(MODEL, MESSAGES, 4096)
        try:
            with pytest.raises(ApplicationError):
                await task
            assert queue.get_nowait()["type"] == "error"
            assert gpu.fault == "UPSTREAM_OPERATION_UNCERTAIN"
            backend.models = [{"name": MODEL, "size_vram": 5000}]
            gpu.model_policies[MODEL]["enabled"] = False
            memory["available"] = False
            result = await gpu.unload()
            assert result["unload_verified"] and backend.models == []
        finally:
            await gpu.close()

    asyncio.run(scenario())


def test_game_arriving_during_experimental_preflight_never_loads(tmp_path, memory):
    async def scenario():
        backend = Backend()
        backend.models = [{"name": "qwen3.5:9b", "size_vram": 5000}]
        backend.status_release = asyncio.Event()
        gpu = manager(tmp_path, backend)
        await gpu.start()
        opening = asyncio.create_task(gpu.open_chat_stream(MODEL, MESSAGES, 4096))
        await backend.status_entered.wait()
        gaming = asyncio.create_task(gpu.set_mode("GAMING"))
        await asyncio.sleep(0)
        backend.status_release.set()
        try:
            with pytest.raises(ApplicationError) as caught:
                await asyncio.wait_for(opening, 1)
            assert caught.value.status_code == 423
            result = await asyncio.wait_for(gaming, 1)
            assert result["unload_verified"] and result["local_requests_blocked"]
            assert not any(item[0] == "stream" for item in backend.calls)
            assert not gpu._active and not gpu.fault
        finally:
            backend.status_release.set()
            await asyncio.gather(opening, gaming, return_exceptions=True)
            await gpu.close()

    asyncio.run(scenario())


def test_cancelled_consumer_does_not_release_active_experimental_generation(tmp_path, memory):
    async def scenario():
        backend = Backend()
        backend.stream_release = asyncio.Event()
        gpu = manager(tmp_path, backend)
        await gpu.start()
        queue, worker = await gpu.open_chat_stream(MODEL, MESSAGES, 4096)
        await backend.stream_entered.wait()
        consumer = asyncio.create_task(queue.get())
        consumer.cancel()
        with pytest.raises(asyncio.CancelledError):
            await consumer
        gaming = asyncio.create_task(gpu.set_mode("GAMING"))
        await asyncio.sleep(0)
        try:
            assert worker in gpu._active and not worker.done()
            assert not gaming.done()
            assert not any(item[0] == "unload" for item in backend.calls)
            backend.stream_release.set()
            assert (await worker)["done"]
            result = await asyncio.wait_for(gaming, 1)
            assert result["unload_verified"] and backend.models == []
        finally:
            backend.stream_release.set()
            await asyncio.gather(worker, gaming, return_exceptions=True)
            await gpu.close()

    asyncio.run(scenario())


class Response(io.BytesIO):
    status = 200


class Opener:
    def __init__(self, *, eval_duration=200000000):
        self.calls = []
        self.eval_duration = eval_duration

    def open(self, request, timeout):
        self.calls.append(request)
        if request.full_url.endswith("/api/tags"):
            return Response(json.dumps({"models": [{"name": MODEL}]}).encode())
        return Response(
            json.dumps(
                {
                    "model": MODEL,
                    "message": {"role": "assistant", "content": "你好Nova"},
                    "done": True,
                    "eval_count": 4,
                    "eval_duration": self.eval_duration,
                }
            ).encode()
            + b"\n"
        )


@pytest.mark.parametrize("num_gpu", [None, 0, 40])
def test_backend_stream_validates_runtime_and_records_token_rate(num_gpu):
    backend = OllamaBackend()
    backend._opener = Opener()
    runtime = dict(RUNTIME)
    if num_gpu is not None:
        runtime["num_gpu"] = num_gpu
    result = backend._stream_chat(MODEL, MESSAGES, lambda text: None, runtime=runtime)
    request = json.loads(backend._opener.calls[-1].data)
    assert request["keep_alive"] == 180 and request["think"] is False
    assert request["options"] == {
        key: value for key, value in runtime.items() if key != "keep_alive"
    }
    assert result["stats"]["eval_duration"] == 200000000
    assert result["stats"]["tokens_per_second"] == 20


@pytest.mark.parametrize(
    "runtime",
    [
        {"num_ctx": 1000000},
        {"num_ctx": True},
        {"num_predict": 0},
        {"num_predict": 1025},
        {"num_gpu": -1},
        {"num_gpu": 257},
        {"keep_alive": -1},
        {"keep_alive": 181},
        {"temperature": float("nan")},
        {"temperature": float("inf")},
        {"temperature": True},
        {"temperature": 2.1},
        {"template": "untrusted"},
        [],
        "invalid",
    ],
)
def test_invalid_runtime_rejected_before_tags_or_any_load(runtime):
    backend = OllamaBackend()
    backend._opener = Opener()
    with pytest.raises(BackendError):
        backend._stream_chat(MODEL, MESSAGES, lambda text: None, runtime=runtime)
    assert backend._opener.calls == []


def test_zero_evaluation_duration_never_fabricates_token_rate():
    backend = OllamaBackend()
    backend._opener = Opener(eval_duration=0)
    result = backend._stream_chat(MODEL, MESSAGES, lambda text: None, runtime=RUNTIME)
    assert result["stats"]["eval_duration"] == 0
    assert "tokens_per_second" not in result["stats"]


def test_legacy_backend_applies_runtime_and_unload_remains_keep_alive_zero(monkeypatch):
    backend = OllamaBackend(model=MODEL)
    calls = []
    monkeypatch.setattr(backend, "_ensure_local", lambda: None)

    def request(method, path, payload, timeout=None):
        calls.append(payload)
        return {
            "model": MODEL,
            "done": True,
            "response": "你好Nova",
            "eval_count": 4,
            "eval_duration": 200000000,
        }

    monkeypatch.setattr(backend, "_request", request)

    async def scenario():
        await backend.wake_model(runtime=RUNTIME)
        result = await backend.generate("你好", runtime=RUNTIME)
        await backend.unload_model(MODEL)
        assert calls[0]["options"]["num_predict"] == 256
        assert calls[1]["options"]["num_ctx"] == 4096
        assert calls[2]["keep_alive"] == 0
        assert result["tokens_per_second"] == 20

    asyncio.run(scenario())
