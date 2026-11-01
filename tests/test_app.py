"""应用工厂与资源生命周期。"""

import os
import subprocess
import sys

import pytest
from fastapi.testclient import TestClient

from nova.main import create_app
from nova.storage.database import DatabaseError


def test_lifespan_initializes_and_closes(settings):
    app = create_app(settings)
    assert not settings.database_path.exists()
    assert not app.state.ready
    with TestClient(app) as client:
        assert app.state.ready
        assert app.state.database.is_ready
        assert settings.database_path.exists()
        assert client.get("/health").status_code == 200
    assert not app.state.ready
    assert not app.state.database.is_ready
    with pytest.raises(DatabaseError), app.state.database.session():
        pytest.fail("关闭后不能再创建数据库会话")


def test_startup_failure_never_becomes_ready(settings, tmp_path):
    blocked = tmp_path / "parent"
    blocked.write_text("preserve", encoding="utf-8")
    settings = settings.model_copy(update={"database_path": blocked / "nova.sqlite3"})
    app = create_app(settings)
    with pytest.raises(RuntimeError, match="数据库初始化失败"), TestClient(app):
        pytest.fail("数据库初始化失败不应启动应用")
    assert not app.state.ready
    assert not app.state.database.is_ready
    assert blocked.read_text(encoding="utf-8") == "preserve"


def test_application_instances_keep_separate_state(settings, tmp_path):
    other = settings.model_copy(update={"database_path": tmp_path / "other.sqlite3"})
    first, second = create_app(settings), create_app(other)
    with TestClient(first) as client_one:
        with TestClient(second) as client_two:
            assert client_one.get("/health").status_code == 200
            assert client_two.get("/health").status_code == 200
            assert first.state.database is not second.state.database
        assert first.state.ready
        assert not second.state.ready
        assert client_one.get("/health").status_code == 200


def test_import_has_no_database_or_logging_side_effects(tmp_path):
    script = (
        "import logging,pathlib; before=list(logging.getLogger('nova').handlers); "
        "import nova.main; assert logging.getLogger('nova').handlers==before; "
        "assert not pathlib.Path('data').exists()"
    )
    result = subprocess.run(
        [sys.executable, "-B", "-c", script],
        cwd=tmp_path,
        env={**os.environ, "PYTHONUTF8": "1"},
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=20,
        check=False,
    )
    assert result.returncode == 0, result.stderr
