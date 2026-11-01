"""显存压力独立于“是否游戏”：多信号、连续确认，只卸载空闲推理模型。"""

import asyncio
import os
import time
from contextlib import suppress

from nova.errors import ApplicationError

MIB = 1024 * 1024
_INFERENCE = {"ollama.exe", "llama-server.exe", "llama-cli.exe", "vllm.exe"}


def _number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and value >= 0


class MemoryPressurePolicy:
    def __init__(self, *, clock=time.monotonic, own_pid=None, active=False, clear_delay=20):
        self.clock = clock
        self.own_pid = os.getpid() if own_pid is None else own_pid
        self.active = active
        self.clear_delay = clear_delay
        self.reserve_bytes = 0
        self._high_samples = 0
        self._clear_since = None

    def evaluate(self, snapshot):
        gpu = snapshot.get("gpu", {})
        result = {
            "enabled": True,
            "active": self.active,
            "state": "unavailable",
            "reasons": [],
            "adapters": [],
            "model_reserve_bytes": self.reserve_bytes,
            "clear_for_seconds": 0.0,
            "available": False,
        }
        if not snapshot.get("available") or not gpu.get("available") or gpu.get("warming_up"):
            self._high_samples = 0
            self._clear_since = None
            result["reasons"] = ["COUNTERS_NOT_READY"]
            return result
        records = snapshot.get("processes", [])
        own = {self.own_pid}
        while True:
            extended = own | {p["pid"] for p in records if p.get("ppid") in own}
            if extended == own:
                break
            own = extended
        names = {p["pid"]: p.get("name", "").casefold() for p in records}
        any_high = False
        all_clear = True
        for adapter in gpu.get("adapters", []):
            if adapter.get("vendor_id") != 0x10DE or adapter.get("software"):
                continue
            total, used = adapter.get("total_dedicated_bytes"), adapter.get("dedicated_used_bytes")
            if not _number(total) or total <= 0 or not _number(used) or used > total:
                continue
            external = 0
            active_pids = []
            for pid, process in gpu.get("processes", {}).items():
                pid = int(pid)
                if pid in own or names.get(pid) in _INFERENCE or pid not in names:
                    continue
                node = process.get("per_adapter", {}).get(adapter["luid"], {})
                memory = node.get("dedicated_bytes")
                if not _number(memory):
                    continue
                external += memory
                engines = node.get("engines", {})
                if memory >= 64 * MIB and any(_number(v) and v >= 1 for v in engines.values()):
                    active_pids.append(pid)
            free = max(0, total - used)
            # 余量只是其中一项，还要求其他PID真实显存需求、引擎活动和连续样本。
            high = (
                free < max(512 * MIB, total * 0.08)
                and external >= max(256 * MIB, total * 0.02)
                and bool(active_pids)
            )
            any_high |= high
            # 卸载后留出原模型再次加载的空间，避免刚释放就重载的循环。
            all_clear &= free - self.reserve_bytes >= max(1024 * MIB, total * 0.15)
            result["adapters"].append(
                {
                    "luid": adapter["luid"],
                    "total_bytes": total,
                    "used_bytes": used,
                    "free_bytes": free,
                    "external_bytes": external,
                    "external_active_pids": active_pids,
                    "probe_budget_bytes": adapter.get("probe_budget_bytes"),
                    "budget_scope": adapter.get("budget_scope", "unknown"),
                    "global_budget_bytes": None,
                }
            )
        if not result["adapters"]:
            self._high_samples = 0
            self._clear_since = None
            result["reasons"] = ["NO_RELIABLE_NVIDIA_MEMORY_SAMPLE"]
            return result
        result["available"] = True
        if any_high:
            self._high_samples += 1
            self._clear_since = None
            if self._high_samples >= 2:
                self.active = True
            result["state"] = "pressure" if self.active else "confirming"
            result["reasons"] = [
                "LOW_PHYSICAL_HEADROOM",
                "EXTERNAL_VRAM_DEMAND",
                "EXTERNAL_ENGINE_ACTIVITY",
            ]
        else:
            self._high_samples = 0
            result["state"] = "normal"
            if self.active:
                if all_clear:
                    if self._clear_since is None:
                        self._clear_since = self.clock()
                    clear = max(0, self.clock() - self._clear_since)
                    result["clear_for_seconds"] = round(clear, 3)
                    result["state"] = "clear_pending"
                    if clear >= self.clear_delay:
                        self.active = False
                        result["state"] = "normal"
                else:
                    self._clear_since = None
                    result["state"] = "reserved_headroom_required"
                    result["reasons"] = ["KEEP_ROOM_FOR_IDLE_MODEL_RELOAD"]
        result["active"] = self.active
        return result


class MemoryPressureGuard:
    def __init__(self, manager, source, *, interval=5, policy=None):
        self.manager = manager
        self.source = source
        self.interval = interval
        self.policy = policy or MemoryPressurePolicy(active=manager.memory_pressure_hold)
        self._task = None
        self._stopping = False

    async def poll_once(self):
        try:
            snapshot = await self.source.observe()
        except Exception:
            snapshot = {"available": False}
        if self._stopping:
            return
        self.policy.reserve_bytes = self.manager.memory_pressure_reserve_bytes
        info = self.policy.evaluate(snapshot)
        try:
            await self.manager.set_memory_pressure(
                info["active"], info, unload_idle=info["available"] and info["active"]
            )
        except ApplicationError as error:
            info["last_action_error"] = error.code
            self.manager.memory_pressure = info

    async def _run(self):
        while not self._stopping:
            await asyncio.sleep(self.interval)
            await self.poll_once()

    async def start(self):
        await self.poll_once()
        self._task = asyncio.create_task(self._run())

    async def stop(self):
        self._stopping = True
        if self._task:
            self._task.cancel()
            with suppress(asyncio.CancelledError):
                await self._task
