"""Read Nova's authoritative admission and game policy instead of copying it.

The benchmark deliberately does not import Nova's FastAPI app, GPU manager or
model router.  Loading Nova's typed settings is the one allowed coupling: the
27B thresholds, the experimental tag family and the game-process list must have
a single source.  If Nova's settings cannot be loaded the benchmark fails
closed instead of guessing a weaker rule.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

# Mirrors nova.chat.registry._qwen38_alias.  Kept here as a strict predicate so
# the benchmark never treats a nearby name (27b_q3, 28b, -cloud) as the 27B.
_LOCAL_27B = re.compile(r"qwen3\.8:27b-[A-Za-z0-9][A-Za-z0-9._-]{0,63}\Z", re.IGNORECASE)
_GGUF_27B = re.compile(
    r"hf\.co/unsloth/Qwen3\.8-27B-GGUF:[A-Za-z0-9][A-Za-z0-9._-]*\Z", re.IGNORECASE
)


class NovaPolicyError(ValueError):
    """Nova settings are unavailable or invalid; real runs must not continue."""


@dataclass(frozen=True)
class NovaPolicy:
    canonical_model: str
    experimental_enabled: bool
    min_total_ram_mib: int
    min_available_ram_mib: int
    min_free_vram_mib: int
    gpu_enabled: bool
    game_processes: tuple[str, ...]
    game_detection_enabled: bool
    auto_game_enabled: bool
    state_path: Path

    def is_experimental(self, model: str) -> bool:
        """Same tag family Nova accepts; anything else is an ordinary model."""
        if not isinstance(model, str) or model.casefold().endswith((":cloud", "-cloud")):
            return False
        folded = model.casefold()
        return (
            folded == self.canonical_model.casefold()
            or folded == "qwen3.8:27b"
            or bool(_LOCAL_27B.fullmatch(model))
            or bool(_GGUF_27B.fullmatch(model))
        )

    def delegates_game_detection(self) -> bool:
        return self.auto_game_enabled or self.game_detection_enabled


def load_policy(repo_root: Path) -> NovaPolicy:
    """Load the same typed settings Nova uses, including its ``.env``."""
    try:
        from nova.config import ConfigurationError, load_settings
    except ImportError as exc:  # pragma: no cover - only outside the repository
        raise NovaPolicyError(
            "无法导入 Nova 配置；请在仓库根目录运行并让 src 位于 PYTHONPATH"
        ) from exc
    try:
        settings = load_settings(Path(repo_root) / ".env")
    except ConfigurationError as exc:
        raise NovaPolicyError(f"Nova 配置无效，benchmark 拒绝执行：{exc}") from None
    return NovaPolicy(
        canonical_model=settings.qwen38_model,
        experimental_enabled=settings.qwen38_enabled,
        min_total_ram_mib=settings.qwen38_min_total_ram_mib,
        min_available_ram_mib=settings.qwen38_min_available_ram_mib,
        min_free_vram_mib=settings.qwen38_min_free_vram_mib,
        gpu_enabled=settings.gpu_enabled,
        game_processes=tuple(settings.gpu_game_processes),
        game_detection_enabled=settings.gpu_game_detection_enabled,
        auto_game_enabled=settings.gpu_auto_game_enabled,
        state_path=Path(settings.data_dir) / "gpu-mode.json",
    )
