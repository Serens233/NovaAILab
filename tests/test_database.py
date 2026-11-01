"""数据库测试只操作 pytest 临时目录，验证事务与资源的真实行为。"""

import sqlite3
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from threading import Barrier, get_ident

import pytest

from nova.storage.database import APPLICATION_ID, SCHEMA_VERSION, Database, DatabaseError


@pytest.fixture
def database(tmp_path):
    return Database(tmp_path / "data" / "nova.sqlite3")


def assert_closed(connection):
    with pytest.raises(sqlite3.ProgrammingError, match="closed"):
        connection.execute("SELECT 1")


def test_initialization_creates_only_metadata_and_enables_foreign_keys(database):
    assert not database.is_ready
    assert not database.path.exists()
    database.initialize()
    assert database.is_ready
    assert database.path.is_file()
    with database.session() as connection:
        assert connection.execute("PRAGMA application_id").fetchone()[0] == APPLICATION_ID
        assert connection.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
        assert connection.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        assert connection.execute("SELECT name FROM sqlite_master").fetchall() == []
    assert_closed(connection)


def test_initialization_is_idempotent_and_preserves_existing_nova_data(database):
    database.initialize()
    with database.session() as connection:
        connection.execute("CREATE TABLE test_marker (value TEXT NOT NULL)")
        connection.execute("INSERT INTO test_marker VALUES (?)", ("preserved",))
    before = database.path.read_bytes()
    database.initialize()
    assert database.path.read_bytes() == before
    with database.session() as connection:
        assert connection.execute("SELECT value FROM test_marker").fetchone()[0] == "preserved"


def test_session_commits_and_closes_connection(database):
    database.initialize()
    with database.session() as connection:
        connection.execute("CREATE TABLE test_marker (value INTEGER)")
        connection.execute("INSERT INTO test_marker VALUES (?)", (12,))
    assert_closed(connection)
    with database.session() as reader:
        assert reader.execute("SELECT value FROM test_marker").fetchone()[0] == 12


def test_session_rolls_back_body_error_and_closes_connection(database):
    database.initialize()
    with database.session() as connection:
        connection.execute("CREATE TABLE test_marker (value INTEGER)")
    with pytest.raises(ValueError, match="test body failure"), database.session() as connection:
        connection.execute("INSERT INTO test_marker VALUES (12)")
        raise ValueError("test body failure")
    assert_closed(connection)
    with database.session() as reader:
        assert reader.execute("SELECT COUNT(*) FROM test_marker").fetchone()[0] == 0


def test_commit_failure_rolls_back_and_closes_connection(database):
    database.initialize()
    with database.session() as connection:
        connection.execute("CREATE TABLE test_parent (id INTEGER PRIMARY KEY)")
        connection.execute(
            "CREATE TABLE test_child (parent_id INTEGER REFERENCES test_parent(id) "
            "DEFERRABLE INITIALLY DEFERRED)"
        )
    with pytest.raises(DatabaseError, match="事务失败"), database.session() as connection:
        # 延迟外键约束使 INSERT 成功，在退出上下文的 commit 才真实失败。
        connection.execute("INSERT INTO test_child VALUES (99)")
    assert_closed(connection)
    with database.session() as reader:
        assert reader.execute("SELECT COUNT(*) FROM test_child").fetchone()[0] == 0


def test_sql_error_is_safe_and_connection_is_closed(database):
    database.initialize()
    with pytest.raises(DatabaseError) as caught, database.session() as connection:
        connection.execute("SELECT * FROM missing_sensitive_table")
    assert "missing_sensitive_table" not in str(caught.value)
    assert str(database.path) not in str(caught.value)
    assert_closed(connection)


def test_sessions_refuse_uninitialized_database_without_creating_file(database):
    with pytest.raises(DatabaseError, match="尚未就绪"), database.session():
        pytest.fail("Uninitialized session must not be entered")
    assert not database.path.exists()


