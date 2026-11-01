"""本地输运的思考隔离、真实连接取消及性能口径；完全不加载模型。"""

import asyncio
import io
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from nova.gpu.backend import (
    BackendError,
    CancelledBackendError,
    OllamaBackend,
    StreamControl,
    model_allocation,
)

MODEL = "qwen3.5:4b"
MESSAGES = [{"role": "user", "content": "合成输运测试"}]
HIDDEN = "SYNTHETIC_INTERNAL_REASONING_MUST_STAY_PRIVATE"


class Response(io.BytesIO):
    status = 200


class Opener:
    def __init__(self, chunks):
        self.raw = b"\n".join(json.dumps(item).encode() for item in chunks) + b"\n"
        self.requests = []

    def open(self, request, timeout):
        self.requests.append(request)
        if request.full_url.endswith("/api/tags"):
            return Response(json.dumps({"models": [{"name": MODEL}]}).encode())
        return Response(self.raw)


def chunk(content="", *, done=False, thinking=None, **stats):
    message = {"role": "assistant", "content": content}
    if thinking is not None:
        message["thinking"] = thinking
    return {"model": MODEL, "message": message, "done": done, **stats}


def backend_for(chunks):
    backend = OllamaBackend(model=MODEL)
    backend._opener = Opener(chunks)
    return backend


def test_thinking_only_chunks_are_not_body_and_stats_measure_first_visible_answer():
    reasoning = chunk(thinking=HIDDEN)
    del reasoning["message"]["content"]
    backend = backend_for(
        [
            reasoning,
            chunk("你好"),
            chunk("，Nova", done=True, eval_count=3, eval_duration=150_000_000, load_duration=7),
        ]
    )
    visible = []
    result = backend._stream_chat(MODEL, MESSAGES, visible.append, runtime={"think": True})
    assert visible == ["你好", "，Nova"]
    assert result["response"] == "你好，Nova"
    assert HIDDEN not in repr(result) and HIDDEN not in repr(visible)
    stats = result["stats"]
    assert 0 <= stats["first_activity_latency_ms"] <= stats["first_token_latency_ms"]
    assert stats["first_token_latency_ms"] <= stats["request_duration_ms"]
    assert stats["tokens_per_second"] == 20
    assert stats["load_duration"] == 7
    payload = json.loads(backend._opener.requests[-1].data)
    assert payload["think"] is True
    assert "think" not in payload["options"]


@pytest.mark.parametrize("tag", ["think", "thinking", "analysis", "reasoning", "THINK"])
def test_split_reasoning_tags_never_reach_delta_or_final_answer(tag):
    backend = backend_for(
        [
            chunk("<" + tag[:2]),
            chunk(tag[2:] + ">" + HIDDEN[:10]),
            chunk(HIDDEN[10:] + "</" + tag[:1]),
            chunk(tag[1:] + ">答"),
            chunk("案", done=True),
        ]
    )
    visible = []
    result = backend._stream_chat(MODEL, MESSAGES, visible.append)
    assert "".join(visible) == "答案"
    assert result["response"] == "答案"
    assert HIDDEN not in repr(result) and HIDDEN not in repr(visible)


def test_nested_reasoning_regions_and_normal_html_are_handled_separately():
    backend = backend_for(
        [chunk(f"<think>x<analysis>{HIDDEN}</analysis>y</think><b>回答</b>", done=True)]
    )
    visible = []
    result = backend._stream_chat(MODEL, MESSAGES, visible.append)
    assert result["response"] == "<b>回答</b>"
    assert visible == ["<b>回答</b>"]


@pytest.mark.parametrize(
    "parts",
    [
        [chunk("<think>" + HIDDEN, done=True)],
        [chunk("<thi", done=True)],
        [chunk(thinking=HIDDEN, done=True)],
        [chunk("<think>x</analysis>答案", done=True)],
    ],
)
def test_unfinished_or_only_reasoning_is_error_without_private_text(parts):
    visible = []
    with pytest.raises(BackendError) as error:
        backend_for(parts)._stream_chat(MODEL, MESSAGES, visible.append)
    assert HIDDEN not in str(error.value) and HIDDEN not in "".join(visible)


@pytest.mark.parametrize("bad", [None, 0, 1, "true", [], {}])
def test_thinking_option_is_strict_boolean_and_rejected_before_http(bad):
    backend = backend_for([chunk("答案", done=True)])
    with pytest.raises(BackendError):
        backend._stream_chat(MODEL, MESSAGES, lambda text: None, runtime={"think": bad})
    assert backend._opener.requests == []


def test_non_string_thinking_field_is_not_a_success():
    backend = backend_for([chunk("答案", done=True, thinking=42)])
    with pytest.raises(BackendError):
        backend._stream_chat(MODEL, MESSAGES, lambda text: None)


def test_cancel_before_admission_makes_no_http_calls():
    backend = backend_for([chunk("答案", done=True)])
    control = StreamControl()
    control.cancel()
    control.cancel()
    assert control.requested
    with pytest.raises(CancelledBackendError):
        backend._stream_chat(MODEL, MESSAGES, lambda text: None, control=control)
    assert backend._opener.requests == []


