"""Ollama 协议、回环边界及 NVIDIA 监控的离线行为验证。"""

import asyncio
import json
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace
from urllib.error import URLError

import pytest

from nova.gpu.backend import BackendError, OllamaBackend, validate_base_url

MODEL = "qwen3.5:4b"


class Response:
    status = 200

    def __init__(self, data):
        self.raw = data if isinstance(data, bytes) else json.dumps(data).encode()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def read(self, limit):
        return self.raw[:limit]


class Opener:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = []

    def open(self, request, timeout):
        self.calls.append((request, timeout))
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return Response(response)


def client(*responses, **kwargs):
    result = OllamaBackend(**kwargs)
    result._opener = Opener(*responses)
    return result


@pytest.mark.parametrize(
    "url",
    [
        "https://127.0.0.1:11434",
        "http://localhost:11434",
        "http://127.1:11434",
        "http://example.com",
        "http://127.0.0.1@evil.example",
        "http://user:password@127.0.0.1:11434",
        "http://127.0.0.1:11434/api",
        "http://127.0.0.1:11434?",
        "http://127.0.0.1:11434#",
        "http://127.0.0.1:0",
        "http://127.0.0.1:65536",
        "http://127.0.0.1:11434\\evil",
        " http://127.0.0.1:11434",
    ],
)
def test_rejects_remote_or_ambiguous_base_urls(url):
    with pytest.raises(BackendError):
        OllamaBackend(url)


def test_normalizes_loopback_root():
    assert validate_base_url("http://127.0.0.1/") == "http://127.0.0.1:11434"
    assert validate_base_url("http://127.0.0.1:9000") == "http://127.0.0.1:9000"


@pytest.mark.parametrize("timeout", [True, 0, -1, 301, float("nan"), float("inf"), "120"])
def test_invalid_timeouts_are_rejected(timeout):
    with pytest.raises(BackendError):
        OllamaBackend(timeout=timeout)


def test_model_status_returns_only_operational_fields():
    backend = client(
        {"models": [{"name": MODEL, "size_vram": 100, "size": 200, "context": "private"}]}
    )
    assert asyncio.run(backend.get_model_status()) == [
        {"name": MODEL, "size_vram": 100, "size": 200}
    ]
    assert backend._opener.calls[0][1] == 5


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"models": None},
        {"models": {}},
        {"models": [None]},
        {"models": [{"name": MODEL}]},
        {"models": [{"name": MODEL, "size_vram": True}]},
        {"models": [{"name": MODEL, "size_vram": -1}]},
    ],
)
def test_invalid_status_cannot_be_treated_as_no_models(payload):
    with pytest.raises(BackendError):
        asyncio.run(client(payload).get_model_status())


@pytest.mark.parametrize("payload", [b"not json", b"[]", {"error": "SYNTHETIC_PRIVATE_BODY"}])
def test_invalid_or_error_json_is_safe(payload):
    with pytest.raises(BackendError) as caught:
        asyncio.run(client(payload).get_model_status())
    assert "SYNTHETIC_PRIVATE_BODY" not in str(caught.value)


def test_network_error_is_safe():
    with pytest.raises(BackendError) as caught:
        asyncio.run(client(URLError("SYNTHETIC_PRIVATE_DETAIL")).get_model_status())
    assert "SYNTHETIC_PRIVATE_DETAIL" not in str(caught.value)


def test_redirects_are_not_followed(monkeypatch):
    hits = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            hits.append(self.path)
            self.send_response(302)
            self.send_header("Location", "/would-leak-prompt")
            self.end_headers()

        def log_message(self, *args):
            pass

    # 即使系统有代理配置，本地请求也只抵达这个测试服务。
    monkeypatch.setenv("http_proxy", "http://127.0.0.1:1")
    with ThreadingHTTPServer(("127.0.0.1", 0), Handler) as server:
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            backend = OllamaBackend(f"http://127.0.0.1:{server.server_port}")
            with pytest.raises(BackendError):
                asyncio.run(backend.get_model_status())
        finally:
            server.shutdown()
            thread.join(timeout=2)
    assert hits == ["/api/ps"]


def test_response_size_is_bounded():
    with pytest.raises(BackendError, match="大小"):
        asyncio.run(client(b"x" * (2 * 1024 * 1024 + 1)).get_model_status())


def test_unload_and_sleep_use_real_unload_protocol():
    backend = client(*[{"model": MODEL, "done": True, "done_reason": "unload"}] * 2)
    asyncio.run(backend.unload_model(MODEL))
    asyncio.run(backend.sleep_model(MODEL))
    for request, timeout in backend._opener.calls:
        payload = json.loads(request.data)
        assert request.full_url.endswith("/api/generate")
        assert payload["model"] == MODEL
        assert payload["prompt"] == ""
        assert payload["keep_alive"] == 0
        assert payload["stream"] is False
        assert timeout == 10


