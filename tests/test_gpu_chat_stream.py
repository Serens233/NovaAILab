"""多模型流、游戏门禁及空闲卸载回归；不访问真实GPU、Ollama或密钥。"""

import asyncio
import io
import json
import threading

import pytest

from nova.errors import ApplicationError
from nova.gpu.backend import BackendError, OllamaBackend
from nova.gpu.manager import GpuManager

MODEL = "qwen3.5:9b"
MESSAGES = [{"role": "user", "content": "合成练习"}]


class ChatBackend:
    model = "qwen3.5:4b"

    def __init__(self):
        self.models = []
        self.calls = []
        self.memory = {
            "available": True,
            "gpus": [{"index": 0, "total_mib": 16311, "used_mib": 2000}],
        }
        self.ensure_started = asyncio.Event()
        self.ensure_release = None
        self.stream_started = asyncio.Event()
        self.stream_release = None
        self.unload_started = asyncio.Event()
        self.unload_release = None
        self.stream_error = False
        self.ensure_error = False

    async def ensure_local_model(self, model):
        self.calls.append(("ensure", model))
        self.ensure_started.set()
        if self.ensure_release:
            await self.ensure_release.wait()
        if self.ensure_error:
            raise BackendError("synthetic unavailable")

    async def get_model_status(self):
        return [dict(item) for item in self.models]

    async def get_gpu_memory_usage(self):
        return self.memory

    async def unload_model(self, name):
        self.calls.append(("unload", name))
        self.unload_started.set()
        if self.unload_release:
            await self.unload_release.wait()
        self.models = [item for item in self.models if item["name"] != name]

    async def stream_chat(self, model, messages, on_delta):
        self.calls.append(("stream", model))
        self.models = [{"name": model, "size_vram": 8 * 1024**3}]
        on_delta("你好")
        self.stream_started.set()
        if self.stream_release:
            await self.stream_release.wait()
        if self.stream_error:
            raise BackendError("synthetic token secret must not escape")
        on_delta("，Nova")
        return {
            "model": model,
            "response": "你好，Nova",
            "done": True,
            "stats": {"load_duration": 7},
        }


def new_manager(tmp_path, backend):
    return GpuManager(backend, tmp_path / "gpu-mode.json", drain_timeout=1, unload_timeout=0.1)


def queued(queue):
    items = []
    while not queue.empty():
        items.append(queue.get_nowait())
    return items


def test_stream_switches_model_without_changing_legacy_target(tmp_path):
    async def scenario():
        backend = ChatBackend()
        backend.models = [{"name": backend.model, "size_vram": 4000}]
        manager = new_manager(tmp_path, backend)
        await manager.start()
        try:
            queue, task = await manager.open_chat_stream(MODEL, MESSAGES, 10000)
            result = await task
            assert result["response"] == "你好，Nova"
            assert [item["type"] for item in queued(queue)] == ["delta", "delta", "done"]
            assert backend.calls.index(("unload", backend.model)) < backend.calls.index(
                ("stream", MODEL)
            )
            assert backend.model == "qwen3.5:4b"
            assert not manager._active
            assert manager.last_operation["operation"] == "chat"
            assert manager.last_operation["load_duration_ns"] == 7
            assert not manager.manual_gaming
            assert not manager.blocked
            audit = (tmp_path / "gpu-events.jsonl").read_text(encoding="utf-8")
            assert "合成练习" not in audit and "你好" not in audit
        finally:
            await manager.close()

    asyncio.run(scenario())


@pytest.mark.parametrize("protection", ["manual", "detected", "pressure", "fault"])
def test_stream_gates_before_any_backend_call(tmp_path, protection):
    async def scenario():
        backend = ChatBackend()
        manager = new_manager(tmp_path, backend)
        await manager.start()
        try:
            if protection == "manual":
                manager.manual_gaming = True
            elif protection == "detected":
                manager.detected_gaming = True
            elif protection == "pressure":
                manager.memory_pressure_hold = True
            else:
                manager.fault = "synthetic"
            with pytest.raises(ApplicationError) as caught:
                await manager.open_chat_stream(MODEL, MESSAGES, 10000)
            assert caught.value.status_code == 423
            assert backend.calls == []
        finally:
            await manager.close()

    asyncio.run(scenario())


