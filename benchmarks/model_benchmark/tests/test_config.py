from __future__ import annotations

import pytest

from benchmarks.model_benchmark.config import BenchmarkConfig


def test_safety_defaults_are_fail_closed(tmp_path) -> None:
    config = BenchmarkConfig()
    assert config.game_processes == ()
    assert config.include_preview is False
    assert config.allow_unload_loaded is False
    assert config.nova_state_path is None  # resolved from Nova settings at preflight
    config.validate()


def test_allow_unload_loaded_requires_execute_and_lease(tmp_path) -> None:
    with pytest.raises(ValueError, match="allow_unload_loaded"):
        BenchmarkConfig(allow_unload_loaded=True).validate()
    with pytest.raises(ValueError, match="lease"):
        BenchmarkConfig(allow_unload_loaded=True, execute=True).validate()
    BenchmarkConfig(
        allow_unload_loaded=True,
        execute=True,
        lease_path=tmp_path / "lease.json",
    ).validate()


def test_invalid_game_process_names_are_rejected() -> None:
    with pytest.raises(ValueError, match="invalid game process"):
        BenchmarkConfig(game_processes=("C:\\Games\\cs2.exe",)).validate()
    with pytest.raises(ValueError, match="invalid game process"):
        BenchmarkConfig(game_processes=("cs2",)).validate()
