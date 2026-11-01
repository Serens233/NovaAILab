"""Measured GPU and memory sampling.

The benchmark never derives VRAM from a GGUF file size.  It records:

* whole-GPU used memory (including the Windows desktop baseline),
* per-process VRAM when ``nvidia-smi`` exposes it,
* Ollama-reported ``size_vram`` from ``/api/ps``,
* system RAM and Ollama/llama-server working sets.

On Windows/WDDM per-process VRAM can be ``N/A``.  In that case the field stays
null and the report says so; it is not replaced with an estimate.
"""

from __future__ import annotations

import ctypes
import ntpath
import os
import threading
import time
from dataclasses import dataclass, field

from .environment import filter_model_apps, global_memory, query_compute_apps, query_gpu

_MIB = 1024.0 * 1024.0
_TARGET_PROCESSES = {
    "ollama.exe",
    "ollama_llama_server.exe",
    "llama-server.exe",
    "ollama_llama_server",
    "llama-server",
}


@dataclass
class ProcessMemory:
    pid: int
    name: str
    working_set_mib: float | None = None
    private_mib: float | None = None


@dataclass
class MemorySample:
    elapsed_s: float
    gpu_total_used_mib: int | float | None
    gpu_util_percent: int | float | None
    gpu_temperature_c: int | float | None
    system_total_mib: int | float | None
    system_available_mib: int | float | None
    system_used_mib: int | float | None
    ollama_working_set_mib: float | None
    llama_working_set_mib: float | None
    ollama_private_mib: float | None
    llama_private_mib: float | None
    process_count: int


@dataclass
class MonitorSummary:
    samples_count: int = 0
    duration_s: float = 0.0
    gpu_available: bool = False
    gpu_total_baseline_mib: int | float | None = None
    gpu_total_peak_mib: int | float | None = None
    gpu_total_delta_peak_mib: int | float | None = None
    gpu_util_peak_percent: int | float | None = None
    gpu_temperature_peak_c: int | float | None = None
    system_total_mib: int | float | None = None
    system_used_baseline_mib: int | float | None = None
    system_used_peak_mib: int | float | None = None
    system_available_min_mib: int | float | None = None
    ollama_working_set_peak_mib: float | None = None
    llama_working_set_peak_mib: float | None = None
    ollama_private_peak_mib: float | None = None
    llama_private_peak_mib: float | None = None
    process_source: str = "unavailable"
    compute_apps_start: list[dict[str, object]] = field(default_factory=list)
    compute_apps_end: list[dict[str, object]] = field(default_factory=list)

    def as_dict(self) -> dict[str, object]:
        return {
            "samples_count": self.samples_count,
            "duration_s": self.duration_s,
            "gpu_available": self.gpu_available,
            "gpu_total_baseline_mib": self.gpu_total_baseline_mib,
            "gpu_total_peak_mib": self.gpu_total_peak_mib,
            "gpu_total_delta_peak_mib": self.gpu_total_delta_peak_mib,
            "gpu_util_peak_percent": self.gpu_util_peak_percent,
            "gpu_temperature_peak_c": self.gpu_temperature_peak_c,
            "system_total_mib": self.system_total_mib,
            "system_used_baseline_mib": self.system_used_baseline_mib,
            "system_used_peak_mib": self.system_used_peak_mib,
            "system_available_min_mib": self.system_available_min_mib,
            "ollama_working_set_peak_mib": self.ollama_working_set_peak_mib,
            "llama_working_set_peak_mib": self.llama_working_set_peak_mib,
            "ollama_private_peak_mib": self.ollama_private_peak_mib,
            "llama_private_peak_mib": self.llama_private_peak_mib,
            "process_source": self.process_source,
            "compute_apps_start": self.compute_apps_start,
            "compute_apps_end": self.compute_apps_end,
        }


class _ProcessMemoryCountersEx(ctypes.Structure):
    _fields_ = [
        ("cb", ctypes.c_ulong),
        ("PageFaultCount", ctypes.c_ulong),
        ("PeakWorkingSetSize", ctypes.c_size_t),
        ("WorkingSetSize", ctypes.c_size_t),
        ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
        ("QuotaPagedPoolUsage", ctypes.c_size_t),
        ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
        ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
        ("PagefileUsage", ctypes.c_size_t),
        ("PeakPagefileUsage", ctypes.c_size_t),
        ("PrivateUsage", ctypes.c_size_t),
    ]


