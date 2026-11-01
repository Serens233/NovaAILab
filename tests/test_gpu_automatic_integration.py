"""自动信号先单测，再验证复用原门禁/卸载与生命周期；不依赖真实游戏。"""

import asyncio
import copy

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

import nova.main as main_module
from nova.config import Settings
from nova.gpu.classifier import SignalClassifier
from nova.gpu.observation import WindowsSignalSource

TOKEN = "SYNTHETIC_AUTOMATIC_TOKEN_AT_LEAST_32_CHARACTERS"


class Catalog:
    summary = {"counts": {}, "errors": []}

    def refresh(self):
        return self.summary

    def match(self, path):
        return None


def snapshot(name="practice-render.exe", present=True):
    return {
        "available": True,
        "processes": [
            {
                "pid": 44,
                "ppid": 1,
                "created": 200,
                "name": name,
                "path": "C:\\Synthetic\\" + name,
                "render_api": True,
            }
        ]
        if present
        else [],
        "foreground": {
            "pid": 44,
            "visible": True,
            "minimized": False,
            "fullscreen": True,
            "borderless": True,
        },
        "gpu": {
            "available": True,
            "warming_up": False,
            "processes": {44: {"engines": {"3D": 20, "VideoDecode": 0}}},
            "adapters": [],
        },
    }


class Source:
    def __init__(self):
        self.present = True
        self.name = "practice-render.exe"
        self.closed = False
        self.classifier = SignalClassifier(own_pid=99)
        self.details = {"strategy": "automatic"}

    async def observe(self):
        return snapshot(self.name, self.present)

    async def scan(self):
        result = self.classifier.evaluate(await self.observe(), Catalog())
        self.details.update(confirmed=result["confirmed"], degraded=result["degraded"])
        return set(result["matched_processes"])

    async def close(self):
        self.closed = True


class Backend:
    model = "practice:tiny"

    def __init__(self):
        self.models = [{"name": self.model, "size_vram": 123}]
        self.unloads = 0
        self.loads = 0

    async def get_model_status(self):
        return copy.deepcopy(self.models)

    async def get_gpu_memory_usage(self):
        return {"available": True, "gpus": []}

    async def unload_model(self, name):
        self.models = []
        self.unloads += 1
        return {"done": True}

    async def generate(self, prompt):
        self.loads += 1
        self.models = [{"name": self.model, "size_vram": 123}]
        return {"done": True, "response": "synthetic"}

    async def wake_model(self):
        return {"done": True}


def test_auto_without_white_list_reuses_gate_and_delayed_restore(monkeypatch, tmp_path):
    source = Source()
    backend = Backend()
    monkeypatch.setattr(main_module, "WindowsSignalSource", lambda: source)
    monkeypatch.setattr(main_module, "OllamaBackend", lambda *args: backend)
    settings = Settings(
        _env_file=None,
        environment="test",
        data_dir=tmp_path,
        database_path=tmp_path / "test.sqlite3",
        gpu_enabled=True,
        gpu_token=TOKEN,
        gpu_auto_game_enabled=True,
    )
    app = main_module.create_app(settings)
    headers = {"Authorization": "Bearer " + TOKEN}
    with TestClient(app) as client:
        detector = app.state.game_detector
        assert detector.automatic and not settings.gpu_game_processes
        client.portal.call(detector.poll_once)
        client.portal.call(detector.poll_once)
        status = client.get("/api/gpu/status", headers=headers).json()
        assert (
            status["mode"] == "GAMING" and status["detected_gaming"] and not status["manual_gaming"]
        )
        assert backend.unloads == 1 and status["backend"]["models"] == []
        assert (
            client.post(
                "/api/gpu/generate", headers=headers, json={"prompt": "synthetic"}
            ).status_code
            == 423
        )
        assert client.get("/health").status_code == 200
        clock = [0.0]
        detector.clock = lambda: clock[0]
        source.present = False
        client.portal.call(detector.poll_once)
        clock[0] = 59.9
        client.portal.call(detector.poll_once)
        assert app.state.gpu_manager.blocked
        clock[0] = 60
        client.portal.call(detector.poll_once)
        assert not app.state.gpu_manager.blocked and backend.loads == 0
        assert (
            client.post(
                "/api/gpu/generate", headers=headers, json={"prompt": "synthetic"}
            ).status_code
            == 200
        )
    assert source.closed and not app.state.database.is_ready


def test_fullscreen_browser_does_not_trigger_auto_game(monkeypatch, tmp_path):
    source = Source()
    source.name = "msedge.exe"
    backend = Backend()
    monkeypatch.setattr(main_module, "WindowsSignalSource", lambda: source)
    monkeypatch.setattr(main_module, "OllamaBackend", lambda *args: backend)
    app = main_module.create_app(
        Settings(
            _env_file=None,
            environment="test",
            data_dir=tmp_path,
            database_path=tmp_path / "db",
            gpu_enabled=True,
            gpu_token=TOKEN,
            gpu_auto_game_enabled=True,
        )
    )
    with TestClient(app) as client:
        for _ in range(4):
            client.portal.call(app.state.game_detector.poll_once)
        assert app.state.gpu_manager.mode == "AUTO" and backend.unloads == 0


@pytest.mark.parametrize(
    "fields",
    [
        {"gpu_auto_game_enabled": True},
        {"gpu_memory_pressure_enabled": True},
        {
            "gpu_enabled": True,
            "gpu_token": TOKEN,
            "gpu_auto_game_enabled": True,
            "gpu_game_detection_enabled": True,
            "gpu_game_processes": ["confirmed.exe"],
        },
    ],
)
def test_new_modes_require_secure_unambiguous_configuration(fields):
    with pytest.raises(ValidationError):
        Settings(_env_file=None, **fields)


def test_observation_shares_samples_and_does_not_expose_paths():
    async def run():
        class Probe:
            calls = 0

            def sample(self, catalog):
                self.calls += 1
                return {k: v for k, v in snapshot().items() if k != "gpu"}

        class Counters:
            closed = False

            def sample(self):
                return snapshot()["gpu"]

            def close(self):
                self.closed = True

        clock = [0.0]
        probe = Probe()
        counters = Counters()
        source = WindowsSignalSource(
            catalog=Catalog(),
            probe=probe,
            counters=counters,
            classifier=SignalClassifier(own_pid=99),
            clock=lambda: clock[0],
        )
        try:
            await source.scan()
            await source.observe()
            assert probe.calls == 1
            clock[0] = 5
            assert not await source.scan()
            clock[0] = 10
            assert await source.scan() == {"practice-render.exe"}
            assert "C:\\Synthetic" not in str(source.details)
        finally:
            await source.close()
        assert counters.closed

    asyncio.run(run())
