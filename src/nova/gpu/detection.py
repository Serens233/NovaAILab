"""按进程白名单巡检；只有连续成功的缺席扫描才能解除自动来源的保护。"""

import asyncio
import logging
import time
from contextlib import suppress
from datetime import UTC, datetime

from nova.errors import ApplicationError
from nova.gpu.processes import ProcessReadError, list_process_names, normalize_game_names

logger = logging.getLogger("nova.gpu.detection")


class GameDetector:
    def __init__(
        self,
        manager,
        names,
        *,
        scan=list_process_names,
        clock=time.monotonic,
        interval=5.0,
        exit_grace=60.0,
        automatic=False,
    ):
        self.manager = manager
        self.names = frozenset(normalize_game_names(list(names)))
        self.automatic = automatic
        if not self.names and not automatic:
            raise ValueError("游戏检测必须配置非空EXE白名单")
        self.scan = scan
        self.clock = clock
        self.interval = interval
        self.exit_grace = exit_grace
        self._task = None
        self._stopping = False
        self._poll_lock = asyncio.Lock()
        self._first_scan = True
        self._absent_since = None
        self._grace_required = manager.saved_detected_gaming
        self._status = {
            "enabled": True,
            "state": "starting",
            "ready": False,
            "matched_processes": [],
            "error": None,
            "last_scan_at": None,
            "clear_for_seconds": 0.0,
            "exit_grace_seconds": exit_grace,
            "poll_seconds": interval,
            "last_action_error": None,
            "strategy": "automatic" if automatic else "whitelist",
        }

    def snapshot(self):
        result = {**self._status, "matched_processes": list(self._status["matched_processes"])}
        owner = getattr(self.scan, "__self__", None)
        if self.automatic and owner is not None and hasattr(owner, "details"):
            result["signals"] = dict(owner.details)
        return result

    async def poll_once(self):
        async with self._poll_lock:
            return await self._poll_once()

    async def _poll_once(self):
        if self._stopping:
            return self.snapshot()
        if self._first_scan:
            # 构造后才载入模式文件也必须重新等待完整退出窗口。
            self._grace_required |= self.manager.saved_detected_gaming
            self._first_scan = False
        try:
            processes = await self.scan()
            if not isinstance(processes, set) or any(not isinstance(x, str) for x in processes):
                raise ProcessReadError("无效进程快照")
        except Exception:
            if self._stopping:
                return self.snapshot()
            self._absent_since = None
            self._grace_required = True
            self._status.update(
                state="scan_failed",
                ready=False,
                error="PROCESS_SCAN_FAILED",
                matched_processes=[],
                clear_for_seconds=0.0,
            )
            hold = True
        else:
            if self._stopping:
                return self.snapshot()
            normalized = {name.casefold() for name in processes}
            matched = sorted(normalized if self.automatic else self.names.intersection(normalized))
            self._status.update(ready=True, error=None, matched_processes=matched)
            if matched:
                self._absent_since = None
                self._grace_required = True
                self._status.update(state="game_detected", clear_for_seconds=0.0)
                hold = True
            elif self._grace_required:
                now = self.clock()
                if self._absent_since is None:
                    self._absent_since = now
                clear = max(0.0, now - self._absent_since)
                self._status.update(state="exit_grace", clear_for_seconds=round(clear, 3))
                hold = clear < self.exit_grace
                if not hold:
                    self._grace_required = False
                    self._status["state"] = "monitoring"
            else:
                hold = False
                self._status.update(state="monitoring", clear_for_seconds=0.0)
        self._status["last_scan_at"] = datetime.now(UTC).isoformat()
        try:
            await self.manager.set_detected_gaming(hold, self.snapshot())
            if not hold:
                self._status["last_action_error"] = None
                self.manager.game_detection = self.snapshot()
        except ApplicationError as error:
            self._status["last_action_error"] = error.code
            self.manager.game_detection = self.snapshot()
        return self.snapshot()

    async def _run(self):
        while not self._stopping:
            await asyncio.sleep(self.interval)
            try:
                await self.poll_once()
            except Exception:
                # 除读进程失败外，循环自身故障也不能让入口默认为可用。
                self._absent_since = None
                self._grace_required = True
                self._status.update(
                    state="scan_failed",
                    ready=False,
                    error="DETECTION_LOOP_FAILED",
                    clear_for_seconds=0.0,
                )
                logger.warning("game_detection_loop_failed")
                try:
                    await self.manager.set_detected_gaming(True, self.snapshot())
                except ApplicationError:
                    # 管理器写盘/卸载失败已经保持GAMING，下一轮不反复卸载。
                    pass

    async def start(self):
        await self.poll_once()
        self._task = asyncio.create_task(self._run())

    async def stop(self):
        self._stopping = True
        if self._task:
            self._task.cancel()
            with suppress(asyncio.CancelledError):
                await self._task
