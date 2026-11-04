"""合成消息与临时数据库：验证聊天并发和持久化失败的恢复。"""

from concurrent.futures import ThreadPoolExecutor
from threading import Event
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from nova.errors import ApplicationError
from tests.test_chat_api import AUTH, chat_payload, events, new_thread
from tests.test_chat_api import app_factory as app_factory
from tests.test_chat_storage import database as database
from tests.test_chat_storage import store as store


def test_context_uses_latest_committed_turn_even_if_another_request_waited(
    app_factory, monkeypatch
):
    create, backend = app_factory
    app = create()
    with TestClient(app) as client:
        identity = new_thread(client)["id"]
        waiting = chat_payload(identity, content="第二个问题")
        entered, release = Event(), Event()
        original = app.state.chat_service.store.begin_run

        def delayed(thread_id, model, content, request_id, **kwargs):
            if request_id == waiting["request_id"]:
                entered.set()
                assert release.wait(10)
            return original(thread_id, model, content, request_id, **kwargs)

        monkeypatch.setattr(app.state.chat_service.store, "begin_run", delayed)
        with ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(client.post, "/api/v1/chat", json=waiting, headers=AUTH)
            try:
                assert entered.wait(10)
                first = chat_payload(identity, content="第一个问题")
                assert (
                    events(client.post("/api/v1/chat", json=first, headers=AUTH))[-1]["type"]
                    == "done"
                )
            finally:
                release.set()
            assert events(future.result(timeout=10))[-1]["type"] == "done"
        assert backend.stream_calls[-1]["messages"][-3:] == [
            {"role": "user", "content": "第一个问题"},
            {"role": "assistant", "content": "你好，Nova。"},
            {"role": "user", "content": "第二个问题"},
        ]


def test_bounded_history_preserves_only_complete_recent_pairs(store):
    thread = store.create_thread()["id"]
    for number in range(24):
        run = store.begin_run(thread, "qwen3.5:9b", f"问题{number}", f"request-{number}")
        status = "interrupted" if number in (18, 20, 22) else "complete"
        store.finish_run(run["run_id"], "\u3000" if number == 23 else f"回复{number}", status)
    run = store.begin_run(thread, "qwen3.5:9b", "新问题", "new-request", prepare=lambda h: h)
    history = run["prepared"]
    expected = [number for number in range(24) if number not in (18, 20, 22, 23)][-10:]
    assert [(entry["role"], entry["content"]) for entry in history] == [
        item
        for number in expected
        for item in (("user", f"问题{number}"), ("assistant", f"回复{number}"))
    ]
    assert len(store.get_messages(thread)) == 50


@pytest.mark.parametrize("commit_first", [False, True])
def test_save_failure_retries_original_reply_without_second_generation(
    app_factory, monkeypatch, commit_first
):
    create, backend = app_factory
    app = create()
    with TestClient(app) as client:
        identity = new_thread(client)["id"]
        payload = chat_payload(identity, stream=False)
        store = app.state.chat_service.store
        original = store.finish_run
        calls = []

        def fail_once(*args, **kwargs):
            calls.append(args)
            if len(calls) == 1:
                if commit_first:
                    original(*args, **kwargs)
                raise ApplicationError("CHAT_STORAGE_UNAVAILABLE", "模拟保存故障", 503)
            return original(*args, **kwargs)

        monkeypatch.setattr(store, "finish_run", fail_once)
        assert client.post("/api/v1/chat", json=payload, headers=AUTH).status_code == 503
        retried = client.post("/api/v1/chat", json=payload, headers=AUTH)
        assert retried.status_code == 200
        assert retried.json()["message"]["content"] == "你好，Nova。"
        assert len(backend.stream_calls) == 1
        assert len(calls) == 2
        assert len(store.get_messages(identity)) == 2
        assert not app.state.chat_service._unresolved_saves


def test_persistent_save_failure_does_not_start_another_generation(app_factory, monkeypatch):
    create, backend = app_factory
    app = create()
    with TestClient(app) as client:
        identity = new_thread(client)["id"]
        payload = chat_payload(identity, stream=False)
        store = app.state.chat_service.store
        original = store.finish_run

        def fail(*args, **kwargs):
            raise ApplicationError("CHAT_STORAGE_UNAVAILABLE", "模拟持续故障", 503)

        monkeypatch.setattr(store, "finish_run", fail)
        assert client.post("/api/v1/chat", json=payload, headers=AUTH).status_code == 503
        assert client.post("/api/v1/chat", json=payload, headers=AUTH).status_code == 503
        assert len(backend.stream_calls) == 1
        assert store.get_messages(identity)[-1]["status"] == "pending"
        monkeypatch.setattr(store, "finish_run", original)
        assert client.post("/api/v1/chat", json=payload, headers=AUTH).status_code == 200
        assert len(backend.stream_calls) == 1


def test_unsaved_reply_is_interrupted_after_restart(app_factory, monkeypatch):
    create, backend = app_factory
    app = create()
    with TestClient(app) as client:
        identity = new_thread(client)["id"]
        payload = chat_payload(identity, stream=False)

        def fail(*args, **kwargs):
            raise ApplicationError("CHAT_STORAGE_UNAVAILABLE", "模拟故障", 503)

        monkeypatch.setattr(app.state.chat_service.store, "finish_run", fail)
        assert client.post("/api/v1/chat", json=payload, headers=AUTH).status_code == 503
        assert len(backend.stream_calls) == 1
    assert not app.state.chat_service._unresolved_saves
    with TestClient(create()) as client:
        messages = client.get("/api/v1/threads/" + identity, headers=AUTH).json()["messages"]
        assert messages[-1]["status"] == "interrupted"
        assert messages[-1]["content"] == ""


