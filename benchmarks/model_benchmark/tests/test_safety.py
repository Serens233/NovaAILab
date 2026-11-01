from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from benchmarks.model_benchmark.config import BenchmarkConfig
from benchmarks.model_benchmark.safety import (
    RunLock,
    SafetyError,
    check_preflight,
    find_running_games,
    read_gaming_state,
    validate_lease,
)


class FakeClient:
    def version(self) -> dict[str, object]:
        return {"version": "test"}

    def loaded_models(self) -> list[str]:
        return []


def _environment() -> dict[str, object]:
    return {
        "gpu": {
            "available": True,
            "name": "Fake GPU",
            "memory_total_mib": 16311,
            "memory_free_mib": 8000,
        },
        "memory": {"total_mib": 16000, "available_mib": 8000},
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
    assert read_gaming_state(tmp_path / "missing.json") is None


def test_validate_lease(tmp_path) -> None:
    path = tmp_path / "lease.json"
    _write_lease(path)
    lease = validate_lease(path, ("qwen3.5:4b",), 300)
    assert lease["granted_by"] == "unit-test"
    assert lease["models"] == ["qwen3.5:4b"]

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
    report = check_preflight(
        _config(),
        FakeClient(),
        _environment(),
        process_lister=lambda: [],
    )
    assert report["ok"] is True
    assert report["loaded_models"] == []


def test_preflight_rejects_running_game() -> None:
    report = check_preflight(
        _config(),
        FakeClient(),
        _environment(),
        process_lister=lambda: [{"name": "cs2.exe", "pid": 7}],
    )
    assert report["ok"] is False
    names = {check["name"] for check in report["checks"] if not check["ok"]}
    assert "game_processes" in names


def test_preflight_rejects_27b_without_ram() -> None:
    report = check_preflight(
        _config(models=("qwen3.8:27b-q3_k_xl",), allow_experimental=True),
        FakeClient(),
        _environment(),
        process_lister=lambda: [],
    )
    assert report["ok"] is False
    failed = {check["name"] for check in report["checks"] if not check["ok"]}
    assert "experimental_admission" in failed


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
