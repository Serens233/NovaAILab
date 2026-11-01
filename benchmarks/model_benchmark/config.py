"""Configuration objects for the standalone benchmark runner."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

PACKAGE_ROOT = Path(__file__).resolve().parent
DEFAULT_CASES_PATH = PACKAGE_ROOT / "cases" / "performance_zh.json"
DEFAULT_OUTPUT_DIR = PACKAGE_ROOT / "results"
DEFAULT_MODELS = ("qwen3.5:9b", "qwen3.5:4b")
DEFAULT_GAME_PROCESSES = (
    "cs2.exe",
    "dota2.exe",
    "overwatch.exe",
    "eldenring.exe",
    "cyberpunk2077.exe",
    "reddeadredemption2.exe",
    "gta5.exe",
)
EXPERIMENTAL_MODEL_PREFIX = "qwen3.8:27b"

MODEL_NAME_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._/:-]{0,127}\Z")

# Nova refuses to load the 27B model below these admission thresholds.  The
# benchmark mirrors that safety rule instead of trying to force a load.
EXPERIMENTAL_MIN_TOTAL_RAM_MIB = 28672
EXPERIMENTAL_MIN_AVAILABLE_RAM_MIB = 12288


@dataclass(frozen=True)
class SamplingOptions:
    """The request-level inference configuration recorded with every result."""

    num_ctx: int = 4096
    num_predict: int = 128
    temperature: float = 0.2
    top_p: float = 0.9
    top_k: int = 40
    seed: int = 42
    repeat_penalty: float = 1.1
    presence_penalty: float = 0.0
    think: bool = False

    def to_ollama_options(self) -> dict[str, int | float]:
        return {
            "num_ctx": self.num_ctx,
            "num_predict": self.num_predict,
            "temperature": self.temperature,
            "top_p": self.top_p,
            "top_k": self.top_k,
            "seed": self.seed,
            "repeat_penalty": self.repeat_penalty,
            "presence_penalty": self.presence_penalty,
        }

    def as_dict(self) -> dict[str, object]:
        data: dict[str, object] = dict(self.to_ollama_options())
        data["think"] = self.think
        return data


@dataclass
class BenchmarkConfig:
    ollama_url: str = "http://127.0.0.1:11434"
    models: tuple[str, ...] = DEFAULT_MODELS
    cases_path: Path = DEFAULT_CASES_PATH
    case_ids: tuple[str, ...] = ()
    suite: str = "performance"
    warm_repetitions: int = 3
    cold_repetitions: int = 1
    output_dir: Path = DEFAULT_OUTPUT_DIR
    timeout_s: float = 180.0
    settle_s: float = 1.0
    sample_interval_s: float = 0.5
    min_free_vram_mib: int = 2048
    min_available_ram_mib: int = 2048
    max_duration_s: float = 900.0
    game_processes: tuple[str, ...] = DEFAULT_GAME_PROCESSES
    allow_experimental: bool = False
    baseline_commit: str | None = None
    lease_path: Path | None = None
    nova_state_path: Path | None = None
    lock_path: Path | None = None
    include_preview: bool = True
    preview_chars: int = 120
    execute: bool = False
    options: SamplingOptions = field(default_factory=SamplingOptions)

    def validate(self) -> None:
        if not self.models:
            raise ValueError("at least one model must be selected")
        for model in self.models:
            if not MODEL_NAME_PATTERN.fullmatch(model):
                raise ValueError(f"invalid model identifier: {model!r}")
        if not 0 <= self.cold_repetitions <= 3:
            raise ValueError("cold_repetitions must be between 0 and 3")
        if not 0 <= self.warm_repetitions <= 10:
            raise ValueError("warm_repetitions must be between 0 and 10")
        if self.cold_repetitions == 0 and self.warm_repetitions == 0:
            raise ValueError("at least one cold or warm repetition is required")
        if not 10.0 <= self.timeout_s <= 1800.0:
            raise ValueError("timeout_s must be between 10 and 1800 seconds")
        if not 30.0 <= self.max_duration_s <= 3600.0:
            raise ValueError("max_duration_s must be between 30 and 3600 seconds")
        if not 0.1 <= self.sample_interval_s <= 5.0:
            raise ValueError("sample_interval_s must be between 0.1 and 5 seconds")
        if not 0.0 <= self.settle_s <= 30.0:
            raise ValueError("settle_s must be between 0 and 30 seconds")
        for name in self.game_processes:
            if (
                not name
                or len(name) > 64
                or "/" in name
                or "\\" in name
                or not name.casefold().endswith(".exe")
            ):
                raise ValueError(f"invalid game process name: {name!r}")
        if not 0 <= self.preview_chars <= 500:
            raise ValueError("preview_chars must be between 0 and 500")
        if self.options.num_ctx < 512 or self.options.num_ctx > 32768:
            raise ValueError("num_ctx must be between 512 and 32768")
        if not 1 <= self.options.num_predict <= 2048:
            raise ValueError("num_predict must be between 1 and 2048")
        if self.execute and self.lease_path is None:
            raise ValueError("execute mode requires a GPU lease file")
        if self.baseline_commit is not None and (
            len(self.baseline_commit) != 40
            or any(char not in "0123456789abcdef" for char in self.baseline_commit)
        ):
            raise ValueError("baseline_commit must be a 40-character lowercase Git SHA")
        if self.output_dir.exists() and not self.output_dir.is_dir():
            raise ValueError(f"output path is not a directory: {self.output_dir}")

    def as_dict(self) -> dict[str, object]:
        return {
            "ollama_url": self.ollama_url,
            "models": list(self.models),
            "cases_path": str(self.cases_path),
            "case_ids": list(self.case_ids),
            "suite": self.suite,
            "warm_repetitions": self.warm_repetitions,
            "cold_repetitions": self.cold_repetitions,
            "output_dir": str(self.output_dir),
            "timeout_s": self.timeout_s,
            "settle_s": self.settle_s,
            "sample_interval_s": self.sample_interval_s,
            "min_free_vram_mib": self.min_free_vram_mib,
            "min_available_ram_mib": self.min_available_ram_mib,
            "max_duration_s": self.max_duration_s,
            "game_processes": list(self.game_processes),
            "allow_experimental": self.allow_experimental,
            "baseline_commit": self.baseline_commit,
            "lease_path": str(self.lease_path) if self.lease_path else None,
            "nova_state_path": str(self.nova_state_path) if self.nova_state_path else None,
            "include_preview": self.include_preview,
            "preview_chars": self.preview_chars,
            "execute": self.execute,
            "options": self.options.as_dict(),
        }


def is_experimental_model(model: str) -> bool:
    return model.casefold().startswith(EXPERIMENTAL_MODEL_PREFIX.casefold())
