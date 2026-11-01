"""Suite interface shared by performance and future evaluation suites."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..cases import TestCase
    from ..config import BenchmarkConfig
    from ..ollama_client import OllamaClient


@dataclass
class SuiteContext:
    config: BenchmarkConfig
    client: OllamaClient
    model: dict[str, object]
    cases: list[TestCase]
    run_performance: Callable[[], list[dict[str, object]]] | None = None


@dataclass
class SuiteResult:
    suite: str
    status: str
    runs: list[dict[str, object]] = field(default_factory=list)
    reason: str = ""
    metadata: dict[str, object] = field(default_factory=dict)

    def as_dict(self) -> dict[str, object]:
        return {
            "suite": self.suite,
            "status": self.status,
            "runs": self.runs,
            "reason": self.reason,
            "metadata": self.metadata,
        }


class Suite(ABC):
    id: str = ""
    description: str = ""
    status: str = "available"
    required_inputs: tuple[str, ...] = ()

    @abstractmethod
    def run(self, context: SuiteContext) -> SuiteResult:
        raise NotImplementedError


class ReservedSuite(Suite):
    """A deliberately non-executable placeholder for a future evaluation."""

    status = "reserved"
    reserved_reason = ""

    def run(self, context: SuiteContext) -> SuiteResult:
        return SuiteResult(
            suite=self.id,
            status="reserved",
            reason=self.reserved_reason,
            metadata={"required_inputs": list(self.required_inputs)},
        )
