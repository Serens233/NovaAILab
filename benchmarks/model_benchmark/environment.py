"""Read-only hardware, OS, and Git environment capture.

No function here starts a model or changes GPU state.  Missing tools are
reported as ``None`` rather than guessed.
"""

from __future__ import annotations

import csv
import ctypes
import io
import os
import platform
import re
import shutil
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

from . import __version__

_MIB = 1024.0 * 1024.0
_NVIDIA_VERSION = re.compile(r"NVIDIA-SMI(?:\s+version)?\s*:?\s*([0-9][0-9.]*)")
_GIT_CANDIDATES = (
    r"C:\Program Files\Git\cmd\git.exe",
    r"C:\Program Files (x86)\Git\cmd\git.exe",
)


def iso_now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def timestamp_slug() -> str:
    return datetime.now().astimezone().strftime("%Y%m%dT%H%M%S%z")


def run_command(args: list[str] | tuple[str, ...], timeout: float = 10.0) -> tuple[int, str, str]:
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
    try:
        completed = subprocess.run(
            list(args),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            check=False,
            creationflags=flags,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return 127, "", str(exc)
    return completed.returncode, completed.stdout or "", completed.stderr or ""


def find_git() -> str | None:
    found = shutil.which("git")
    if found:
        return found
    for candidate in _GIT_CANDIDATES:
        if Path(candidate).is_file():
            return candidate
    return None


def _optional_number(value: str) -> int | float | None:
    text = value.strip()
    if not text or text.upper() in {"N/A", "[N/A]", "NA", "NOT SUPPORTED"}:
        return None
    try:
        number = float(text)
    except ValueError:
        return None
    if number.is_integer():
        return int(number)
    return number


def parse_gpu_csv(text: str) -> dict[str, object]:
    rows = list(csv.reader(io.StringIO(text)))
    for row in rows:
        if len(row) < 9:
            continue
        return {
            "available": True,
            "index": _optional_number(row[0]),
            "name": row[1].strip(),
            "memory_total_mib": _optional_number(row[2]),
            "memory_used_mib": _optional_number(row[3]),
            "memory_free_mib": _optional_number(row[4]),
            "utilization_percent": _optional_number(row[5]),
            "temperature_c": _optional_number(row[6]),
            "driver_version": row[7].strip(),
            "uuid": row[8].strip(),
        }
    return {"available": False, "error": "nvidia-smi returned no GPU row"}


def parse_compute_apps_csv(text: str) -> list[dict[str, object]]:
    apps = []
    for row in csv.reader(io.StringIO(text)):
        if len(row) < 3:
            continue
        pid = row[0].strip()
        if not pid.isdigit():
            continue
        apps.append(
            {
                "pid": int(pid),
                "name": Path(row[1].strip()).name,
                "used_memory_mib": _optional_number(row[2]),
            }
        )
    return apps


def query_gpu(runner=run_command) -> dict[str, object]:
    args = [
        "nvidia-smi",
        "--query-gpu=index,name,memory.total,memory.used,memory.free,"
        "utilization.gpu,temperature.gpu,driver_version,uuid",
        "--format=csv,noheader,nounits",
    ]
    code, stdout, stderr = runner(args)
    if code != 0:
        return {
            "available": False,
            "error": (stderr or stdout or "nvidia-smi unavailable").strip()[:300],
        }
    gpu = parse_gpu_csv(stdout)
    if gpu.get("available"):
        gpu["nvidia_smi_version"] = query_nvidia_smi_version(runner)
    return gpu


def query_nvidia_smi_version(runner=run_command) -> str | None:
    code, stdout, _stderr = runner(["nvidia-smi", "--version"])
    if code != 0:
        return None
    match = _NVIDIA_VERSION.search(stdout)
    return match.group(1) if match else None


def query_compute_apps(runner=run_command) -> list[dict[str, object]]:
    args = [
        "nvidia-smi",
        "--query-compute-apps=pid,process_name,used_gpu_memory",
        "--format=csv,noheader,nounits",
    ]
    code, stdout, _stderr = runner(args)
    if code != 0:
        return []
    return parse_compute_apps_csv(stdout)


def is_model_process(name: object) -> bool:
    folded = Path(str(name)).name.casefold()
    return folded.startswith("ollama") or folded.startswith("llama")


def filter_model_apps(apps: list[dict[str, object]]) -> list[dict[str, object]]:
    """Keep only Ollama/llama processes; avoid retaining unrelated app history."""
    return [app for app in apps if is_model_process(app.get("name", ""))]


class _MemoryStatusEx(ctypes.Structure):
    _fields_ = [
        ("dwLength", ctypes.c_ulong),
        ("dwMemoryLoad", ctypes.c_ulong),
        ("ullTotalPhys", ctypes.c_ulonglong),
        ("ullAvailPhys", ctypes.c_ulonglong),
        ("ullTotalPageFile", ctypes.c_ulonglong),
        ("ullAvailPageFile", ctypes.c_ulonglong),
        ("ullTotalVirtual", ctypes.c_ulonglong),
        ("ullAvailVirtual", ctypes.c_ulonglong),
        ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
    ]


def global_memory() -> dict[str, object]:
    if os.name == "nt":
        status = _MemoryStatusEx()
        status.dwLength = ctypes.sizeof(_MemoryStatusEx)
        try:
            ok = ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status))
        except AttributeError, OSError:
            ok = 0
        if ok:
            total = status.ullTotalPhys / _MIB
            available = status.ullAvailPhys / _MIB
            return {
                "total_mib": round(total, 1),
                "available_mib": round(available, 1),
                "used_mib": round(total - available, 1),
                "load_percent": int(status.dwMemoryLoad),
            }
        return {"total_mib": None, "available_mib": None, "used_mib": None}
    try:
        page_size = os.sysconf("SC_PAGE_SIZE")
        total = os.sysconf("SC_PHYS_PAGES") * page_size / _MIB
        available = os.sysconf("SC_AVPHYS_PAGES") * page_size / _MIB
    except AttributeError, OSError, ValueError:
        return {"total_mib": None, "available_mib": None, "used_mib": None}
    return {
        "total_mib": round(total, 1),
        "available_mib": round(available, 1),
        "used_mib": round(total - available, 1),
    }


