"""Benchmark orchestration: model inventory, cold/warm runs, and output.

The runner accepts an injected monitor factory and preflight callback so unit
tests can exercise the complete flow against a fake Ollama server without a
GPU.  Real execution is gated by ``config.execute`` plus a lease file.
"""

from __future__ import annotations

import hashlib
import secrets
import time

from .cases import TestCase, load_cases
from .config import (
    EXPERIMENTAL_MIN_AVAILABLE_RAM_MIB,
    EXPERIMENTAL_MIN_TOTAL_RAM_MIB,
    BenchmarkConfig,
    is_experimental_model,
)
from .environment import iso_now, timestamp_slug
from .metrics import extract_timings
from .monitor import ResourceMonitor
from .ollama_client import OllamaClient, OllamaError
from .report import build_payload, write_outputs
from .safety import RunLock, SafetyError, assert_preflight, check_preflight
from .suites import get_suite

_STATIC_LIMITATIONS = [
    "冷启动定义为模型不在 Ollama /api/ps 中；没有清空 Windows 文件系统页缓存。",
    "整卡显存包含桌面、浏览器等基线占用，应结合峰值增量和 Ollama size_vram 阅读。",
    "Windows/WDDM 下 nvidia-smi 的逐进程显存可能为 N/A；为空时不会用文件大小推算。",
    "单机、单卡、固定样例的单次工程基线，不足以单独决定生产模型选择。",
    "本套件不评价回答质量、人格一致性、自然对话或记忆准确性。",
    "27B 实验模型在 16GB 内存机器上只做元数据检查，不执行加载。",
]


