"""Structured JSON/CSV output and a concise human summary."""

from __future__ import annotations

import csv
import json
from pathlib import Path

from .cases import TestCase, cases_as_dict, cases_sha256
from .metrics import stats

_METRIC_PATHS = {
    "ttft_ms": ("timings", "ttft_ms"),
    "wall_time_ms": ("timings", "wall_time_ms"),
    "load_duration_ms": ("timings", "load_duration_ms"),
    "prompt_eval_duration_ms": ("timings", "prompt_eval_duration_ms"),
    "eval_duration_ms": ("timings", "eval_duration_ms"),
    "total_duration_ms": ("timings", "total_duration_ms"),
    "tokens_per_second": ("timings", "tokens_per_second"),
    "end_to_end_tokens_per_second": ("timings", "end_to_end_tokens_per_second"),
    "prompt_eval_count": ("timings", "prompt_eval_count"),
    "eval_count": ("timings", "eval_count"),
    "response_chars": ("response", "chars"),
}

_CSV_FIELDS = (
    "run_index",
    "model",
    "case_id",
    "phase",
    "repetition",
    "success",
    "started_at",
    "wall_time_ms",
    "ttft_ms",
    "load_duration_ms",
    "prompt_eval_duration_ms",
    "eval_duration_ms",
    "total_duration_ms",
    "prompt_eval_count",
    "eval_count",
    "tokens_per_second",
    "end_to_end_tokens_per_second",
    "response_chars",
    "thinking_chars",
    "gpu_total_baseline_mib",
    "gpu_total_peak_mib",
    "gpu_total_delta_peak_mib",
    "gpu_util_peak_percent",
    "system_used_baseline_mib",
    "system_used_peak_mib",
    "system_available_min_mib",
    "ollama_working_set_peak_mib",
    "llama_working_set_peak_mib",
    "model_size_vram_mib",
    "error",
)


def _nested(run: dict[str, object], path: tuple[str, str]) -> object:
    container = run.get(path[0])
    if not isinstance(container, dict):
        return None
    return container.get(path[1])


