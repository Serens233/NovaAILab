from __future__ import annotations

import json
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from benchmarks.model_benchmark.config import BenchmarkConfig
from benchmarks.model_benchmark.nova_policy import NovaPolicy
from benchmarks.model_benchmark.safety import (
    RunLock,
    SafetyError,
    check_preflight,
    find_running_games,
    read_gaming_state,
    validate_lease,
)


class FakeClient:
    def __init__(self) -> None:
        self.loaded: list[str] = []

    def version(self) -> dict[str, object]:
        return {"version": "test"}

    def loaded_models(self) -> list[str]:
        return list(self.loaded)


def _policy(**overrides) -> NovaPolicy:
    values = {
        "canonical_model": "hf.co/unsloth/Qwen3.8-27B-GGUF:UD-Q3_K_XL",
        "experimental_enabled": False,
        "min_total_ram_mib": 28672,
        "min_available_ram_mib": 18432,
        "min_free_vram_mib": 4096,
        "gpu_enabled": False,
        "game_processes": (),
        "game_detection_enabled": False,
        "auto_game_enabled": False,
        "state_path": Path("nonexistent-benchmark-state") / "gpu-mode.json",
    }
    values.update(overrides)
    return NovaPolicy(**values)


def _environment() -> dict[str, object]:
    return {
        "gpu": {
            "available": True,
            "name": "Fake GPU",
            "memory_total_mib": 16311,
            "memory_free_mib": 8000,
        },
        "memory": {"total_mib": 32768, "available_mib": 24576},
    }


def _config(**overrides) -> BenchmarkConfig:
    values = {
        "models": ("qwen3.5:4b",),
        "game_processes": ("cs2.exe",),
        "min_free_vram_mib": 1024,
        "min_available_ram_mib": 1024,
        "execute": False,
    }
    values.update(overrides)
    return BenchmarkConfig(**values)


def _write_lease(path: Path, **overrides) -> None:
    document = {
        "granted_by": "unit-test",
        "expires_at": (datetime.now(UTC) + timedelta(seconds=60)).isoformat(),
        "models": ["qwen3.5:4b"],
        "max_seconds": 600,
    }
    document.update(overrides)
    path.write_text(json.dumps(document), encoding="utf-8")


def _preflight(config, client, *, policy=None, process_lister=None):
    return check_preflight(
        config,
        client,
        _environment(),
        process_lister=process_lister or (lambda: []),
        policy=policy or _policy(),
    )


def test_find_running_games_is_case_insensitive() -> None:
    matches = find_running_games(
        ("cs2.exe",),
        process_lister=lambda: [{"name": "CS2.EXE", "pid": 42}],
    )
    assert matches == [{"name": "CS2.EXE", "pid": 42}]


def test_read_gaming_state(tmp_path) -> None:
    state = tmp_path / "gpu-mode.json"
    state.write_text(json.dumps({"mode": "GAMING", "manual_gaming": True}), encoding="utf-8")
    document = read_gaming_state(state)
    assert document is not None
    assert document["active"] is True
    assert document["path"] == "gpu-mode.json"
    assert read_gaming_state(tmp_path / "missing.json") is None


def test_validate_lease(tmp_path) -> None:
    path = tmp_path / "lease.json"
    _write_lease(path, note="private scheduling note")
    lease = validate_lease(path, ("qwen3.5:4b",), 300)
    assert lease["granted_by"] == "unit-test"
    assert lease["models"] == ["qwen3.5:4b"]
    assert "note" not in lease

    with pytest.raises(SafetyError):
        validate_lease(path, ("qwen3.5:4b",), 900)


def test_expired_and_wrong_scope_lease_are_rejected(tmp_path) -> None:
    path = tmp_path / "lease.json"
    _write_lease(path, expires_at=(datetime.now(UTC) - timedelta(seconds=1)).isoformat())
    with pytest.raises(SafetyError):
        validate_lease(path, ("qwen3.5:4b",), 300)
    _write_lease(path)
    with pytest.raises(SafetyError):
        validate_lease(path, ("qwen3.5:9b",), 300)
    with pytest.raises(SafetyError):
        validate_lease(None, ("qwen3.5:4b",), 300)


def test_preflight_passes_clean_environment() -> None:
    report = _preflight(_config(), FakeClient())
    assert report["ok"] is True
    assert report["loaded_models"] == []


def test_preflight_rejects_running_game() -> None:
    report = _preflight(
        _config(), FakeClient(), process_lister=lambda: [{"name": "cs2.exe", "pid": 7}]
    )
    assert report["ok"] is False
    names = {check["name"] for check in report["checks"] if not check["ok"]}
    assert "game_processes" in names


def test_preflight_fails_closed_when_process_enumeration_fails() -> None:
    def broken_lister():
        raise SafetyError("tasklist unavailable")

    report = _preflight(_config(), FakeClient(), process_lister=broken_lister)
    assert report["ok"] is False
    checks = [check for check in report["checks"] if check["name"] == "game_processes"]
    assert checks and checks[0]["ok"] is False
    assert "tasklist" in str(checks[0]["detail"])


def test_preflight_requires_a_game_list_when_nova_has_no_detector() -> None:
    report = _preflight(_config(game_processes=()), FakeClient())
    assert report["ok"] is False
    checks = [check for check in report["checks"] if check["name"] == "game_processes"]
    assert checks and checks[0]["ok"] is False
    assert "--game-process" in str(checks[0]["detail"])


