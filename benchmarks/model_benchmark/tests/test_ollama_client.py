from __future__ import annotations

import pytest
from fake_ollama import FakeOllamaServer

from benchmarks.model_benchmark.ollama_client import OllamaClient, OllamaError


def _client(server: FakeOllamaServer) -> OllamaClient:
    return OllamaClient(server.url, timeout_s=5.0)


def test_version_tags_show_and_ps() -> None:
    with FakeOllamaServer() as server:
        client = _client(server)
        assert client.version()["version"] == "0.40.13-test"
        tags = client.tags()
        assert len(tags) == 3
        assert tags[0]["details"]["quantization_level"] == "Q4_K_M"
        shown = client.show("qwen3.5:4b")
        assert shown["details"]["parameter_size"] == "4.2B"
        assert shown["model_info"]["qwen35.context_length"] == 262144
        assert client.ps() == []
        assert client.loaded_models() == []


def test_chat_stream_content_and_final_metrics() -> None:
    with FakeOllamaServer() as server:
        client = _client(server)
        chunks = list(
            client.chat_stream(
                "qwen3.5:4b",
                [{"role": "user", "content": "你好"}],
                options={"num_ctx": 4096, "num_predict": 32},
                think=False,
            )
        )
        assert chunks[-1]["done"] is True
        content = "".join(
            str(chunk["message"].get("content", ""))
            for chunk in chunks
            if isinstance(chunk.get("message"), dict)
        )
        assert content == "你好，世界。"
        final = chunks[-1]
        assert final["eval_count"] == 20
        assert final["load_duration"] == 20_000_000
        assert client.loaded_models() == ["qwen3.5:4b"]
        sent = [item for item in server.state.requests if item["path"] == "/api/chat"]
        assert sent
        assert sent[0]["payload"]["stream"] is True
        assert sent[0]["payload"]["think"] is False
        assert sent[0]["payload"]["options"]["num_ctx"] == 4096


def test_unload_waits_for_ps() -> None:
    with FakeOllamaServer() as server:
        client = _client(server)
        list(
            client.chat_stream(
                "qwen3.5:4b",
                [{"role": "user", "content": "hi"}],
                options={"num_predict": 4},
            )
        )
        assert client.loaded_models() == ["qwen3.5:4b"]
        result = client.unload("qwen3.5:4b", wait_s=5.0)
        assert result["already_unloaded"] is False
        assert server.state.unload_calls == ["qwen3.5:4b"]
        assert client.loaded_models() == []
        again = client.unload("qwen3.5:4b", wait_s=5.0)
        assert again["already_unloaded"] is True


def test_http_error_is_reported() -> None:
    with FakeOllamaServer() as server:
        server.state.fail_chat = True
        client = _client(server)
        with pytest.raises(OllamaError) as raised:
            list(
                client.chat_stream(
                    "qwen3.5:4b",
                    [{"role": "user", "content": "hi"}],
                    options={"num_predict": 4},
                )
            )
        assert raised.value.status == 500


def test_malformed_stream_is_reported() -> None:
    with FakeOllamaServer() as server:
        server.state.malformed = True
        client = _client(server)
        with pytest.raises(OllamaError):
            list(
                client.chat_stream(
                    "qwen3.5:4b",
                    [{"role": "user", "content": "hi"}],
                    options={"num_predict": 4},
                )
            )


def test_rejects_non_loopback_and_invalid_model() -> None:
    with pytest.raises(ValueError):
        OllamaClient("http://192.0.2.10:11434")
    with FakeOllamaServer() as server:
        client = _client(server)
        with pytest.raises(ValueError):
            list(
                client.chat_stream(
                    "bad model",
                    [{"role": "user", "content": "hi"}],
                )
            )