def _windows_processes() -> list[ProcessMemory]:
    from ctypes import wintypes

    psapi = ctypes.WinDLL("psapi", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    process_query_limited_information = 0x1000
    process_vm_read = 0x0010
    count = 4096
    pids = (wintypes.DWORD * count)()
    needed = wintypes.DWORD()
    psapi.EnumProcesses.argtypes = [
        ctypes.POINTER(wintypes.DWORD),
        wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD),
    ]
    psapi.EnumProcesses.restype = wintypes.BOOL
    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL
    kernel32.QueryFullProcessImageNameW.argtypes = [
        wintypes.HANDLE,
        wintypes.DWORD,
        wintypes.LPWSTR,
        ctypes.POINTER(wintypes.DWORD),
    ]
    kernel32.QueryFullProcessImageNameW.restype = wintypes.BOOL
    psapi.GetProcessMemoryInfo.argtypes = [
        wintypes.HANDLE,
        ctypes.POINTER(_ProcessMemoryCountersEx),
        wintypes.DWORD,
    ]
    psapi.GetProcessMemoryInfo.restype = wintypes.BOOL
    if not psapi.EnumProcesses(pids, ctypes.sizeof(pids), ctypes.byref(needed)):
        return []
    total = needed.value // ctypes.sizeof(wintypes.DWORD)
    results: list[ProcessMemory] = []
    for pid in pids[:total]:
        if not pid:
            continue
        handle = kernel32.OpenProcess(
            process_query_limited_information | process_vm_read,
            False,
            pid,
        )
        if not handle:
            continue
        try:
            name_buffer = ctypes.create_unicode_buffer(32768)
            name_size = wintypes.DWORD(len(name_buffer))
            if not kernel32.QueryFullProcessImageNameW(
                handle,
                0,
                name_buffer,
                ctypes.byref(name_size),
            ):
                continue
            name = ntpath.basename(name_buffer.value)
            if name.casefold() not in _TARGET_PROCESSES:
                continue
            counters = _ProcessMemoryCountersEx()
            counters.cb = ctypes.sizeof(counters)
            working = None
            private = None
            if psapi.GetProcessMemoryInfo(handle, ctypes.byref(counters), counters.cb):
                working = counters.WorkingSetSize / _MIB
                private = counters.PrivateUsage / _MIB
            results.append(
                ProcessMemory(
                    pid=int(pid),
                    name=name,
                    working_set_mib=round(working, 3) if working is not None else None,
                    private_mib=round(private, 3) if private is not None else None,
                )
            )
        finally:
            kernel32.CloseHandle(handle)
    return results


def _psutil_processes() -> list[ProcessMemory] | None:
    try:
        import psutil
    except ImportError:
        return None
    results: list[ProcessMemory] = []
    try:
        for proc in psutil.process_iter(["pid", "name"]):
            name = (proc.info.get("name") or "").casefold()
            if name not in _TARGET_PROCESSES:
                continue
            working = None
            private = None
            try:
                info = proc.memory_info()
                working = info.rss / _MIB
                private = getattr(info, "private", info.vms) / _MIB
            except psutil.Error:
                pass
            results.append(
                ProcessMemory(
                    pid=int(proc.info["pid"]),
                    name=proc.info.get("name") or "",
                    working_set_mib=round(working, 3) if working is not None else None,
                    private_mib=round(private, 3) if private is not None else None,
                )
            )
    except psutil.Error:
        return results
    return results


def list_process_memory() -> tuple[list[ProcessMemory], str]:
    if os.name == "nt":
        try:
            return _windows_processes(), "ctypes"
        except OSError, AttributeError, ValueError:
            pass
    psutil_result = _psutil_processes()
    if psutil_result is not None:
        return psutil_result, "psutil"
    return [], "unavailable"