def test_explicit_cancel_unblocks_real_local_socket_and_joins_worker():
    release = threading.Event()
    connected = threading.Event()

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            body = json.dumps({"models": [{"name": MODEL}]}).encode()
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self):
            self.rfile.read(int(self.headers["Content-Length"]))
            self.send_response(200)
            self.send_header("Content-Type", "application/x-ndjson")
            self.end_headers()
            self.wfile.write(json.dumps(chunk("部分回答")).encode() + b"\n")
            self.wfile.flush()
            connected.set()
            release.wait(timeout=5)

        def log_message(self, *args):
            pass

    async def scenario(server):
        backend = OllamaBackend(f"http://127.0.0.1:{server.server_port}", MODEL, timeout=5)
        control = StreamControl()
        deltas = []
        task = asyncio.create_task(
            backend.stream_chat(MODEL, MESSAGES, deltas.append, control=control)
        )
        try:
            assert await asyncio.to_thread(connected.wait, 2)
            for _ in range(100):
                if deltas:
                    break
                await asyncio.sleep(0.001)
            assert deltas == ["部分回答"]
            control.cancel()
            with pytest.raises(CancelledBackendError):
                await asyncio.wait_for(task, 1)
            assert task.done()
        finally:
            release.set()
            if not task.done():
                control.cancel()
                await asyncio.gather(task, return_exceptions=True)

    with ThreadingHTTPServer(("127.0.0.1", 0), Handler) as server:
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            asyncio.run(scenario(server))
        finally:
            release.set()
            server.shutdown()
            thread.join(timeout=2)


def test_total_timeout_is_not_reset_by_each_chunk():
    class SlowResponse(Response):
        def readline(self, limit):
            time.sleep(0.03)
            return super().readline(limit)

    class SlowOpener(Opener):
        def open(self, request, timeout):
            if request.full_url.endswith("/api/chat"):
                return SlowResponse(self.raw)
            return super().open(request, timeout)

    backend = OllamaBackend(timeout=0.05)
    backend._opener = SlowOpener([chunk("一"), chunk("二"), chunk("三", done=True)])
    with pytest.raises(BackendError, match="时限"):
        backend._stream_chat(MODEL, MESSAGES, lambda text: None)


def test_slow_byte_trickle_cannot_extend_total_deadline_indefinitely():
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            body = json.dumps({"models": [{"name": MODEL}]}).encode()
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self):
            self.rfile.read(int(self.headers["Content-Length"]))
            self.send_response(200)
            self.end_headers()
            try:
                for _ in range(100):
                    self.wfile.write(b" ")
                    self.wfile.flush()
                    time.sleep(0.02)
            except OSError:
                pass

        def log_message(self, *args):
            pass

    with ThreadingHTTPServer(("127.0.0.1", 0), Handler) as server:
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            backend = OllamaBackend(f"http://127.0.0.1:{server.server_port}", MODEL, timeout=0.15)
            started = time.monotonic()
            with pytest.raises(BackendError, match="时限"):
                backend._stream_chat(MODEL, MESSAGES, lambda text: None)
            assert time.monotonic() - started < 0.8
        finally:
            server.shutdown()
            thread.join(timeout=2)


def test_synchronous_generate_also_filters_explicit_reasoning(monkeypatch):
    backend = OllamaBackend()
    monkeypatch.setattr(backend, "_ensure_local", lambda: None)
    monkeypatch.setattr(
        backend,
        "_request",
        lambda *args, **kwargs: {
            "model": backend.model,
            "done": True,
            "response": f"<think>{HIDDEN}</think>最终回答",
            "thinking": HIDDEN,
        },
    )
    result = asyncio.run(backend.generate("合成问题"))
    assert result["response"] == "最终回答" and HIDDEN not in repr(result)


@pytest.mark.parametrize("text", [f"<think>{HIDDEN}", f"<think>{HIDDEN}</think>"])
def test_synchronous_generate_does_not_succeed_on_reasoning_alone(monkeypatch, text):
    backend = OllamaBackend()
    monkeypatch.setattr(backend, "_ensure_local", lambda: None)
    monkeypatch.setattr(
        backend,
        "_request",
        lambda *args, **kwargs: {"model": backend.model, "done": True, "response": text},
    )
    with pytest.raises(BackendError) as error:
        asyncio.run(backend.generate("合成问题"))
    assert HIDDEN not in str(error.value)


def test_memory_allocation_is_estimated_and_does_not_invent_layer_counts():
    result = model_allocation({"name": MODEL, "size": 100, "size_vram": 60})
    assert result["gpu_fraction_estimate"] == 0.6
    assert result["cpu_bytes_estimate"] == 40
    assert result["gpu_layer_count"] is None and result["cpu_layer_count"] is None
    assert model_allocation({"name": MODEL, "size_vram": 60})["gpu_fraction_estimate"] is None
    assert (
        model_allocation({"name": MODEL, "size": 0, "size_vram": 60})["cpu_bytes_estimate"] is None
    )