@pytest.mark.parametrize("case", ["low", "missing", "unknown_tags"])
def test_preflight_fails_before_returning_a_stream(tmp_path, case):
    async def scenario():
        backend = ChatBackend()
        if case == "low":
            backend.memory["gpus"][0]["used_mib"] = 15000
        elif case == "missing":
            backend.memory = {"available": False, "gpus": []}
        else:
            backend.ensure_error = True
        manager = new_manager(tmp_path, backend)
        await manager.start()
        try:
            with pytest.raises(ApplicationError) as caught:
                await asyncio.wait_for(manager.open_chat_stream(MODEL, MESSAGES, 10000), 1)
            assert caught.value.status_code == (409 if case == "low" else 503)
            assert not manager._active
            assert not any(call[0] == "stream" for call in backend.calls)
            assert not manager.fault  # 未发上游加载请求，不制造不确定状态。
        finally:
            await manager.close()

    asyncio.run(scenario())


def test_resident_model_uses_headroom_not_full_weight_requirement(tmp_path):
    async def scenario():
        backend = ChatBackend()
        backend.models = [{"name": MODEL, "size_vram": 8 * 1024**3}]
        backend.memory["gpus"][0]["used_mib"] = 10000
        manager = new_manager(tmp_path, backend)
        await manager.start()
        try:
            _, task = await manager.open_chat_stream(MODEL, MESSAGES, 10000)
            assert (await task)["done"]
            assert not any(call[0] == "unload" for call in backend.calls)
        finally:
            await manager.close()

    asyncio.run(scenario())


def test_game_arriving_during_preflight_cannot_deadlock_or_load(tmp_path):
    async def scenario():
        backend = ChatBackend()
        backend.models = [{"name": backend.model, "size_vram": 1}]
        backend.ensure_release = asyncio.Event()
        manager = new_manager(tmp_path, backend)
        await manager.start()
        pending = asyncio.create_task(manager.open_chat_stream(MODEL, MESSAGES, 10000))
        await backend.ensure_started.wait()
        gaming = asyncio.create_task(manager.set_mode("GAMING"))
        await asyncio.sleep(0)
        backend.ensure_release.set()
        try:
            results = await asyncio.wait_for(
                asyncio.gather(pending, gaming, return_exceptions=True), 2
            )
            assert isinstance(results[0], ApplicationError)
            assert results[0].status_code == 423
            assert results[1]["unload_verified"]
            assert not any(call[0] == "stream" for call in backend.calls)
        finally:
            await manager.close()

    asyncio.run(scenario())


def test_game_arriving_during_switch_waits_then_prevents_loading(tmp_path):
    async def scenario():
        backend = ChatBackend()
        backend.models = [{"name": backend.model, "size_vram": 1}]
        backend.unload_release = asyncio.Event()
        manager = new_manager(tmp_path, backend)
        await manager.start()
        pending = asyncio.create_task(manager.open_chat_stream(MODEL, MESSAGES, 10000))
        await backend.unload_started.wait()
        gaming = asyncio.create_task(manager.set_mode("GAMING"))
        await asyncio.sleep(0)
        backend.unload_release.set()
        try:
            results = await asyncio.wait_for(
                asyncio.gather(pending, gaming, return_exceptions=True), 2
            )
            assert isinstance(results[0], ApplicationError)
            assert results[0].status_code == 423
            assert results[1]["unload_verified"]
            assert not any(call[0] == "stream" for call in backend.calls)
        finally:
            await manager.close()

    asyncio.run(scenario())


def test_disconnected_consumer_keeps_active_until_upstream_finishes(tmp_path):
    async def scenario():
        backend = ChatBackend()
        backend.stream_release = asyncio.Event()
        manager = new_manager(tmp_path, backend)
        await manager.start()
        queue, task = await manager.open_chat_stream(MODEL, MESSAGES, 10000)
        assert (await queue.get())["type"] == "delta"
        consumer = asyncio.create_task(queue.get())
        await asyncio.sleep(0)
        consumer.cancel()
        with pytest.raises(asyncio.CancelledError):
            await consumer
        assert len(manager._active) == 1
        with pytest.raises(ApplicationError) as caught:
            await manager.open_chat_stream(MODEL, MESSAGES, 10000)
        assert caught.value.status_code == 409
        gaming = asyncio.create_task(manager.set_mode("GAMING"))
        await asyncio.sleep(0)
        assert not gaming.done()
        backend.stream_release.set()
        try:
            assert (await task)["done"]
            assert (await gaming)["unload_verified"]
            assert not manager._active
        finally:
            await manager.close()

    asyncio.run(scenario())


def test_upstream_error_is_error_event_and_persistent_safety_lock(tmp_path):
    async def scenario():
        backend = ChatBackend()
        backend.stream_error = True
        manager = new_manager(tmp_path, backend)
        await manager.start()
        try:
            queue, task = await manager.open_chat_stream(MODEL, MESSAGES, 10000)
            with pytest.raises(ApplicationError):
                await task
            events = queued(queue)
            assert events[-1]["type"] == "error"
            assert "done" not in [item["type"] for item in events]
            assert "secret" not in json.dumps(events)
            assert manager.blocked
            state = json.loads((tmp_path / "gpu-mode.json").read_text())
            assert state["fault"] == "UPSTREAM_OPERATION_UNCERTAIN"
        finally:
            await manager.close()

    asyncio.run(scenario())


