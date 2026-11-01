"""游戏检测配置与 FastAPI 生命周期验收；仅使用合成进程快照。"""

import json

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr, ValidationError

from nova.config import ConfigurationError, Settings, load_settings
from nova.gpu.detection import GameDetector
from nova.gpu.processes import ProcessReadError
from nova.main import create_app

TOKEN = "synthetic_game_detection_token_for_tests_only"
AUTH = {"Authorization": f"Bearer {TOKEN}"}
GAME = "synthetic-game.exe"


def game_settings(**changes):
    return Settings(
        _env_file=None,
        **{
            "environment": "test",
            "gpu_enabled": True,
            "gpu_token": SecretStr(TOKEN),
            "gpu_game_detection_enabled": True,
            "gpu_game_processes": [GAME],
            **changes,
        },
    )


@pytest.mark.parametrize("changes", [{"gpu_enabled": False}, {"gpu_game_processes": []}])
def test_detection_requires_gpu_manager_and_nonempty_white_list(changes):
    with pytest.raises(ValidationError):
        game_settings(**changes)


def test_white_list_json_from_environment_is_normalized(monkeypatch):
    monkeypatch.setenv("NOVA_GPU_ENABLED", "true")
    monkeypatch.setenv("NOVA_GPU_TOKEN", TOKEN)
    monkeypatch.setenv("NOVA_GPU_GAME_DETECTION_ENABLED", "true")
    monkeypatch.setenv(
        "NOVA_GPU_GAME_PROCESSES",
        json.dumps(["  Synthetic-Game.EXE ", GAME, "Other Synthetic.EXE"]),
    )
    settings = load_settings(None)
    assert settings.gpu_game_processes == [GAME, "other synthetic.exe"]
    assert settings.gpu_game_poll_seconds == 5


@pytest.mark.parametrize("interval", [5, 10])
def test_supported_poll_interval_boundaries(interval):
    assert game_settings(gpu_game_poll_seconds=interval).gpu_game_poll_seconds == interval


@pytest.mark.parametrize("interval", [4, 11, True, False, 5.5, "not-a-number"])
def test_out_of_range_noninteger_or_boolean_poll_interval_is_rejected(interval):
    with pytest.raises(ValidationError):
        game_settings(gpu_game_poll_seconds=interval)


@pytest.mark.parametrize(
    "invalid_name", ["SYNTHETIC_PRIVATE/path/game.exe", "SYNTHETIC_PRIVATE*.exe", ""]
)
def test_config_errors_name_field_without_echoing_private_input(monkeypatch, invalid_name):
    monkeypatch.setenv("NOVA_GPU_GAME_PROCESSES", json.dumps([invalid_name]))
    with pytest.raises(ConfigurationError) as caught:
        load_settings(None)
    assert "NOVA_GPU_GAME_PROCESSES" in str(caught.value)
    assert "SYNTHETIC_PRIVATE" not in str(caught.value)
    assert caught.value.__suppress_context__


def test_json_decode_error_does_not_echo_private_environment_value(monkeypatch):
    monkeypatch.setenv("NOVA_GPU_GAME_PROCESSES", "SYNTHETIC_PRIVATE_INVALID_JSON")
    with pytest.raises(ConfigurationError) as caught:
        load_settings(None)
    assert "SYNTHETIC_PRIVATE" not in str(caught.value)
    assert caught.value.__suppress_context__


def test_missing_white_list_root_error_contains_safe_game_configuration_hint(monkeypatch):
    monkeypatch.setenv("NOVA_GPU_ENABLED", "true")
    monkeypatch.setenv("NOVA_GPU_TOKEN", TOKEN)
    monkeypatch.setenv("NOVA_GPU_GAME_DETECTION_ENABLED", "true")
    with pytest.raises(ConfigurationError) as caught:
        load_settings(None)
    assert "NOVA_GPU_GAME_PROCESSES" in str(caught.value)
    assert TOKEN not in str(caught.value)


class FakeBackend:
    model = "synthetic-local-model:small"

    def __init__(self):
        self.calls = []
        self.models = [{"name": self.model, "size_vram": 100}]

    async def get_model_status(self):
        return [dict(model) for model in self.models]

    async def get_gpu_memory_usage(self):
        return {"available": True, "gpus": [{"total_mib": 16000, "used_mib": 1000}]}

    async def unload_model(self, name):
        self.calls.append("unload")
        self.models = []
        return {"done": True}

    async def wake_model(self):
        self.calls.append("wake")
        return {"done": True}

    async def generate(self, prompt):
        self.calls.append("generate")
        return {"done": True, "response": "合成回复"}


class FakeScan:
    def __init__(self, names):
        self.names = set(names)
        self.calls = 0
        self.failure = None
        self.now = 1000.0

    async def __call__(self):
        self.calls += 1
        if self.failure:
            raise self.failure
        return set(self.names)


