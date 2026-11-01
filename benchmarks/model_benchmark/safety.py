"""Scheduling guards for real GPU work.

GPU/Ollama stress tests must be serialized with the rest of the machine.  The
runner refuses to load a model unless the caller supplies a short-lived lease
file that names the approved models and expiry.  The lease is a coordination
artifact, not an authentication secret: it records that the main session gave
an exclusive GPU window.

The rules about the 27B model, the game-process list and GAMING state belong to
Nova.  They are read from Nova's typed settings instead of duplicated here.
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

from .config import REPO_ROOT, BenchmarkConfig
from .environment import run_command
from .nova_policy import NovaPolicy, NovaPolicyError, load_policy


class SafetyError(RuntimeError):
    pass


class DeadlineExceeded(SafetyError):
    """The lease window or ``max_duration_s`` ran out during the run."""


def _process_rows() -> list[dict[str, object]]:
    if os.name != "nt":
        raise SafetyError("当前平台无法枚举进程；游戏门禁失败关闭，不会按无游戏处理")
    code, stdout, stderr = run_command(["tasklist", "/FO", "CSV", "/NH"])
    if code != 0:
        detail = (stderr or stdout or "tasklist failed").strip()[:200]
        raise SafetyError(f"无法枚举进程，游戏门禁失败关闭：{detail}")
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
        raise SafetyError(f"无法读取 Nova GPU 状态文件 {path.name}：{exc}") from exc
    if not isinstance(document, dict):
        raise SafetyError(f"Nova GPU 状态文件不是对象：{path.name}")
    mode = document.get("mode")
    active = (
        mode == "GAMING"
        or bool(document.get("manual_gaming"))
        or bool(document.get("detected_gaming"))
    )
    return {
        "path": path.name,
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
        raise SafetyError(f"GPU lease file does not exist: {path.name}")
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
    }


def _lease_covers(lease: dict[str, object], names: list[str]) -> bool:
    models = lease.get("models")
    return models == "*" or all(name in (models or []) for name in names)


def check_preflight(
    config: BenchmarkConfig,
    client,
    environment: dict[str, object],
    *,
    process_lister=None,
    policy: NovaPolicy | None = None,
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
    total_ram = memory.get("total_mib")
    add(
        "available_ram",
        isinstance(available_ram, (int, float)) and available_ram >= config.min_available_ram_mib,
        {"available_mib": available_ram, "required_mib": config.min_available_ram_mib},
    )

    policy = policy if policy is not None else _load_policy_checked(add)
    if policy is not None:
        add(
            "nova_policy",
            True,
            {
                "canonical_model": policy.canonical_model,
                "experimental_enabled": policy.experimental_enabled,
                "min_total_ram_mib": policy.min_total_ram_mib,
                "min_available_ram_mib": policy.min_available_ram_mib,
                "min_free_vram_mib": policy.min_free_vram_mib,
            },
        )
        _check_gaming_state(config, policy, add)
        _check_game_processes(config, policy, add, process_lister)
    else:
        add("nova_gaming_state", False, "Nova 策略不可用，无法确认 GAMING 状态")
        add("game_processes", False, "Nova 策略不可用，无法确认游戏进程名单")

    lease = None
    if config.execute:
        try:
            lease = validate_lease(config.lease_path, config.models, config.max_duration_s)
            add("gpu_lease", True, lease)
        except SafetyError as exc:
            add("gpu_lease", False, str(exc))
    else:
        add("gpu_lease", True, "dry run: no lease required")

    loaded_models: list[str] = []
    try:
        loaded_models = client.loaded_models()
    except Exception as exc:  # noqa: BLE001 - unknown residency must fail closed
        add("loaded_models", False, f"无法读取 /api/ps：{exc}")
    else:
        if not loaded_models:
            add("loaded_models", True, [])
        elif not config.allow_unload_loaded:
            add(
                "loaded_models",
                False,
                {
                    "loaded": loaded_models,
                    "hint": "默认不驱逐已驻留模型；仅在租约明确列出且用户同意时使用 --allow-unload-loaded",
                },
            )
        elif lease is None:
            add("loaded_models", False, "接管已驻留模型要求有效租约")
        elif _lease_covers(lease, loaded_models):
            add(
                "loaded_models",
                True,
                {
                    "loaded": loaded_models,
                    "takeover_authorized": True,
                    "lease_models": lease["models"],
                },
            )
        else:
            add(
                "loaded_models",
                False,
                {
                    "loaded": loaded_models,
                    "lease_models": lease["models"],
                    "reason": "租约没有列出这些已驻留模型，拒绝卸载",
                },
            )

    if policy is not None:
        for model in config.models:
            if not policy.is_experimental(model):
                continue
            if not config.allow_experimental:
                add("experimental_admission", False, f"{model} is experimental and disabled")
                continue
            enough_total = (
                isinstance(total_ram, (int, float)) and total_ram >= policy.min_total_ram_mib
            )
            enough_available = (
                isinstance(available_ram, (int, float))
                and available_ram >= policy.min_available_ram_mib
            )
            enough_vram = (
                isinstance(free_vram, (int, float)) and free_vram >= policy.min_free_vram_mib
            )
            if not policy.experimental_enabled:
                add("experimental_admission", False, f"{model} 在 Nova 配置中未启用")
                continue
            add(
                "experimental_admission",
                enough_total and enough_available and enough_vram,
                {
                    "model": model,
                    "total_ram_mib": total_ram,
                    "available_ram_mib": available_ram,
                    "free_vram_mib": free_vram,
                    "required_total_mib": policy.min_total_ram_mib,
                    "required_available_mib": policy.min_available_ram_mib,
                    "required_free_vram_mib": policy.min_free_vram_mib,
                },
            )

    return {
        "ok": all(check["ok"] for check in checks),
        "checks": checks,
        "warnings": warnings,
        "loaded_models": loaded_models,
        "lease": lease,
    }


def _load_policy_checked(add) -> NovaPolicy | None:
    try:
        return load_policy(REPO_ROOT)
    except NovaPolicyError as exc:
        add("nova_policy", False, str(exc))
        return None


def _check_gaming_state(config: BenchmarkConfig, policy: NovaPolicy, add) -> None:
    path = config.nova_state_path or policy.state_path
    try:
        gaming = read_gaming_state(path)
    except SafetyError as exc:
        add("nova_gaming_state", False, str(exc))
        return
    if gaming is None:
        add(
            "nova_gaming_state",
            not policy.gpu_enabled,
            {
                "path": path.name,
                "present": False,
                "gpu_enabled": policy.gpu_enabled,
                "detail": "Nova GPU 管理未启用，没有持久化 GAMING 状态"
                if not policy.gpu_enabled
                else "Nova GPU 管理已启用但找不到状态文件，无法确认 GAMING",
            },
        )
        return
    add("nova_gaming_state", not bool(gaming.get("active")), gaming)


def _check_game_processes(config: BenchmarkConfig, policy: NovaPolicy, add, process_lister) -> None:
    processes = tuple(config.game_processes) or tuple(policy.game_processes)
    if processes:
        try:
            games = find_running_games(processes, process_lister=process_lister)
        except SafetyError as exc:
            add("game_processes", False, str(exc))
            return
        add(
            "game_processes",
            not games,
            {"source": "cli_or_nova_config", "processes": list(processes), "running": games},
        )
        return
    if policy.delegates_game_detection():
        add(
            "game_processes",
            True,
            {"source": "nova_detector", "delegated_to_nova": True},
        )
        return
    add(
        "game_processes",
        False,
        (
            "Nova 没有配置游戏进程名单，也没有启用自动检测；"
            "请显式传 --game-process 或先配置 NOVA_GPU_GAME_PROCESSES"
        ),
    )


def assert_preflight(report: dict[str, object]) -> None:
    if report.get("ok"):
        return
    failed = [
        f"{check['name']}: {check['detail']}"
        for check in report.get("checks", [])
        if not check.get("ok")
    ]
    raise SafetyError("; ".join(failed) or "preflight failed")


_PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
_STILL_ACTIVE = 259
_WINDOWS_ERROR_ACCESS_DENIED = 5
_WINDOWS_ERROR_INVALID_PARAMETER = 87


def _process_state(pid: int) -> tuple[bool | None, int | None]:
    """Return ``(alive, creation_stamp)``; ``None`` means unable to prove.

    Access-denied or unexpected API errors return ``(None, None)`` so callers
    treat the process as possibly alive instead of deleting another run's lock.
    """
    if pid <= 0:
        return False, None
    if os.name != "nt":
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return False, None
        except OSError:
            return None, None
        return True, None

    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
    kernel32.GetExitCodeProcess.argtypes = (wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD))
    kernel32.GetProcessTimes.argtypes = (
        wintypes.HANDLE,
        ctypes.POINTER(wintypes.FILETIME),
        ctypes.POINTER(wintypes.FILETIME),
        ctypes.POINTER(wintypes.FILETIME),
        ctypes.POINTER(wintypes.FILETIME),
    )
    kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)

    handle = kernel32.OpenProcess(_PROCESS_QUERY_LIMITED_INFORMATION, False, int(pid))
    if not handle:
        error = ctypes.get_last_error()
        if error == _WINDOWS_ERROR_INVALID_PARAMETER:
            return False, None
        if error == _WINDOWS_ERROR_ACCESS_DENIED:
            return None, None
        return None, None
    try:
        exit_code = wintypes.DWORD()
        if not kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code)):
            return None, None
        created = wintypes.FILETIME()
        exited = wintypes.FILETIME()
        kernel = wintypes.FILETIME()
        user = wintypes.FILETIME()
        if not kernel32.GetProcessTimes(
            handle,
            ctypes.byref(created),
            ctypes.byref(exited),
            ctypes.byref(kernel),
            ctypes.byref(user),
        ):
            return None, None
        stamp = (created.dwHighDateTime << 32) | created.dwLowDateTime
        if exit_code.value != _STILL_ACTIVE:
            return False, stamp
        return True, stamp
    finally:
        kernel32.CloseHandle(handle)


def _pid_alive(pid: int) -> bool:
    alive, _stamp = _process_state(pid)
    return alive is not False


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
                raise SafetyError(
                    f"another benchmark run holds the lock: {self.path.name}"
                ) from None
            _alive, stamp = _process_state(os.getpid())
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                json.dump(
                    {
                        "pid": os.getpid(),
                        "run_id": self.run_id,
                        "process_start": stamp,
                        "started_at": datetime.now(UTC).isoformat(timespec="seconds"),
                    },
                    handle,
                )
            self._owned = True
            return
        raise SafetyError(f"could not acquire benchmark lock: {self.path.name}")

    def _is_stale(self) -> bool:
        try:
            document = json.loads(self.path.read_text(encoding="utf-8"))
            age = time.time() - self.path.stat().st_mtime
            pid = int(document.get("pid", 0))
        except OSError, ValueError, json.JSONDecodeError, TypeError:
            return True
        if age > 7200:
            return True
        alive, stamp = _process_state(pid)
        if alive is False:
            return True
        recorded = document.get("process_start")
        if isinstance(recorded, int) and stamp is not None and recorded != stamp:
            # The PID now belongs to a different process; the original holder is gone.
            return True
        return False

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
