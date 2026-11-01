"""聊天数据只操作临时库，覆盖备份、无损扩展、并发、重启及请求幂等。"""

import sqlite3
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from threading import Barrier

import pytest

from nova.errors import ApplicationError
from nova.storage.chat import ChatStore
from nova.storage.database import APPLICATION_ID, SCHEMA_VERSION, Database


@pytest.fixture
def database(tmp_path):
    value = Database(tmp_path / "Nova 测试" / "nova.sqlite3")
    value.initialize()
    return value


@pytest.fixture
def store(database):
    value = ChatStore(database)
    value.initialize()
    return value


def read_backup(path, sql):
    with closing(
        sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, autocommit=True)
    ) as connection:
        return connection.execute(sql).fetchall()


def test_initialize_backs_up_first_and_preserves_base_version_and_custom_data(database):
    with database.session() as connection:
        connection.execute("CREATE TABLE personal_marker(value TEXT)")
        connection.execute("INSERT INTO personal_marker VALUES ('keep old data')")
    store = ChatStore(database)
    store.initialize()

    assert store.last_backup_path.parent == database.path.parent / "backups"
    assert read_backup(store.last_backup_path, "SELECT * FROM personal_marker") == [
        ("keep old data",)
    ]
    assert (
        read_backup(
            store.last_backup_path,
            "SELECT name FROM sqlite_master WHERE name LIKE 'nova_%'",
        )
        == []
    )
    with database.session() as connection:
        assert connection.execute("PRAGMA application_id").fetchone()[0] == APPLICATION_ID
        assert connection.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION == 1
        assert connection.execute("SELECT value FROM personal_marker").fetchone()[0] == (
            "keep old data"
        )
        assert tuple(connection.execute("SELECT * FROM nova_chat_schema").fetchone()) == (1, 1)
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
        assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"


def test_initialize_is_idempotent_without_extra_backups_or_database_mutation(store, database):
    before = database.path.read_bytes()
    backup_paths = set((database.path.parent / "backups").iterdir())
    store.initialize()
    another = ChatStore(database)
    another.initialize()
    assert database.path.read_bytes() == before
    assert set((database.path.parent / "backups").iterdir()) == backup_paths
    assert another.last_backup_path is None


def test_backup_failure_leaves_original_database_unchanged(database):
    before = database.path.read_bytes()
    (database.path.parent / "backups").write_text("existing user file", encoding="utf-8")
    store = ChatStore(database)
    with pytest.raises(ApplicationError) as caught:
        store.initialize()
    assert caught.value.code == "CHAT_BACKUP_FAILED"
    assert caught.value.status_code == 503
    assert str(database.path) not in caught.value.public_message
    assert database.path.read_bytes() == before
    with database.session() as connection:
        assert connection.execute("SELECT name FROM sqlite_master").fetchall() == []
    with pytest.raises(ApplicationError, match="尚未就绪"):
        store.list_threads()


@pytest.mark.parametrize(
    "statement",
    [
        "CREATE TABLE nova_threads(existing_user_value TEXT)",
        "CREATE VIEW nova_messages AS SELECT 'keep me' AS value",
        "CREATE TABLE nova_chat_schema(version INTEGER)",
    ],
)
def test_unmarked_existing_chat_names_are_rejected_without_mutation(database, statement):
    with database.session() as connection:
        connection.execute(statement)
    before = database.path.read_bytes()
    with pytest.raises(ApplicationError) as caught:
        ChatStore(database).initialize()
    assert caught.value.code == "CHAT_SCHEMA_UNSUPPORTED"
    assert database.path.read_bytes() == before
    assert not (database.path.parent / "backups").exists()


