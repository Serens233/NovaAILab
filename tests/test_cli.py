"""启动器使用真实配置，失败时返回非零且不泄漏输入。"""

import os
import sqlite3
import subprocess
import sys

from nova.__main__ import main
from nova.storage.database import APPLICATION_ID, SCHEMA_VERSION


def test_launcher_uses_host_port_and_logging_settings(monkeypatch):
    monkeypatch.setenv("NOVA_HOST", "::1")
    monkeypatch.setenv("NOVA_PORT", "8765")
    monkeypatch.setenv("NOVA_LOG_LEVEL", "DEBUG")
    received = {}

    def run(app, **kwargs):
        received.update(kwargs)

    monkeypatch.setattr("nova.__main__.uvicorn.run", run)
    assert main([]) == 0
    assert received["host"] == "::1"
    assert received["port"] == 8765
    assert received["log_level"] == "debug"
    assert received["log_config"] is None
    assert received["access_log"] is False


def test_init_db_command_is_idempotent_and_creates_no_business_tables(tmp_path, capsys):
    assert main(["--init-db"]) == 0
    assert main(["--init-db"]) == 0
    connection = sqlite3.connect(tmp_path / "data" / "nova.sqlite3")
    try:
        assert connection.execute("PRAGMA application_id").fetchone()[0] == APPLICATION_ID
        assert connection.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
        assert connection.execute("SELECT name FROM sqlite_master").fetchall() == []
    finally:
        connection.close()
    assert "初始化完成" in capsys.readouterr().out


def test_invalid_configuration_returns_two_without_value(monkeypatch, capsys):
    value = "SYNTHETIC_BAD_CONFIG_VALUE_456"
    monkeypatch.setenv("NOVA_PORT", value)
    assert main([]) == 2
    output = capsys.readouterr()
    assert "NOVA_PORT" in output.err
    assert value not in output.err + output.out
    assert "Traceback" not in output.err


def test_initialization_failure_returns_one_preserving_file(tmp_path, monkeypatch, capsys):
    parent = tmp_path / "existing-file"
    parent.write_text("preserved", encoding="utf-8")
    monkeypatch.setenv("NOVA_DATABASE_PATH", str(parent / "database.sqlite3"))
    assert main(["--init-db"]) == 1
    assert parent.read_text(encoding="utf-8") == "preserved"
    assert "database_initialization_failed" in capsys.readouterr().err


def test_python_module_entrypoint_reads_env_and_reports_safe_failure(tmp_path):
    value = "SYNTHETIC_SUBPROCESS_PRIVATE_123"
    (tmp_path / ".env").write_text(f"NOVA_PORT={value}\n", encoding="utf-8")
    result = subprocess.run(
        [sys.executable, "-m", "nova"],
        cwd=tmp_path,
        env={**os.environ, "PYTHONUTF8": "1"},
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=20,
        check=False,
    )
    assert result.returncode == 2
    assert "NOVA_PORT" in result.stderr
    assert value not in result.stdout + result.stderr
    assert "Traceback" not in result.stderr