def query_cpu() -> dict[str, object]:
    name = None
    if os.name == "nt":
        try:
            import winreg

            with winreg.OpenKey(
                winreg.HKEY_LOCAL_MACHINE,
                r"HARDWARE\DESCRIPTION\System\CentralProcessor\0",
            ) as key:
                name = str(winreg.QueryValueEx(key, "ProcessorNameString")[0]).strip()
        except OSError, ImportError:
            name = None
    if not name:
        name = platform.processor() or None
    return {
        "name": name,
        "logical_processors": os.cpu_count(),
        "machine": platform.machine(),
    }


def query_os() -> dict[str, object]:
    return {
        "system": platform.system(),
        "release": platform.release(),
        "version": platform.version(),
        "platform": platform.platform(),
        "python": sys.version.split()[0],
        "python_executable": sys.executable,
    }


def query_git(repo_root: Path | None) -> dict[str, object]:
    result: dict[str, object] = {
        "commit": None,
        "branch": None,
        "dirty": None,
        "dirty_entries": None,
        "available": False,
    }
    if repo_root is None:
        return result
    git = find_git()
    if git is None:
        return result
    repo = str(repo_root)
    code, stdout, _stderr = run_command([git, "-C", repo, "rev-parse", "HEAD"])
    if code != 0:
        return result
    result["commit"] = stdout.strip() or None
    code, stdout, _stderr = run_command([git, "-C", repo, "branch", "--show-current"])
    if code == 0:
        result["branch"] = stdout.strip() or None
    code, stdout, _stderr = run_command([git, "-C", repo, "status", "--porcelain"])
    if code == 0:
        entries = [line for line in stdout.splitlines() if line.strip()]
        result["dirty_entries"] = len(entries)
        result["dirty"] = bool(entries)
    result["available"] = True
    return result


def collect_environment(repo_root: Path | None = None) -> dict[str, object]:
    all_apps = query_compute_apps()
    model_apps = filter_model_apps(all_apps)
    return {
        "captured_at": iso_now(),
        "timezone": str(datetime.now().astimezone().tzinfo),
        "benchmark_version": __version__,
        "os": query_os(),
        "cpu": query_cpu(),
        "memory": global_memory(),
        "gpu": query_gpu(),
        "compute_apps": model_apps,
        "compute_apps_other_count": max(0, len(all_apps) - len(model_apps)),
        "compute_apps_note": "only Ollama/llama processes are retained; per-process VRAM may be N/A",
        "git": query_git(repo_root),
    }


def utc_now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")