@pytest.mark.parametrize(
    "statement",
    [
        "UPDATE nova_chat_schema SET version = 2",
        "DELETE FROM nova_chat_schema",
        "ALTER TABLE nova_messages ADD COLUMN incompatible TEXT",
        "DROP INDEX nova_chat_one_pending_run",
        "DROP TABLE nova_chat_runs",
    ],
)
def test_incompatible_marked_extension_is_rejected_without_mutation(store, database, statement):
    with database.session() as connection:
        connection.execute(statement)
    before = database.path.read_bytes()
    with pytest.raises(ApplicationError) as caught:
        ChatStore(database).initialize()
    assert caught.value.code == "CHAT_SCHEMA_UNSUPPORTED"
    assert database.path.read_bytes() == before


def test_initialization_and_operations_require_base_database_ready(tmp_path):
    database = Database(tmp_path / "not-created.sqlite3")
    store = ChatStore(database)
    with pytest.raises(ApplicationError) as caught:
        store.initialize()
    assert caught.value.code == "CHAT_STORAGE_UNAVAILABLE"
    assert not database.path.exists()
    with pytest.raises(ApplicationError):
        store.create_thread()


def test_threads_create_get_rename_and_list(store):
    first = store.create_thread()
    second = store.create_thread("自定义会话", "qwen3.5:4b")
    assert first["title"] == "新对话"
    assert first["model"] == "qwen3.5:9b"
    assert set(first) == {"id", "title", "model", "created_at", "updated_at"}
    assert store.get_thread(second["id"]) == second
    assert store.get_messages(first["id"]) == []
    renamed = store.rename_thread(first["id"], "  中文名称  ")
    assert renamed["title"] == "中文名称"
    assert [thread["id"] for thread in store.list_threads()] == [first["id"], second["id"]]


@pytest.mark.parametrize("method", ["get_thread", "get_messages", "delete_thread"])
def test_missing_threads_return_safe_404(store, method):
    with pytest.raises(ApplicationError) as caught:
        getattr(store, method)("missing-sensitive-value")
    assert caught.value.code == "CHAT_THREAD_NOT_FOUND"
    assert caught.value.status_code == 404
    assert "missing-sensitive-value" not in caught.value.public_message


@pytest.mark.parametrize("title", [None, 23, "", " \n ", "字" * 201])
def test_invalid_titles_are_rejected(store, title):
    with pytest.raises(ApplicationError) as caught:
        store.create_thread(title)
    assert caught.value.status_code == 422
    assert store.list_threads() == []


@pytest.mark.parametrize("model", [None, "", " ", "x" * 129])
def test_invalid_models_are_rejected(store, model):
    with pytest.raises(ApplicationError) as caught:
        store.create_thread(model=model)
    assert caught.value.status_code == 422


def test_two_turns_preserve_message_order_and_selected_model(store):
    thread = store.create_thread()
    content = "第一条消息，自动标题最多保留三十个字符。" + "继续" * 20
    first = store.begin_run(thread["id"], "qwen3.5:9b", content, "request-1")
    assert set(first) == {"run_id", "user_message", "assistant_message", "is_replay"}
    assert not first["is_replay"]
    assert first["user_message"]["status"] == "complete"
    assert first["assistant_message"]["status"] == "pending"
    assert first["assistant_message"]["content"] == ""
    assert store.get_thread(thread["id"])["title"] == content[:30]
    reply = store.finish_run(first["run_id"], "第一条回复")
    assert reply["id"] == first["assistant_message"]["id"]
    assert reply["status"] == "complete"
    second = store.begin_run(thread["id"], "qwen3.5:4b", "第二条消息", "request-2")
    store.finish_run(second["run_id"], "第二条回复")
    messages = store.get_messages(thread["id"])
    assert [message["position"] for message in messages] == [0, 1, 2, 3]
    assert [message["role"] for message in messages] == ["user", "assistant"] * 2
    assert [message["model"] for message in messages] == ["qwen3.5:9b"] * 2 + ["qwen3.5:4b"] * 2
    assert set(messages[0]) == {
        "id",
        "thread_id",
        "role",
        "content",
        "model",
        "created_at",
        "status",
        "position",
    }
    assert store.get_thread(thread["id"])["model"] == "qwen3.5:4b"
    assert store.get_thread(thread["id"])["title"] == content[:30]


