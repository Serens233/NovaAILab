"""可选DeepSeek流；固定HTTPS目标，无自动回退，不在日志记录正文或密钥。"""

import asyncio
import http.client
import json
import threading
import time

from nova.errors import ApplicationError
from nova.gpu.backend import CancelledBackendError, StreamControl
from nova.gpu.streaming import AnswerFilter


class DeepSeekBackend:
    def __init__(self, settings):
        self.settings = settings

    async def open_chat_stream(self, messages, *, control=None):
        queue = asyncio.Queue()
        loop = asyncio.get_running_loop()
        control = control if control is not None else StreamControl()

        def send(event):
            loop.call_soon_threadsafe(queue.put_nowait, event)

        def generate():
            connection = None
            content = []
            filtered = AnswerFilter()
            deadline = time.monotonic() + 180
            timer = threading.Timer(180, control.expire)
            timer.daemon = True
            timer.start()
            try:
                control.check()
                connection = http.client.HTTPSConnection("api.deepseek.com", timeout=120)
                # 显式连接后、发送正文或读取响应头前绑定socket；头部慢速滴流也可取消。
                # DNS由操作系统解析，socket超时不能提供其硬期限；TCP/TLS使用连接超时。
                connect = getattr(connection, "connect", None)
                if connect is not None:
                    connect()
                    control.bind_socket(getattr(connection, "sock", None))
                control.check()
                payload = {
                    "model": self.settings.model or "deepseek-chat",
                    "messages": messages,
                    "stream": True,
                    "max_tokens": 512,
                }
                connection.request(
                    "POST",
                    "/chat/completions",
                    body=json.dumps(payload).encode("utf-8"),
                    headers={
                        "Content-Type": "application/json",
                        "Authorization": "Bearer " + self.settings.api_key.get_secret_value(),
                    },
                )
                response = connection.getresponse()
                control.bind(response)
                control.check()
                if response.status != 200:
                    raise ValueError("upstream rejected")
                size = 0
                completed = False
                while time.monotonic() < deadline:
                    control.check()
                    connection_socket = getattr(
                        getattr(getattr(response, "fp", None), "raw", None), "_sock", None
                    )
                    if connection_socket is not None:
                        connection_socket.settimeout(max(0.001, deadline - time.monotonic()))
                    line = response.readline(131073)
                    control.check()
                    if not line:
                        break
                    size += len(line)
                    if len(line) > 131072 or size > 2 * 1024 * 1024:
                        raise ValueError("bounded stream exceeded")
                    if not line.startswith(b"data:"):
                        continue
                    data = line[5:].strip()
                    if data == b"[DONE]":
                        completed = True
                        break
                    item = json.loads(data)
                    if not isinstance(item, dict) or "error" in item:
                        raise ValueError("upstream error")
                    choices = item.get("choices", [])
                    if not isinstance(choices, list):
                        raise ValueError("invalid choices")
                    if not choices:
                        continue
                    if not isinstance(choices[0], dict):
                        raise ValueError("invalid choice")
                    delta = choices[0].get("delta", {})
                    if not isinstance(delta, dict):
                        raise ValueError("invalid delta")
                    # reasoning_content 是独立的不可信内部字段，绝不拼入content。
                    if delta.get("reasoning_content") is not None and not isinstance(
                        delta["reasoning_content"], str
                    ):
                        raise ValueError("invalid reasoning field")
                    text = delta.get("content")
                    if text is not None and not isinstance(text, str):
                        raise ValueError("invalid content")
                    if text:
                        visible = filtered.feed(text)
                        if visible:
                            content.append(visible)
                            send({"type": "delta", "text": visible})
                filtered.finish()
                control.check()
                if not completed or not "".join(content).strip():
                    raise ValueError("incomplete stream")
                return {"model": "deepseek", "response": "".join(content), "done": True}
            except Exception:
                # 连接shutdown引发OSError时仍以控制标志识别正常取消。
                control.check()
                raise
            finally:
                timer.cancel()
                control.unbind()
                if connection is not None:
                    connection.close()

        async def worker():
            operation = asyncio.create_task(asyncio.to_thread(generate))
            try:
                try:
                    result = await asyncio.shield(operation)
                except asyncio.CancelledError:
                    # HTTP线程不能由取消Future终止；先等待真实结束再释放跟踪。
                    await asyncio.shield(operation)
                    raise ValueError("consumer cancelled") from None
                await queue.put({"type": "done", "model": "deepseek", "stats": {}})
                return result
            except CancelledBackendError:
                await queue.put(
                    {"type": "error", "code": "CLOUD_CANCELLED", "message": "云端回复已取消"}
                )
                raise ApplicationError("CLOUD_CANCELLED", "云端回复已取消", 409) from None
            except Exception:
                await queue.put(
                    {"type": "error", "code": "CLOUD_FAILED", "message": "云端请求失败，请稍后重试"}
                )
                raise ApplicationError("CLOUD_FAILED", "云端请求失败，请稍后重试", 503) from None

        return queue, asyncio.create_task(worker())