def _sha256_text(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _parse_parameters(raw: object) -> dict[str, object]:
    if not isinstance(raw, str):
        return {}
    parsed: dict[str, object] = {}
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        key, _separator, value = line.partition(" ")
        if not key:
            continue
        text = value.strip()
        try:
            parsed[key] = int(text)
        except ValueError:
            try:
                parsed[key] = float(text)
            except ValueError:
                parsed[key] = text
    return parsed


def _model_info_value(model_info: dict[str, object], suffix: str) -> object:
    direct = model_info.get(f"general.{suffix}")
    if direct is not None:
        return direct
    for key, value in model_info.items():
        if key.endswith(f".{suffix}"):
            return value
    return None


def _tag_entry(tags: list[dict[str, object]], model: str) -> dict[str, object] | None:
    for tag in tags:
        if tag.get("name") == model or tag.get("model") == model:
            return tag
    folded = model.casefold()
    for tag in tags:
        if (
            str(tag.get("name") or "").casefold() == folded
            or str(tag.get("model") or "").casefold() == folded
        ):
            return tag
    return None


def _extract_show(show: dict[str, object]) -> dict[str, object]:
    details_raw = show.get("details")
    details = details_raw if isinstance(details_raw, dict) else {}
    info_raw = show.get("model_info")
    info = info_raw if isinstance(info_raw, dict) else {}
    capabilities = show.get("capabilities")
    selected_info = {
        "architecture": _model_info_value(info, "architecture"),
        "context_length": _model_info_value(info, "context_length"),
        "parameter_count": _model_info_value(info, "parameter_count"),
        "embedding_length": _model_info_value(info, "embedding_length"),
        "block_count": _model_info_value(info, "block_count"),
        "quantization_version": _model_info_value(info, "quantization_version"),
        "license": _model_info_value(info, "license"),
        "base_model_name": info.get("general.base_model.0.name"),
        "base_model_repo": info.get("general.base_model.0.repo_url"),
    }
    return {
        "details": {
            "format": details.get("format"),
            "family": details.get("family"),
            "families": details.get("families"),
            "parameter_size": details.get("parameter_size"),
            "quantization_level": details.get("quantization_level"),
            "runner": details.get("runner"),
            "parent_model": details.get("parent_model"),
        },
        "model_info": selected_info,
        "default_parameters": _parse_parameters(show.get("parameters")),
        "capabilities": capabilities if isinstance(capabilities, list) else [],
        "modified_at": show.get("modified_at"),
        "template_sha256": _sha256_text(show.get("template")),
        "modelfile_sha256": _sha256_text(show.get("modelfile")),
        "license_sha256": _sha256_text(show.get("license")),
    }


def _admission(
    model: str,
    *,
    installed: bool,
    environment: dict[str, object],
    allow_experimental: bool,
) -> dict[str, object]:
    if not installed:
        return {"allowed": False, "reason": "模型未安装"}
    if not is_experimental_model(model):
        return {"allowed": True, "reason": ""}
    memory = environment.get("memory") if isinstance(environment.get("memory"), dict) else {}
    total = memory.get("total_mib")
    available = memory.get("available_mib")
    if not allow_experimental:
        return {"allowed": False, "reason": "27B 实验模型默认关闭，且未获准本轮加载"}
    if not isinstance(total, (int, float)) or total < EXPERIMENTAL_MIN_TOTAL_RAM_MIB:
        return {
            "allowed": False,
            "reason": (
                f"系统总内存 {total} MiB 低于 Nova 的 {EXPERIMENTAL_MIN_TOTAL_RAM_MIB} MiB 准入线"
            ),
        }
    if not isinstance(available, (int, float)) or available < EXPERIMENTAL_MIN_AVAILABLE_RAM_MIB:
        return {
            "allowed": False,
            "reason": (
                f"系统可用内存 {available} MiB 低于 {EXPERIMENTAL_MIN_AVAILABLE_RAM_MIB} MiB 准入线"
            ),
        }
    return {"allowed": True, "reason": ""}


def describe_models(
    client: OllamaClient,
    config: BenchmarkConfig,
    environment: dict[str, object],
) -> list[dict[str, object]]:
    tags = client.tags()
    models = []
    for model in config.models:
        tag = _tag_entry(tags, model)
        installed = tag is not None
        record: dict[str, object] = {
            "id": model,
            "installed": installed,
            "digest": tag.get("digest") if tag else None,
            "size_bytes": tag.get("size") if tag else None,
            "modified_at": tag.get("modified_at") if tag else None,
            "details": {},
            "model_info": {},
            "default_parameters": {},
            "capabilities": [],
            "admission": _admission(
                model,
                installed=installed,
                environment=environment,
                allow_experimental=config.allow_experimental,
            ),
        }
        size = tag.get("size") if tag else None
        if isinstance(size, (int, float)):
            record["size_gib"] = round(float(size) / (1024.0**3), 3)
        if installed:
            try:
                record.update(_extract_show(client.show(model)))
            except OllamaError as exc:
                record["metadata_error"] = str(exc)
        models.append(record)
    return models


class BenchmarkRunner:
    def __init__(
        self,
        config: BenchmarkConfig,
        client: OllamaClient,
        environment: dict[str, object],
        *,
        monitor_factory=ResourceMonitor,
        preflight_fn=check_preflight,
        output_writer=write_outputs,
    ) -> None:
        self.config = config
        self.client = client
        self.environment = environment
        self.monitor_factory = monitor_factory
        self.preflight_fn = preflight_fn
        self.output_writer = output_writer
        self._run_counter = 0

    def plan(self) -> dict[str, object]:
        cases = load_cases(self.config.cases_path, self.config.case_ids)
        suite = get_suite(self.config.suite)
        return {
            "suite": suite.id,
            "suite_status": suite.status,
            "models": list(self.config.models),
            "cases": [case.id for case in cases],
            "cold_repetitions": self.config.cold_repetitions,
            "warm_repetitions": self.config.warm_repetitions,
            "options": self.config.options.as_dict(),
            "output_dir": str(self.config.output_dir),
            "execute": self.config.execute,
            "lease": str(self.config.lease_path) if self.config.lease_path else None,
        }

    def run(self) -> dict[str, object]:
        if not self.config.execute:
            raise SafetyError("runner requires execute=True; use plan() for a dry run")
        self.config.validate()
        suite = get_suite(self.config.suite)
        if suite.status != "available":
            raise SafetyError(f"suite {suite.id!r} is reserved and cannot be executed")
        cases = load_cases(self.config.cases_path, self.config.case_ids)
        environment = dict(self.environment)
        try:
            environment["ollama"] = {"version": self.client.version().get("version")}
        except OllamaError as exc:
            environment["ollama"] = {"version": None, "error": str(exc)}
        models = describe_models(self.client, self.config, environment)
        preflight = self.preflight_fn(self.config, self.client, environment)
        assert_preflight(preflight)

        run_id = f"{timestamp_slug()}-{secrets.token_hex(2)}"
        run_dir = self.config.output_dir / run_id
        if run_dir.exists():
            raise SafetyError(f"output directory already exists: {run_dir}")
        runs: list[dict[str, object]] = []
        all_suite_results: list[dict[str, object]] = []
        baseline = {
            "commit": (environment.get("git") or {}).get("commit"),
            "branch": (environment.get("git") or {}).get("branch"),
            "dirty": (environment.get("git") or {}).get("dirty"),
            "baseline_commit": self.config.baseline_commit,
        }
        lock = RunLock(self.config.lock_path, run_id=run_id)
        with lock:
            for model in models:
                admission = (
                    model.get("admission") if isinstance(model.get("admission"), dict) else {}
                )
                if not admission.get("allowed"):
                    model["status"] = "skipped"
                    continue
                try:
                    model_runs = self._run_model(model, cases, preflight)
                except (OllamaError, SafetyError, OSError, ValueError) as exc:
                    model["status"] = "failed"
                    model["error"] = _safe_error(exc)
                    continue
                runs.extend(model_runs)
                successes = [run for run in model_runs if run.get("success")]
                failures = [run for run in model_runs if not run.get("success")]
                if not successes:
                    model["status"] = "failed"
                elif failures:
                    model["status"] = "partial"
                else:
                    model["status"] = "completed"
                model["suite"] = suite.id
                all_suite_results.append(
                    {
                        "suite": suite.id,
                        "status": "completed" if not failures else "partial",
                        "runs": len(model_runs),
                        "successes": len(successes),
                        "failures": len(failures),
                    }
                )

        status = self._overall_status(models, runs)
        limitations = list(_STATIC_LIMITATIONS)
        if self.config.cold_repetitions == 0:
            limitations.append("本次未执行冷启动卸载/加载。")
        if any(is_experimental_model(str(model.get("id"))) for model in models):
            limitations.append("27B 只做了元数据/准入检查；未加载、未测量。")
        if self.config.include_preview:
            limitations.append("响应仅保留截断预览和 SHA-256，不保存完整原始输出。")
        if preflight.get("warnings"):
            limitations.extend(str(item) for item in preflight["warnings"])
        payload = build_payload(
            run_id=run_id,
            status=status,
            baseline=baseline,
            config=self.config.as_dict(),
            environment=environment,
            models=models,
            runs=runs,
            cases=cases,
            suite_results=all_suite_results,
            limitations=limitations,
        )
        payload["preflight"] = preflight
        self.output_writer(run_dir, payload, cases)
        return payload

    def _overall_status(
        self,
        models: list[dict[str, object]],
        runs: list[dict[str, object]],
    ) -> str:
        successes = [run for run in runs if run.get("success")]
        failures = [run for run in runs if not run.get("success")]
        if not successes:
            return "failed" if runs else "blocked"
        executed = [
            model for model in models if model.get("status") in {"completed", "partial", "failed"}
        ]
        if failures or any(model.get("status") in {"partial", "failed"} for model in executed):
            return "partial"
        if any(model.get("status") == "skipped" for model in models):
            return "partial"
        return "completed"

    def _run_model(
        self,
        model: dict[str, object],
        cases: list[TestCase],
        preflight: dict[str, object],
    ) -> list[dict[str, object]]:
        model_id = str(model["id"])
        loaded = sorted(set(self.client.loaded_models()))
        model["previously_loaded"] = loaded
        unloaded = []
        for name in loaded:
            unloaded.append(self.client.unload(name))
        model["preload_cleanup"] = unloaded
        runs: list[dict[str, object]] = []
        try:
            for repetition in range(self.config.cold_repetitions):
                self.client.unload(model_id)
                run = self._run_chat(model_id, cases[0], "cold", repetition)
                runs.append(run)
                if not run.get("success"):
                    return runs
            if self.config.cold_repetitions == 0:
                warmup = self._run_chat(model_id, cases[0], "warmup", 0)
                runs.append(warmup)
                if not warmup.get("success"):
                    return runs
            for case in cases:
                for repetition in range(self.config.warm_repetitions):
                    run = self._run_chat(model_id, case, "warm", repetition)
                    runs.append(run)
        finally:
            try:
                still_loaded = self.client.loaded_models()
                if model_id in still_loaded:
                    model["cleanup"] = self.client.unload(model_id)
                else:
                    model["cleanup"] = {"already_unloaded": True}
                model["loaded_after"] = self.client.loaded_models()
            except OllamaError as exc:
                model["cleanup"] = {"error": str(exc)}
        model["preflight_notes"] = preflight.get("warnings", [])
        return runs

    def _run_chat(
        self,
        model: str,
        case: TestCase,
        phase: str,
        repetition: int,
    ) -> dict[str, object]:
        self._run_counter += 1
        run_index = self._run_counter
        num_predict = case.max_tokens or self.config.options.num_predict
        request_options = self.config.options.to_ollama_options()
        request_options["num_predict"] = num_predict
        messages = case.messages()
        messages_hash = _sha256_text(
            "\n".join(f"{item['role']}:{item['content']}" for item in messages)
        )
        request = {
            "case_id": case.id,
            "messages_count": len(messages),
            "prompt_chars": len(case.prompt),
            "system_chars": len(case.system),
            "messages_sha256": messages_hash,
            "options": request_options,
            "keep_alive": "5m",
            "think": self.config.options.think,
        }
        monitor = self.monitor_factory(self.config.sample_interval_s)
        started_at = iso_now()
        start_ns = time.perf_counter_ns()
        first_content_ns: int | None = None
        content_parts: list[str] = []
        thinking_chars = 0
        chunk_count = 0
        final: dict[str, object] | None = None
        monitor_started = False
        error: str | None = None
        try:
            monitor.start()
            monitor_started = True
            for chunk in self.client.chat_stream(
                model,
                messages,
                options=request_options,
                keep_alive="5m",
                think=self.config.options.think,
                timeout_s=self.config.timeout_s,
            ):
                chunk_count += 1
                message = chunk.get("message")
                if isinstance(message, dict):
                    content = message.get("content")
                    if isinstance(content, str) and content:
                        if first_content_ns is None:
                            first_content_ns = time.perf_counter_ns()
                        content_parts.append(content)
                    thinking = message.get("thinking")
                    if isinstance(thinking, str):
                        thinking_chars += len(thinking)
                if chunk.get("done") is True:
                    final = chunk
                    break
            if final is None:
                raise OllamaError("stream ended without a done chunk")
        except (OllamaError, OSError, ValueError) as exc:
            error = _safe_error(exc)
        finally:
            end_ns = time.perf_counter_ns()
            if monitor_started:
                monitor.stop()
            else:
                try:
                    monitor.stop()
                except OSError, ValueError:
                    pass
        wall_ms = (end_ns - start_ns) / 1_000_000.0
        ttft_ms = (
            (first_content_ns - start_ns) / 1_000_000.0 if first_content_ns is not None else None
        )
        timings = extract_timings(final, ttft_ms=ttft_ms, wall_time_ms=wall_ms)
        memory = monitor.summary().as_dict()
        model_ps = self._model_ps_entry(model)
        if model_ps:
            size_vram = model_ps.get("size_vram")
            size = model_ps.get("size")
            if isinstance(size_vram, (int, float)):
                memory["model_size_vram_mib"] = round(float(size_vram) / (1024.0**2), 3)
            if isinstance(size, (int, float)):
                memory["model_size_mib"] = round(float(size) / (1024.0**2), 3)
        content = "".join(content_parts)
        success = error is None and bool(content)
        if error is None and not content:
            error = "model returned no content"
        response = {
            "chars": len(content),
            "sha256": hashlib.sha256(content.encode("utf-8")).hexdigest() if content else None,
            "thinking_chars": thinking_chars,
            "chunks": chunk_count,
            "done_reason": final.get("done_reason") if isinstance(final, dict) else None,
            "preview": (
                content[: self.config.preview_chars] if self.config.include_preview else None
            ),
        }
        return {
            "run_index": run_index,
            "model": model,
            "case_id": case.id,
            "phase": phase,
            "repetition": repetition,
            "success": success,
            "started_at": started_at,
            "timings": timings,
            "memory": memory,
            "response": response,
            "request": request,
            "error": error,
        }

    def _model_ps_entry(self, model: str) -> dict[str, object] | None:
        try:
            entries = self.client.ps()
        except OllamaError:
            return None
        for entry in entries:
            if entry.get("name") == model or entry.get("model") == model:
                return entry
        return entries[0] if entries else None


def _safe_error(exc: BaseException) -> str:
    return f"{type(exc).__name__}: {exc}"[:500]
