"""聊天扩展使用原 SQLite；只做加表初始化和短事务，不持有推理期间的连接。"""

import sqlite3
import threading
from contextlib import closing, contextmanager
from datetime import UTC, datetime
from uuid import uuid4

from nova.errors import ApplicationError
from nova.storage.database import Database, DatabaseError

CHAT_SCHEMA_VERSION = 1
DEFAULT_TITLE = "新对话"
_TABLES = {
    "nova_chat_schema": """CREATE TABLE nova_chat_schema (
        singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
        version INTEGER NOT NULL CHECK (version > 0)
    )""",
    "nova_threads": """CREATE TABLE nova_threads (
        id TEXT PRIMARY KEY NOT NULL,
        title TEXT NOT NULL,
        model TEXT NOT NULL,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    )""",
    "nova_messages": """CREATE TABLE nova_messages (
        id TEXT PRIMARY KEY NOT NULL,
        thread_id TEXT NOT NULL REFERENCES nova_threads(id) ON DELETE CASCADE,
        role TEXT NOT NULL CHECK (role IN ('user', 'assistant')),
        content TEXT NOT NULL,
        model TEXT NOT NULL,
        created_at TEXT NOT NULL,
        status TEXT NOT NULL CHECK (status IN ('pending', 'complete', 'error', 'interrupted')),
        position INTEGER NOT NULL CHECK (position >= 0),
        UNIQUE (thread_id, position)
    )""",
    "nova_chat_runs": """CREATE TABLE nova_chat_runs (
        id TEXT PRIMARY KEY NOT NULL,
        thread_id TEXT NOT NULL REFERENCES nova_threads(id) ON DELETE CASCADE,
        request_id TEXT NOT NULL,
        model TEXT NOT NULL,
        user_message_id TEXT NOT NULL UNIQUE REFERENCES nova_messages(id) ON DELETE CASCADE,
        assistant_message_id TEXT NOT NULL UNIQUE REFERENCES nova_messages(id) ON DELETE CASCADE,
        status TEXT NOT NULL CHECK (status IN ('pending', 'complete', 'error', 'interrupted')),
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        UNIQUE (thread_id, request_id),
        CHECK (user_message_id <> assistant_message_id)
    )""",
}
_INDEX = (
    "CREATE UNIQUE INDEX nova_chat_one_pending_run "
    "ON nova_chat_runs(thread_id) WHERE status = 'pending'"
)


def _now():
    return datetime.now(UTC).isoformat()


def _sql_signature(value):
    return " ".join(value.split())


def _invalid_schema():
    return ApplicationError("CHAT_SCHEMA_UNSUPPORTED", "聊天数据库扩展结构或版本不受支持", 503)


def _text(value, label, *, maximum=None, allow_empty=False):
    if (
        not isinstance(value, str)
        or (not allow_empty and not value.strip())
        or (maximum is not None and len(value) > maximum)
    ):
        raise ApplicationError("CHAT_INVALID_INPUT", label, 422)
    return value


