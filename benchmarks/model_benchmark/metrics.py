"""Timing and summary statistics.

Ollama reports durations in nanoseconds.  The benchmark keeps both the
provider-reported durations and the wall-clock values measured by the client;
they answer different questions (model load vs. observed first token).
"""

from __future__ import annotations

import math
import statistics
from collections.abc import Iterable


def ns_to_ms(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if value < 0:
        return None
    return round(float(value) / 1_000_000.0, 3)


def safe_rate(numerator: object, denominator_ms: object) -> float | None:
    if isinstance(numerator, bool) or not isinstance(numerator, (int, float)):
        return None
    if isinstance(denominator_ms, bool) or not isinstance(denominator_ms, (int, float)):
        return None
    if numerator < 0 or denominator_ms <= 0:
        return None
    return round(float(numerator) / (float(denominator_ms) / 1000.0), 3)


def extract_timings(
    final: dict[str, object] | None,
    *,
    ttft_ms: float | None,
    wall_time_ms: float,
) -> dict[str, object]:
    if not isinstance(final, dict):
        final = {}
    prompt_eval_count = final.get("prompt_eval_count")
    eval_count = final.get("eval_count")
    load_duration_ms = ns_to_ms(final.get("load_duration"))
    prompt_eval_duration_ms = ns_to_ms(final.get("prompt_eval_duration"))
    eval_duration_ms = ns_to_ms(final.get("eval_duration"))
    total_duration_ms = ns_to_ms(final.get("total_duration"))
    tokens_per_second = safe_rate(eval_count, eval_duration_ms)
    prompt_tokens_per_second = safe_rate(prompt_eval_count, prompt_eval_duration_ms)
    end_to_end_tokens_per_second = safe_rate(eval_count, wall_time_ms)
    return {
        "ttft_ms": round(ttft_ms, 3) if ttft_ms is not None else None,
        "wall_time_ms": round(wall_time_ms, 3),
        "load_duration_ms": load_duration_ms,
        "prompt_eval_duration_ms": prompt_eval_duration_ms,
        "eval_duration_ms": eval_duration_ms,
        "total_duration_ms": total_duration_ms,
        "prompt_eval_count": prompt_eval_count if isinstance(prompt_eval_count, int) else None,
        "eval_count": eval_count if isinstance(eval_count, int) else None,
        "tokens_per_second": tokens_per_second,
        "prompt_tokens_per_second": prompt_tokens_per_second,
        "end_to_end_tokens_per_second": end_to_end_tokens_per_second,
        "provider_timings": bool(final),
    }


def percentile(values: Iterable[float], p: float) -> float | None:
    ordered = sorted(float(value) for value in values)
    if not ordered:
        return None
    if len(ordered) == 1:
        return round(ordered[0], 3)
    rank = (len(ordered) - 1) * (p / 100.0)
    lower = math.floor(rank)
    upper = math.ceil(rank)
    if lower == upper:
        return round(ordered[lower], 3)
    weight = rank - lower
    return round(ordered[lower] * (1.0 - weight) + ordered[upper] * weight, 3)


def stats(values: Iterable[float]) -> dict[str, float | int | None]:
    ordered = [float(value) for value in values if value is not None]
    if not ordered:
        return {
            "count": 0,
            "mean": None,
            "median": None,
            "p50": None,
            "p95": None,
            "min": None,
            "max": None,
            "stdev": None,
        }
    return {
        "count": len(ordered),
        "mean": round(statistics.fmean(ordered), 3),
        "median": round(statistics.median(ordered), 3),
        "p50": percentile(ordered, 50),
        "p95": percentile(ordered, 95),
        "min": round(min(ordered), 3),
        "max": round(max(ordered), 3),
        "stdev": round(statistics.stdev(ordered), 3) if len(ordered) > 1 else None,
    }
