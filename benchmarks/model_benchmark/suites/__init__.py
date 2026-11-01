"""Suite registry for the standalone benchmark CLI."""

from __future__ import annotations

from .base import ReservedSuite, Suite, SuiteContext, SuiteResult
from .memory_accuracy import MemoryAccuracySuite
from .natural_dialogue import NaturalDialogueSuite
from .performance import PerformanceSuite
from .persona_consistency import PersonaConsistencySuite

SUITES: dict[str, Suite] = {
    suite.id: suite
    for suite in (
        PerformanceSuite(),
        PersonaConsistencySuite(),
        NaturalDialogueSuite(),
        MemoryAccuracySuite(),
    )
}


def get_suite(suite_id: str) -> Suite:
    try:
        return SUITES[suite_id]
    except KeyError as exc:
        raise ValueError(f"unknown suite: {suite_id!r}") from exc


def list_suites() -> list[dict[str, object]]:
    return [
        {
            "id": suite.id,
            "status": suite.status,
            "description": suite.description,
            "required_inputs": list(suite.required_inputs),
        }
        for suite in SUITES.values()
    ]


__all__ = [
    "ReservedSuite",
    "SUITES",
    "Suite",
    "SuiteContext",
    "SuiteResult",
    "get_suite",
    "list_suites",
]
