from __future__ import annotations

import json

import pytest

from benchmarks.model_benchmark.cases import cases_sha256, load_cases
from benchmarks.model_benchmark.config import DEFAULT_CASES_PATH


def test_default_cases_are_fixed_and_hashable() -> None:
    cases = load_cases(DEFAULT_CASES_PATH)
    assert [case.id for case in cases] == [
        "short_factual_zh",
        "medium_reasoning_zh",
        "long_context_zh",
    ]
    assert cases[0].messages()[0]["role"] == "system"
    assert cases[0].messages()[-1]["role"] == "user"
    assert len(cases_sha256(cases)) == 64
    assert cases_sha256(cases) == cases_sha256(load_cases(DEFAULT_CASES_PATH))


def test_case_filter_and_validation(tmp_path) -> None:
    path = tmp_path / "cases.json"
    path.write_text(
        json.dumps(
            {
                "cases": [
                    {
                        "id": "one_case",
                        "prompt": "你好",
                        "system": "你是助手",
                        "max_tokens": 8,
                    }
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    cases = load_cases(path, ("one_case",))
    assert len(cases) == 1
    with pytest.raises(ValueError):
        load_cases(path, ("missing_case",))


def test_duplicate_ids_are_rejected(tmp_path) -> None:
    path = tmp_path / "cases.json"
    path.write_text(
        json.dumps(
            {
                "cases": [
                    {"id": "same_case", "prompt": "a"},
                    {"id": "same_case", "prompt": "b"},
                ]
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(ValueError):
        load_cases(path)