def test_custom_title_is_not_replaced_by_first_message(store):
    thread = store.create_thread("我的名字")
    store.begin_run(thread["id"], "qwen3.5:9b", "其他内容", "request")
    assert store.get_thread(thread["id"])["title"] == "我的名字"


def test_complete_request_replays_after_restart_without_duplicate_messages(store, database):
    thread = store.create_thread()
    first = store.begin_run(thread["id"], "qwen3.5:9b", "你好", "same-request")
    store.finish_run(first["run_id"], "你好，世界")
    restored = ChatStore(database)
    restored.initialize()
    replay = restored.begin_run(thread["id"], "qwen3.5:9b", "你好", "same-request")
    assert replay["is_replay"] is True
    assert replay["run_id"] == first["run_id"]
    assert replay["assistant_message"]["content"] == "你好，世界"
    assert len(restored.get_messages(thread["id"])) == 2


@pytest.mark.parametrize("different", ["content", "model"])
def test_request_id_cannot_be_reused_for_different_input(store, different):
    thread = store.create_thread()
    run = store.begin_run(thread["id"], "qwen3.5:9b", "原始消息", "same-request")
    store.finish_run(run["run_id"], "回复")
    with pytest.raises(ApplicationError) as caught:
        store.begin_run(
            thread["id"],
            "qwen3.5:4b" if different == "model" else "qwen3.5:9b",
            "不同消息" if different == "content" else "原始消息",
            "same-request",
        )
    assert caught.value.code == "CHAT_REQUEST_CONFLICT"
    assert caught.value.status_code == 409
    assert len(store.get_messages(thread["id"])) == 2


def test_active_run_prevents_duplicate_other_run_rename_and_delete(store):
    thread = store.create_thread()
    first = store.begin_run(thread["id"], "qwen3.5:9b", "你好", "request-1")
    operations = [
        lambda: store.begin_run(thread["id"], "qwen3.5:9b", "你好", "request-1"),
        lambda: store.begin_run(thread["id"], "qwen3.5:4b", "新问题", "request-2"),
        lambda: store.rename_thread(thread["id"], "不能在运行中改名"),
        lambda: store.delete_thread(thread["id"]),
    ]
    for operation in operations:
        with pytest.raises(ApplicationError) as caught:
            operation()
        assert caught.value.status_code == 409
    assert store.get_messages(thread["id"])[1]["id"] == first["assistant_message"]["id"]


@pytest.mark.parametrize("status", ["error", "interrupted"])
def test_failed_run_preserves_partial_reply_and_requires_new_request_id(store, status):
    thread = store.create_thread()
    run = store.begin_run(thread["id"], "qwen3.5:9b", "你好", "request")
    reply = store.finish_run(run["run_id"], "部分回复", status=status)
    assert reply["content"] == "部分回复"
    assert reply["status"] == status
    with pytest.raises(ApplicationError) as caught:
        store.begin_run(thread["id"], "qwen3.5:9b", "你好", "request")
    assert caught.value.status_code == 409
    assert not store.begin_run(thread["id"], "qwen3.5:9b", "重试", "new-request")["is_replay"]


def test_restart_marks_pending_interrupted_and_backup_contains_original_pending(store, database):
    thread = store.create_thread()
    run = store.begin_run(thread["id"], "qwen3.5:9b", "原始提问", "request")
    with database.session() as connection:
        connection.execute(
            "UPDATE nova_messages SET content = ? WHERE id = ?",
            ("已保存的部分输出", run["assistant_message"]["id"]),
        )
    restored = ChatStore(database)
    restored.initialize()
    messages = restored.get_messages(thread["id"])
    assert messages[0]["status"] == "complete"
    assert messages[0]["content"] == "原始提问"
    assert messages[1]["status"] == "interrupted"
    assert messages[1]["content"] == "已保存的部分输出"
    assert read_backup(restored.last_backup_path, "SELECT status FROM nova_chat_runs") == [
        ("pending",)
    ]
    with database.session() as connection:
        assert (
            connection.execute("SELECT status FROM nova_chat_runs").fetchone()[0] == "interrupted"
        )
    # 恢复完成后允许用户自行发起新的请求，绝不伪称旧任务完成。
    restored.begin_run(thread["id"], "qwen3.5:9b", "新请求", "new-request")


