from __future__ import annotations

from benchmarks.model_benchmark.config import REPO_ROOT
from benchmarks.model_benchmark.nova_policy import load_policy
from nova.config import Settings


def test_thresholds_and_tag_family_come_from_nova_settings() -> None:
    policy = load_policy(REPO_ROOT)
    settings = Settings(_env_file=None)
    assert policy.canonical_model == settings.qwen38_model
    assert policy.min_total_ram_mib == settings.qwen38_min_total_ram_mib
    assert policy.min_available_ram_mib == settings.qwen38_min_available_ram_mib
    assert policy.min_free_vram_mib == settings.qwen38_min_free_vram_mib
    assert policy.state_path.name == "gpu-mode.json"


def test_experimental_predicate_matches_nova_strict_family() -> None:
    policy = load_policy(REPO_ROOT)
    for name in (
        "qwen3.8:27b",
        "qwen3.8:27b-q3_k_xl",
        "qwen3.8:27b-Q4_K_M",
        "hf.co/unsloth/Qwen3.8-27B-GGUF:UD-Q3_K_XL",
        policy.canonical_model,
    ):
        assert policy.is_experimental(name) is True
    for name in (
        "qwen3.8:27b-cloud",
        "qwen3.8:28b-q3_k_xl",
        "qwen3.8:27b_q3_k_xl",
        "qwen3.8:27b--q3",
        "qwen3.8:27b-",
        "qwen3.8:27b-a:b",
        "qwen3.8:27b-a/b",
        "qwen3.8:27b- good",
        "qwen3.5:9b",
        "",
        None,
    ):
        assert policy.is_experimental(name) is False