def _is_number(value: object) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def summarize_runs(runs: list[dict[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {"runs": len(runs), "success": 0}
    successful = [run for run in runs if run.get("success")]
    result["success"] = len(successful)
    for name, path in _METRIC_PATHS.items():
        values = [value for run in successful if _is_number(value := _nested(run, path))]
        result[name] = stats(values)
    return result


def _memory_summary(runs: list[dict[str, object]]) -> dict[str, object]:
    successful = [run for run in runs if run.get("success") and isinstance(run.get("memory"), dict)]
    memories = [run["memory"] for run in successful]

    def values(key: str) -> list[float]:
        return [
            float(memory[key])
            for memory in memories
            if isinstance(memory.get(key), (int, float)) and not isinstance(memory.get(key), bool)
        ]

    def peak(key: str) -> float | None:
        items = values(key)
        return round(max(items), 3) if items else None

    def minimum(key: str) -> float | None:
        items = values(key)
        return round(min(items), 3) if items else None

    def first(key: str) -> float | None:
        items = values(key)
        return round(items[0], 3) if items else None

    return {
        "samples": len(memories),
        "gpu_total_baseline_mib": first("gpu_total_baseline_mib"),
        "gpu_total_peak_mib": peak("gpu_total_peak_mib"),
        "gpu_total_delta_peak_mib": peak("gpu_total_delta_peak_mib"),
        "gpu_util_peak_percent": peak("gpu_util_peak_percent"),
        "system_used_baseline_mib": first("system_used_baseline_mib"),
        "system_used_peak_mib": peak("system_used_peak_mib"),
        "system_available_min_mib": minimum("system_available_min_mib"),
        "ollama_working_set_peak_mib": peak("ollama_working_set_peak_mib"),
        "llama_working_set_peak_mib": peak("llama_working_set_peak_mib"),
        "ollama_private_peak_mib": peak("ollama_private_peak_mib"),
        "llama_private_peak_mib": peak("llama_private_peak_mib"),
        "model_size_vram_mib": peak("model_size_vram_mib"),
        "model_size_mib": peak("model_size_mib"),
        "process_source": memories[0].get("process_source") if memories else None,
    }


def summarize_model(model: dict[str, object], runs: list[dict[str, object]]) -> dict[str, object]:
    cold = [run for run in runs if run.get("phase") == "cold"]
    warm = [run for run in runs if run.get("phase") == "warm"]
    by_case: dict[str, object] = {}
    case_ids = sorted({str(run.get("case_id")) for run in warm})
    for case_id in case_ids:
        case_runs = [run for run in warm if run.get("case_id") == case_id]
        by_case[case_id] = summarize_runs(case_runs)
    return {
        "id": model.get("id"),
        "admission": model.get("admission"),
        "cold": summarize_runs(cold),
        "warm": summarize_runs(warm),
        "warm_by_case": by_case,
        "memory": _memory_summary(runs),
        "failures": len([run for run in runs if not run.get("success")]),
    }


def build_payload(
    *,
    run_id: str,
    status: str,
    baseline: dict[str, object],
    config: dict[str, object],
    environment: dict[str, object],
    models: list[dict[str, object]],
    runs: list[dict[str, object]],
    cases: list[TestCase],
    suite_results: list[dict[str, object]] | None = None,
    limitations: list[str] | None = None,
    error: str | None = None,
) -> dict[str, object]:
    summaries = {
        str(model["id"]): summarize_model(
            model, [run for run in runs if run.get("model") == model["id"]]
        )
        for model in models
    }
    return {
        "schema_version": "1.0",
        "run_id": run_id,
        "generated_at": environment.get("captured_at"),
        "status": status,
        "baseline": baseline,
        "config": config,
        "environment": environment,
        "models": models,
        "cases": {
            "sha256": cases_sha256(cases),
            "items": cases_as_dict(cases),
        },
        "runs": runs,
        "suite_results": suite_results or [],
        "summary": summaries,
        "limitations": limitations or [],
        "error": error,
    }


def _fmt(value: object, digits: int = 1) -> str:
    if value is None:
        return "—"
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, (int, float)):
        if isinstance(value, int):
            return f"{value:,}"
        return f"{value:,.{digits}f}"
    return str(value)


def _md_table(headers: list[str], rows: list[list[object]]) -> str:
    lines = [
        "| " + " | ".join(headers) + " |",
        "| " + " | ".join("---" for _ in headers) + " |",
    ]
    for row in rows:
        lines.append("| " + " | ".join(str(cell) for cell in row) + " |")
    return "\n".join(lines)


def _stats_cell(block: object, key: str, field: str = "p50", digits: int = 1) -> str:
    if not isinstance(block, dict):
        return "—"
    metric = block.get(key)
    if not isinstance(metric, dict):
        return "—"
    return _fmt(metric.get(field), digits)


def _model_name(model: dict[str, object]) -> str:
    return str(model.get("id") or "—")


def _render_models(models: list[dict[str, object]]) -> str:
    rows = []
    for model in models:
        details = model.get("details") if isinstance(model.get("details"), dict) else {}
        info = model.get("model_info") if isinstance(model.get("model_info"), dict) else {}
        rows.append(
            [
                _model_name(model),
                _fmt(info.get("parameter_count")),
                _fmt(details.get("parameter_size")),
                _fmt(details.get("quantization_level")),
                _fmt(info.get("context_length")),
                _fmt(model.get("size_gib"), 2),
                _fmt(str(model.get("digest", ""))[:12]),
            ]
        )
    return _md_table(
        ["模型", "参数量", "参数规模", "量化", "模型上下文", "文件 GiB", "digest"],
        rows,
    )


def _admission_label(model: dict[str, object]) -> str:
    admission = model.get("admission")
    if not isinstance(admission, dict):
        return "—"
    if admission.get("allowed"):
        return "允许"
    reason = admission.get("reason") or "未准入"
    return f"跳过：{reason}"


def _render_cold(models: list[dict[str, object]], summaries: dict[str, object]) -> str:
    rows = []
    for model in models:
        summary = summaries.get(str(model.get("id")))
        if not isinstance(summary, dict):
            continue
        cold = summary.get("cold") if isinstance(summary.get("cold"), dict) else {}
        rows.append(
            [
                _model_name(model),
                _admission_label(model),
                _stats_cell(cold, "load_duration_ms", "median"),
                _stats_cell(cold, "ttft_ms", "median"),
                _stats_cell(cold, "tokens_per_second", "median"),
                _stats_cell(cold, "wall_time_ms", "median"),
            ]
        )
    if not rows:
        return "无冷启动样本。"
    return _md_table(
        ["模型", "准入", "加载 ms", "冷启动 TTFT ms", "tok/s", "总耗时 ms"],
        rows,
    )


def _render_warm(models: list[dict[str, object]], summaries: dict[str, object]) -> str:
    rows = []
    for model in models:
        summary = summaries.get(str(model.get("id")))
        if not isinstance(summary, dict):
            continue
        by_case = summary.get("warm_by_case")
        if not isinstance(by_case, dict):
            continue
        for case_id, block in by_case.items():
            rows.append(
                [
                    _model_name(model),
                    case_id,
                    _stats_cell(block, "ttft_ms", "p50"),
                    _stats_cell(block, "ttft_ms", "p95"),
                    _stats_cell(block, "tokens_per_second", "p50"),
                    _stats_cell(block, "tokens_per_second", "p95"),
                    _stats_cell(block, "eval_duration_ms", "p50"),
                ]
            )
    if not rows:
        return "无热启动样本。"
    return _md_table(
        ["模型", "样例", "TTFT p50 ms", "TTFT p95 ms", "tok/s p50", "tok/s p95", "eval p50 ms"],
        rows,
    )


def _render_memory(models: list[dict[str, object]], summaries: dict[str, object]) -> str:
    rows = []
    for model in models:
        summary = summaries.get(str(model.get("id")))
        if not isinstance(summary, dict):
            continue
        memory = summary.get("memory") if isinstance(summary.get("memory"), dict) else {}
        rows.append(
            [
                _model_name(model),
                _fmt(memory.get("gpu_total_baseline_mib")),
                _fmt(memory.get("gpu_total_peak_mib")),
                _fmt(memory.get("gpu_total_delta_peak_mib")),
                _fmt(memory.get("model_size_vram_mib")),
                _fmt(memory.get("llama_working_set_peak_mib")),
                _fmt(memory.get("system_used_peak_mib")),
                _fmt(memory.get("system_available_min_mib")),
            ]
        )
    if not rows:
        return "无资源样本。"
    return _md_table(
        [
            "模型",
            "GPU 基线 MiB",
            "GPU 峰值 MiB",
            "GPU 峰值增量 MiB",
            "Ollama size_vram MiB",
            "llama 工作集峰值 MiB",
            "系统已用峰值 MiB",
            "系统可用最低 MiB",
        ],
        rows,
    )


def _render_environment(environment: dict[str, object]) -> str:
    os_info = environment.get("os") if isinstance(environment.get("os"), dict) else {}
    cpu = environment.get("cpu") if isinstance(environment.get("cpu"), dict) else {}
    memory = environment.get("memory") if isinstance(environment.get("memory"), dict) else {}
    gpu = environment.get("gpu") if isinstance(environment.get("gpu"), dict) else {}
    ollama = environment.get("ollama") if isinstance(environment.get("ollama"), dict) else {}
    rows = [
        ["OS", _fmt(os_info.get("platform"))],
        ["CPU", _fmt(cpu.get("name"))],
        ["逻辑处理器", _fmt(cpu.get("logical_processors"))],
        ["系统内存", f"{_fmt(memory.get('total_mib'))} MiB"],
        ["GPU", _fmt(gpu.get("name"))],
        ["GPU 显存", f"{_fmt(gpu.get('memory_total_mib'))} MiB"],
        ["驱动", _fmt(gpu.get("driver_version"))],
        ["Ollama", _fmt(ollama.get("version"))],
        ["采样时间", _fmt(environment.get("captured_at"))],
        ["时区", _fmt(environment.get("timezone"))],
    ]
    return _md_table(["项目", "值"], rows)


def _case_token_limits(payload: dict[str, object], options: dict[str, object]) -> str:
    cases = payload.get("cases")
    items = cases.get("items") if isinstance(cases, dict) else None
    if not isinstance(items, list):
        return "—"
    default = options.get("num_predict")
    parts = []
    for case in items:
        if not isinstance(case, dict):
            continue
        parts.append(f"{case.get('id')}={case.get('max_tokens') or default}")
    return ", ".join(parts) if parts else "—"


def render_summary(payload: dict[str, object]) -> str:
    environment = payload.get("environment") if isinstance(payload.get("environment"), dict) else {}
    models = payload.get("models") if isinstance(payload.get("models"), list) else []
    summaries = payload.get("summary") if isinstance(payload.get("summary"), dict) else {}
    baseline = payload.get("baseline") if isinstance(payload.get("baseline"), dict) else {}
    config = payload.get("config") if isinstance(payload.get("config"), dict) else {}
    options = config.get("options") if isinstance(config.get("options"), dict) else {}
    lines = [
        "# Nova 本地模型 Benchmark 汇总",
        "",
        f"- 运行 ID：`{payload.get('run_id')}`",
        f"- 状态：`{payload.get('status')}`",
        f"- 统一基准 Commit：`{baseline.get('baseline_commit')}`",
        f"- 运行代码 Commit：`{baseline.get('commit')}`",
        f"- 分支：`{baseline.get('branch')}`",
        f"- 测试时间：{environment.get('captured_at')}",
        f"- 测试样例 SHA-256：`{payload.get('cases', {}).get('sha256') if isinstance(payload.get('cases'), dict) else None}`",
        "",
        "## 环境",
        "",
        _render_environment(environment),
        "",
        "## 模型与量化",
        "",
        _render_models(models),
        "",
        "## 冷启动",
        "",
        _render_cold(models, summaries),
        "",
        "## 热启动",
        "",
        _render_warm(models, summaries),
        "",
        "## 实测资源占用",
        "",
        "> 显存数据来自 `nvidia-smi` 与 Ollama `/api/ps` 的实测采样；",
        "> 不根据 GGUF 量化文件大小推算。整卡显存包含桌面及其他程序，",
        "> 应同时查看峰值增量与 Ollama 报告的 `size_vram`。",
        "",
        _render_memory(models, summaries),
        "",
        "## 测试条件",
        "",
        f"- 上下文长度 `num_ctx`：{options.get('num_ctx')}",
        f"- 全局 `num_predict`：{options.get('num_predict')}；每个样例的 `max_tokens` 会覆盖",
        f"- 样例 token 上限：{_case_token_limits(payload, options)}",
        f"- 采样：temperature={options.get('temperature')}, top_p={options.get('top_p')}, "
        f"top_k={options.get('top_k')}, seed={options.get('seed')}, "
        f"repeat_penalty={options.get('repeat_penalty')}",
        f"- 思考内容：{'启用' if options.get('think') else '关闭'}",
        f"- 冷启动次数/模型：{config.get('cold_repetitions')}；热启动次数/样例："
        f"{config.get('warm_repetitions')}",
        f"- 测试样例：{', '.join(str(case.get('id')) for case in payload.get('cases', {}).get('items', []))}",
        "",
        "## 限制与未执行项",
        "",
    ]
    limitations = payload.get("limitations") if isinstance(payload.get("limitations"), list) else []
    if limitations:
        lines.extend(f"- {item}" for item in limitations)
    else:
        lines.append("- 无额外记录。")
    lines.extend(
        [
            "",
            "## 结论边界",
            "",
            "本报告是单机、单次、固定样例的工程性能基线，不是模型质量排名，",
            "也不代表人格一致性、自然对话或记忆准确性。后三类评测接口已保留，",
            "但尚未实现评分，不能把本报告的吞吐数据当作质量结论。",
            "",
        ]
    )
    return "\n".join(lines)


def write_json(path: Path, data: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2, default=str) + "\n",
        encoding="utf-8",
        newline="\n",
    )


def _csv_row(run: dict[str, object]) -> dict[str, object]:
    timings = run.get("timings") if isinstance(run.get("timings"), dict) else {}
    memory = run.get("memory") if isinstance(run.get("memory"), dict) else {}
    response = run.get("response") if isinstance(run.get("response"), dict) else {}
    row: dict[str, object] = {
        "run_index": run.get("run_index"),
        "model": run.get("model"),
        "case_id": run.get("case_id"),
        "phase": run.get("phase"),
        "repetition": run.get("repetition"),
        "success": run.get("success"),
        "started_at": run.get("started_at"),
        "error": run.get("error") or "",
    }
    for key in (
        "wall_time_ms",
        "ttft_ms",
        "load_duration_ms",
        "prompt_eval_duration_ms",
        "eval_duration_ms",
        "total_duration_ms",
        "prompt_eval_count",
        "eval_count",
        "tokens_per_second",
        "end_to_end_tokens_per_second",
    ):
        row[key] = timings.get(key)
    row["response_chars"] = response.get("chars")
    row["thinking_chars"] = response.get("thinking_chars")
    for key in (
        "gpu_total_baseline_mib",
        "gpu_total_peak_mib",
        "gpu_total_delta_peak_mib",
        "gpu_util_peak_percent",
        "system_used_baseline_mib",
        "system_used_peak_mib",
        "system_available_min_mib",
        "ollama_working_set_peak_mib",
        "llama_working_set_peak_mib",
        "model_size_vram_mib",
    ):
        row[key] = memory.get(key)
    return row


def write_csv(path: Path, runs: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=_CSV_FIELDS)
        writer.writeheader()
        for run in runs:
            writer.writerow(_csv_row(run))


def write_outputs(run_dir: Path, payload: dict[str, object], cases: list[TestCase]) -> list[Path]:
    run_dir.mkdir(parents=True, exist_ok=True)
    paths = [
        run_dir / "results.json",
        run_dir / "environment.json",
        run_dir / "models.json",
        run_dir / "cases.snapshot.json",
        run_dir / "results.csv",
        run_dir / "summary.md",
    ]
    write_json(paths[0], payload)
    write_json(paths[1], payload.get("environment", {}))
    write_json(paths[2], payload.get("models", []))
    write_json(paths[3], {"sha256": cases_sha256(cases), "cases": cases_as_dict(cases)})
    runs = payload.get("runs") if isinstance(payload.get("runs"), list) else []
    write_csv(paths[4], runs)
    paths[5].write_text(render_summary(payload), encoding="utf-8", newline="\n")
    return paths


def render_inventory_summary(
    environment: dict[str, object],
    models: list[dict[str, object]],
    cases: list[TestCase],
) -> str:
    lines = [
        "# Nova 本地模型清单与测试环境",
        "",
        "本文件只读采集 `ollama list/show` 与硬件信息，不加载模型、不做 GPU 压测。",
        "",
        "## 环境",
        "",
        _render_environment(environment),
        "",
        "## 模型",
        "",
        _render_models(models),
        "",
        "## 固定样例",
        "",
        _md_table(
            ["样例", "类别", "语言", "prompt 字符数", "max_tokens"],
            [
                [
                    case.id,
                    case.category,
                    case.language,
                    len(case.prompt),
                    _fmt(case.max_tokens),
                ]
                for case in cases
            ],
        ),
        "",
        "> `ollama show` 的模板、许可证全文和 modelfile 未写入结果，只保留哈希与",
        "> 必要元数据，避免把大段模型文件内容放进仓库。",
        "",
    ]
    return "\n".join(lines)


def write_inventory(
    output_dir: Path,
    environment: dict[str, object],
    models: list[dict[str, object]],
    cases: list[TestCase],
) -> list[Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    inventory = {
        "schema_version": "1.0",
        "captured_at": environment.get("captured_at"),
        "environment": environment,
        "models": models,
        "cases": {"sha256": cases_sha256(cases), "items": cases_as_dict(cases)},
    }
    paths = [
        output_dir / "inventory.json",
        output_dir / "environment.json",
        output_dir / "models.json",
        output_dir / "cases.snapshot.json",
        output_dir / "summary.md",
    ]
    write_json(paths[0], inventory)
    write_json(paths[1], environment)
    write_json(paths[2], models)
    write_json(paths[3], {"sha256": cases_sha256(cases), "cases": cases_as_dict(cases)})
    paths[4].write_text(
        render_inventory_summary(environment, models, cases),
        encoding="utf-8",
        newline="\n",
    )
    return paths
