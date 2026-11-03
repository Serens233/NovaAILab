"""先关闭请求入口，再排空并验证卸载；不操作训练进程或数据库。"""

import asyncio
import hashlib
import json
import logging
import math
import os
import tempfile
import time
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

from nova.errors import ApplicationError
from nova.gpu.backend import (
    BackendError,
    CancelledBackendError,
    ModelNotInstalledError,
    OllamaBackend,
    model_allocation,
)
from nova.gpu.protocol import ModelBackend
from nova.gpu.resources import get_system_memory, require_system_memory
from nova.model_policy import is_qwen38_alias

logger = logging.getLogger("nova.gpu")


class InstanceLease:
    """同用户、同 Ollama 地址只允许一个调度进程；OS 在进程退出时释放锁。"""

    def __init__(self, identity):
        name = hashlib.sha256(identity.encode()).hexdigest()[:24]
        self.path = Path(tempfile.gettempdir()) / f"nova-gpu-{name}.lock"
        self.file = None

    def acquire(self):
        self.file = self.path.open("a+b")
        self.file.seek(0, 2)
        if self.file.tell() == 0:
            self.file.write(b"0")
            self.file.flush()
        self.file.seek(0)
        try:
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(self.file.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(self.file, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            self.file.close()
            self.file = None
            raise RuntimeError("该 Ollama 服务已有 GPU 调度实例；请使用单 worker") from None

    def release(self):
        if self.file:
            self.file.close()
            self.file = None


class GpuManager:
    def __init__(
        self,
        backend: ModelBackend,
        state_path: Path,
        drain_timeout=30.0,
        unload_timeout=15.0,
        *,
        detection_enabled=False,
        pressure_enabled=False,
        model_policies=None,
        experimental_policy=None,
    ):
        self.backend = backend
        self.model_policies = dict(model_policies or {})
        # 注册表模板让尚未被发现的27B标签也继承同一门禁，不能因查不到键而放行。
        self.experimental_policy = (
            dict(experimental_policy) if isinstance(experimental_policy, dict) else None
        )
        self.state_path = Path(state_path)
        self.drain_timeout = drain_timeout
        self.unload_timeout = unload_timeout
        self.mode = "AUTO"
        self.detection_enabled = detection_enabled
        self.manual_gaming = False
        self.detected_gaming = detection_enabled
        self.saved_detected_gaming = False
        self.pressure_enabled = pressure_enabled
        self.memory_pressure_hold = False
        self.memory_pressure_reserve_bytes = 0
        self._pressure_unload_attempted = False
        self.memory_pressure = {
            "enabled": pressure_enabled,
            "active": False,
            "state": "starting" if pressure_enabled else "disabled",
        }
        self._detector_action_attempted = False
        self.game_detection = {
            "enabled": detection_enabled,
            "state": "starting" if detection_enabled else "disabled",
            "ready": not detection_enabled,
            "matched_processes": [],
            "error": None,
        }
        self.fault = None
        self.training_lease = None
        self.last_unload_at = None
        self.last_operation = None
        self.unload_verified = False
        self._active = set()
        self._preparing = set()
        self._last_request_finished = time.monotonic()
        self._idle_check = asyncio.Lock()
        self._controls = set()
        # 取消或排空超时后，卸载责任必须显式登记，不能靠“控制锁被占用”推断。
        self._reclaims = {}
        # 已进入原卸载流程的回收可能持有to_thread HTTP，不能通过取消Future结束。
        self._reclaim_io = set()
        self._idle = asyncio.Event()
        self._idle.set()
        self._control = asyncio.Lock()
        self._suspended = False
        self._closing = False
        self._pending_controls = 0
        self._lease = InstanceLease(getattr(backend, "base_url", str(self.state_path)))

    async def start(self):
        self._lease.acquire()
        try:
            if self.state_path.exists():
                state = json.loads(self.state_path.read_text(encoding="utf-8"))
                if state["mode"] not in {"AUTO", "GAMING"}:
                    raise ValueError("mode")
                self.mode = state["mode"]
                manual = state.get("manual_gaming", self.mode == "GAMING")
                detected = state.get("detected_gaming", False)
                if not isinstance(manual, bool) or not isinstance(detected, bool):
                    raise ValueError("gaming source")
                self.manual_gaming = manual
                self.saved_detected_gaming = detected
                pressure = state.get("memory_pressure_hold", False)
                reserve = state.get("memory_pressure_reserve_bytes", 0)
                if not isinstance(pressure, bool) or type(reserve) is not int or reserve < 0:
                    raise ValueError("pressure state")
                self.memory_pressure_hold = pressure and self.pressure_enabled
                self.memory_pressure_reserve_bytes = reserve
                if pressure and not self.pressure_enabled:
                    self.manual_gaming = True
                if detected and not self.detection_enabled:
                    # 关闭扫描不能成为绕过上次游戏保护的方式；改由手动确认退出。
                    self.manual_gaming = True
                self.last_unload_at = state.get("last_unload_at")
                training = state.get("training_lease")
                if training is not None:
                    if not isinstance(training, dict) or any(
                        not isinstance(training.get(key), str) for key in ("id", "job_id")
                    ):
                        raise ValueError("training lease")
                    UUID(training["id"])
                    UUID(training["job_id"])
                    if (
                        type(training["required_mib"]) is not int
                        or not 1 <= training["required_mib"] <= 65536
                    ):
                        raise ValueError("training lease")
                    self.training_lease = {
                        "id": training["id"],
                        "job_id": training["job_id"],
                        "required_mib": training["required_mib"],
                        "state": "unknown_after_restart",
                    }
                if state.get("uncertain") or state.get("fault"):
                    self.mode = "GAMING"
                    self.fault = "PREVIOUS_OPERATION_UNCERTAIN"
            if self.manual_gaming or self.detected_gaming:
                self.mode = "GAMING"
            self._persist()
        except ValueError, KeyError, TypeError, OSError:
            self.mode = "GAMING"
            self.fault = "STATE_UNAVAILABLE"
            # 状态不可读时仅保守阻止推理，保留原文件供排错。

    def _persist(self, *, uncertain=None):
        state = {
            "mode": self.mode,
            "fault": self.fault,
            "uncertain": bool(self._active) if uncertain is None else uncertain,
            "last_unload_at": self.last_unload_at,
            "manual_gaming": self.manual_gaming,
            "detected_gaming": self.detected_gaming,
            "memory_pressure_hold": self.memory_pressure_hold,
            "memory_pressure_reserve_bytes": self.memory_pressure_reserve_bytes,
            "training_lease": self.training_lease,
        }
        temporary = self.state_path.with_suffix(".pending")
        try:
            self.state_path.parent.mkdir(parents=True, exist_ok=True)
            with temporary.open("w", encoding="utf-8") as output:
                json.dump(state, output)
                output.flush()
                os.fsync(output.fileno())
            temporary.replace(self.state_path)
        except OSError:
            self.mode = "GAMING"
            self.fault = "STATE_WRITE_FAILED"
            raise ApplicationError(
                "GPU_STATE_FAILED", "无法保存 GPU 安全状态，本地推理已锁定", 503
            ) from None

    @property
    def blocked(self):
        return self._safety_blocked or self.training_lease is not None

    @property
    def _safety_blocked(self):
        return (
            self.mode == "GAMING"
            or self.manual_gaming
            or self.detected_gaming
            or self.memory_pressure_hold
            or self.fault is not None
            or self._suspended
            or self._closing
            or self._pending_controls > 0
        )

    def _admit(self, *, preparing_task=None, training_lease_id=None):
        if self.memory_pressure_hold and self.mode == "AUTO" and not self.fault:
            raise ApplicationError(
                "GPU_MEMORY_PRESSURE", "显存压力保护生效，等待其他应用释放显存", 423
            )
        if self._safety_blocked:
            raise ApplicationError(
                "GPU_GAMING", "本地模型暂不可用；游戏模式或安全锁定生效，云端未启用", 423
            )
        if self.training_lease and self.training_lease["id"] != training_lease_id:
            raise ApplicationError(
                "GPU_TRAINING_ACTIVE", "训练资源已预留，请安全结束训练并释放租约", 409
            )
        if self._active - ({preparing_task} if preparing_task is not None else set()):
            raise ApplicationError("GPU_BUSY", "已有本地模型请求，请等待完成", 409)

    def update_model_policies(self, policies):
        """仅由服务端注册表装配；发现的别名必须继承完整实验门禁。"""
        self.model_policies.update(policies)

    async def _model_policy(self, model):
        """所有加载入口必须具有显式资源策略，安装或命名本身不构成准入。"""
        if not isinstance(model, str) or not model.strip():
            raise ApplicationError("MODEL_POLICY_INVALID", "模型标识未匹配受控资源策略", 503)
        folded = model.casefold()
        matches = [
            policy
            for name, policy in self.model_policies.items()
            if (isinstance(name, str) and name.casefold() == folded)
            or (
                isinstance(policy, dict)
                and isinstance(policy.get("model"), str)
                and policy["model"].casefold() == folded
            )
        ]
        if not matches:
            template = self.experimental_policy
            if isinstance(template, dict) and is_qwen38_alias(model, template.get("model")):
                # 实际量化标签/HF标识尚未被注册表快照收录时，仍使用同一模板。
                policy = {
                    **template,
                    "model": model,
                    "runtime": dict(template.get("runtime") or {}),
                }
            else:
                raise ApplicationError(
                    "MODEL_POLICY_INVALID", "模型尚未登记资源策略，暂不允许加载", 503
                )
        else:
            policy = matches[0]
        # Ollama可能规范化大小写；冲突的重复策略不能靠精确键优先绕过限制。
        if any(candidate != policy for candidate in matches[1:]):
            raise ApplicationError("MODEL_POLICY_INVALID", "实验模型资源配置冲突", 503)
        if not isinstance(policy, dict) or policy.get("enabled") is not True:
            raise ApplicationError("MODEL_EXPERIMENT_DISABLED", "实验模型未启用，请选择9B或4B", 503)
        try:
            for key in ("required_mib", "min_total_ram_mib", "min_available_ram_mib"):
                # 稳定模型没有实验RAM门槛，0表示不限制；显存需求仍必须为正。
                minimum = 1 if key == "required_mib" else 0
                if type(policy[key]) is not int or policy[key] < minimum:
                    raise ValueError
            runtime = dict(policy["runtime"])
            OllamaBackend.validate_runtime(runtime)
        except KeyError, TypeError, ValueError, BackendError:
            raise ApplicationError("MODEL_POLICY_INVALID", "实验模型资源配置无效", 503) from None
        require_system_memory(policy["min_total_ram_mib"], policy["min_available_ram_mib"])
        return {**policy, "runtime": runtime}

    @staticmethod
    def _check_gpu_memory(memory, models, model, required_mib):
        gpus = memory.get("gpus", []) if isinstance(memory, dict) else []
        valid = [
            gpu
            for gpu in gpus
            if isinstance(gpu, dict)
            and type(gpu.get("index")) is int
            and type(gpu.get("total_mib")) is int
            and type(gpu.get("used_mib")) is int
            and gpu["index"] >= 0
            and 0 <= gpu["used_mib"] <= gpu["total_mib"]
        ]
        if not isinstance(memory, dict) or not memory.get("available") or not valid:
            raise ApplicationError("GPU_MEMORY_UNAVAILABLE", "无法核实显存，暂不加载本地模型", 503)
        gpu = min(valid, key=lambda item: item["index"])
        resident = any(item["name"] == model and item.get("size_vram", 0) > 0 for item in models)
        needed = 512 if resident else required_mib
        if gpu["total_mib"] - gpu["used_mib"] < needed:
            raise ApplicationError(
                "GPU_MEMORY_INSUFFICIENT", "当前显存不足，请释放其他负载或切换4B", 409
            )

    def _audit(self):
        # 只记录运行元数据；不保存 prompt、response 或认证头。
        try:
            with (self.state_path.parent / "gpu-events.jsonl").open(
                "a", encoding="utf-8"
            ) as output:
                output.write(json.dumps(self.last_operation, ensure_ascii=False) + "\n")
        except OSError:
            self.mode = "GAMING"
            self.fault = "OPERATION_RECORD_UNAVAILABLE"
            raise ApplicationError(
                "GPU_AUDIT_FAILED", "GPU 运行记录无法保存，已停止本地调度", 503
            ) from None

    async def status(self):
        try:
            models = await self.backend.get_model_status()
            backend = {"available": True, "models": models}
        except BackendError:
            backend = {"available": False, "models": None, "error": "OLLAMA_UNAVAILABLE"}
        return {
            "mode": self.mode,
            "mode_source": (
                "manual"
                if self.manual_gaming
                else "safety"
                if self.fault
                else "game_detection"
                if self.detected_gaming
                else "memory_pressure"
                if self.memory_pressure_hold
                else "auto"
            ),
            "manual_gaming": self.manual_gaming,
            "detected_gaming": self.detected_gaming,
            "local_requests_blocked": self.blocked,
            "active_requests": len(self._active),
            "pending_unloads": len(self._reclaims),
            "unload_verified": self.unload_verified
            and backend["available"]
            and not backend["models"],
            "last_unload_at": self.last_unload_at,
            "last_operation": self.last_operation,
            "backend": backend,
            "gpu_memory": await self.backend.get_gpu_memory_usage(),
            "system_memory": get_system_memory(),
            "model_allocation": [model_allocation(item) for item in backend.get("models") or []],
            "fault": self.fault,
            "training": {
                "state": self.training_lease["state"] if self.training_lease else "idle",
                "lease": dict(self.training_lease) if self.training_lease else None,
                "safe_pause_supported": False,
                "scope": "cooperative_registered_jobs_only",
                "game_conflict": bool(self.training_lease and self.mode == "GAMING"),
            },
            "game_detection": dict(self.game_detection),
            "memory_pressure": dict(self.memory_pressure),
            "cloud_fallback": {"enabled": False},
        }

    async def _worker(self, action, prompt=None):
        task = asyncio.current_task()
        try:
            target = getattr(self.backend, "model", None)
            policy = await self._model_policy(target)
            # 一次只允许一个受管模型；外部模型不在这里擅自驱逐。
            models = await self.backend.get_model_status()
            self._admit(preparing_task=task)
            await self._model_policy(target)
            if any(item["name"] != target for item in models):
                raise ApplicationError("GPU_MODEL_CONFLICT", "已有其他模型驻留，请先显式卸载", 409)
            before = await self.backend.get_gpu_memory_usage()
            self._admit(preparing_task=task)
            policy = await self._model_policy(target)
            self._check_gpu_memory(before, models, target, policy["required_mib"])
            runtime = policy["runtime"]
            kwargs = {} if not runtime else {"runtime": runtime}
            result = await (
                self.backend.wake_model(**kwargs)
                if action == "wake"
                else self.backend.generate(prompt, **kwargs)
            )
            self.last_operation = {
                "operation": action,
                "at": datetime.now(UTC).isoformat(),
                "load_duration_ns": result.get("load_duration"),
                "eval_duration_ns": result.get("eval_duration"),
                "tokens_per_second": result.get("tokens_per_second"),
                "gpu_before": before,
                "gpu_after": await self.backend.get_gpu_memory_usage(),
            }
            logger.info("gpu_model_request_completed", extra={"operation": action})
            self._audit()
            return result
        except ModelNotInstalledError:
            # 模型缺失是明确的准入失败，不是上游不确定故障，不能因此永久锁定。
            raise ApplicationError(
                "MODEL_NOT_INSTALLED", "模型尚未下载，不会自动下载", 503
            ) from None
        except ApplicationError:
            raise
        except Exception, asyncio.CancelledError:
            self.mode = "GAMING"
            self.fault = "UPSTREAM_OPERATION_UNCERTAIN"
            raise ApplicationError(
                "GPU_UPSTREAM_FAILED", "本地模型请求未确认完成，已锁定；请卸载复核后恢复", 503
            ) from None
        finally:
            self._active.discard(task)
            self._last_request_finished = time.monotonic()
            if not self._active:
                self._idle.set()
            self._persist()

    async def _request(self, action, prompt=None):
        self._admit()
        await self._model_policy(getattr(self.backend, "model", None))
        self.unload_verified = False
        # 先落盘“进行中”；进程异常退出后的下一次启动不能默认为安全 AUTO。
        self._persist(uncertain=True)
        task = asyncio.create_task(self._worker(action, prompt))
        self._active.add(task)
        self._idle.clear()
        # 客户端取消不会取消后台 HTTP 线程，也不能提前宣称没有活动请求。
        task.add_done_callback(lambda t: t.exception() if not t.cancelled() else None)
        return await asyncio.shield(task)

    async def generate(self, prompt):
        return await self._request("generate", prompt)

    async def wake(self):
        return await self._request("wake")

    async def open_chat_stream(self, model, messages, required_mib, *, think=None, control=None):
        """返回(queue,task)；预检失败在返回前抛出，适合HTTP响应头前的门禁。

        队列为delta/done/error事件。task成功结果是
        {model,response,done:True,stats:{...}}，正文只供聊天存储，不写GPU审计。
        消费方不能取消task来假装中止GPU；断开只应停止读取队列。
        """
        self._admit()
        if control is not None and control.requested:
            raise ApplicationError("CHAT_CANCELLED", "生成已取消", 409)
        if think is not None and type(think) is not bool:
            raise ApplicationError("CHAT_THINK_INVALID", "思考模式必须为布尔值", 422)
        policy = await self._model_policy(model)
        required_mib = policy["required_mib"]
        if type(required_mib) is not int or not 1 <= required_mib <= 65536:
            raise ApplicationError("GPU_MEMORY_REQUIREMENT", "模型显存配置无效", 422)
        try:
            messages = OllamaBackend.validate_chat_messages(messages)
        except BackendError:
            raise ApplicationError("GPU_CHAT_INVALID", "聊天上下文无效或过长", 422) from None
        loop = asyncio.get_running_loop()
        ready = loop.create_future()
        queue = asyncio.Queue()
        self.unload_verified = False
        self._persist(uncertain=True)
        # create_task到登记之间没有await，所有预检都受同一活动请求保护。
        task = asyncio.create_task(
            self._chat_worker(model, messages, required_mib, queue, ready, think, control)
        )
        self._active.add(task)
        self._preparing.add(task)
        self._idle.clear()
        task.add_done_callback(lambda t: t.exception() if not t.cancelled() else None)
        ready.add_done_callback(lambda f: f.exception() if not f.cancelled() else None)
        await asyncio.shield(ready)
        return queue, task

    async def _prepare_chat(self, model, required_mib):
        task = asyncio.current_task()
        policy = await self._model_policy(model)
        required_mib = policy["required_mib"]
        await self.backend.ensure_local_model(model)
        self._admit(preparing_task=task)
        await self._model_policy(model)
        models = await self.backend.get_model_status()
        self._admit(preparing_task=task)
        if any(item["name"] != model for item in models):
            await self._model_policy(model)
            # 游戏卸载可能正在等此任务结束，不能反过来排队等待其控制锁。
            if self._control.locked():
                raise ApplicationError("GPU_TRANSITION_BUSY", "模型控制正忙，请稍后重试", 409)
            async with self._control:
                self._admit(preparing_task=task)
                await self._model_policy(model)
                await self._unload("model_switch", preparing_task=task)
            self._admit(preparing_task=task)
            models = await self.backend.get_model_status()
            if any(item["name"] != model for item in models):
                raise ApplicationError("GPU_MODEL_CONFLICT", "模型切换未完成，请重试", 409)
        memory = await self.backend.get_gpu_memory_usage()
        self._admit(preparing_task=task)
        await self._model_policy(model)
        # 同一NVIDIA设备口径，不将多卡余量相加。
        self._check_gpu_memory(memory, models, model, required_mib)
        return memory

    async def _chat_worker(
        self, model, messages, required_mib, queue, ready, think=None, control=None
    ):
        task = asyncio.current_task()
        started = False
        result = None
        error = None
        try:
            before = await self._prepare_chat(model, required_mib)
            self._admit(preparing_task=task)
            if control is not None and control.requested:
                raise ApplicationError("CHAT_CANCELLED", "生成已取消", 409)
            policy = await self._model_policy(model)
            self._preparing.discard(task)
            ready.set_result(None)
            started = True
            runtime = policy["runtime"]
            kwargs = {} if not runtime else {"runtime": runtime}
            if think is not None:
                kwargs["runtime"] = {**kwargs.get("runtime", {}), "think": think}
            if control is not None and getattr(self.backend, "supports_stream_control", False):
                kwargs["control"] = control
            ram_before = get_system_memory()
            result = await self.backend.stream_chat(
                model,
                messages,
                lambda text: queue.put_nowait({"type": "delta", "text": text}),
                **kwargs,
            )
            if control is not None and control.requested:
                raise CancelledBackendError()
            self.last_operation = {
                "operation": "chat",
                "model": model,
                "at": datetime.now(UTC).isoformat(),
                "load_duration_ns": result["stats"].get("load_duration"),
                "eval_duration_ns": result["stats"].get("eval_duration"),
                "tokens_per_second": result["stats"].get("tokens_per_second"),
                "first_token_latency_ms": result["stats"].get("first_token_latency_ms"),
                "first_activity_latency_ms": result["stats"].get("first_activity_latency_ms"),
                "request_duration_ms": result["stats"].get("request_duration_ms"),
                "system_memory_before": ram_before,
                "system_memory_after": get_system_memory(),
                "model_allocation": [
                    model_allocation(item) for item in await self.backend.get_model_status()
                ],
                "gpu_before": before,
                "gpu_after": await self.backend.get_gpu_memory_usage(),
            }
            self._audit()
        except ModelNotInstalledError:
            # 与注册表声明一致：缺失模型使用 MODEL_NOT_INSTALLED，且不写安全故障。
            error = ApplicationError("MODEL_NOT_INSTALLED", "模型尚未下载，不会自动下载", 503)
        except CancelledBackendError:
            # HTTP线程已结束，但GPU释放仍需原卸载机制确认；不能仅凭断开连接放行。
            self.unload_verified = False
            try:
                if self._control.locked() or self._pending_controls:
                    # 控制锁可能属于 AUTO 状态查询，不能假定持有者会卸载；
                    # 显式登记回收任务，待锁释放且本任务退出后执行原卸载与复核。
                    self.fault = "CANCELLED_AWAITING_UNLOAD"
                    self._persist()
                    self._schedule_reclaim("task_cancelled")
                else:
                    async with self._control:
                        self._preparing.add(task)
                        await self._unload("task_cancelled", preparing_task=task)
                error = ApplicationError("CHAT_CANCELLED", "生成已取消，部分回复已标记为中断", 409)
            except ApplicationError as caught:
                error = caught
        except ApplicationError as caught:
            error = caught
        except Exception, asyncio.CancelledError:
            if started:
                self.mode = "GAMING"
                self.fault = "UPSTREAM_OPERATION_UNCERTAIN"
                error = ApplicationError(
                    "GPU_UPSTREAM_FAILED", "本地聊天未确认完成，已锁定；请卸载复核后恢复", 503
                )
            else:
                error = ApplicationError(
                    "GPU_PREFLIGHT_FAILED", "无法确认本地模型状态，请检查服务", 503
                )
        finally:
            self._preparing.discard(task)
            self._active.discard(task)
            self._last_request_finished = time.monotonic()
            if not self._active:
                self._idle.set()
            try:
                self._persist()
            except ApplicationError as caught:
                error = caught
        if error:
            if not ready.done():
                ready.set_exception(error)
            queue.put_nowait({"type": "error", "code": error.code, "message": error.public_message})
            raise error
        queue.put_nowait({"type": "done", "model": model, "stats": result["stats"]})
        return result

    async def idle_tick(self, now=None, idle_seconds=180):
        """由lifespan周期调用；空闲卸载复用原流程，不设置人工游戏锁。"""
        now = time.monotonic() if now is None else now
        if (
            isinstance(now, bool)
            or not isinstance(now, (int, float))
            or not math.isfinite(now)
            or isinstance(idle_seconds, bool)
            or not isinstance(idle_seconds, (int, float))
            or not math.isfinite(idle_seconds)
            or idle_seconds <= 0
        ):
            raise ValueError("空闲检查时间无效")
        if self.blocked or self._active or self._idle_check.locked():
            return {"unloaded": False}
        async with self._idle_check:
            observed_finished = self._last_request_finished
            if now - observed_finished < idle_seconds:
                return {"unloaded": False}
            models = await self.backend.get_model_status()
            # 查询期间可能有新请求或GAMING，必须重新核对。
            if (
                self.blocked
                or self._active
                or self._last_request_finished != observed_finished
                or (not models and self.unload_verified)
            ):
                return {"unloaded": False}
            result = await self._control_operation(
                gaming=False, idle_only=True, reason="idle_timeout"
            )
            return {"unloaded": not result.get("deferred", False)}

    def _cancel_reclaims(self, *, exclude=None):
        """成功卸载后撤销其他待办；当前执行卸载的任务不能取消自己。"""
        for task in tuple(self._reclaims):
            if task is not exclude and task not in self._reclaim_io and not task.done():
                task.cancel()

    def _schedule_reclaim(self, reason):
        """显式登记谁负责卸载；同一原因只保留一个重试链。"""
        for task, existing in self._reclaims.items():
            if existing == reason and not task.done():
                return task
        if self._closing:
            return None
        task = asyncio.create_task(self._reclaim(reason))
        self._reclaims[task] = reason

        def finished(done):
            self._reclaims.pop(done, None)
            if done.cancelled():
                return
            try:
                done.result()
            except Exception:
                # 失败原因已写入 fault；这里只保留后台任务异常的可诊断记录。
                logger.warning("gpu_reclaim_failed", exc_info=True)

        task.add_done_callback(finished)
        return task

    async def _reclaim(self, reason):
        """等待排空后复用原卸载流程；退避重试直到确认或安全关闭。"""
        delay = max(0.05, min(1.0, float(self.drain_timeout)))
        while not self._closing:
            await self._idle.wait()
            if self._closing:
                return
            if self.unload_verified and self.fault is None:
                return
            if reason == "drain_timeout" and not (
                self.manual_gaming or self.detected_gaming or self.mode == "GAMING"
            ):
                return
            try:
                async with self._control:
                    if self.unload_verified and self.fault is None:
                        return
                    if reason == "drain_timeout" and not (
                        self.manual_gaming or self.detected_gaming or self.mode == "GAMING"
                    ):
                        return
                    task = asyncio.current_task()
                    self._reclaim_io.add(task)
                    try:
                        await self._unload(reason, compensate=False)
                    finally:
                        self._reclaim_io.discard(task)
                return
            except asyncio.CancelledError:
                raise
            except ApplicationError, BackendError:
                if self._closing:
                    return
                # 卸载失败时 fault 仍保留；退避避免持续冲击 Ollama 或状态文件。
                await asyncio.sleep(delay)
                delay = min(delay * 2, 30.0)

    async def _unload(self, reason="manual", *, preparing_task=None, compensate=True):
        self.unload_verified = False
        self._suspended = True
        try:
            if preparing_task is not None:
                if preparing_task not in self._preparing or self._active != {preparing_task}:
                    raise ApplicationError("GPU_BUSY", "模型切换正在等待安全空闲", 409)
            else:
                try:
                    await asyncio.wait_for(self._idle.wait(), timeout=self.drain_timeout)
                except TimeoutError:
                    raise ApplicationError(
                        "GPU_DRAIN_TIMEOUT", "已有推理尚未结束；保持游戏模式，未强制中断", 409
                    ) from None
            before = await self.backend.get_gpu_memory_usage()
            models = await self.backend.get_model_status()
            for model in models:
                await self.backend.unload_model(model["name"])
            deadline = asyncio.get_running_loop().time() + self.unload_timeout
            while True:
                remaining = await self.backend.get_model_status()
                if not remaining:
                    break
                if asyncio.get_running_loop().time() >= deadline:
                    raise BackendError("unload_not_verified")
                await asyncio.sleep(0.1)
            after = await self.backend.get_gpu_memory_usage()
            self.unload_verified = True
            self.fault = None
            # 成功的原流程卸载已覆盖其他待办；除当前重试链外不再重复卸载。
            self._cancel_reclaims(exclude=asyncio.current_task())
            self.last_unload_at = datetime.now(UTC).isoformat()
            self.last_operation = {
                "operation": "unload",
                "reason": reason,
                "at": self.last_unload_at,
                "models_before": models,
                "models_after": remaining,
                "gpu_before": before,
                "gpu_after": after,
                "unload_verified": True,
            }
            logger.info("gpu_unload_verified")
            self._audit()
            self._persist()
            return await self.status()
        except (Exception, asyncio.CancelledError) as error:
            self.unload_verified = False
            self.mode = "GAMING"
            self.fault = "UNLOAD_NOT_VERIFIED"
            self._persist()
            if (
                compensate
                and isinstance(error, ApplicationError)
                and error.code == "GPU_DRAIN_TIMEOUT"
            ):
                # 推理结束后不能依赖空闲调度（fault 会让它跳过），必须显式补偿卸载。
                self._schedule_reclaim("drain_timeout")
            logger.warning("gpu_unload_unverified")
            if isinstance(error, ApplicationError):
                raise
            raise ApplicationError(
                "GPU_UNLOAD_FAILED", "无法确认模型已卸载；本地入口保持锁定，请检查状态", 503
            ) from None
        finally:
            self._suspended = False

    async def unload(self):
        # 不论请求者是否中途断开，管理操作都执行至明确成功/失败。
        self.manual_gaming = True
        self.mode = "GAMING"
        self._persist()
        return await self._control_operation()

    async def _control_operation(self, *, gaming=True, idle_only=False, reason="manual_or_game"):
        self._pending_controls += 1

        async def run():
            try:
                async with self._control:
                    if idle_only and self._active:
                        return {"deferred": True}
                    if gaming:
                        self.mode = "GAMING"
                    self._persist()
                    return await self._unload(reason)
            finally:
                self._pending_controls -= 1

        task = asyncio.create_task(run())
        self._controls.add(task)

        def finished(task):
            self._controls.discard(task)
            if not task.cancelled():
                task.exception()

        task.add_done_callback(finished)
        return await asyncio.shield(task)

    async def set_mode(self, mode):
        if mode == "GAMING":
            self.manual_gaming = True
            self.mode = "GAMING"
            self._persist()
            return await self._control_operation()
        if mode != "AUTO":
            raise ApplicationError("GPU_MODE_UNSUPPORTED", "本轮仅支持 AUTO 与 GAMING", 422)
        if self.detected_gaming:
            raise ApplicationError(
                "GPU_GAME_DETECTED", "游戏检测保护生效，等待游戏退出并完成确认窗口", 409
            )
        if self.memory_pressure_hold:
            raise ApplicationError("GPU_PRESSURE_ACTIVE", "显存压力保护尚未解除", 409)
        if self._pending_controls:
            raise ApplicationError(
                "GPU_TRANSITION_BUSY", "游戏模式切换尚在处理，请等待卸载结果", 409
            )
        async with self._control:
            if self.memory_pressure_hold:
                raise ApplicationError("GPU_PRESSURE_ACTIVE", "显存压力保护尚未解除", 409)
            if self.detected_gaming:
                raise ApplicationError("GPU_GAME_DETECTED", "游戏检测保护生效，不能手动绕过", 409)
            if self._pending_controls:
                raise ApplicationError(
                    "GPU_TRANSITION_BUSY", "游戏模式切换尚在处理，请等待卸载结果", 409
                )
            if self.fault or self._active:
                raise ApplicationError(
                    "GPU_RECOVERY_REQUIRED", "请先等待推理结束并显式卸载复核，再恢复 AUTO", 409
                )
            self.mode = "AUTO"
            self.manual_gaming = False
            # 显式退出保护后不再让旧卸载重试链影响后续按需加载。
            self._cancel_reclaims()
            self._persist()
            return await self.status()

    async def set_detected_gaming(self, hold: bool, info: dict):
        """只供扫描器调用；只改变自动来源，不得清理手动锁或故障。"""
        if not self.detection_enabled:
            raise RuntimeError("游戏检测未启用")
        self.game_detection = dict(info)
        if hold:
            first_action = not self._detector_action_attempted
            self.detected_gaming = True
            self.mode = "GAMING"
            if first_action:
                self._detector_action_attempted = True
                self._persist()
                if not self.fault:
                    await self._control_operation(reason="game_detection")
            return
        async with self._control:
            changed = self.detected_gaming
            self.detected_gaming = False
            self._detector_action_attempted = False
            if (
                not self.manual_gaming
                and not self.fault
                and not self._pending_controls
                and not self._active
            ):
                self.mode = "AUTO"
            if changed:
                self._persist()

    async def set_memory_pressure(self, active, info, *, unload_idle=False):
        """独立压力来源；不等待/中断在途推理，不接管外部训练。"""
        self.memory_pressure = dict(info)
        changed = self.memory_pressure_hold != active
        self.memory_pressure_hold = active
        if changed:
            self._persist()
        if not active:
            self._pressure_unload_attempted = False
            self.memory_pressure_reserve_bytes = 0
            if changed:
                self._persist()
            return
        if self._active or not unload_idle or self._pressure_unload_attempted or self.fault:
            return
        self._pressure_unload_attempted = True
        try:
            models = await self.backend.get_model_status()
            self.memory_pressure_reserve_bytes = max(
                self.memory_pressure_reserve_bytes, sum(m.get("size_vram", 0) for m in models)
            )
            self.memory_pressure["model_reserve_bytes"] = self.memory_pressure_reserve_bytes
            self._persist()
            result = await self._control_operation(
                gaming=False, idle_only=True, reason="memory_pressure"
            )
            if result.get("deferred"):
                self._pressure_unload_attempted = False
        except BackendError:
            self.mode = "GAMING"
            self.fault = "PRESSURE_UNLOAD_UNCERTAIN"
            self._persist()
            raise ApplicationError(
                "GPU_PRESSURE_UNLOAD_FAILED", "无法确认空闲模型状态，保留显存保护", 503
            ) from None

    async def claim_training(self, job_id, required_mib):
        """协作任务先申请再启动；不接管、暂停或杀死外部训练进程。"""
        try:
            job_id = str(UUID(job_id))
        except ValueError, TypeError, AttributeError:
            raise ApplicationError(
                "TRAINING_JOB_INVALID", "训练任务必须使用UUID标识", 422
            ) from None
        if type(required_mib) is not int or not 1 <= required_mib <= 65536:
            raise ApplicationError("TRAINING_MEMORY_INVALID", "训练显存需求无效", 422)
        self._admit()
        lease_id = str(uuid4())
        self.training_lease = {
            "id": lease_id,
            "job_id": job_id,
            "required_mib": required_mib,
            "state": "preparing",
        }
        try:
            self._persist()
        except ApplicationError:
            # 首次落盘失败时租约从未持久化，也尚未交付给训练程序；
            # 必须撤销内存中的 preparing 预留，否则调用方拿不到 id 也无法释放。
            if (
                self.training_lease is not None
                and self.training_lease["id"] == lease_id
                and self.training_lease["state"] == "preparing"
            ):
                self.training_lease = None
                try:
                    self._persist()
                except ApplicationError:
                    # 存储仍不可用时保留 STATE_WRITE_FAILED；内存中的租约已撤销。
                    pass
            raise

        async def prepare():
            try:
                async with self._control:
                    self._admit(training_lease_id=lease_id)
                    await self._unload("training_reservation")
                    self._admit(training_lease_id=lease_id)
                    memory = await self.backend.get_gpu_memory_usage()
                    self._admit(training_lease_id=lease_id)
                    self._check_gpu_memory(memory, [], "training", required_mib)
                    self.training_lease["state"] = "reserved"
                    self._persist()
                    return await self.status()
            except BaseException:
                # 仅撤销尚未交付给训练程序的预留；故障/游戏来源保持不变。
                self.training_lease = None
                self._persist()
                raise

        task = asyncio.create_task(prepare())
        self._controls.add(task)

        def finished(task):
            self._controls.discard(task)
            if not task.cancelled():
                task.exception()

        task.add_done_callback(finished)
        return await asyncio.shield(task)

    async def release_training(self, lease_id, *, stopped):
        """调用者确认已安全结束训练后才释放；游戏退出不自动续训。"""
        if stopped is not True:
            raise ApplicationError("TRAINING_STOP_CONFIRMATION", "请先安全保存并结束训练", 409)
        if not self.training_lease or self.training_lease["id"] != lease_id:
            raise ApplicationError("TRAINING_LEASE_MISMATCH", "训练租约不存在或不匹配", 409)
        if self.training_lease["state"] == "preparing":
            raise ApplicationError("TRAINING_PREPARING", "资源预留尚未完成", 409)
        self.training_lease = None
        self._persist()
        return await self.status()

    async def close(self):
        self._closing = True
        reclaims = tuple(self._reclaims)
        for task in reclaims:
            if task not in self._reclaim_io:
                task.cancel()
        # 等待/退避回收可取消；已执行的HTTP卸载和原控制任务一样必须保持跟踪。
        # asyncio.wait超时不会取消其中的任务，尚未结束时也不释放实例租约。
        tasks = self._active | self._controls | set(reclaims)
        pending = set()
        if tasks:
            _, pending = await asyncio.wait(tasks, timeout=self.drain_timeout)
        if pending:
            self.mode = "GAMING"
            self.fault = "SHUTDOWN_OPERATION_UNCERTAIN"
            self._persist(uncertain=True)
        else:
            self._lease.release()