def test_delete_completed_thread_cascades_messages_and_runs(store, database):
    thread = store.create_thread()
    other = store.create_thread("保留")
    run = store.begin_run(thread["id"], "qwen3.5:9b", "删除前提问", "request")
    store.finish_run(run["run_id"], "删除前回复")
    store.delete_thread(thread["id"])
    assert store.get_thread(other["id"])["title"] == "保留"
    with database.session() as connection:
        assert connection.execute("SELECT COUNT(*) FROM nova_messages").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM nova_chat_runs").fetchone()[0] == 0
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []


def test_finished_reply_is_idempotent_but_cannot_be_overwritten(store):
    thread = store.create_thread()
    run = store.begin_run(thread["id"], "qwen3.5:9b", "问题", "request")
    reply = store.finish_run(run["run_id"], "已完成")
    assert store.finish_run(run["run_id"], "已完成") == reply
    with pytest.raises(ApplicationError) as caught:
        store.finish_run(run["run_id"], "篡改回复")
    assert caught.value.code == "CHAT_RUN_FINISHED"
    assert store.get_messages(thread["id"])[1] == reply


def test_missing_run_and_invalid_status_are_safe(store):
    with pytest.raises(ApplicationError) as caught:
        store.finish_run("missing", "回复")
    assert caught.value.code == "CHAT_RUN_NOT_FOUND"
    assert caught.value.status_code == 404
    with pytest.raises(ApplicationError) as caught:
        store.finish_run("missing", "回复", status="pending")
    assert caught.value.status_code == 422


def test_two_threads_cannot_create_overlapping_runs_in_same_chat(store):
    thread = store.create_thread()
    barrier = Barrier(2)

    def begin(number):
        barrier.wait(timeout=5)
        try:
            return store.begin_run(thread["id"], "qwen3.5:9b", f"消息{number}", f"request-{number}")
        except ApplicationError as error:
            return error.status_code

    with ThreadPoolExecutor(max_workers=2) as workers:
        futures = [workers.submit(begin, number) for number in (1, 2)]
        results = [future.result(timeout=10) for future in futures]
    assert sum(isinstance(result, dict) for result in results) == 1
    assert results.count(409) == 1
    assert len(store.get_messages(thread["id"])) == 2


def test_begin_failure_rolls_back_messages_run_and_thread_title(store, database, monkeypatch):
    thread = store.create_thread()

    def fail_after_inserts(connection, identity):
        raise ApplicationError("SYNTHETIC_TEST_FAILURE", "合成写入失败", 503)

    monkeypatch.setattr(store, "_message", fail_after_inserts)
    with pytest.raises(ApplicationError, match="合成写入失败"):
        store.begin_run(thread["id"], "qwen3.5:4b", "不能留下半次写入", "request")
    assert store.get_messages(thread["id"]) == []
    assert store.get_thread(thread["id"]) == thread
    with database.session() as connection:
        assert connection.execute("SELECT COUNT(*) FROM nova_chat_runs").fetchone()[0] == 0


def test_content_uses_bound_parameters_and_persists_unicode(store):
    thread = store.create_thread()
    content = "中文🙂 '; DROP TABLE nova_threads; --"
    run = store.begin_run(thread["id"], "qwen3.5:9b", content, "request")
    assert run["user_message"]["content"] == content
    assert store.get_thread(thread["id"])["id"] == thread["id"]


def test_closed_database_gives_public_storage_error_without_paths(store, database):
    database.close()
    with pytest.raises(ApplicationError) as caught:
        store.list_threads()
    assert caught.value.code == "CHAT_STORAGE_UNAVAILABLE"
    assert str(database.path) not in caught.value.public_message
