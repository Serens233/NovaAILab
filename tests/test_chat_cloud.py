"""DeepSeek传输完全使用本地替身；不联网、不读取真实密钥、不产生费用。"""

import asyncio
import io
import json
import threading

import pytest
from pydantic import SecretStr

from nova.chat.cloud import DeepSeekBackend
from nova.errors import ApplicationError

SYNTHETIC_KEY = "SYNTHETIC_CLOUD_KEY_NEVER_REAL_0123456789"
PRIVATE_UPSTREAM = "SYNTHETIC_PRIVATE_UPSTREAM_DETAIL"
MESSAGES = [{"role": "user", "content": "合成云端测试"}]


def sse(*items, done=True):
    data = [
        b"data: " + json.dumps(item, ensure_ascii=False).encode("utf-8") + b"\n\n" for item in items
    ]
    if done:
        data.append(b"data: [DONE]\n\n")
    return b"".join(data)


def delta(text):
    return {"choices": [{"delta": {"content": text}}]}


class Response(io.BytesIO):
    def __init__(self, raw, status=200):
        super().__init__(raw)
        self.status = status


class Connection:
    def __init__(self, response, *, failure=None):
        self.response = response
        self.failure = failure
        self.request_data = None
        self.closed = False

    def request(self, method, path, *, body, headers):
        self.request_data = {"method": method, "path": path, "body": body, "headers": headers}
        if self.failure:
            raise self.failure

    def getresponse(self):
        return self.response

    def close(self):
        self.closed = True


def mocked_backend(monkeypatch, settings, response, *, failure=None, model=""):
    connection = Connection(response, failure=failure)
    targets = []

    def factory(host, *, timeout):
        targets.append((host, timeout))
        return connection

    monkeypatch.setattr("nova.chat.cloud.http.client.HTTPSConnection", factory)
    configured = settings.model_copy(
        update={"api_key": SecretStr(SYNTHETIC_KEY), "cloud_enabled": True, "model": model}
    )
    return DeepSeekBackend(configured), connection, targets


def drain(queue):
    result = []
    while not queue.empty():
        result.append(queue.get_nowait())
    return result


@pytest.mark.parametrize("configured_model", ["", "synthetic-configured-model"])
def test_cloud_stream_multiple_deltas_and_fixed_server_target(
    monkeypatch, settings, configured_model
):
    async def scenario():
        backend, connection, targets = mocked_backend(
            monkeypatch,
            settings,
            Response(
                sse({"choices": [{"delta": {"role": "assistant"}}]}, delta("你好"), delta("，Nova"))
            ),
            model=configured_model,
        )
        queue, worker = await backend.open_chat_stream(MESSAGES)
        result = await worker
        events = drain(queue)
        assert result == {"model": "deepseek", "response": "你好，Nova", "done": True}
        assert [item["type"] for item in events] == ["delta", "delta", "done"]
        assert [item["text"] for item in events[:-1]] == ["你好", "，Nova"]
        assert targets == [("api.deepseek.com", 120)]
        assert connection.closed
        request = connection.request_data
        assert request["method"] == "POST" and request["path"] == "/chat/completions"
        assert request["headers"]["Authorization"] == "Bearer " + SYNTHETIC_KEY
        payload = json.loads(request["body"])
        assert payload["model"] == (configured_model or "deepseek-chat")
        assert payload["messages"] == MESSAGES and payload["stream"] is True
        assert payload["max_tokens"] == 512
        assert SYNTHETIC_KEY not in json.dumps(result) + json.dumps(events)

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "raw",
    [sse(), sse(delta("")), sse(delta(" \n\t"))],
    ids=["done-only", "empty-content", "whitespace-content"],
)
def test_cloud_done_without_reply_is_failure(monkeypatch, settings, raw):
    async def scenario():
        backend, connection, _ = mocked_backend(monkeypatch, settings, Response(raw))
        queue, worker = await backend.open_chat_stream(MESSAGES)
        with pytest.raises(ApplicationError) as caught:
            await worker
        assert caught.value.code == "CLOUD_FAILED" and caught.value.status_code == 503
        events = drain(queue)
        assert events[-1] == {
            "type": "error",
            "code": "CLOUD_FAILED",
            "message": "云端请求失败，请稍后重试",
        }
        assert not any(item["type"] == "done" for item in events)
        assert connection.closed

    asyncio.run(scenario())


