"""The implemented cold/warm local-model performance suite."""

from __future__ import annotations

from .base import Suite, SuiteContext, SuiteResult


class PerformanceSuite(Suite):
    id = "performance"
    description = (
        "Cold start, first-token latency, generation throughput, and measured "
        "GPU/RAM usage using fixed synthetic Chinese prompts."
    )
    status = "available"

    def run(self, context: SuiteContext) -> SuiteResult:
        if context.run_performance is None:
            raise RuntimeError("performance suite requires a runner callback")
        runs = context.run_performance()
        return SuiteResult(
            suite=self.id,
            status="completed",
            runs=runs,
            metadata={"cases": [case.id for case in context.cases]},
        )
