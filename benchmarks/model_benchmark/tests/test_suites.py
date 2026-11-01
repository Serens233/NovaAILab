from __future__ import annotations

from types import SimpleNamespace

import pytest

from benchmarks.model_benchmark.suites import get_suite, list_suites
from benchmarks.model_benchmark.suites.performance import PerformanceSuite


def test_suite_registry_has_implemented_and_reserved_entries() -> None:
    suites = {item["id"]: item for item in list_suites()}
    assert suites["performance"]["status"] == "available"
    assert suites["persona_consistency"]["status"] == "reserved"
    assert suites["natural_dialogue"]["status"] == "reserved"
    assert suites["memory_accuracy"]["status"] == "reserved"
    with pytest.raises(ValueError):
        get_suite("does_not_exist")


def test_reserved_suite_never_calls_a_model() -> None:
    suite = get_suite("persona_consistency")
    result = suite.run(SimpleNamespace())
    assert result.status == "reserved"
    assert result.runs == []
    assert "Character Core" in result.reason


def test_performance_suite_delegates_to_runner() -> None:
    suite = PerformanceSuite()
    context = SimpleNamespace(
        run_performance=lambda: [{"run_index": 1}],
        cases=[],
    )
    result = suite.run(context)
    assert result.status == "completed"
    assert result.runs == [{"run_index": 1}]