@pytest.mark.parametrize("status", [401, 429, 500])
def test_cloud_http_failure_is_fixed_and_private(monkeypatch, settings, caplog, status):
    async def scenario():
        response = Response((PRIVATE_UPSTREAM + SYNTHETIC_KEY).encode(), status=status)
        backend, connection, _ = mocked_backend(monkeypatch, settings, response)
        queue, worker = await backend.open_chat_stream(MESSAGES)
        with pytest.raises(ApplicationError) as caught:
            await worker
        assert caught.value.public_message == "云端请求失败，请稍后重试"
        events = drain(queue)
        assert [item["type"] for item in events] == ["error"]
        public = str(caught.value) + json.dumps(events) + caplog.text
        assert PRIVATE_UPSTREAM not in public and SYNTHETIC_KEY not in public
        assert connection.closed

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "raw",
    [
        b"data: {bad-json:" + PRIVATE_UPSTREAM.encode() + b"}\n\n",
        sse({"error": {"message": PRIVATE_UPSTREAM + SYNTHETIC_KEY}}),
        sse(["bad-top-level"]),
        sse({"choices": [None]}),
        sse(delta({"private": PRIVATE_UPSTREAM})),
        sse(delta("部分回复"), done=False),
        b"data: " + b"x" * 131073,
    ],
    ids=["json", "error-object", "array", "choice-type", "content-type", "eof", "line-limit"],
)
def test_cloud_invalid_or_interrupted_stream_never_completes(monkeypatch, settings, caplog, raw):
    async def scenario():
        backend, connection, _ = mocked_backend(monkeypatch, settings, Response(raw))
        queue, worker = await backend.open_chat_stream(MESSAGES)
        with pytest.raises(ApplicationError) as caught:
            await worker
        events = drain(queue)
        assert events[-1]["type"] == "error"
        assert events[-1]["code"] == "CLOUD_FAILED"
        assert not any(item["type"] == "done" for item in events)
        public = str(caught.value) + json.dumps(events) + caplog.text
        assert PRIVATE_UPSTREAM not in public and SYNTHETIC_KEY not in public
        assert connection.closed

    asyncio.run(scenario())


def test_cloud_transport_exception_does_not_expose_details(monkeypatch, settings, caplog):
    async def scenario():
        backend, connection, _ = mocked_backend(
            monkeypatch,
            settings,
            Response(b""),
            failure=OSError(PRIVATE_UPSTREAM + SYNTHETIC_KEY),
        )
        queue, worker = await backend.open_chat_stream(MESSAGES)
        with pytest.raises(ApplicationError) as caught:
            await worker
        events = drain(queue)
        assert [item["type"] for item in events] == ["error"]
        assert connection.closed
        public = str(caught.value) + json.dumps(events) + caplog.text
        assert PRIVATE_UPSTREAM not in public and SYNTHETIC_KEY not in public

    asyncio.run(scenario())


def test_cancelled_cloud_worker_stays_tracked_until_http_thread_finishes(monkeypatch, settings):
    entered, release = threading.Event(), threading.Event()

    class BlockingResponse(Response):
        def readline(self, limit=-1):
            if not entered.is_set():
                entered.set()
                if not release.wait(timeout=5):
                    raise AssertionError("test did not release synthetic HTTP thread")
            return super().readline(limit)

    async def scenario():
        backend, connection, _ = mocked_backend(
            monkeypatch, settings, BlockingResponse(sse(delta("安全结束")))
        )
        queue, worker = await backend.open_chat_stream(MESSAGES)
        try:
            assert await asyncio.to_thread(entered.wait, 2)
            worker.cancel()
            await asyncio.sleep(0)
            assert not worker.done()
            assert not connection.closed
            assert queue.empty()
            release.set()
            with pytest.raises(ApplicationError) as caught:
                await asyncio.wait_for(asyncio.shield(worker), 2)
            assert caught.value.code == "CLOUD_FAILED"
            events = drain(queue)
            assert events[-1]["type"] == "error"
            assert not any(item["type"] == "done" for item in events)
            assert connection.closed
        finally:
            release.set()
            await asyncio.gather(worker, return_exceptions=True)

    asyncio.run(scenario())
