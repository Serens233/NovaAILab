"""云端思考隔离及协作取消；只运行合成本地替身，不发送私人数据。"""

import asyncio
import io
import json
import threading
from types import SimpleNamespace

import pytest
from pydantic import SecretStr

from nova.chat.cloud import DeepSeekBackend
from nova.errors import ApplicationError
from nova.gpu.backend import StreamControl

HIDDEN = "SYNTHETIC_REASONING_NEVER_PUBLIC"
KEY = "SYNTHETIC_CLOUD_TEST_KEY_ONLY"
MESSAGES = [{"role": "user", "content": "合成云端验证"}]


def sse(deltas, *, done=True):
    raw = b"".join(
        b"data: " + json.dumps({"choices": [{"delta": delta}]}).encode() + b"\n\n"
        for delta in deltas
    )
    return raw + (b"data: [DONE]\n\n" if done else b"")


class Response(io.BytesIO):
    status = 200


class Connection:
    def __init__(self, response, *, failure=None):
        self.response = response
        self.closed = False
        self.sent = False
        self.failure = failure

    def request(self, *args, **kwargs):
        self.sent = True
        if self.failure:
            raise self.failure

    def getresponse(self):
        return self.response

    def close(self):
        self.closed = True


def backend_for(monkeypatch, settings, response, *, failure=None):
    connection = Connection(response, failure=failure)
    calls = []

    def factory(host, *, timeout):
        calls.append((host, timeout))
        return connection

    monkeypatch.setattr("nova.chat.cloud.http.client.HTTPSConnection", factory)
    configured = settings.model_copy(update={"api_key": SecretStr(KEY), "cloud_enabled": True})
    return DeepSeekBackend(configured), connection, calls


def drain(queue):
    result = []
    while not queue.empty():
        result.append(queue.get_nowait())
    return result


def test_cloud_reasoning_content_and_split_tags_never_enter_answer(monkeypatch, settings):
    async def scenario():
        backend, connection, _ = backend_for(
            monkeypatch,
            settings,
            Response(
                sse(
                    [
                        {"reasoning_content": HIDDEN},
                        {"content": "<thi"},
                        {"content": "nk>" + HIDDEN + "</th"},
                        {"content": "ink>最终"},
                        {"content": "回答", "reasoning_content": None},
                    ]
                )
            ),
        )
        queue, worker = await backend.open_chat_stream(MESSAGES)
        result = await worker
        events = drain(queue)
        assert result["response"] == "最终回答"
        assert "".join(item["text"] for item in events if item["type"] == "delta") == "最终回答"
        assert events[-1]["type"] == "done" and connection.closed
        assert HIDDEN not in repr(events) + repr(result)

    asyncio.run(scenario())


@pytest.mark.parametrize(
    ("deltas", "done"),
    [
        ([{"reasoning_content": HIDDEN}], True),
        ([{"content": "<think>" + HIDDEN}], True),
        ([{"content": "<think>" + HIDDEN + "</think>"}], True),
        ([{"reasoning_content": HIDDEN}, {"content": "正常部分"}], False),
        ([{"reasoning_content": {"private": HIDDEN}}], True),
    ],
)
def test_cloud_only_reasoning_or_missing_finish_is_safe_failure(
    monkeypatch, settings, caplog, deltas, done
):
    async def scenario():
        backend, connection, _ = backend_for(
            monkeypatch, settings, Response(sse(deltas, done=done))
        )
        queue, worker = await backend.open_chat_stream(MESSAGES)
        with pytest.raises(ApplicationError) as error:
            await worker
        events = drain(queue)
        assert error.value.code == "CLOUD_FAILED"
        assert events[-1]["code"] == "CLOUD_FAILED"
        assert not any(event["type"] == "done" for event in events)
        assert HIDDEN not in repr(events) + str(error.value) + caplog.text
        assert connection.closed

    asyncio.run(scenario())


def test_cloud_cancel_before_connection_sends_nothing(monkeypatch, settings):
    async def scenario():
        backend, connection, calls = backend_for(monkeypatch, settings, Response(b""))
        control = StreamControl()
        control.cancel()
        queue, worker = await backend.open_chat_stream(MESSAGES, control=control)
        with pytest.raises(ApplicationError) as error:
            await worker
        assert error.value.code == "CLOUD_CANCELLED"
        assert drain(queue) == [
            {"type": "error", "code": "CLOUD_CANCELLED", "message": "云端回复已取消"}
        ]
        assert calls == [] and not connection.sent

    asyncio.run(scenario())


def test_cloud_cancel_shuts_down_bound_socket_but_waits_for_worker_cleanup(monkeypatch, settings):
    entered, interrupted, cleaned = threading.Event(), threading.Event(), threading.Event()

    class Socket:
        def shutdown(self, mode):
            interrupted.set()

        def settimeout(self, value):
            assert value > 0

    class BlockingResponse(Response):
        fp = SimpleNamespace(raw=SimpleNamespace(_sock=Socket()))

        def readline(self, limit=-1):
            entered.set()
            if not interrupted.wait(timeout=3) or not cleaned.wait(timeout=3):
                raise AssertionError("test failed to finish synthetic HTTP cleanup")
            raise OSError(HIDDEN)

    async def scenario():
        backend, connection, _ = backend_for(monkeypatch, settings, BlockingResponse(b""))
        control = StreamControl()
        queue, worker = await backend.open_chat_stream(MESSAGES, control=control)
        try:
            assert await asyncio.to_thread(entered.wait, 2)
            control.cancel()
            assert interrupted.is_set()
            await asyncio.sleep(0)
            assert not worker.done() and not connection.closed
            cleaned.set()
            with pytest.raises(ApplicationError) as error:
                await asyncio.wait_for(worker, 2)
            assert error.value.code == "CLOUD_CANCELLED" and error.value.status_code == 409
            events = drain(queue)
            assert events[-1]["code"] == "CLOUD_CANCELLED"
            assert HIDDEN not in repr(events) + str(error.value)
            assert connection.closed
        finally:
            interrupted.set()
            cleaned.set()
            await asyncio.gather(worker, return_exceptions=True)

    asyncio.run(scenario())


def test_cloud_failure_does_not_expose_keys_or_internal_reasoning(monkeypatch, settings, caplog):
    async def scenario():
        backend, connection, _ = backend_for(
            monkeypatch, settings, Response(b""), failure=OSError(HIDDEN + KEY)
        )
        queue, worker = await backend.open_chat_stream(MESSAGES, control=StreamControl())
        with pytest.raises(ApplicationError) as error:
            await worker
        events = drain(queue)
        assert error.value.code == "CLOUD_FAILED" and connection.closed
        public = repr(events) + str(error.value) + caplog.text
        assert HIDDEN not in public and KEY not in public

    asyncio.run(scenario())