def test_idle_timeout_unloads_once_without_manual_gaming_lock(tmp_path):
    async def scenario():
        backend = ChatBackend()
        manager = new_manager(tmp_path, backend)
        await manager.start()
        try:
            _, task = await manager.open_chat_stream(MODEL, MESSAGES, 10000)
            await task
            finished = manager._last_request_finished
            assert not (await manager.idle_tick(finished + 179.9))["unloaded"]
            assert (await manager.idle_tick(finished + 180))["unloaded"]
            assert not (await manager.idle_tick(finished + 181))["unloaded"]
            assert backend.calls.count(("unload", MODEL)) == 1
            assert manager.last_operation["reason"] == "idle_timeout"
            assert manager.mode == "AUTO" and not manager.manual_gaming and not manager.blocked
        finally:
            await manager.close()

    asyncio.run(scenario())


def test_idle_timeout_verifies_ollama_expiry_instead_of_repeating_empty_unloads(tmp_path):
    async def scenario():
        backend = ChatBackend()
        manager = new_manager(tmp_path, backend)
        await manager.start()
        try:
            _, task = await manager.open_chat_stream(MODEL, MESSAGES, 10000)
            await task
            backend.models = []  # Ollama的keep_alive可能在下一次周期检查前自行到期。
            finished = manager._last_request_finished
            assert (await manager.idle_tick(finished + 180))["unloaded"]
            assert manager.unload_verified
            assert manager.last_operation["reason"] == "idle_timeout"
            assert not (await manager.idle_tick(finished + 190))["unloaded"]
        finally:
            await manager.close()

    asyncio.run(scenario())


def test_idle_timeout_cannot_interrupt_active_stream(tmp_path):
    async def scenario():
        backend = ChatBackend()
        backend.stream_release = asyncio.Event()
        manager = new_manager(tmp_path, backend)
        await manager.start()
        _, task = await manager.open_chat_stream(MODEL, MESSAGES, 10000)
        await backend.stream_started.wait()
        try:
            assert not (await manager.idle_tick(manager._last_request_finished + 1000))["unloaded"]
            assert not any(call[0] == "unload" for call in backend.calls)
        finally:
            backend.stream_release.set()
            await task
            await manager.close()

    asyncio.run(scenario())


def test_idle_status_query_race_does_not_unload_a_newly_finished_request(tmp_path):
    class IdleGateBackend(ChatBackend):
        def __init__(self):
            super().__init__()
            self.pause_next_status = False
            self.status_entered = asyncio.Event()
            self.status_release = asyncio.Event()

        async def get_model_status(self):
            snapshot = await super().get_model_status()
            if self.pause_next_status:
                self.pause_next_status = False
                self.status_entered.set()
                await self.status_release.wait()
            return snapshot

    async def scenario():
        backend = IdleGateBackend()
        manager = new_manager(tmp_path, backend)
        await manager.start()
        _, first = await manager.open_chat_stream(MODEL, MESSAGES, 10000)
        await first
        previous_finished = manager._last_request_finished
        backend.pause_next_status = True
        idle = asyncio.create_task(manager.idle_tick(previous_finished + 180))
        await asyncio.wait_for(backend.status_entered.wait(), 1)
        try:
            # 空闲查询在await中，新请求能够完成；检查返回时active又为空。
            _, newer = await manager.open_chat_stream(MODEL, MESSAGES, 10000)
            assert (await newer)["done"]
            assert not manager._active
            assert manager._last_request_finished > previous_finished
            backend.status_release.set()
            assert not (await asyncio.wait_for(idle, 1))["unloaded"]
            assert not any(call[0] == "unload" for call in backend.calls)
            assert backend.models[0]["name"] == MODEL
            assert not (await manager.idle_tick(manager._last_request_finished + 179))["unloaded"]
        finally:
            backend.status_release.set()
            await asyncio.gather(idle, return_exceptions=True)
            await manager.close()

    asyncio.run(scenario())


class Response(io.BytesIO):
    status = 200


class StreamOpener:
    def __init__(self, chunks):
        self.chunks = chunks
        self.requests = []

    def open(self, request, timeout):
        self.requests.append(request)
        if request.full_url.endswith("/api/tags"):
            return Response(json.dumps({"models": [{"name": MODEL}]}).encode())
        return Response(self.chunks)


