from __future__ import annotations

import json

from benchmarks.model_benchmark.cli import main
from benchmarks.model_benchmark.config import DEFAULT_MODELS


def test_suites_command(capsys) -> None:
    assert main(["suites"]) == 0
    document = json.loads(capsys.readouterr().out)
    ids = {item["id"] for item in document["suites"]}
    assert {"performance", "persona_consistency", "natural_dialogue", "memory_accuracy"} <= ids


def test_execute_without_lease_is_rejected(capsys) -> None:
    assert main(["run", "--execute"]) == 2
    captured = capsys.readouterr()
    assert "lease" in captured.err.lower()


def test_default_models_are_local_only() -> None:
    assert DEFAULT_MODELS == ("qwen3.5:9b", "qwen3.5:4b")
