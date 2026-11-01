"""自动检测与显存保护共享一次轻量采样；对外状态不包含进程路径。"""

import asyncio
import time

from nova.gpu.catalog import GameCatalog
from nova.gpu.classifier import SignalClassifier
from nova.gpu.perf_counters import WindowsGpuCounters
from nova.gpu.processes import ProcessReadError
from nova.gpu.windows_probe import WindowsProcessProbe


class WindowsSignalSource:
    def __init__(
        self, *, catalog=None, probe=None, counters=None, classifier=None, clock=time.monotonic
    ):
        self.catalog = catalog if catalog is not None else GameCatalog()
        self.probe = probe
        self.counters = counters
        self.classifier = classifier if classifier is not None else SignalClassifier()
        self.clock = clock
        self._lock = asyncio.Lock()
        self._last_sample = None
        self._last_refresh = None
        self._snapshot = None
        self._classification = None
        self._closed = False
        self.details = {"strategy": "automatic", "state": "starting"}

    def _sample(self):
        started = time.perf_counter()
        now = self.clock()
        if self._last_refresh is None or now - self._last_refresh >= 300:
            self.catalog.refresh()
            self._last_refresh = now
        if self.probe is None:
            self.probe = WindowsProcessProbe()
        if self.counters is None:
            self.counters = WindowsGpuCounters()
        snapshot = self.probe.sample(self.catalog)
        snapshot["gpu"] = self.counters.sample()
        snapshot["duration_ms"] = round((time.perf_counter() - started) * 1000, 3)
        return snapshot

    async def observe(self):
        async with self._lock:
            if self._closed:
                raise ProcessReadError("自动采集已关闭")
            now = self.clock()
            # 两个5秒轮询器共享同一批数据，不重复累计分类置信度。
            if self._snapshot is not None and now - self._last_sample < 2:
                return self._snapshot
            try:
                snapshot = await asyncio.to_thread(self._sample)
                classified = self.classifier.evaluate(snapshot, self.catalog)
            except Exception:
                self.details = {
                    "strategy": "automatic",
                    "state": "unavailable",
                    "error": "WINDOWS_SIGNALS_UNAVAILABLE",
                }
                raise ProcessReadError("无法可靠采集自动游戏信号") from None
            self._snapshot = snapshot
            self._classification = classified
            self._last_sample = self.clock()
            self.details = {
                "strategy": "automatic",
                "state": "sampled",
                "catalog": self.catalog.summary,
                "confirmed": classified.get("confirmed", []),
                "candidates": classified.get("candidates", []),
                "degraded": classified.get("degraded", []),
                "gpu_counters_available": snapshot["gpu"].get("available", False),
                "gpu_counters_warming_up": snapshot["gpu"].get("warming_up", False),
                "sample_duration_ms": snapshot["duration_ms"],
                "process_count": len(snapshot.get("processes", [])),
                "presentmon": {"enabled": False, "state": "not_enabled"},
            }
            return snapshot

    async def scan(self):
        await self.observe()
        if not self._classification.get("state_ready", True):
            raise ProcessReadError("自动检测快照尚未可靠确认")
        return set(self._classification["matched_processes"])

    async def close(self):
        self._closed = True
        async with self._lock:
            if self.counters is not None:
                await asyncio.to_thread(self.counters.close)