def wire(*items):
    return b"\n".join(json.dumps(item, ensure_ascii=False).encode() for item in items) + b"\n"


def chunk(text="", done=False, **kwargs):
    return {
        "model": MODEL,
        "message": {"role": "assistant", "content": text},
        "done": done,
        **kwargs,
    }


def test_backend_ndjson_is_incremental_and_has_conservative_options():
    backend = OllamaBackend()
    backend._opener = StreamOpener(wire(chunk("你"), chunk("好", True, eval_count=2)))
    pieces = []
    result = backend._stream_chat(MODEL, MESSAGES, pieces.append)
    assert pieces == ["你", "好"]
    assert result["response"] == "你好" and result["stats"]["eval_count"] == 2
    payload = json.loads(backend._opener.requests[-1].data)
    assert payload["stream"] is True and payload["think"] is False
    assert payload["options"] == {"num_ctx": 4096, "num_predict": 512}
    assert payload["keep_alive"] == 180


def test_backend_async_callbacks_arrive_before_done_result():
    async def scenario():
        backend = OllamaBackend()
        backend._opener = StreamOpener(wire(chunk("你"), chunk("好", True)))
        pieces = []
        result = await backend.stream_chat(MODEL, MESSAGES, pieces.append)
        assert pieces == ["你", "好"]
        assert result["response"] == "你好"

    asyncio.run(scenario())


def test_backend_bounds_total_stream_bytes():
    backend = OllamaBackend()
    raw = wire(chunk("x" * 1000)) * 2100
    backend._opener = StreamOpener(raw)
    with pytest.raises(BackendError, match="大小"):
        backend._stream_chat(MODEL, MESSAGES, lambda text: None)


@pytest.mark.parametrize(
    "raw",
    [
        wire(chunk("partial")),
        wire(chunk("", True)),
        wire({"error": "a sensitive upstream error"}),
        wire(chunk("x", True, eval_count=-1)),
        b"bad-json\n",
        b"x" * 65537,
    ],
    ids=["missing-done", "empty", "upstream-error", "invalid-stats", "invalid-json", "long-line"],
)
def test_backend_malformed_or_unfinished_stream_never_returns_success(raw):
    backend = OllamaBackend()
    backend._opener = StreamOpener(raw)
    with pytest.raises(BackendError) as caught:
        backend._stream_chat(MODEL, MESSAGES, lambda text: None)
    assert "sensitive" not in str(caught.value)


@pytest.mark.parametrize(
    "messages",
    [
        [],
        [{"role": "tool", "content": "a"}],
        [{"role": "user", "content": " "}],
        [{"role": "user", "content": "x" * 24001}],
        ["bad"],
    ],
    ids=["empty", "role", "blank", "oversized", "not-object"],
)
def test_backend_rejects_invalid_messages_before_http(messages):
    backend = OllamaBackend()
    backend._opener = StreamOpener(b"")
    with pytest.raises(BackendError):
        backend._stream_chat(MODEL, messages, lambda text: None)
    assert not backend._opener.requests


def test_backend_cancellation_waits_for_actual_thread_completion(monkeypatch):
    async def scenario():
        backend = OllamaBackend()
        entered = threading.Event()
        release = threading.Event()

        def fake_stream(*args):
            entered.set()
            if not release.wait(timeout=2):
                raise AssertionError("test did not release thread")
            return {"done": True}

        monkeypatch.setattr(backend, "_stream_chat", fake_stream)
        task = asyncio.create_task(backend.stream_chat(MODEL, MESSAGES, lambda text: None))
        assert await asyncio.to_thread(entered.wait, 1)
        task.cancel()
        await asyncio.sleep(0)
        assert not task.done()
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(scenario())


def test_installed_model_listing_excludes_remote_and_cloud(monkeypatch):
    backend = OllamaBackend()
    monkeypatch.setattr(
        backend,
        "_request",
        lambda *args, **kwargs: {
            "models": [
                {"name": MODEL},
                {"name": MODEL},
                {"name": "qwen:cloud"},
                {"name": "qwen:remote", "remote_host": "https://remote.invalid"},
                {"name": "qwen:remote2", "remote_model": "qwen"},
            ]
        },
    )
    assert asyncio.run(backend.list_installed_models()) == [MODEL]


@pytest.mark.parametrize("models", [None, {}, [None], [{"name": "bad name"}]])
def test_installed_model_listing_invalid_is_not_empty_success(monkeypatch, models):
    backend = OllamaBackend()
    monkeypatch.setattr(backend, "_request", lambda *args, **kwargs: {"models": models})
    with pytest.raises(BackendError):
        asyncio.run(backend.list_installed_models())
