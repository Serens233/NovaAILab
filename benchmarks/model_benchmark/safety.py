"""Scheduling guards for real GPU work.

GPU/Ollama stress tests must be serialized with the rest of the machine.  The
runner refuses to load a model unless the caller supplies a short-lived lease
file that names the approved models and expiry.  The lease is a coordination
artifact, not an authentication secret: it records that the main session gave
an exclusive GPU window.
"""

from __future__ import annotations

import csv
import io
import json
import os
import tempfile
import time
from contextlib import AbstractContextManager
from datetime import UTC, datetime
from pathlib import Path

from .config import (
    EXPERIMENTAL_MIN_AVAILABLE_RAM_MIB,
    EXPERIMENTAL_MIN_TOTAL_RAM_MIB,
    BenchmarkConfig,
    is_experimental_model,
)
from .environment import run_command


class SafetyError(RuntimeError):
    pass


def _process_rows() -> list[dict[str, object]]:
    if os.name != "nt":
        return []
    code, stdout, _stderr = run_command(["tasklist", "/FO", "CSV", "/NH"])
    if code != 0:
        return []
    rows = []
    for row in csv.reader(io.StringIO(stdout)):
        if len(row) < 2 or not row[1].strip().isdigit():
            continue
        rows.append({"name": row[0].strip(), "pid": int(row[1].strip())})
    return rows


def find_running_games(
    process_names: tuple[str, ...] | list[str],
    *,
    process_lister=None,
) -> list[dict[str, object]]:
    wanted = {name.casefold() for name in process_names if name}
    if not wanted:
        return []
    rows = (process_lister or _process_rows)()
    matches = []
    for row in rows:
        name = str(row.get("name", ""))
        if name.casefold() in wanted:
            matches.append({"name": name, "pid": row.get("pid")})
    return matches


def read_gaming_state(path: Path | None) -> dict[str, object] | None:
    if path is None or not path.exists():
        return None
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SafetyError(f"cannot read Nova GPU state file {path}: {exc}") from exc
    if not isinstance(document, dict):
        raise SafetyError(f"Nova GPU state file is not an object: {path}")
    mode = document.get("mode")
    active = (
        mode == "GAMING"
        or bool(document.get("manual_gaming"))
        or bool(document.get("detected_gaming"))
    )
    return {
        "path": str(path),
        "mode": mode,
        "manual_gaming": bool(document.get("manual_gaming")),
        "detected_gaming": bool(document.get("detected_gaming")),
        "active": active,
    }


def validate_lease(
    path: Path | None, models: tuple[str, ...], max_seconds: float
) -> dict[str, object]:
    if path is None:
        raise SafetyError("GPU stress testing requires --lease; no lease was supplied")
    if not path.is_file():
        raise SafetyError(f"GPU lease file does not exist: {path}")
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SafetyError(f"invalid GPU lease JSON: {exc}") from exc
    if not isinstance(document, dict):
        raise SafetyError("GPU lease must be a JSON object")
    granted_by = document.get("granted_by")
    if not isinstance(granted_by, str) or not granted_by.strip():
        raise SafetyError("GPU lease must name granted_by")
    expires_raw = document.get("expires_at")
    if not isinstance(expires_raw, str):
        raise SafetyError("GPU lease must contain expires_at")
    try:
        expires_at = datetime.fromisoformat(expires_raw)
    except ValueError as exc:
        raise SafetyError("GPU lease expires_at is not ISO-8601") from exc
    if expires_at.tzinfo is None:
        raise SafetyError("GPU lease expires_at must include a timezone offset")
    now = datetime.now(UTC)
    remaining = (expires_at.astimezone(UTC) - now).total_seconds()
    if remaining <= 0:
        raise SafetyError("GPU lease has expired")
    lease_max = document.get("max_seconds")
    if not isinstance(lease_max, int) or not 30 <= lease_max <= 1800:
        raise SafetyError("GPU lease max_seconds must be an integer from 30 to 1800")
    if max_seconds > lease_max:
        raise SafetyError(
            f"requested max_seconds {max_seconds:.0f} exceeds lease limit {lease_max}"
        )
    allowed = document.get("models")
    if allowed == "*":
        allowed_models = "*"
    elif isinstance(allowed, list) and all(isinstance(item, str) for item in allowed):
        allowed_models = tuple(allowed)
    else:
        raise SafetyError("GPU lease models must be '*' or a list of model identifiers")
    if allowed_models != "*":
        missing = [model for model in models if model not in allowed_models]
        if missing:
            raise SafetyError(f"GPU lease does not cover models: {missing}")
    return {
        "granted_by": granted_by.strip(),
        "expires_at": expires_at.isoformat(),
        "remaining_s": round(max(0.0, remaining), 1),
        "models": list(allowed_models) if allowed_models != "*" else "*",
        "max_seconds": lease_max,
        "note": document.get("note", ""),
    }