def test_deleting_committed_thread_releases_uncertain_save(app_factory, monkeypatch):
    create, backend = app_factory
    app = create()
    with TestClient(app) as client:
        identity = new_thread(client)["id"]
        payload = chat_payload(identity, stream=False)
        service = app.state.chat_service
        original = service.store.finish_run

        def commit_then_fail(*args, **kwargs):
            original(*args, **kwargs)
            raise ApplicationError("CHAT_STORAGE_UNAVAILABLE", "模拟响应丢失", 503)

        monkeypatch.setattr(service.store, "finish_run", commit_then_fail)
        assert client.post("/api/v1/chat", json=payload, headers=AUTH).status_code == 503
        assert identity in service._unresolved_saves
        assert client.delete("/api/v1/threads/" + identity, headers=AUTH).status_code == 204
        assert identity not in service._unresolved_saves
        assert client.post("/api/v1/chat", json=payload, headers=AUTH).status_code == 404
        assert len(backend.stream_calls) == 1


def test_concurrent_recovery_of_one_result_is_idempotent(app_factory, monkeypatch):
    create, backend = app_factory
    app = create()
    with TestClient(app) as client:
        identity = new_thread(client)["id"]
        payload = chat_payload(identity, stream=False)
        store = app.state.chat_service.store
        original = store.finish_run
        waiting, release = Event(), Event()
        calls = []

        def fail_and_delay(*args, **kwargs):
            calls.append(args)
            if len(calls) == 1:
                raise ApplicationError("CHAT_STORAGE_UNAVAILABLE", "模拟故障", 503)
            if len(calls) == 2:
                waiting.set()
                assert release.wait(10)
            return original(*args, **kwargs)

        monkeypatch.setattr(store, "finish_run", fail_and_delay)
        assert client.post("/api/v1/chat", json=payload, headers=AUTH).status_code == 503
        with ThreadPoolExecutor(max_workers=1) as executor:
            retry = executor.submit(client.post, "/api/v1/chat", json=payload, headers=AUTH)
            try:
                assert waiting.wait(10)
                newer = chat_payload(identity, content="下一条消息", stream=False)
                assert client.post("/api/v1/chat", json=newer, headers=AUTH).status_code == 200
            finally:
                release.set()
            assert retry.result(timeout=10).json()["message"]["content"] == "你好，Nova。"
        assert len(store.get_messages(identity)) == 4
        assert len(backend.stream_calls) == 2
        assert not app.state.chat_service._unresolved_saves


def test_cancel_save_failure_recovers_interrupted_without_generation(app_factory, monkeypatch):
    create, backend = app_factory
    app = create()
    with TestClient(app) as client:
        identity = new_thread(client)["id"]
        payload = chat_payload(identity, stream=False)
        assert (
            client.post(
                "/api/v1/chat/cancel",
                json={"thread_id": identity, "request_id": payload["request_id"]},
                headers=AUTH,
            ).status_code
            == 200
        )
        store = app.state.chat_service.store
        original = store.finish_run

        def fail(*args, **kwargs):
            raise ApplicationError("CHAT_STORAGE_UNAVAILABLE", "模拟故障", 503)

        monkeypatch.setattr(store, "finish_run", fail)
        assert client.post("/api/v1/chat", json=payload, headers=AUTH).status_code == 503
        assert not backend.stream_calls
        monkeypatch.setattr(store, "finish_run", original)
        newer = chat_payload(identity, content="取消后新问题", stream=False)
        assert client.post("/api/v1/chat", json=newer, headers=AUTH).status_code == 200
        assert store.get_messages(identity)[1]["status"] == "interrupted"
        assert len(backend.stream_calls) == 1


def test_unresolved_save_capacity_blocks_new_work(app_factory):
    create, backend = app_factory
    app = create()
    with TestClient(app) as client:
        identity = new_thread(client)["id"]
        service = app.state.chat_service
        service._unresolved_saves.update(
            {str(uuid4()): ("run", "", "error", None) for _ in range(128)}
        )
        response = client.post("/api/v1/chat", json=chat_payload(identity), headers=AUTH)
        assert response.status_code == 503
        assert not backend.stream_calls


def test_capacity_still_allows_one_recovery_at_limit(app_factory, monkeypatch):
    create, backend = app_factory
    app = create()
    with TestClient(app) as client:
        identity = new_thread(client)["id"]
        payload = chat_payload(identity, stream=False)
        service = app.state.chat_service
        original = service.store.finish_run

        def fail(*args, **kwargs):
            raise ApplicationError("CHAT_STORAGE_UNAVAILABLE", "模拟故障", 503)

        monkeypatch.setattr(service.store, "finish_run", fail)
        assert client.post("/api/v1/chat", json=payload, headers=AUTH).status_code == 503
        service._unresolved_saves.update(
            {str(uuid4()): ("run", "", "error", None) for _ in range(127)}
        )
        monkeypatch.setattr(service.store, "finish_run", original)
        assert client.post("/api/v1/chat", json=payload, headers=AUTH).status_code == 200
        assert len(backend.stream_calls) == 1
