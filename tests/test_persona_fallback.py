"""回退重新遵守实际模型上下文预算；全为离线替身，不加载27B。"""

import asyncio

from fastapi.testclient import TestClient

from nova.config import QWEN38_MODEL
from nova.errors import ApplicationError
from tests.test_v3_api import AUTH, payload, thread
from tests.test_v3_api import factory as factory


def assert_no_generation_tasks(app, client):
    client.portal.call(asyncio.sleep, 0)
    service = app.state.chat_service
    assert not service._requests
    assert not service._preparing
    assert not service._runs
    assert client.get("/api/gpu/status", headers=AUTH).json()["active_requests"] == 0


def test_gpu_preflight_fallback_records_actual_model_and_budget(factory, monkeypatch):
    make, backend, _, _ = factory
    app = make(chat_fallback_model="qwen3.5:4b")
    with TestClient(app) as client:
        identity = thread(client)
        original = app.state.gpu_manager.open_chat_stream
        attempts = []

        async def preflight(model, *args, **kwargs):
            attempts.append(model)
            if model == "qwen3.5:9b":
                raise ApplicationError("GPU_MEMORY_INSUFFICIENT", "合成显存预检不足", 409)
            return await original(model, *args, **kwargs)

        monkeypatch.setattr(app.state.gpu_manager, "open_chat_stream", preflight)
        response = client.post(
            "/api/v1/chat",
            json=payload(identity, model="qwen3.5:9b", stream=False),
            headers=AUTH,
        )
        assert response.status_code == 200
        assert response.json()["message"]["model"] == "qwen3.5:4b"
        assert attempts == ["qwen3.5:9b", "qwen3.5:4b"]
        assert [call["model"] for call in backend.stream_calls] == ["qwen3.5:4b"]
        metrics = app.state.chat_service.last_metrics
        assert metrics["model"] == "qwen3.5:4b"
        assert (metrics["context_tokens"], metrics["reserved_output_tokens"]) == (4096, 512)
        assert metrics["estimated_prompt_tokens"] + 512 + 128 <= 4096
        assert_no_generation_tasks(app, client)


def test_smaller_fallback_rechecks_budget_before_loading_and_finishes_run(factory, monkeypatch):
    make, backend, _, _ = factory
    app = make(chat_fallback_model="qwen3.5:4b", qwen38_context=8192, qwen38_num_predict=1024)
    with TestClient(app) as client:
        identity = thread(client)
        content = "界" * 1000
        service = app.state.chat_service
        prepared = service.orchestrator.build(
            identity, [], content, context_tokens=8192, output_tokens=1024
        )
        assert prepared.messages[-1]["content"] == content
        assert prepared.estimated_prompt_tokens + 1024 + 128 <= 8192
        assert prepared.estimated_prompt_tokens + 512 + 128 > 4096
        attempts = []

        async def preflight(model, *args, **kwargs):
            attempts.append(model)
            if model == QWEN38_MODEL:
                raise ApplicationError("GPU_MEMORY_INSUFFICIENT", "合成显存预检不足", 409)
            raise AssertionError("超出回退上下文预算时不得启动回退模型")

        monkeypatch.setattr(app.state.gpu_manager, "open_chat_stream", preflight)
        response = client.post(
            "/api/v1/chat", json=payload(identity, content=content, stream=False), headers=AUTH
        )
        assert response.status_code == 422
        assert response.json()["error"]["code"] == "CHAT_CONTEXT_TOO_LARGE"
        assert attempts == [QWEN38_MODEL]
        assert backend.stream_calls == backend.ensured == []
        messages = client.get("/api/v1/threads/" + identity, headers=AUTH).json()["messages"]
        assert [(message["role"], message["status"]) for message in messages] == [
            ("user", "complete"),
            ("assistant", "error"),
        ]
        with app.state.database.session() as connection:
            assert [row[0] for row in connection.execute("SELECT status FROM nova_chat_runs")] == [
                "error"
            ]
        assert service.last_metrics is None
        assert service.orchestrator.persona.dynamic.get(identity) is None
        assert_no_generation_tasks(app, client)
