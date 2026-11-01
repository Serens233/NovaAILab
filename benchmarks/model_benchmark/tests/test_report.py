from __future__ import annotations

import csv
import json

from benchmarks.model_benchmark.cases import load_cases
from benchmarks.model_benchmark.config import DEFAULT_CASES_PATH
from benchmarks.model_benchmark.report import (
    build_payload,
    render_summary,
    write_inventory,
    write_outputs,
)


def _run(index: int, phase: str, *, success: bool = True) -> dict[str, object]:
    return {
        "run_index": index,
        "model": "qwen3.5:4b",
        "case_id": "short_factual_zh",
        "phase": phase,
        "repetition": 0,
        "success": success,
        "started_at": "2026-11-01T22:00:00+08:00",
        "timings": {
            "ttft_ms": 120.0 if success else None,
            "wall_time_ms": 500.0,
            "load_duration_ms": 20.0 if phase == "cold" else 0.0,
            "prompt_eval_duration_ms": 10.0,
            "eval_duration_ms": 100.0,
            "total_duration_ms": 200.0,
            "prompt_eval_count": 12,
            "eval_count": 20,
            "tokens_per_second": 200.0,
            "end_to_end_tokens_per_second": 40.0,
        },
        "memory": {
            "gpu_total_baseline_mib": 1500,
            "gpu_total_peak_mib": 4500,
            "gpu_total_delta_peak_mib": 3000,
            "gpu_util_peak_percent": 90,
            "system_used_baseline_mib": 4000,
            "system_used_peak_mib": 5000,
            "system_available_min_mib": 3000,
            "ollama_working_set_peak_mib": 300.0,
            "llama_working_set_peak_mib": 2500.0,
            "model_size_vram_mib": 2800.0,
            "model_size_mib": 3000.0,
            "process_source": "fake",
        },
        "response": {
            "chars": 10,
            "sha256": "a" * 64,
            "thinking_chars": 0,
            "chunks": 5,
            "done_reason": "stop",
            "preview": "这是合成样例输出。",
        },
        "error": None if success else "OllamaError: simulated",
    }


def _payload(cases) -> dict[str, object]:
    models = [
        {
            "id": "qwen3.5:4b",
            "installed": True,
            "digest": "d" * 64,
            "size_gib": 3.1,
            "details": {
                "format": "gguf",
                "family": "qwen35",
                "parameter_size": "4.2B",
                "quantization_level": "Q4_K_M",
            },
            "model_info": {
                "architecture": "qwen35",
                "context_length": 262144,
                "parameter_count": 4_205_751_296,
            },
            "admission": {"allowed": True, "reason": ""},
        }
    ]
    return build_payload(
        run_id="test-run",
        status="completed",
        baseline={"commit": "a" * 40, "branch": "experiment/test", "dirty": False},
        config={
            "cold_repetitions": 1,
            "warm_repetitions": 2,
            "options": {"num_ctx": 4096, "num_predict": 128, "think": False},
        },
        environment={
            "captured_at": "2026-11-01T22:00:00+08:00",
            "timezone": "CST",
            "os": {"platform": "Windows"},
            "cpu": {"name": "Ryzen 7 9700X", "logical_processors": 16},
            "memory": {"total_mib": 16000},
            "gpu": {
                "name": "RTX 5060 Ti",
                "memory_total_mib": 16311,
                "driver_version": "621.01",
            },
            "ollama": {"version": "0.40.13"},
        },
        models=models,
        runs=[_run(1, "cold"), _run(2, "warm"), _run(3, "warm")],
        cases=cases,
    )


def test_write_outputs_and_read_formats(tmp_path) -> None:
    cases = load_cases(DEFAULT_CASES_PATH)
    payload = _payload(cases)
    run_dir = tmp_path / "run"
    paths = write_outputs(run_dir, payload, cases)
    assert all(path.exists() for path in paths)
    document = json.loads((run_dir / "results.json").read_text(encoding="utf-8"))
    assert document["run_id"] == "test-run"
    assert document["summary"]["qwen3.5:4b"]["warm"]["tokens_per_second"]["p50"] == 200.0
    assert document["summary"]["qwen3.5:4b"]["memory"]["gpu_total_delta_peak_mib"] == 3000.0
    with (run_dir / "results.csv").open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    assert len(rows) == 3
    assert rows[0]["model"] == "qwen3.5:4b"
    assert rows[0]["tokens_per_second"] == "200.0"
    assert rows[0]["gpu_total_delta_peak_mib"] == "3000"
    summary = (run_dir / "summary.md").read_text(encoding="utf-8")
    assert "Nova 本地模型 Benchmark 汇总" in summary
    assert "不根据 GGUF" in summary
    assert "qwen3.5:4b" in summary


def test_render_summary_marks_27b_and_limitations() -> None:
    cases = load_cases(DEFAULT_CASES_PATH)
    payload = _payload(cases)
    payload["models"][0]["id"] = "qwen3.8:27b-q3_k_xl"
    payload["models"][0]["admission"] = {"allowed": False, "reason": "系统内存不足"}
    payload["limitations"] = ["27B 只做了元数据/准入检查；未加载、未测量。"]
    text = render_summary(payload)
    assert "27B 只做了元数据/准入检查" in text


def test_write_inventory(tmp_path) -> None:
    cases = load_cases(DEFAULT_CASES_PATH)
    environment = {"captured_at": "2026-11-01T22:00:00+08:00", "gpu": {}, "memory": {}}
    models = [{"id": "qwen3.5:4b", "installed": True, "details": {}, "model_info": {}}]
    paths = write_inventory(tmp_path / "inventory", environment, models, cases)
    assert all(path.exists() for path in paths)
    text = (tmp_path / "inventory" / "summary.md").read_text(encoding="utf-8")
    assert "不加载模型" in text
    assert "qwen3.5:4b" in text