def detector_app(monkeypatch, tmp_path, *, detection_enabled=True, names=(GAME,)):
    backend = FakeBackend()
    scanner = FakeScan(names)
    created = []
    monkeypatch.setattr("nova.main.OllamaBackend", lambda *args, **kwargs: backend)

    def factory(manager, whitelist, **kwargs):
        # scan 是构造器默认绑定的函数，必须在工厂中显式注入，防止测试读真实进程。
        detector = GameDetector(
            manager, whitelist, scan=scanner, clock=lambda: scanner.now, **kwargs
        )
        created.append(detector)
        return detector

    monkeypatch.setattr("nova.main.GameDetector", factory)
    settings = Settings(
        _env_file=None,
        environment="test",
        gpu_enabled=True,
        gpu_token=SecretStr(TOKEN),
        data_dir=tmp_path / "manager",
        database_path=tmp_path / "database" / "nova.sqlite3",
        **(
            {"gpu_game_detection_enabled": True, "gpu_game_processes": [GAME]}
            if detection_enabled
            else {}
        ),
    )
    return create_app(settings), backend, scanner, created


def test_startup_scan_enters_gaming_before_serving_and_blocks_manual_auto(monkeypatch, tmp_path):
    app, backend, scanner, created = detector_app(monkeypatch, tmp_path, names={GAME.upper()})
    with TestClient(app) as client:
        assert scanner.calls == 1
        status = client.get("/api/gpu/status", headers=AUTH)
        assert status.status_code == 200
        body = status.json()
        assert body["mode"] == "GAMING"
        assert body["mode_source"] == "game_detection"
        assert body["local_requests_blocked"]
        assert body["game_detection"]["enabled"] is True
        assert body["game_detection"]["ready"] is True
        assert body["game_detection"]["state"] == "game_detected"
        assert body["game_detection"]["matched_processes"] == [GAME]
        assert body["game_detection"]["poll_seconds"] == 5
        assert body["game_detection"]["exit_grace_seconds"] == 60
        assert body["backend"]["models"] == []
        assert body["unload_verified"]
        assert (
            client.post("/api/gpu/generate", json={"prompt": "合成问题"}, headers=AUTH).status_code
            == 423
        )
        assert client.post("/api/gpu/wake", headers=AUTH).status_code == 423
        refused = client.post("/api/gpu/mode", json={"mode": "AUTO"}, headers=AUTH)
        assert refused.status_code == 409
        assert refused.json()["error"]["code"] == "GPU_GAME_DETECTED"
        assert backend.calls == ["unload"]
        assert client.get("/health").json() == {"status": "ok", "database": "ok"}
        assert app.state.database.is_ready
        assert len(created) == 1 and not created[0]._task.done()
    assert created[0]._stopping
    assert created[0]._task.done()
    assert not app.state.database.is_ready


def test_detection_disabled_by_default_does_not_construct_or_scan(monkeypatch, tmp_path):
    app, backend, scanner, created = detector_app(monkeypatch, tmp_path, detection_enabled=False)
    with TestClient(app) as client:
        assert app.state.settings.gpu_game_processes == []
        assert app.state.settings.gpu_game_detection_enabled is False
        status = client.get("/api/gpu/status", headers=AUTH).json()
        assert status["game_detection"]["enabled"] is False
        assert status["mode"] == "AUTO"
        assert not status["local_requests_blocked"]
        assert scanner.calls == 0
        assert created == []
        assert backend.calls == []
        assert client.get("/health").status_code == 200
    assert not app.state.database.is_ready
    assert app.state.game_detector is None


def test_first_scan_failure_stays_locked_but_database_and_status_remain_available(
    monkeypatch, tmp_path
):
    app, backend, scanner, created = detector_app(monkeypatch, tmp_path)
    scanner.failure = ProcessReadError("SYNTHETIC_PRIVATE_OS_ERROR")
    with TestClient(app) as client:
        status = client.get("/api/gpu/status", headers=AUTH)
        body = status.json()
        assert body["mode"] == "GAMING"
        assert body["local_requests_blocked"]
        assert body["game_detection"]["state"] == "scan_failed"
        assert body["game_detection"]["ready"] is False
        assert body["game_detection"]["error"] == "PROCESS_SCAN_FAILED"
        assert "SYNTHETIC_PRIVATE" not in status.text
        assert client.post("/api/gpu/wake", headers=AUTH).status_code == 423
        assert backend.calls == ["unload"]
        assert client.get("/health").status_code == 200
    assert created[0]._task.done()
    assert not app.state.database.is_ready


def test_user_manual_gaming_survives_automatic_exit_window(monkeypatch, tmp_path):
    app, backend, scanner, created = detector_app(monkeypatch, tmp_path)
    with TestClient(app) as client:
        assert (
            client.post("/api/gpu/mode", json={"mode": "GAMING"}, headers=AUTH).status_code == 200
        )
        scanner.names = set()
        client.portal.call(created[0].poll_once)
        scanner.now += 60
        client.portal.call(created[0].poll_once)
        status = client.get("/api/gpu/status", headers=AUTH).json()
        assert status["game_detection"]["state"] == "monitoring"
        assert status["detected_gaming"] is False
        assert status["manual_gaming"] is True
        assert status["mode_source"] == "manual"
        assert status["mode"] == "GAMING"
        assert status["local_requests_blocked"]
        assert "wake" not in backend.calls and "generate" not in backend.calls
        assert client.post("/api/gpu/wake", headers=AUTH).status_code == 423
        # 游戏保护已解除后，只有用户明确手动恢复 AUTO 才解除手动来源的锁。
        restored = client.post("/api/gpu/mode", json={"mode": "AUTO"}, headers=AUTH)
        assert restored.status_code == 200
        assert restored.json()["mode"] == "AUTO"
        assert restored.json()["backend"]["models"] == []
    assert created[0]._task.done()