class ChatStore:
    """同步仓储；调用方把整个操作交给线程，不将连接跨线程传递。

    nova_chat_schema 单独记录聊天扩展版本；不改变基础 Database 的版本契约。
    每次启动只初始化一次，且应在开始接收请求之前调用 initialize。
    """

    def __init__(self, database: Database):
        self.database = database
        self.last_backup_path = None
        self._ready = False
        # 单应用实例内的写操作串行；数据库唯一约束同时防止重复消息和活动 run。
        self._write_lock = threading.RLock()

    @staticmethod
    def _schema_present(connection):
        entries = {
            row["name"]: row
            for row in connection.execute(
                "SELECT type, name, sql FROM sqlite_master WHERE name IN (?, ?, ?, ?, ?)",
                (*_TABLES, "nova_chat_one_pending_run"),
            )
        }
        if not entries:
            return False
        if set(entries) != {*_TABLES, "nova_chat_one_pending_run"}:
            raise _invalid_schema()
        for name, expected in _TABLES.items():
            item = entries[name]
            if item["type"] != "table" or _sql_signature(item["sql"] or "") != _sql_signature(
                expected
            ):
                raise _invalid_schema()
        index = entries["nova_chat_one_pending_run"]
        if index["type"] != "index" or _sql_signature(index["sql"] or "") != _INDEX:
            raise _invalid_schema()
        rows = connection.execute("SELECT singleton, version FROM nova_chat_schema").fetchall()
        if len(rows) != 1 or tuple(rows[0]) != (1, CHAT_SCHEMA_VERSION):
            raise _invalid_schema()
        return True

    def _backup(self, connection):
        """先复制完整已提交数据库；失败时尚未执行任何聊天 DDL/DML。"""
        folder = self.database.path.parent / "backups"
        target_path = folder / (
            f"{self.database.path.stem}-before-chat-v1-"
            f"{datetime.now(UTC):%Y%m%dT%H%M%S}-{uuid4().hex}.sqlite3"
        )
        try:
            folder.mkdir(parents=True, exist_ok=True)
            # UUID 文件名，x 模式先保留唯一目标，绝不覆盖已有备份。
            with target_path.open("xb"):
                pass
            with closing(sqlite3.connect(target_path, autocommit=True)) as target:
                connection.backup(target)
                if target.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                    raise sqlite3.DatabaseError
        except OSError, sqlite3.Error:
            raise ApplicationError(
                "CHAT_BACKUP_FAILED", "聊天数据库备份失败，未修改聊天结构或消息", 503
            ) from None
        self.last_backup_path = target_path

    def initialize(self):
        """加表初始化；已有 pending run 在重启后明确标记 interrupted。"""
        with self._write_lock:
            self._ready = False
            try:
                with self.database.session() as connection:
                    present = self._schema_present(connection)
                    pending = (
                        connection.execute(
                            "SELECT COUNT(*) FROM nova_chat_runs WHERE status = 'pending'"
                        ).fetchone()[0]
                        if present
                        else 0
                    )
                    if not present or pending:
                        self._backup(connection)
                    if not present:
                        for statement in _TABLES.values():
                            connection.execute(statement)
                        connection.execute(_INDEX)
                        connection.execute(
                            "INSERT INTO nova_chat_schema(singleton, version) VALUES (1, ?)",
                            (CHAT_SCHEMA_VERSION,),
                        )
                    if pending:
                        connection.execute(
                            "UPDATE nova_messages SET status = 'interrupted' WHERE id IN "
                            "(SELECT assistant_message_id FROM nova_chat_runs WHERE status = 'pending')"
                        )
                        connection.execute(
                            "UPDATE nova_chat_runs SET status = 'interrupted', updated_at = ? "
                            "WHERE status = 'pending'",
                            (_now(),),
                        )
            except DatabaseError:
                raise ApplicationError(
                    "CHAT_STORAGE_UNAVAILABLE", "聊天数据库初始化失败，未覆盖已有数据", 503
                ) from None
            self._ready = True

    @contextmanager
    def _session(self):
        if not self._ready:
            raise ApplicationError("CHAT_STORAGE_UNAVAILABLE", "聊天存储尚未就绪", 503)
        try:
            with self.database.session() as connection:
                yield connection
        except DatabaseError:
            raise ApplicationError(
                "CHAT_STORAGE_UNAVAILABLE", "聊天存储暂不可用，请稍后重试", 503
            ) from None

    @staticmethod
    def _thread(connection, thread_id):
        row = connection.execute("SELECT * FROM nova_threads WHERE id = ?", (thread_id,)).fetchone()
        if row is None:
            raise ApplicationError("CHAT_THREAD_NOT_FOUND", "会话不存在", 404)
        return dict(row)

    @staticmethod
    def _message(connection, message_id):
        row = connection.execute(
            "SELECT * FROM nova_messages WHERE id = ?", (message_id,)
        ).fetchone()
        if row is None:
            raise _invalid_schema()
        return dict(row)

    @staticmethod
    def _require_idle(connection, thread_id):
        if connection.execute(
            "SELECT 1 FROM nova_chat_runs WHERE thread_id = ? AND status = 'pending'",
            (thread_id,),
        ).fetchone():
            raise ApplicationError("CHAT_THREAD_BUSY", "当前会话正在生成回复，请等待完成", 409)

    def list_threads(self):
        with self._session() as connection:
            return [
                dict(row)
                for row in connection.execute(
                    "SELECT * FROM nova_threads ORDER BY updated_at DESC, id"
                )
            ]

    def create_thread(self, title=DEFAULT_TITLE, model="qwen3.5:9b"):
        title = _text(title, "会话标题不合法", maximum=200).strip()
        model = _text(model, "模型标识不合法", maximum=128)
        identity, now = str(uuid4()), _now()
        with self._write_lock, self._session() as connection:
            connection.execute(
                "INSERT INTO nova_threads(id, title, model, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (identity, title, model, now, now),
            )
            return self._thread(connection, identity)

    def get_thread(self, thread_id):
        with self._session() as connection:
            return self._thread(connection, thread_id)

    def get_messages(self, thread_id):
        with self._session() as connection:
            self._thread(connection, thread_id)
            return [
                dict(row)
                for row in connection.execute(
                    "SELECT * FROM nova_messages WHERE thread_id = ? ORDER BY position",
                    (thread_id,),
                )
            ]

    def rename_thread(self, thread_id, title):
        title = _text(title, "会话标题不合法", maximum=200).strip()
        with self._write_lock, self._session() as connection:
            self._thread(connection, thread_id)
            self._require_idle(connection, thread_id)
            connection.execute(
                "UPDATE nova_threads SET title = ?, updated_at = ? WHERE id = ?",
                (title, _now(), thread_id),
            )
            return self._thread(connection, thread_id)

    def delete_thread(self, thread_id):
        with self._write_lock, self._session() as connection:
            self._thread(connection, thread_id)
            self._require_idle(connection, thread_id)
            connection.execute("DELETE FROM nova_threads WHERE id = ?", (thread_id,))

    def get_completed_run(self, thread_id, model, content, request_id):
        """只读查找已完成请求；未命中返回None，冲突语义与begin_run一致。"""
        model = _text(model, "模型标识不合法", maximum=128)
        content = _text(content, "消息内容不能为空")
        request_id = _text(request_id, "请求标识不合法", maximum=128)
        with self._session() as connection:
            self._thread(connection, thread_id)
            prior = connection.execute(
                "SELECT * FROM nova_chat_runs WHERE thread_id = ? AND request_id = ?",
                (thread_id, request_id),
            ).fetchone()
            if prior is None:
                return None
            user = self._message(connection, prior["user_message_id"])
            if user["content"] != content or prior["model"] != model:
                raise ApplicationError(
                    "CHAT_REQUEST_CONFLICT", "同一请求标识不能用于不同消息或模型", 409
                )
            if prior["status"] != "complete":
                raise ApplicationError(
                    "CHAT_REQUEST_CONFLICT", "该请求正在执行或已中断，请使用新的请求标识", 409
                )
            assistant = self._message(connection, prior["assistant_message_id"])
            if assistant["status"] != "complete":
                raise _invalid_schema()
            return {
                "run_id": prior["id"],
                "user_message": user,
                "assistant_message": assistant,
                "is_replay": True,
            }

    def begin_run(self, thread_id, model, content, request_id):
        model = _text(model, "模型标识不合法", maximum=128)
        content = _text(content, "消息内容不能为空")
        request_id = _text(request_id, "请求标识不合法", maximum=128)
        with self._write_lock, self._session() as connection:
            thread = self._thread(connection, thread_id)
            prior = connection.execute(
                "SELECT * FROM nova_chat_runs WHERE thread_id = ? AND request_id = ?",
                (thread_id, request_id),
            ).fetchone()
            if prior:
                user = self._message(connection, prior["user_message_id"])
                if user["content"] != content or prior["model"] != model:
                    raise ApplicationError(
                        "CHAT_REQUEST_CONFLICT", "同一请求标识不能用于不同消息或模型", 409
                    )
                if prior["status"] != "complete":
                    raise ApplicationError(
                        "CHAT_REQUEST_CONFLICT", "该请求正在执行或已中断，请使用新的请求标识", 409
                    )
                return {
                    "run_id": prior["id"],
                    "user_message": user,
                    "assistant_message": self._message(connection, prior["assistant_message_id"]),
                    "is_replay": True,
                }
            self._require_idle(connection, thread_id)
            position = connection.execute(
                "SELECT COALESCE(MAX(position), -1) + 1 FROM nova_messages WHERE thread_id = ?",
                (thread_id,),
            ).fetchone()[0]
            run_id, user_id, assistant_id = (str(uuid4()) for _ in range(3))
            now = _now()
            connection.executemany(
                "INSERT INTO nova_messages "
                "(id, thread_id, role, content, model, created_at, status, position) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                [
                    (user_id, thread_id, "user", content, model, now, "complete", position),
                    (assistant_id, thread_id, "assistant", "", model, now, "pending", position + 1),
                ],
            )
            connection.execute(
                "INSERT INTO nova_chat_runs "
                "(id, thread_id, request_id, model, user_message_id, assistant_message_id, "
                "status, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, 'pending', ?, ?)",
                (run_id, thread_id, request_id, model, user_id, assistant_id, now, now),
            )
            title = thread["title"]
            if position == 0 and title == DEFAULT_TITLE:
                title = " ".join(content.split())[:30]
            connection.execute(
                "UPDATE nova_threads SET title = ?, model = ?, updated_at = ? WHERE id = ?",
                (title, model, now, thread_id),
            )
            return {
                "run_id": run_id,
                "user_message": self._message(connection, user_id),
                "assistant_message": self._message(connection, assistant_id),
                "is_replay": False,
            }

    def finish_run(self, run_id, content, status="complete", *, actual_model=None):
        content = _text(content, "回复内容不合法", allow_empty=True)
        if status not in {"complete", "error", "interrupted"}:
            raise ApplicationError("CHAT_INVALID_INPUT", "回复状态不合法", 422)
        if actual_model is not None:
            actual_model = _text(actual_model, "模型标识不合法", maximum=128)
        with self._write_lock, self._session() as connection:
            run = connection.execute(
                "SELECT * FROM nova_chat_runs WHERE id = ?", (run_id,)
            ).fetchone()
            if run is None:
                raise ApplicationError("CHAT_RUN_NOT_FOUND", "生成任务不存在", 404)
            prior = self._message(connection, run["assistant_message_id"])
            if run["status"] != "pending":
                if prior["content"] == content and prior["status"] == status:
                    return prior
                raise ApplicationError("CHAT_RUN_FINISHED", "生成任务已经结束，不能覆盖回复", 409)
            now = _now()
            connection.execute(
                "UPDATE nova_messages SET content = ?, status = ?, model = COALESCE(?, model) WHERE id = ?",
                (content, status, actual_model, run["assistant_message_id"]),
            )
            connection.execute(
                "UPDATE nova_chat_runs SET status = ?, updated_at = ? WHERE id = ?",
                (status, now, run_id),
            )
            connection.execute(
                "UPDATE nova_threads SET updated_at = ? WHERE id = ?", (now, run["thread_id"])
            )
            return self._message(connection, run["assistant_message_id"])