class ResourceMonitor:
    def __init__(
        self,
        interval_s: float = 0.5,
        *,
        gpu_reader=query_gpu,
        process_reader=list_process_memory,
        compute_apps_reader=query_compute_apps,
    ) -> None:
        self.interval_s = float(interval_s)
        self._gpu_reader = gpu_reader
        self._process_reader = process_reader
        self._compute_apps_reader = compute_apps_reader
        self._samples: list[MemorySample] = []
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._started = 0.0
        self._compute_apps_start: list[dict[str, object]] = []
        self._compute_apps_end: list[dict[str, object]] = []
        self._process_source = "unavailable"

    def start(self) -> None:
        self._samples = []
        self._stop.clear()
        self._started = time.monotonic()
        try:
            self._compute_apps_start = filter_model_apps(list(self._compute_apps_reader()))
        except OSError, ValueError:
            self._compute_apps_start = []
        self._thread = threading.Thread(target=self._loop, name="nova-bench-monitor", daemon=True)
        self._thread.start()

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                self._samples.append(self._snapshot())
            except OSError, ValueError:
                pass
            self._stop.wait(self.interval_s)

    def _snapshot(self) -> MemorySample:
        gpu = self._gpu_reader() or {}
        memory = global_memory()
        processes, source = self._process_reader()
        self._process_source = source
        ollama_working = 0.0
        llama_working = 0.0
        ollama_private = 0.0
        llama_private = 0.0
        has_ollama = False
        has_llama = False
        for process in processes:
            folded = process.name.casefold()
            if folded == "ollama.exe":
                has_ollama = True
                ollama_working += process.working_set_mib or 0.0
                ollama_private += process.private_mib or 0.0
            else:
                has_llama = True
                llama_working += process.working_set_mib or 0.0
                llama_private += process.private_mib or 0.0
        return MemorySample(
            elapsed_s=round(time.monotonic() - self._started, 3),
            gpu_total_used_mib=gpu.get("memory_used_mib"),
            gpu_util_percent=gpu.get("utilization_percent"),
            gpu_temperature_c=gpu.get("temperature_c"),
            system_total_mib=memory.get("total_mib"),
            system_available_mib=memory.get("available_mib"),
            system_used_mib=memory.get("used_mib"),
            ollama_working_set_mib=round(ollama_working, 3) if has_ollama else None,
            llama_working_set_mib=round(llama_working, 3) if has_llama else None,
            ollama_private_mib=round(ollama_private, 3) if has_ollama else None,
            llama_private_mib=round(llama_private, 3) if has_llama else None,
            process_count=len(processes),
        )

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=3.0)
            self._thread = None
        try:
            self._samples.append(self._snapshot())
        except OSError, ValueError:
            pass
        try:
            self._compute_apps_end = filter_model_apps(list(self._compute_apps_reader()))
        except OSError, ValueError:
            self._compute_apps_end = []

    @property
    def samples(self) -> list[MemorySample]:
        return list(self._samples)

    def summary(self) -> MonitorSummary:
        if not self._samples:
            return MonitorSummary(
                process_source=self._process_source,
                compute_apps_start=self._compute_apps_start,
                compute_apps_end=self._compute_apps_end,
            )
        first = self._samples[0]
        last = self._samples[-1]
        gpu_values = [
            sample.gpu_total_used_mib
            for sample in self._samples
            if isinstance(sample.gpu_total_used_mib, (int, float))
        ]
        gpu_util_values = [
            sample.gpu_util_percent
            for sample in self._samples
            if isinstance(sample.gpu_util_percent, (int, float))
        ]
        gpu_temp_values = [
            sample.gpu_temperature_c
            for sample in self._samples
            if isinstance(sample.gpu_temperature_c, (int, float))
        ]
        used_values = [
            sample.system_used_mib
            for sample in self._samples
            if isinstance(sample.system_used_mib, (int, float))
        ]
        available_values = [
            sample.system_available_mib
            for sample in self._samples
            if isinstance(sample.system_available_mib, (int, float))
        ]
        ollama_working = [
            sample.ollama_working_set_mib
            for sample in self._samples
            if isinstance(sample.ollama_working_set_mib, (int, float))
        ]
        llama_working = [
            sample.llama_working_set_mib
            for sample in self._samples
            if isinstance(sample.llama_working_set_mib, (int, float))
        ]
        ollama_private = [
            sample.ollama_private_mib
            for sample in self._samples
            if isinstance(sample.ollama_private_mib, (int, float))
        ]
        llama_private = [
            sample.llama_private_mib
            for sample in self._samples
            if isinstance(sample.llama_private_mib, (int, float))
        ]
        baseline = (
            first.gpu_total_used_mib if isinstance(first.gpu_total_used_mib, (int, float)) else None
        )
        peak = max(gpu_values) if gpu_values else None
        delta = peak - baseline if peak is not None and baseline is not None else None
        return MonitorSummary(
            samples_count=len(self._samples),
            duration_s=round(last.elapsed_s, 3),
            gpu_available=bool(gpu_values),
            gpu_total_baseline_mib=baseline,
            gpu_total_peak_mib=peak,
            gpu_total_delta_peak_mib=round(delta, 3) if delta is not None else None,
            gpu_util_peak_percent=max(gpu_util_values) if gpu_util_values else None,
            gpu_temperature_peak_c=max(gpu_temp_values) if gpu_temp_values else None,
            system_total_mib=first.system_total_mib,
            system_used_baseline_mib=first.system_used_mib,
            system_used_peak_mib=max(used_values) if used_values else None,
            system_available_min_mib=min(available_values) if available_values else None,
            ollama_working_set_peak_mib=max(ollama_working) if ollama_working else None,
            llama_working_set_peak_mib=max(llama_working) if llama_working else None,
            ollama_private_peak_mib=max(ollama_private) if ollama_private else None,
            llama_private_peak_mib=max(llama_private) if llama_private else None,
            process_source=self._process_source,
            compute_apps_start=self._compute_apps_start,
            compute_apps_end=self._compute_apps_end,
        )
