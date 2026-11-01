from __future__ import annotations

import sys

from benchmarks.model_benchmark.environment import (
    collect_environment,
    iso_now,
    parse_compute_apps_csv,
    parse_gpu_csv,
    run_command,
)


def test_parse_gpu_csv() -> None:
    text = (
        "0, NVIDIA GeForce RTX 5060 Ti, 16311, 1590, 14461, 8, 47, 621.01, "
        "GPU-c7b71b16-979d-c15b-583b-b49c97460e42\n"
    )
    gpu = parse_gpu_csv(text)
    assert gpu["available"] is True
    assert gpu["name"] == "NVIDIA GeForce RTX 5060 Ti"
    assert gpu["memory_total_mib"] == 16311
    assert gpu["memory_free_mib"] == 14461
    assert gpu["utilization_percent"] == 8
    assert gpu["driver_version"] == "621.01"


def test_parse_compute_apps_csv_handles_na() -> None:
    apps = parse_compute_apps_csv("1234, C:\\tmp\\llama-server.exe, [N/A]\n")
    assert apps == [{"pid": 1234, "name": "llama-server.exe", "used_memory_mib": None}]


def test_run_command_and_iso_timestamp() -> None:
    code, stdout, _stderr = run_command([sys.executable, "--version"])
    assert code == 0
    assert stdout.strip()
    stamp = iso_now()
    assert "T" in stamp
    assert stamp.endswith("+08:00") or "+" in stamp or "-" in stamp[10:]


def test_collect_environment_shape() -> None:
    environment = collect_environment()
    assert "os" in environment
    assert "cpu" in environment
    assert "memory" in environment
    assert "gpu" in environment
    assert isinstance(environment["memory"]["total_mib"], (int, float, type(None)))
