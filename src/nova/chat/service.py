"""后端拥有会话历史与生成生命周期；浏览器断开不提前释放GPU或写入假完成。"""

import asyncio
import logging
import time

from nova.chat.cloud import DeepSeekBackend
from nova.chat.orchestrator import SYSTEM_MESSAGE as SYSTEM_MESSAGE
from nova.chat.orchestrator import ConversationOrchestrator
from nova.chat.registry import ModelRegistry
from nova.errors import ApplicationError
from nova.gpu.backend import BackendError, StreamControl
from nova.persona import PersonaConfigError, PersonaManager

logger = logging.getLogger("nova.chat")


class ChatService:
    def __init__(self, store, manager, settings):
        self.store = store
        self.manager = manager
        self.settings = settings
        self.registry = ModelRegistry(settings, manager.backend)
        self.manager.update_model_policies(self.registry.gpu_policies())
        self.cloud = DeepSeekBackend(settings)
        self._runs = set()
        self._preparing = set()
        self._requests = {}
        self._cancel_tombstones = {}
        self._unresolved_saves = {}
        self.last_metrics = None
        try:
            self.orchestrator = ConversationOrchestrator(
                PersonaManager(settings.persona_config_dir)
            )
        except PersonaConfigError:
            # 人格不可用只拒绝新生成，健康、历史和已完成重放仍可用。
            self.orchestrator = None

    async def open(self, thread_id, model, content, request_id, cloud_consent=False, *, think=None):
        key = (thread_id, request_id)
        if key in self._requests:
            raise ApplicationError("CHAT_REQUEST_CONFLICT", "该请求正在执行，请勿重复发送", 409)
        request_threads = {item[0] for item in self._requests}
        slots = len(self._requests) + len(self._unresolved_saves.keys() - request_threads)
        recovery = thread_id in self._unresolved_saves and thread_id not in request_threads
        if slots >= 128 and not recovery:
            raise ApplicationError("CHAT_CAPACITY_EXCEEDED", "聊天服务繁忙，请稍后重试", 503)
        control = StreamControl()
        if self._cancel_tombstones.get(key, 0) > time.monotonic():
            control.cancel()
        record = {"control": control, "producing": False}
        self._requests[key] = record
        task = asyncio.create_task(
            self._prepare(thread_id, model, content, request_id, cloud_consent, think, record)
        )
        self._preparing.add(task)

        def finished(task):
            self._preparing.discard(task)
            if not record["producing"] and self._requests.get(key) is record:
                self._requests.pop(key, None)
            if not task.cancelled():
                task.exception()

        task.add_done_callback(finished)
        return await asyncio.shield(task)

    async def cancel(self, thread_id, request_id):
        await asyncio.to_thread(self.store.get_thread, thread_id)
        key = (thread_id, request_id)
        now = time.monotonic()
        self._cancel_tombstones = {
            item: expiry for item, expiry in self._cancel_tombstones.items() if expiry > now
        }
        record = self._requests.get(key)
        if record:
            record["control"].cancel()
        else:
            # Stop可能先于chat抵达；有界、短时标记阻止迟到请求偷偷唤醒GPU。
            if len(self._cancel_tombstones) >= 256:
                oldest = min(self._cancel_tombstones, key=self._cancel_tombstones.get)
                self._cancel_tombstones.pop(oldest)
            self._cancel_tombstones[key] = now + 120
        return {
            "cancel_requested": True,
            "state": "cancelling" if record else "marked",
            "request_id": request_id,
        }

    async def _resolve(self, model):
        if model != "deepseek":
            self.manager._admit()
        try:
            entry = (
                self.registry.get(model)
                if model == "deepseek"
                else await self.registry.resolve(model)
            )
        except ApplicationError as error:
            fallback = self.settings.chat_fallback_model
            if (
                model == "deepseek"
                or not fallback
                or fallback == model
                or error.code
                not in {
                    "MODEL_NOT_INSTALLED",
                    "MODEL_RAM_INSUFFICIENT",
                    "GPU_MEMORY_INSUFFICIENT",
                }
            ):
                raise
            self.manager._admit()
            entry = await self.registry.resolve(fallback)
            entry["fallback_reason"] = error.code
        self.manager.update_model_policies(self.registry.gpu_policies())
        return entry

    def _compose(self, entry, thread_id, history, content, record):
        runtime = entry.get("policy", {}).get("runtime", {})
        prepared = self.orchestrator.build(
            thread_id,
            history,
            content,
            context_tokens=runtime.get("num_ctx", 4096),
            output_tokens=runtime.get("num_predict", 512),
        )
        record["context_metrics"] = {
            "model": entry["id"],
            "versions": prepared.versions,
            "estimated_prompt_tokens": prepared.estimated_prompt_tokens,
            "estimation_method": prepared.estimation_method,
            "dropped_messages": prepared.dropped_messages,
            "context_tokens": prepared.context_tokens,
            "reserved_output_tokens": prepared.reserved_output_tokens,
        }
        return prepared.messages

    async def _prepare(self, thread_id, model, content, request_id, cloud_consent, think, record):
        await self._recover_save(thread_id)
        # 先只读校验请求身份并查找已完成结果：命中时不得查询Ollama、检查资源或启动生成。
        replay = await asyncio.to_thread(
            self.store.get_completed_run, thread_id, model, content, request_id
        )
        if replay is not None:
            message = replay["assistant_message"]
            output = asyncio.Queue()
            output.put_nowait(
                {
                    "type": "meta",
                    "thread_id": thread_id,
                    "user_message_id": replay["user_message"]["id"],
                    "assistant_message_id": message["id"],
                    "model": message["model"],
                    "requested_model": model,
                    "fallback_reason": None,
                }
            )
            output.put_nowait({"type": "delta", "text": message["content"]})
            output.put_nowait({"type": "done", "message": message})
            return output
        if self.orchestrator is None:
            raise ApplicationError("PERSONA_UNAVAILABLE", "人格配置不可用，请检查已审核版本", 503)
        entry = await self._resolve(model)
        if entry["provider"] == "deepseek":
            self.registry.require_cloud(cloud_consent)

        def prepare(history):
            return history, self._compose(entry, thread_id, history, content, record)

        run = await asyncio.to_thread(
            self.store.begin_run, thread_id, model, content, request_id, prepare=prepare
        )
        meta = {
            "type": "meta",
            "thread_id": thread_id,
            "user_message_id": run["user_message"]["id"],
            "assistant_message_id": run["assistant_message"]["id"],
            "model": entry["id"],
            "requested_model": model,
            "fallback_reason": entry.get("fallback_reason"),
        }
        output = asyncio.Queue()
        output.put_nowait(meta)
        if run["is_replay"]:
            message = run["assistant_message"]
            output.put_nowait({"type": "delta", "text": message["content"]})
            output.put_nowait({"type": "done", "message": message})
            return output
        history, messages = run["prepared"]
        if record["control"].requested:
            await self._finish_run(thread_id, run["run_id"], "", "interrupted")
            output.put_nowait({"type": "error", "code": "CHAT_CANCELLED", "message": "生成已取消"})
            return output

        try:
            if entry["provider"] == "ollama":
                kwargs = {"control": record["control"]}
                selected_think = self.settings.chat_think if think is None else think
                if selected_think:
                    kwargs["think"] = True
                try:
                    upstream, worker = await self.manager.open_chat_stream(
                        entry["id"], messages, entry["required_mib"], **kwargs
                    )
                except ApplicationError as error:
                    fallback = self.settings.chat_fallback_model
                    if (
                        error.code != "GPU_MEMORY_INSUFFICIENT"
                        or not fallback
                        or fallback == entry["id"]
                    ):
                        raise
                    self.manager._admit()
                    entry = await self._resolve(fallback)
                    meta.update(model=entry["id"], fallback_reason=error.code)
                    messages = self._compose(entry, thread_id, history, content, record)
                    upstream, worker = await self.manager.open_chat_stream(
                        entry["id"], messages, entry["required_mib"], **kwargs
                    )
            else:
                upstream, worker = await self.cloud.open_chat_stream(
                    messages, control=record["control"]
                )
        except Exception:
            status = "interrupted" if record["control"].requested else "error"
            await self._finish_run(thread_id, run["run_id"], "", status)
            raise

        record["producing"] = True
        # 明确的输入摘录只是本会话临时数据，不由模型推断情绪或持久化人格。
        topic = "".join(char for char in " ".join(content.split()) if ord(char) >= 32)[:120]
        self.orchestrator.persona.dynamic.update(thread_id, topic=topic)
        task = asyncio.create_task(
            self._produce(
                run["run_id"],
                upstream,
                worker,
                output,
                record,
                (thread_id, request_id),
                entry["id"],
            )
        )
        self._runs.add(task)
        task.add_done_callback(self._runs.discard)
        return output

    async def _produce(self, run_id, upstream, worker, output, record, key, model):
        fragments = []
        try:
            failure = None
            while True:
                event = await upstream.get()
                if event["type"] == "delta":
                    fragments.append(event["text"])
                    await output.put(event)
                elif event["type"] == "error":
                    failure = event
                    break
                elif event["type"] == "done":
                    break
            try:
                result = await asyncio.shield(worker)
            except Exception:
                result = None
            if failure or not result or result.get("done") is not True:
                status = "interrupted" if record["control"].requested else "error"
                await self._finish_run(key[0], run_id, "".join(fragments), status)
                await output.put(
                    failure
                    or {"type": "error", "code": "CHAT_FAILED", "message": "回复未完成，请重试"}
                )
                return
            message = await self._finish_run(key[0], run_id, result["response"], "complete", model)
            self.last_metrics = {**record["context_metrics"], **result.get("stats", {})}
            await output.put({"type": "done", "message": message})
        except Exception:
            logger.error("chat_persistence_or_stream_failed")
            await output.put(
                {
                    "type": "error",
                    "code": "CHAT_SAVE_FAILED",
                    "message": "回复保存失败，请刷新历史确认",
                }
            )
        finally:
            if self._requests.get(key) is record:
                self._requests.pop(key, None)

    async def _finish_run(self, thread_id, run_id, content, status, actual_model=None):
        pending = (run_id, content, status, actual_model)
        self._unresolved_saves[thread_id] = pending
        result = await asyncio.to_thread(
            self.store.finish_run, run_id, content, status, actual_model=actual_model
        )
        if self._unresolved_saves.get(thread_id) is pending:
            self._unresolved_saves.pop(thread_id, None)
        return result

    async def _recover_save(self, thread_id):
        pending = self._unresolved_saves.get(thread_id)
        if pending is not None:
            await self._finish_run(thread_id, *pending)

    async def close(self):
        if self._preparing:
            await asyncio.gather(*tuple(self._preparing), return_exceptions=True)
        if self._runs:
            # 不取消上游线程，不在仍需保存消息时提前关闭数据库。
            await asyncio.gather(*tuple(self._runs), return_exceptions=True)
        # 未能持久化的内容仅暂存于进程；重启时由存储层将残留任务标记为中断。
        self._unresolved_saves.clear()
        if self.orchestrator is not None:
            self.orchestrator.persona.dynamic.clear()

    def reset_dynamic(self, thread_id):
        # 路由仅在数据库成功删除会话后调用；连同暂存回复一起释放。
        self._unresolved_saves.pop(thread_id, None)
        if self.orchestrator is not None:
            self.orchestrator.persona.dynamic.reset(thread_id)


async def read_events(queue):
    while True:
        event = await queue.get()
        yield event
        if event["type"] in {"done", "error"}:
            break


async def idle_monitor(manager, idle_seconds):
    while True:
        await asyncio.sleep(min(5, idle_seconds))
        try:
            await manager.idle_tick(idle_seconds=idle_seconds)
        except ApplicationError, BackendError:
            logger.warning("idle_unload_failed_protection_retained")
