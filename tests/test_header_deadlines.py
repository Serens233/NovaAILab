"""响应头前及安装预检同样受截止线/取消约束；只有本机合成HTTP与云端替身。"""

import asyncio
import io
import json
import threading
import time
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from nova.chat.cloud import DeepSeekBackend
from nova.errors import ApplicationError
from nova.gpu.backend import BackendError, CancelledBackendError, OllamaBackend, StreamControl

MODEL = "practice:tiny"
MESSAGES = [{"role": "user", "content": "合成截止线测试"}]


@contextmanager
def slow_server(stage):
    started = threading.Event()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def respond(self):
            tags = self.path == "/api/tags"
            body = (
                json.dumps({"models": [{"name": MODEL}]}).encode()
                if tags
                else json.dumps(
                    {
                        "model": MODEL,
                        "message": {"role": "assistant", "content": "答案"},
                        "done": True,
                    }
                ).encode()
                + b"\n"
            )
            slow = (tags and stage.startswith("tags")) or (not tags and stage == "chat_headers")
            try:
                if slow and stage.endswith("headers"):
                    self.connection.sendall(b"HTTP/1.1 200 OK\r\nX-Slow: ")
                    started.set()
                    for _ in range(45):
                        self.connection.sendall(b"x")
                        time.sleep(0.02)
                    self.connection.sendall(
                        b"\r\nContent-Length: " + str(len(body)).encode() + b"\r\n\r\n" + body
                    )
                else:
                    self.send_response(200)
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    if slow:
                        started.set()
                        for byte in body:
                            self.connection.sendall(bytes([byte]))
                            time.sleep(0.02)
                    else:
                        self.wfile.write(body)
            except OSError:
                pass

        def do_GET(self):
            self.respond()

        def do_POST(self):
            self.rfile.read(int(self.headers["Content-Length"]))
            self.respond()

    with ThreadingHTTPServer(("127.0.0.1", 0), Handler) as server:
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            yield f"http://127.0.0.1:{server.server_port}", started
        finally:
            server.shutdown()
            thread.join(timeout=2)


@pytest.mark.parametrize("stage", ["tags_headers", "tags_body", "chat_headers"])
def test_stream_deadline_covers_installation_preflight_and_response_headers(stage):
    with slow_server(stage) as (url, _):
        backend = OllamaBackend(url, MODEL, timeout=0.12)
        started = time.monotonic()
        with pytest.raises(BackendError):
            backend._stream_chat(MODEL, MESSAGES, lambda text: None)
        assert time.monotonic() - started < 0.6


@pytest.mark.parametrize("stage", ["tags_headers", "tags_body", "chat_headers"])
def test_stop_unblocks_headers_and_installation_preflight(stage):
    with slow_server(stage) as (url, entered):

        async def scenario():
            control = StreamControl()
            backend = OllamaBackend(url, MODEL, timeout=3)
            operation = asyncio.create_task(
                backend.stream_chat(MODEL, MESSAGES, lambda text: None, control=control)
            )
            try:
                assert await asyncio.to_thread(entered.wait, 2)
                cancelled_at = time.monotonic()
                control.cancel()
                with pytest.raises(CancelledBackendError):
                    await asyncio.wait_for(operation, 2)
                assert time.monotonic() - cancelled_at < 0.6
            finally:
                control.cancel()
                await asyncio.gather(operation, return_exceptions=True)

        asyncio.run(scenario())


def test_nonstream_http_request_has_total_header_deadline():
    with slow_server("tags_headers") as (url, _):
        backend = OllamaBackend(url, MODEL)
        started = time.monotonic()
        with pytest.raises(BackendError):
            backend._request("GET", "/api/tags", timeout=0.12)
        assert time.monotonic() - started < 0.6


@pytest.mark.parametrize("interrupt", ["cancel", "deadline"])
def test_cloud_headers_are_bound_before_getresponse(monkeypatch, settings, interrupt):
    entered, interrupted = threading.Event(), threading.Event()
    real_timer = threading.Timer

    class Socket:
        def shutdown(self, mode):
            interrupted.set()

        def settimeout(self, value):
            assert value > 0

    class Response(io.BytesIO):
        status = 200

    class Connection:
        def __init__(self):
            self.sock = None
            self.closed = False

        def connect(self):
            self.sock = Socket()

        def request(self, *args, **kwargs):
            pass

        def getresponse(self):
            entered.set()
            if interrupted.wait(0.9):
                raise OSError("synthetic interrupted headers")
            return Response(b"data: [DONE]\n\n")

        def close(self):
            self.closed = True

    connection = Connection()
    monkeypatch.setattr("nova.chat.cloud.http.client.HTTPSConnection", lambda *a, **kw: connection)
    if interrupt == "deadline":
        monkeypatch.setattr(
            "nova.chat.cloud.threading.Timer", lambda delay, fn: real_timer(0.12, fn)
        )

    async def scenario():
        control = StreamControl()
        backend = DeepSeekBackend(settings)
        started = time.monotonic()
        queue, worker = await backend.open_chat_stream(MESSAGES, control=control)
        assert await asyncio.to_thread(entered.wait, 2)
        if interrupt == "cancel":
            control.cancel()
        with pytest.raises(ApplicationError) as caught:
            await worker
        assert caught.value.code == ("CLOUD_CANCELLED" if interrupt == "cancel" else "CLOUD_FAILED")
        assert time.monotonic() - started < 0.6
        assert connection.closed
        assert queue.get_nowait()["type"] == "error"

    asyncio.run(scenario())