def test_wake_checks_local_install_and_uses_short_residency():
    backend = client({"models": [{"name": MODEL}]}, {"model": MODEL, "done": True})
    asyncio.run(backend.wake_model())
    assert backend._opener.calls[0][0].full_url.endswith("/api/tags")
    request, timeout = backend._opener.calls[1]
    payload = json.loads(request.data)
    assert payload["prompt"] == ""
    assert payload["keep_alive"] == 180
    assert payload["options"]["num_ctx"] == 4096
    assert timeout == 120


def test_generate_returns_no_context_thinking_or_unknown_fields():
    backend = client(
        {"models": [{"name": MODEL}]},
        {
            "model": MODEL,
            "done": True,
            "response": "你好",
            "load_duration": 12,
            "context": [123],
            "thinking": "private",
            "secret": "private",
        },
    )
    assert asyncio.run(backend.generate("测试")) == {
        "model": MODEL,
        "done": True,
        "response": "你好",
        "load_duration": 12,
    }
    payload = json.loads(backend._opener.calls[1][0].data)
    assert payload["think"] is False
    assert payload["options"]["num_predict"] == 128


@pytest.mark.parametrize("model", ["qwen:cloud", "qwen:4b-cloud"])
def test_cloud_model_names_never_make_a_request(model):
    backend = client(model=model)
    with pytest.raises(BackendError, match="云端"):
        asyncio.run(backend.wake_model())
    assert backend._opener.calls == []


@pytest.mark.parametrize("remote", [{"remote_host": "example.com"}, {"remote_model": "qwen"}])
def test_remote_metadata_never_allows_generate(remote):
    backend = client({"models": [{"name": MODEL, **remote}]})
    with pytest.raises(BackendError, match="云端"):
        asyncio.run(backend.generate("测试"))
    assert len(backend._opener.calls) == 1


def test_missing_model_is_not_downloaded():
    backend = client({"models": []})
    with pytest.raises(BackendError, match="不会自动下载"):
        asyncio.run(backend.generate("测试"))
    assert len(backend._opener.calls) == 1


@pytest.mark.parametrize("prompt", [None, "", "  ", "x" * 8193])
def test_invalid_prompts_make_no_request(prompt):
    backend = client()
    with pytest.raises(BackendError):
        asyncio.run(backend.generate(prompt))
    assert backend._opener.calls == []


@pytest.mark.parametrize(
    "answer",
    [
        {"model": MODEL, "done": False, "response": "text"},
        {"model": MODEL, "done": True, "response": ""},
        {"model": "different:1b", "done": True, "response": "text"},
        {"model": MODEL, "done": True, "response": "text", "load_duration": -1},
    ],
)
def test_unfinished_or_invalid_generations_fail(answer):
    with pytest.raises(BackendError):
        asyncio.run(client({"models": [{"name": MODEL}]}, answer).generate("测试"))


def test_nvidia_memory_and_wddm_unknown_process_memory(monkeypatch):
    calls = []
    outputs = iter(
        [
            "0, GPU-example, NVIDIA GeForce RTX 5060 Ti, 16311, 3000\n",
            "123, C:\\Users\\private\\ollama.exe, [N/A]\n",
        ]
    )

    def run(command, **kwargs):
        calls.append((command, kwargs))
        return SimpleNamespace(stdout=next(outputs))

    monkeypatch.setattr(subprocess, "run", run)
    result = asyncio.run(OllamaBackend().get_gpu_memory_usage())
    assert result["available"] is True
    assert result["gpus"][0]["used_mib"] == 3000
    assert result["gpus"][0]["total_mib"] == 16311
    assert result["compute_processes"] == [{"pid": 123, "name": "ollama.exe", "used_mib": None}]
    for command, kwargs in calls:
        assert command[0] == "nvidia-smi"
        assert kwargs["timeout"] <= 5
        assert not kwargs.get("shell")


@pytest.mark.parametrize("failure", [FileNotFoundError, subprocess.TimeoutExpired, ValueError])
def test_gpu_monitor_errors_do_not_fabricate_zero_usage(monkeypatch, failure):
    def fail(*args, **kwargs):
        if failure is subprocess.TimeoutExpired:
            raise failure("nvidia-smi", 5)
        raise failure("SYNTHETIC_PRIVATE_DETAIL")

    monkeypatch.setattr(subprocess, "run", fail)
    result = asyncio.run(OllamaBackend().get_gpu_memory_usage())
    assert result["available"] is False
    assert result["gpus"] == []
    assert "SYNTHETIC_PRIVATE_DETAIL" not in str(result)
