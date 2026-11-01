"""按操作管理 SQLite 连接、事务和显式释放。"""

import math
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

APPLICATION_ID = 0x4E4F5641  # ASCII: NOVA，仅标记由本项目初始化的数据库。
SCHEMA_VERSION = 1


class DatabaseError(RuntimeError):
    """数据库边界错误；消息不得包含 SQL、文件路径或原始异常内容。"""


class Database:
    """保存配置与就绪状态，不持有跨请求、跨线程的共享连接。

    一个 session 内的连接必须在创建它的同步函数/线程中使用。
    异步调用方应把整个数据库操作放在线程执行，而非把连接传回事件循环。
    """

    def __init__(self, path: Path, timeout: float = 5.0) -> None:
        if isinstance(timeout, bool) or not math.isfinite(timeout) or not 0 < timeout <= 30:
            raise DatabaseError("数据库等待时间必须大于 0 且不超过 30 秒。")
        try:
            self.path = Path(path).expanduser().resolve()
        except OSError, ValueError, TypeError:
            raise DatabaseError("数据库路径配置无效。") from None
        self.timeout = timeout
        self._is_ready = False

    @property
    def is_ready(self) -> bool:
        return self._is_ready

    def _open(self, mode: str) -> sqlite3.Connection:
        """先配置连接本地外键约束，再启用显式的 DB-API 事务语义。"""
        try:
            connection = sqlite3.connect(
                f"{self.path.as_uri()}?mode={mode}",
                uri=True,
                timeout=self.timeout,
                autocommit=True,
            )
            try:
                connection.execute("PRAGMA foreign_keys = ON")
                connection.row_factory = sqlite3.Row
                connection.autocommit = False
                return connection
            except BaseException:
                connection.close()
                raise
        except sqlite3.Error, OSError, ValueError:
            raise DatabaseError("无法打开数据库，请检查本地存储配置。") from None

    @staticmethod
    def _validate(connection: sqlite3.Connection, *, allow_initialize: bool) -> None:
        application_id = connection.execute("PRAGMA application_id").fetchone()[0]
        version = connection.execute("PRAGMA user_version").fetchone()[0]
        if application_id == APPLICATION_ID and version == SCHEMA_VERSION:
            return
        if allow_initialize and application_id == 0 and version == 0:
            if connection.execute("SELECT name FROM sqlite_master LIMIT 1").fetchone() is None:
                connection.execute(f"PRAGMA application_id = {APPLICATION_ID}")
                connection.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
                return
        raise DatabaseError("数据库标识或版本不受支持，未修改已有数据。")

    def initialize(self) -> None:
        """幂等初始化空库；已有未知结构或版本只拒绝，不清空、不迁移。"""
        self._is_ready = False
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            connection = self._open("rwc")
            try:
                with connection:
                    self._validate(connection, allow_initialize=True)
            finally:
                connection.close()
        except sqlite3.Error, OSError, ValueError:
            raise DatabaseError("数据库初始化失败，请检查本地存储配置。") from None
        self._is_ready = True

    @contextmanager
    def session(self) -> Iterator[sqlite3.Connection]:
        """事务上下文：成功提交、失败回滚，任意退出路径均关闭连接。"""
        if not self.is_ready:
            raise DatabaseError("数据库尚未就绪。")
        # rw 禁止在原数据库被移走后悄悄创建一个空文件。
        connection = self._open("rw")
        try:
            with connection:
                self._validate(connection, allow_initialize=False)
                yield connection
        except sqlite3.Error:
            raise DatabaseError("数据库事务失败。") from None
        finally:
            connection.close()

    def probe(self) -> None:
        """验证数据库仍可访问且具有受支持的标识与版本。"""
        with self.session() as connection:
            connection.execute("SELECT 1").fetchone()

    def close(self) -> None:
        """停用管理器；连接均由各自 session 负责释放。"""
        self._is_ready = False
