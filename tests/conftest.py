"""测试隔离：不读取开发者 .env，不使用个人数据库。"""

import os

import pytest

from nova.config import Settings


@pytest.fixture(autouse=True)
def isolated_environment(monkeypatch, tmp_path):
    for name in tuple(os.environ):
        if name.upper().startswith(("NOVA_", "DEEPSEEK_")):
            monkeypatch.delenv(name, raising=False)
    monkeypatch.chdir(tmp_path)


@pytest.fixture
def settings(tmp_path):
    return Settings(_env_file=None, database_path=tmp_path / "nova.sqlite3")


@pytest.fixture
def practice_policy():
    """每次构造独立的合成模型策略，避免各测试模块共享可变状态。"""

    def make():
        return {
            "practice:tiny": {
                "model": "practice:tiny",
                "enabled": True,
                "required_mib": 1,
                "min_total_ram_mib": 0,
                "min_available_ram_mib": 0,
                "runtime": {},
            }
        }

    return make