def test_preflight_delegates_game_check_to_nova_detector() -> None:
    report = _preflight(
        _config(game_processes=()),
        FakeClient(),
        policy=_policy(auto_game_enabled=True),
    )
    assert report["ok"] is True
    checks = [check for check in report["checks"] if check["name"] == "game_processes"]
    assert checks and checks[0]["detail"]["delegated_to_nova"] is True


def test_preflight_rejects_27b_without_ram() -> None:
    report = check_preflight(
        _config(models=("qwen3.8:27b-q3_k_xl",), allow_experimental=True),
        FakeClient(),
        {
            "gpu": {"available": True, "memory_free_mib": 8000},
            "memory": {"total_mib": 15477, "available_mib": 9000},
        },
        process_lister=lambda: [],
        policy=_policy(experimental_enabled=True),
    )
    assert report["ok"] is False
    failed = {check["name"] for check in report["checks"] if not check["ok"]}
    assert "experimental_admission" in failed


def test_preflight_uses_nova_thresholds_for_27b() -> None:
    environment = {
        "gpu": {"available": True, "memory_free_mib": 8000},
        "memory": {"total_mib": 32768, "available_mib": 13000},
    }
    report = check_preflight(
        _config(models=("qwen3.8:27b-q3_k_xl",), allow_experimental=True),
        FakeClient(),
        environment,
        process_lister=lambda: [],
        policy=_policy(experimental_enabled=True),
    )
    assert report["ok"] is False
    admission = [check for check in report["checks"] if check["name"] == "experimental_admission"]
    assert admission and admission[0]["detail"]["required_available_mib"] == 18432
    assert admission[0]["detail"]["available_ram_mib"] == 13000


def test_preflight_rejects_resident_models_by_default() -> None:
    client = FakeClient()
    client.loaded = ["qwen3.5:9b"]
    report = _preflight(_config(), client)
    assert report["ok"] is False
    checks = [check for check in report["checks"] if check["name"] == "loaded_models"]
    assert checks and checks[0]["ok"] is False
    assert checks[0]["detail"]["loaded"] == ["qwen3.5:9b"]


def test_preflight_takeover_requires_lease_coverage(tmp_path) -> None:
    lease = tmp_path / "lease.json"
    _write_lease(lease, models=["qwen3.5:4b"])
    client = FakeClient()
    client.loaded = ["qwen3.5:9b"]
    report = _preflight(
        _config(execute=True, lease_path=lease, allow_unload_loaded=True, max_duration_s=300),
        client,
    )
    assert report["ok"] is False
    checks = [check for check in report["checks"] if check["name"] == "loaded_models"]
    assert checks and checks[0]["ok"] is False
    assert "租约" in str(checks[0]["detail"])


def test_preflight_takeover_passes_when_lease_lists_loaded_models(tmp_path) -> None:
    lease = tmp_path / "lease.json"
    _write_lease(lease, models=["qwen3.5:4b", "qwen3.5:9b"])
    client = FakeClient()
    client.loaded = ["qwen3.5:9b"]
    report = _preflight(
        _config(execute=True, lease_path=lease, allow_unload_loaded=True, max_duration_s=300),
        client,
    )
    assert report["ok"] is True
    checks = [check for check in report["checks"] if check["name"] == "loaded_models"]
    assert checks and checks[0]["detail"]["takeover_authorized"] is True


def test_preflight_requires_gaming_state_when_gpu_manager_enabled(tmp_path) -> None:
    missing = tmp_path / "gpu-mode.json"
    report = _preflight(
        _config(nova_state_path=missing),
        FakeClient(),
        policy=_policy(gpu_enabled=True),
    )
    assert report["ok"] is False
    checks = [check for check in report["checks"] if check["name"] == "nova_gaming_state"]
    assert checks and checks[0]["ok"] is False


def test_preflight_rejects_active_gaming_state(tmp_path) -> None:
    state = tmp_path / "gpu-mode.json"
    state.write_text(json.dumps({"mode": "GAMING"}), encoding="utf-8")
    report = _preflight(_config(nova_state_path=state), FakeClient())
    assert report["ok"] is False
    checks = [check for check in report["checks"] if check["name"] == "nova_gaming_state"]
    assert checks and checks[0]["ok"] is False


def test_run_lock_is_exclusive(tmp_path) -> None:
    path = tmp_path / "bench.lock"
    first = RunLock(path, run_id="first")
    second = RunLock(path, run_id="second")
    first.acquire()
    with pytest.raises(SafetyError):
        second.acquire()
    first.release()
    second.acquire()
    second.release()
    assert not path.exists()


def test_run_lock_treats_reused_pid_as_stale(tmp_path) -> None:
    path = tmp_path / "bench.lock"
    path.write_text(
        json.dumps({"pid": 999999, "run_id": "old", "process_start": 1}),
        encoding="utf-8",
    )
    lock = RunLock(path, run_id="new")
    assert lock._is_stale() is True
    lock.acquire()
    assert json.loads(path.read_text(encoding="utf-8"))["run_id"] == "new"
    lock.release()


def test_run_lock_keeps_recent_lock_it_cannot_inspect(tmp_path, monkeypatch) -> None:
    path = tmp_path / "bench.lock"
    path.write_text(json.dumps({"pid": 999999, "run_id": "old"}), encoding="utf-8")
    monkeypatch.setattr(
        "benchmarks.model_benchmark.safety._process_state", lambda pid: (None, None)
    )
    assert RunLock(path, run_id="new")._is_stale() is False
    assert time.time() - path.stat().st_mtime < 60
