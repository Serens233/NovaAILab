from __future__ import annotations

import json

from benchmarks.model_benchmark.cases import load_cases
from benchmarks.model_benchmark.config import DEFAULT_CASES_PATH, REPO_ROOT
from benchmarks.model_benchmark.report import build_payload, write_outputs
from benchmarks.model_benchmark.sanitize import (
    sanitize_config,
    sanitize_environment,
    sanitize_lease,
)


def test_config_paths_become_relative_and_lease_is_basename() -> None:
    config = {
        "cases_path": str(REPO_ROOT / "benchmarks" / "case.json"),
        "output_dir": str(REPO_ROOT / "benchmarks" / "results"),
        "nova_state_path": r"C:\Users\Administrator\Documents\Codex\AI-Workspace\nova\data\gpu-mode.json",
        "lease_path": r"C:\Users\Administrator\lease.json",
        "models": ["qwen3.5:4b"],
    }
    sanitized = sanitize_config(config)
    assert sanitized["cases_path"] == "benchmarks/case.json"
    assert sanitized["output_dir"] == "benchmarks/results"
    assert sanitized["nova_state_path"] == "<external>/gpu-mode.json"
    assert sanitized["lease_path"] == "<lease>/lease.json"
    assert config["cases_path"].startswith(str(REPO_ROOT))  # input is not mutated


def test_environment_drops_python_executable_and_gpu_uuid() -> None:
    environment = {
        "os": {"platform": "Windows", "python_executable": r"C:\python.exe"},
        "gpu": {"name": "RTX", "uuid": "GPU-secret"},
    }
    sanitized = sanitize_environment(environment)
    assert "python_executable" not in sanitized["os"]
    assert "uuid" not in sanitized["gpu"]
    assert "python_executable" in environment["os"]


def test_lease_keeps_only_scheduling_fields() -> None:
    lease = {
        "granted_by": "main session",
        "expires_at": "2026-11-01T23:30:00+08:00",
        "models": ["qwen3.5:4b"],
        "max_seconds": 900,
        "remaining_s": 899.1,
        "note": "private note with C:\\Users\\Administrator",
    }
    assert sanitize_lease(lease) == {
        "granted_by": "main session",
        "expires_at": "2026-11-01T23:30:00+08:00",
        "models": ["qwen3.5:4b"],
        "max_seconds": 900,
        "remaining_s": 899.1,
    }


def test_write_outputs_never_contains_local_identity(tmp_path) -> None:
    cases = load_cases(DEFAULT_CASES_PATH)
    payload = build_payload(
        run_id="privacy-run",
        status="completed",
        baseline={"commit": "a" * 40},
        config={
            "cases_path": str(REPO_ROOT / "benchmarks" / "model_benchmark" / "cases" / "x.json"),
            "output_dir": str(tmp_path),
            "lease_path": r"C:\Users\Administrator\lease.json",
            "options": {"num_ctx": 4096, "num_predict": 128},
        },
        environment={
            "captured_at": "2026-11-01T22:00:00+08:00",
            "os": {
                "platform": "Windows",
                "python_executable": r"C:\Users\Administrator\python.exe",
            },
            "gpu": {"name": "RTX", "uuid": "GPU-c7b71b16-979d-c15b-583b-b49c97460e42"},
            "memory": {"total_mib": 16000},
        },
        models=[],
        runs=[],
        cases=cases,
        limitations=[],
    )
    payload["preflight"] = {
        "checks": [],
        "warnings": [],
        "loaded_models": [],
        "lease": {"models": "*", "expires_at": "2026-11-01T23:30:00+08:00", "note": "private"},
    }
    run_dir = tmp_path / "privacy-run"
    write_outputs(run_dir, payload, cases)
    for name in ("results.json", "environment.json"):
        text = (run_dir / name).read_text(encoding="utf-8")
        assert "Administrator" not in text
        assert "GPU-c7b71b16" not in text
    document = json.loads((run_dir / "results.json").read_text(encoding="utf-8"))
    assert document["config"]["lease_path"] == "<lease>/lease.json"
    assert document["config"]["output_dir"].startswith("<external>/")
    assert document["environment"]["gpu"]["name"] == "RTX"
    assert "note" not in document["preflight"]["lease"]
