"""后端拥有会话历史与生成生命周期；浏览器断开不提前释放GPU或写入假完成。"""

import asyncio
import logging
import time

from nova.chat.cloud import DeepSeekBackend
from nova.chat.registry import ModelRegistry
from nova.errors import ApplicationError
from nova.gpu.backend import BackendError, StreamControl

logger = logging.getLogger("nova.chat")
SYSTEM_MESSAGE = "你是Nova，一位温暖、诚实、务实的中文AI助手。清楚区分已知事实和不确定信息。"


class ChatService:
    def __init__(self, store, manager, settings):
        self.store = store
        self.manager = manager
        self.settings = settings
        self.registry = ModelRegistry(settings, manager.backend)
        self.cloud = DeepSeekBackend(settings)
        self._runs = set()
        self._preparing = set()
        self._requests = {}
        self._cancel_tombstones = {}

    async def open(self, thread_id, model, content, request_id, cloud_consent=False, *, think=None):
        key = (thread_id, request_id)
        if key in self._requests:
            raise ApplicationError("CHAT_REQUEST_CONFLICT", "该请求正在执行，请勿重复发送", 409)
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
        return {"cancel_requested": True, "state": "cancelling" if record else "marked", "request_id": request_id}

    async def _resolve(self, model):
        if model != "deepseek":
            self.manager._admit()
        try:
            entry = self.registry.get(model) if model == "deepseek" else await self.registry.resolve(model)
        except ApplicationError as error:
            fallback = self.settings.chat_fallback_model
            if model == "deepseek" or not fallback or fallback == model or error.code not in {
                "MODEL_NOT_INSTALLED", "MODEL_RAM_INSUFFICIENT", "GPU_MEMORY_INSUFFICIENT",
            }:
                raise
            self.manager._admit()
            entry = await self.registry.resolve(fallback)
            entry["fallback_reason"] = error.code
        self.manager.update_model_policies(self.registry.local_policies())
        return entry

    async def _prepare(self, thread_id, model, content, request_id, cloud_consent, think, record):
        entry = await self._resolve(model)
        if entry["provider"] == "deepseek":
            self.registry.require_cloud(cloud_consent)
        await asyncio.to_thread(self.store.get_thread, thread_id)
        history = await asyncio.to_thread(self.store.get_messages, thread_id)
        run = await asyncio.to_thread(self.store.begin_run, thread_id, model, content, request_id)
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
        if record["control"].requested:
            await asyncio.to_thread(self.store.finish_run, run["run_id"], "", "interrupted")
            output.put_nowait({"type": "error", "code": "CHAT_CANCELLED", "message": "生成已取消"})
            return output

        # 初版采用有界的最近消息窗口；完整历史仍保留在SQLite，不发送客户端伪造历史。
        prior = [
            {"role": item["role"], "content": item["content"]}
            for item in history
            if item["status"] == "complete" and item["content"]
        ]
        selected, budget = [], 6000 - len(content)
        for item in reversed(prior[-20:]):
            if len(item["content"]) > budget:
                break
            selected.insert(0, item)
            budget -= len(item["content"])
        messages = [
            {"role": "system", "content": SYSTEM_MESSAGE},
            *selected,
            {"role": "user", "content": content},
        ]
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
                    if error.code != "GPU_MEMORY_INSUFFICIENT" or not fallback or fallback == entry["id"]:
                        raise
                    self.manager._admit()
                    entry = await self._resolve(fallback)
                    meta.update(model=entry["id"], fallback_reason=error.code)
                    upstream, worker = await self.manager.open_chat_stream(
                        entry["id"], messages, entry["required_mib"], **kwargs
                    )
            else:
                upstream, worker = await self.cloud.open_chat_stream(messages, control=record["control"])
        except Exception:
            status = "interrupted" if record["control"].requested else "error"
            await asyncio.to_thread(self.store.finish_run, run["run_id"], "", status)
            raise

        record["producing"] = True
        task = asyncio.create_task(self._produce(
            run["run_id"], upstream, worker, output, record,
            (thread_id, request_id), entry["id"],
        ))
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
                await asyncio.to_thread(self.store.finish_run, run_id, "".join(fragments), status)
                await output.put(
                    failure
                    or {"type": "error", "code": "CHAT_FAILED", "message": "回复未完成，请重试"}
                )
                return
            message = await asyncio.to_thread(
                self.store.finish_run, run_id, result["response"], "complete", actual_model=model
            )
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

    async def close(self):
        if self._preparing:
            await asyncio.gather(*tuple(self._preparing), return_exceptions=True)
        if self._runs:
            # 不取消上游线程，不在仍需保存消息时提前关闭数据库。
            await asyncio.gather(*tuple(self._runs), return_exceptions=True)


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