def test_close_disables_access_and_can_be_initialized_again(database):
    database.initialize()
    database.close()
    database.close()
    assert not database.is_ready
    with pytest.raises(DatabaseError, match="尚未就绪"):
        database.probe()
    database.initialize()
    database.probe()


def test_missing_database_is_not_silently_recreated(database):
    database.initialize()
    database.path.unlink()
    with pytest.raises(DatabaseError):
        database.probe()
    assert not database.path.exists()


def test_unknown_database_schema_is_rejected_without_mutation(tmp_path):
    path = tmp_path / "foreign.sqlite3"
    with closing(sqlite3.connect(path)) as connection:
        connection.execute("CREATE TABLE existing_user_data (value TEXT)")
        connection.execute("INSERT INTO existing_user_data VALUES ('keep me')")
        connection.commit()
    before = path.read_bytes()
    database = Database(path)
    with pytest.raises(DatabaseError, match="不受支持"):
        database.initialize()
    assert not database.is_ready
    assert path.read_bytes() == before


def test_future_database_version_is_rejected_without_mutation(database):
    database.initialize()
    with database.session() as connection:
        connection.execute("PRAGMA user_version = 999")
    before = database.path.read_bytes()
    with pytest.raises(DatabaseError, match="不受支持"):
        database.initialize()
    assert not database.is_ready
    assert database.path.read_bytes() == before


def test_corrupt_database_is_rejected_without_mutation(tmp_path):
    path = tmp_path / "corrupt.sqlite3"
    original = b"this is not a sqlite database\x00" * 10
    path.write_bytes(original)
    database = Database(path)
    with pytest.raises(DatabaseError) as caught:
        database.initialize()
    assert not database.is_ready
    assert path.read_bytes() == original
    assert str(path) not in str(caught.value)


def test_parent_path_that_is_file_fails_safely(tmp_path):
    parent = tmp_path / "not_a_directory"
    parent.write_text("keep me", encoding="utf-8")
    database = Database(parent / "nova.sqlite3")
    with pytest.raises(DatabaseError):
        database.initialize()
    assert not database.is_ready
    assert parent.read_text(encoding="utf-8") == "keep me"


def test_database_path_that_is_directory_fails_safely(tmp_path):
    database = Database(tmp_path)
    with pytest.raises(DatabaseError):
        database.initialize()
    assert not database.is_ready


def test_database_path_handles_spaces_and_unicode(tmp_path):
    database = Database(tmp_path / "本地 数据" / "Nova 文件.sqlite3")
    database.initialize()
    database.probe()
    assert database.path.is_file()


def test_busy_writer_fails_safely_and_releases_its_connection(database):
    database.initialize()
    with database.session() as connection:
        connection.execute("CREATE TABLE test_marker (value INTEGER)")
    contender = Database(database.path, timeout=0.01)
    contender.initialize()
    with database.session() as writer:
        writer.execute("INSERT INTO test_marker VALUES (1)")
        with pytest.raises(DatabaseError, match="事务失败"), contender.session() as blocked:
            blocked.execute("INSERT INTO test_marker VALUES (2)")
        assert_closed(blocked)
    with database.session() as reader:
        assert reader.execute("SELECT value FROM test_marker").fetchall()[0][0] == 1
        assert reader.execute("SELECT COUNT(*) FROM test_marker").fetchone()[0] == 1


@pytest.mark.parametrize("timeout", [0, -1, 31, float("inf"), float("nan"), True])
def test_invalid_timeouts_are_rejected(tmp_path, timeout):
    with pytest.raises(DatabaseError, match="等待时间"):
        Database(tmp_path / "nova.sqlite3", timeout=timeout)


def test_operations_can_run_on_different_workers_without_sharing_connections(database):
    database.initialize()
    barrier = Barrier(2)

    def probe():
        barrier.wait(timeout=5)
        database.probe()
        return get_ident()

    with ThreadPoolExecutor(max_workers=2) as workers:
        futures = [workers.submit(probe) for _ in range(2)]
        assert len({future.result(timeout=10) for future in futures}) == 2
    database.close()
    assert not database.is_ready
