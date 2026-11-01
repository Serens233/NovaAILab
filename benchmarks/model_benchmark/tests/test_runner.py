from __future__ import annotations

import json

import pytest
from fake_ollama import FakeOllamaServer

from benchmarks.model_benchmark.config import BenchmarkConfig
from benchmarks.model_benchmark.monitor import MonitorSummary
from benchmarks.model_benchmark.ollama_client import OllamaClient
from benchmarks.model_benchmark.runner import BenchmarkRunner
from benchmarks.model_benchmark.safety import SafetyError


class FakeMonitor:
    def __init__(self) -> None:
        self.started = 0
        self.stopped = 0

    def start(self) -> None:
        self.started += 1

    def stop(self) -> None:
        self.stopped += 1

    def summary(self) -> MonitorSummary:
        return MonitorSummary(
            samples_count=3,
            duration_s=0.2,
            gpu_available=True,
            gpu_total_baseline_mib=1500,
            gpu_total_peak_mib=4500,
            gpu_total_delta_peak_mib=3000,
            gpu_util_peak_percent=80,
            system_total_mib=16000,
            system_used_baseline_mib=4000,
            system_used_peak_mib=5000,
            system_available_min_mib=3000,
            ollama_working_set_peak_mib=300.0,
            llama_working_set_peak_mib=2500.0,
            process_source="fake",
        )


def _cases_path(tmp_path):
    path = tmp_path / "cases.json"
    path.write_text(
        json.dumps(
            {
                "cases": [
                    {"id": "case_one", "prompt": "请回答一。", "max_tokens": 16},
                    {"id": "case_two", "prompt": "请回答二。", "max_tokens": 16},
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return path


def _environment() -> dict[str, object]:
    return {
        "captured_at": "2026-11-01T22:00:00+08:00",
        "timezone": "CST",
        "os": {"platform": "Windows"},
        "cpu": {"name": "Fake CPU", "logical_processors": 16},
        "memory": {"total_mib": 16000, "available_mib": 8000},
        "gpu": {
            "available": True,
            "name": "Fake GPU",
            "memory_total_mib": 16311,
            "memory_free_mib": 8000,
        },
        "git": {"commit": "b" * 40, "branch": "experiment/test", "dirty": True},
    }


def _config(path, **overrides) -> BenchmarkConfig:
    values = {
        "models": ("qwen3.5:4b",),
        "cases_path": path,
        "output_dir": path.parent / "out",
        "warm_repetitions": 1,
        "cold_repetitions": 1,
        "timeout_s": 10.0,
        "settle_s": 0.0,
        "sample_interval_s": 0.1,
        "max_duration_s": 60.0,
        "execute": True,
        "lease_path": path.parent / "lease.json",
        "lock_path": path.parent / "bench.lock",
        "baseline_commit": "3bb0f6a7ffe868be1dc014772601fe41fa9036a6",
    }
    values.update(overrides)
    return BenchmarkConfig(**values)


def _preflight(_config, _client, _environment):
    return {
        "ok": True,
        "checks": [],
        "warnings": [],
        "loaded_models": [],
        "lease": None,
    }


def test_full_cold_and_warm_run(tmp_path) -> None:
    monitors: list[FakeMonitor] = []
    with FakeOllamaServer() as server:
        client = OllamaClient(server.url, timeout_s=5.0)
        config = _config(_cases_path(tmp_path))
        runner = BenchmarkRunner(
            config,
            client,
            _environment(),
            monitor_factory=lambda interval: monitors.append(FakeMonitor()) or monitors[-1],
            preflight_fn=_preflight,
        )
        payload = runner.run()

        assert payload["status"] == "completed"
        assert len(payload["runs"]) == 3
        assert [run["phase"] for run in payload["runs"]] == ["cold", "warm", "warm"]
        assert payload["models"][0]["status"] == "completed"
        assert payload["models"][0]["details"]["quantization_level"] == "Q4_K_M"
        assert payload["baseline"]["baseline_commit"] == config.baseline_commit
        assert payload["summary"]["qwen3.5:4b"]["cold"]["load_duration_ms"]["median"] == 20.0
        assert payload["runs"][0]["timings"]["tokens_per_second"] == 200.0
        assert payload["runs"][0]["timings"]["ttft_ms"] > 0
        assert payload["runs"][0]["memory"]["model_size_vram_mib"] == pytest.approx(
            1430.511, abs=0.01
        )
        assert payload["runs"][0]["response"]["preview"] == "你好，世界。"
        assert all(monitor.started == 1 and monitor.stopped == 1 for monitor in monitors)
        assert server.state.unload_calls == ["qwen3.5:4b"]
        assert server.state.loaded == {}
        chat_requests = [item for item in server.state.requests if item["path"] == "/api/chat"]
        assert chat_requests[0]["payload"]["options"]["num_ctx"] == 4096
        assert chat_requests[0]["payload"]["think"] is False

        run_dir = config.output_dir / str(payload["run_id"])
        assert (run_dir / "results.json").exists()
        assert (run_dir / "results.csv").exists()
        assert (run_dir / "summary.md").exists()
        assert (run_dir / "cases.snapshot.json").exists()


def test_failed_cold_run_is_reported(tmp_path) -> None:
    with FakeOllamaServer() as server:
        server.state.fail_chat = True
        config = _config(_cases_path(tmp_path))
        runner = BenchmarkRunner(
            config,
            OllamaClient(server.url, timeout_s=5.0),
            _environment(),
            monitor_factory=lambda interval: FakeMonitor(),
            preflight_fn=_preflight,
        )
        payload = runner.run()
        assert payload["status"] == "failed"
        assert len(payload["runs"]) == 1
        assert payload["runs"][0]["success"] is False
        assert "HTTP 500" in payload["runs"][0]["error"]
        assert payload["models"][0]["status"] == "failed"


def test_missing_model_is_skipped(tmp_path) -> None:
    with FakeOllamaServer() as server:
        config = _config(_cases_path(tmp_path), models=("missing:1b",))
        runner = BenchmarkRunner(
            config,
            OllamaClient(server.url, timeout_s=5.0),
            _environment(),
            monitor_factory=lambda interval: FakeMonitor(),
            preflight_fn=_preflight,
        )
        payload = runner.run()
        assert payload["status"] == "blocked"
        assert payload["runs"] == []
        assert payload["models"][0]["status"] == "skipped"


def test_reserved_suite_is_refused(tmp_path) -> None:
    with FakeOllamaServer() as server:
        config = _config(_cases_path(tmp_path), suite="persona_consistency")
        runner = BenchmarkRunner(
            config,
            OllamaClient(server.url, timeout_s=5.0),
            _environment(),
            monitor_factory=lambda interval: FakeMonitor(),
            preflight_fn=_preflight,
        )
        with pytest.raises(SafetyError):
            runner.run()
