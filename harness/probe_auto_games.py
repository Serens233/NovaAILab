"""只读实机信号/开销采样；不启动游戏、不卸载模型，不输出私密进程路径。"""

import argparse
import asyncio
import ctypes
import json
import statistics
import sys
import time
from ctypes import wintypes
from datetime import UTC, datetime
from pathlib import Path

from nova.gpu.observation import WindowsSignalSource


def working_set():
    class Counters(ctypes.Structure):
        _fields_ = [
            ("cb", wintypes.DWORD),
            ("page_faults", wintypes.DWORD),
            *[
                (name, ctypes.c_size_t)
                for name in (
                    "peak_working_set",
                    "working_set",
                    "peak_paged",
                    "paged",
                    "peak_nonpaged",
                    "nonpaged",
                    "pagefile",
                    "peak_pagefile",
                )
            ],
        ]

    if sys.platform != "win32":
        return None
    psapi = ctypes.WinDLL("psapi")
    kernel = ctypes.WinDLL("kernel32")
    kernel.GetCurrentProcess.restype = wintypes.HANDLE
    psapi.GetProcessMemoryInfo.argtypes = [
        wintypes.HANDLE,
        ctypes.POINTER(Counters),
        wintypes.DWORD,
    ]
    psapi.GetProcessMemoryInfo.restype = wintypes.BOOL
    value = Counters()
    value.cb = ctypes.sizeof(value)
    return (
        value.working_set
        if psapi.GetProcessMemoryInfo(kernel.GetCurrentProcess(), ctypes.byref(value), value.cb)
        else None
    )


async def run(args):
    source = WindowsSignalSource()
    samples = []
    cpu_start = time.process_time()
    wall_start = time.perf_counter()
    memory_before = working_set()
    try:
        for index in range(args.samples):
            snapshot = await source.observe()
            result = await source.scan()
            samples.append(
                {
                    "at_utc": datetime.now(UTC).isoformat(),
                    "available": snapshot.get("available"),
                    "gpu_available": snapshot["gpu"].get("available"),
                    "warming_up": snapshot["gpu"].get("warming_up"),
                    "duration_ms": snapshot["duration_ms"],
                    "process_count": len(snapshot["processes"]),
                    "foreground_available": snapshot["foreground"].get("available"),
                    "confirmed": source.details["confirmed"],
                    "candidates": source.details["candidates"],
                    "degraded": source.details["degraded"],
                    "matched_count": len(result),
                }
            )
            print(
                f"sample {index + 1}/{args.samples}: {snapshot['duration_ms']} ms; confirmed={len(result)}",
                flush=True,
            )
            if index + 1 < args.samples:
                await asyncio.sleep(args.interval)
        memory_after = working_set()
        elapsed = time.perf_counter() - wall_start
        durations = [x["duration_ms"] for x in samples]
        report = {
            "scope": "read_only_native_signals_no_model_control",
            "tested_at_utc": datetime.now(UTC).isoformat(),
            "samples": samples,
            "catalog": source.catalog.summary,
            "elapsed_seconds": round(elapsed, 3),
            "cpu_seconds": round(time.process_time() - cpu_start, 4),
            "cpu_one_core_percent": round(100 * (time.process_time() - cpu_start) / elapsed, 3),
            "first_sample_ms": durations[0],
            "warm_median_ms": statistics.median(durations[1:] or durations),
            "max_sample_ms": max(durations),
            "working_set_before_bytes": memory_before,
            "working_set_after_bytes": memory_after,
            "presentmon_enabled": False,
        }
        with args.output.open("x", encoding="utf-8") as out:
            json.dump(report, out, ensure_ascii=False, indent=2)
            out.write("\n")
        print("Report: " + str(args.output), flush=True)
    finally:
        await source.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--samples", type=int, default=6)
    parser.add_argument("--interval", type=float, default=5)
    args = parser.parse_args()
    if not 2 <= args.samples <= 120 or not 2 <= args.interval <= 10:
        parser.error("samples范围2–120，interval范围2–10秒")
    asyncio.run(run(args))


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()