def check_preflight(
    config: BenchmarkConfig,
    client,
    environment: dict[str, object],
    *,
    process_lister=None,
) -> dict[str, object]:
    checks: list[dict[str, object]] = []
    warnings: list[str] = []

    def add(name: str, ok: bool, detail: object) -> None:
        checks.append({"name": name, "ok": bool(ok), "detail": detail})

    try:
        version = client.version()
        add("ollama_reachable", True, version.get("version"))
    except Exception as exc:  # noqa: BLE001 - the check must report any transport failure
        add("ollama_reachable", False, str(exc))

    gpu = environment.get("gpu") if isinstance(environment.get("gpu"), dict) else {}
    gpu_available = bool(gpu.get("available"))
    free_vram = gpu.get("memory_free_mib")
    add(
        "gpu_available",
        gpu_available,
        {"name": gpu.get("name"), "free_mib": free_vram, "total_mib": gpu.get("memory_total_mib")},
    )
    if gpu_available and isinstance(free_vram, (int, float)):
        add(
            "free_vram",
            free_vram >= config.min_free_vram_mib,
            {"free_mib": free_vram, "required_mib": config.min_free_vram_mib},
        )
    else:
        add("free_vram", False, "nvidia-smi did not report free VRAM")

    memory = environment.get("memory") if isinstance(environment.get("memory"), dict) else {}
    available_ram = memory.get("available_mib")
    add(
        "available_ram",
        isinstance(available_ram, (int, float)) and available_ram >= config.min_available_ram_mib,
        {"available_mib": available_ram, "required_mib": config.min_available_ram_mib},
    )

    try:
        gaming = read_gaming_state(config.nova_state_path)
        add("nova_gaming_state", not bool(gaming and gaming.get("active")), gaming)
    except SafetyError as exc:
        add("nova_gaming_state", False, str(exc))

    games = find_running_games(config.game_processes, process_lister=process_lister)
    add("game_processes", not games, games)

    loaded_models: list[str] = []
    try:
        loaded_models = client.loaded_models()
        if loaded_models:
            warnings.append(f"models already loaded before the run: {loaded_models}")
    except Exception as exc:  # noqa: BLE001 - preflight reports transport failure
        warnings.append(f"could not inspect /api/ps: {exc}")

    total_ram = memory.get("total_mib")
    for model in config.models:
        if not is_experimental_model(model):
            continue
        if not config.allow_experimental:
            add("experimental_admission", False, f"{model} is experimental and disabled")
            continue
        enough_total = (
            isinstance(total_ram, (int, float)) and total_ram >= EXPERIMENTAL_MIN_TOTAL_RAM_MIB
        )
        enough_available = (
            isinstance(available_ram, (int, float))
            and available_ram >= EXPERIMENTAL_MIN_AVAILABLE_RAM_MIB
        )
        add(
            "experimental_admission",
            enough_total and enough_available,
            {
                "model": model,
                "total_ram_mib": total_ram,
                "available_ram_mib": available_ram,
                "required_total_mib": EXPERIMENTAL_MIN_TOTAL_RAM_MIB,
                "required_available_mib": EXPERIMENTAL_MIN_AVAILABLE_RAM_MIB,
            },
        )

    lease = None
    if config.execute:
        try:
            lease = validate_lease(config.lease_path, config.models, config.max_duration_s)
            add("gpu_lease", True, lease)
        except SafetyError as exc:
            add("gpu_lease", False, str(exc))
    else:
        add("gpu_lease", True, "dry run: no lease required")

    return {
        "ok": all(check["ok"] for check in checks),
        "checks": checks,
        "warnings": warnings,
        "loaded_models": loaded_models,
        "lease": lease,
    }


def assert_preflight(report: dict[str, object]) -> None:
    if report.get("ok"):
        return
    failed = [
        f"{check['name']}: {check['detail']}"
        for check in report.get("checks", [])
        if not check.get("ok")
    ]
    raise SafetyError("; ".join(failed) or "preflight failed")


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if os.name == "nt":
        import ctypes

        handle = ctypes.windll.kernel32.OpenProcess(0x1000, False, int(pid))
        if not handle:
            return False
        ctypes.windll.kernel32.CloseHandle(handle)
        return True
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


class RunLock(AbstractContextManager):
    """Best-effort single-run lock; stale locks older than two hours are removed."""

    def __init__(self, path: Path | None = None, *, run_id: str = "") -> None:
        self.path = path or Path(tempfile.gettempdir()) / "nova-model-benchmark.lock"
        self.run_id = run_id
        self._owned = False

    def acquire(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        for _attempt in range(2):
            try:
                descriptor = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            except FileExistsError:
                if self._is_stale():
                    self.path.unlink(missing_ok=True)
                    continue
                raise SafetyError(f"another benchmark run holds the lock: {self.path}") from None
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                json.dump(
                    {
                        "pid": os.getpid(),
                        "run_id": self.run_id,
                        "started_at": datetime.now(UTC).isoformat(timespec="seconds"),
                    },
                    handle,
                )
            self._owned = True
            return
        raise SafetyError(f"could not acquire benchmark lock: {self.path}")

    def _is_stale(self) -> bool:
        try:
            document = json.loads(self.path.read_text(encoding="utf-8"))
            age = time.time() - self.path.stat().st_mtime
            pid = int(document.get("pid", 0))
        except OSError, ValueError, json.JSONDecodeError, TypeError:
            return True
        return age > 7200 or not _pid_alive(pid)

    def release(self) -> None:
        if not self._owned:
            return
        try:
            document = json.loads(self.path.read_text(encoding="utf-8"))
            if document.get("run_id") == self.run_id:
                self.path.unlink(missing_ok=True)
        except OSError, json.JSONDecodeError:
            pass
        finally:
            self._owned = False

    def __enter__(self) -> RunLock:
        self.acquire()
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        self.release()
