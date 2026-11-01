from __future__ import annotations

from benchmarks.model_benchmark.metrics import (
    extract_timings,
    ns_to_ms,
    percentile,
    safe_rate,
    stats,
)


def test_percentile_and_stats() -> None:
    assert percentile([1, 2, 3, 4], 50) == 2.5
    assert percentile([1, 2, 3, 4], 95) == 3.85
    assert percentile([], 50) is None
    result = stats([10, 20, 30])
    assert result["count"] == 3
    assert result["median"] == 20
    assert result["min"] == 10
    assert result["max"] == 30
    assert stats([])["count"] == 0


def test_unit_conversion_and_rates() -> None:
    assert ns_to_ms(1_500_000) == 1.5
    assert ns_to_ms(None) is None
    assert ns_to_ms(-1) is None
    assert safe_rate(20, 100.0) == 200.0
    assert safe_rate(20, 0) is None


def test_extract_provider_and_wall_timings() -> None:
    final = {
        "load_duration": 20_000_000,
        "prompt_eval_count": 12,
        "prompt_eval_duration": 10_000_000,
        "eval_count": 20,
        "eval_duration": 100_000_000,
        "total_duration": 200_000_000,
    }
    timings = extract_timings(final, ttft_ms=120.0, wall_time_ms=500.0)
    assert timings["ttft_ms"] == 120.0
    assert timings["load_duration_ms"] == 20.0
    assert timings["prompt_eval_duration_ms"] == 10.0
    assert timings["eval_duration_ms"] == 100.0
    assert timings["tokens_per_second"] == 200.0
    assert timings["end_to_end_tokens_per_second"] == 40.0
    assert timings["provider_timings"] is True


def test_extract_without_final_chunk_is_null() -> None:
    timings = extract_timings(None, ttft_ms=None, wall_time_ms=250.0)
    assert timings["load_duration_ms"] is None
    assert timings["tokens_per_second"] is None
    assert timings["provider_timings"] is False
    assert timings["wall_time_ms"] == 250.0
