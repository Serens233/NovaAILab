"""本机 Ollama 与 NVIDIA 查询适配器；不下载模型，不访问云端。"""

import asyncio
import csv
import http.client
import io
import json
import math
import re
import socket
import subprocess
import threading
import time
from urllib.error import URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

from nova.gpu.streaming import AnswerFilter, final_answer

_MAX_RESPONSE_BYTES = 2 * 1024 * 1024
_MODEL_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._/-]*(?::[A-Za-z0-9._-]+)?\Z")


class BackendError(Exception):
    """固定公开错误；不携带原始上游响应、异常或请求正文。"""


class CancelledBackendError(BackendError):
    def __init__(self):
        super().__init__("本地推理已请求取消，仍需管理器复核模型资源。")


class StreamControl:
    """只关闭本请求的连接；取消成功不等于 Ollama 已经释放显存。"""

    def __init__(self):
        self._requested = threading.Event()
        self._expired = threading.Event()
        self._lock = threading.Lock()
        self._socket = None

    @property
    def requested(self):
        return self._requested.is_set()

    def check(self):
        if self.requested:
            raise CancelledBackendError()
        if self._expired.is_set():
            raise BackendError("本地聊天超过时限，请卸载复核后重试。")

    def bind(self, response):
        connection = getattr(getattr(getattr(response, "fp", None), "raw", None), "_sock", None)
        with self._lock:
            self._socket = connection
            interrupted = self.requested or self._expired.is_set()
        if interrupted:
            self._shutdown()
            self.check()

    def unbind(self):
        with self._lock:
            self._socket = None

    def cancel(self):
        self._requested.set()
        self._shutdown()

    def expire(self):
        self._expired.set()
        self._shutdown()

    def _shutdown(self):
        with self._lock:
            connection = self._socket
            if connection is not None:
                try:
                    # shutdown 唤醒阻塞的 readline；不跨线程 close 缓冲流以免死锁。
                    connection.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass


def model_allocation(status):
    """Ollama 内存字节口径的估计，不伪造层数、CPU利用率或WDDM进程显存。"""
    size, vram = status.get("size"), status.get("size_vram")
    known = _nonnegative_int(size) and size > 0 and _nonnegative_int(vram)
    return {
        "name": status.get("name"),
        "gpu_bytes": vram if _nonnegative_int(vram) else None,
        "cpu_bytes_estimate": max(0, size - vram) if known else None,
        "gpu_fraction_estimate": min(1.0, vram / size) if known else None,
        "cpu_layer_count": None,
        "gpu_layer_count": None,
        "source": "ollama_size_estimate",
    }


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _model_name(value):
    if not isinstance(value, str) or len(value) > 256 or not _MODEL_NAME.fullmatch(value):
        raise BackendError("本地模型名称无效。")
    return value


def _nonnegative_int(value):
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def _runtime_config(runtime, *, default_predict=512):
    """仅接受资源有界的推理参数；不允许路径、端点、模板或任意上游选项。"""
    options = {"num_ctx": 4096, "num_predict": default_predict}
    if runtime is None:
        return 180, options
    if not isinstance(runtime, dict) or set(runtime) - {
        "num_ctx",
        "num_predict",
        "keep_alive",
        "temperature",
        "num_gpu",
        "think",
    }:
        raise BackendError("本地模型运行配置无效。")
    if "think" in runtime and type(runtime["think"]) is not bool:
        raise BackendError("本地模型思考模式必须为布尔值。")
    bounds = {
        "num_ctx": (512, 8192),
        "num_predict": (1, 1024),
        "keep_alive": (0, 180),
        "num_gpu": (0, 256),
    }
    for key, (minimum, maximum) in bounds.items():
        if key in runtime:
            value = runtime[key]
            if type(value) is not int or not minimum <= value <= maximum:
                raise BackendError("本地模型运行配置无效。")
            if key != "keep_alive":
                options[key] = value
    if "temperature" in runtime:
        value = runtime["temperature"]
        if (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(value)
            or not 0 <= value <= 2
        ):
            raise BackendError("本地模型运行配置无效。")
        options["temperature"] = value
    return runtime.get("keep_alive", 180), options


def validate_base_url(base_url):
    try:
        if (
            not isinstance(base_url, str)
            or any(character.isspace() for character in base_url)
            or any(character in base_url for character in "\\?#")
        ):
            raise ValueError
        parsed = urlsplit(base_url)
        if (
            parsed.scheme != "http"
            or parsed.hostname != "127.0.0.1"
            or parsed.username is not None
            or parsed.password is not None
            or parsed.path not in {"", "/"}
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError
        port = parsed.port if parsed.port is not None else 11434
        if not 1 <= port <= 65535:
            raise ValueError
    except ValueError, TypeError:
        raise BackendError("Ollama 地址必须为无凭据、无路径的本机 HTTP 回环地址。") from None
    return f"http://127.0.0.1:{port}"


class OllamaBackend:
    """异步入口内执行有界同步 HTTP；请求取消由上层管理器跟踪。"""

    supports_stream_control = True

    def __init__(
        self,
        base_url="http://127.0.0.1:11434",
        model="qwen3.5:4b",
        timeout=120.0,
    ):
        self.base_url = validate_base_url(base_url)
        if (
            isinstance(timeout, bool)
            or not isinstance(timeout, (int, float))
            or not math.isfinite(timeout)
            or not 0 < timeout <= 300
        ):
            raise BackendError("Ollama 超时必须为 0 至 300 秒之间的有限数值。")
        self.model = _model_name(model)
        self.timeout = float(timeout)
        self._opener = build_opener(ProxyHandler({}), _NoRedirect())

    def _request(self, method, path, payload=None, *, timeout=None):
        body = json.dumps(payload).encode("utf-8") if payload is not None else None
        request = Request(
            self.base_url + path,
            data=body,
            method=method,
            headers={"Content-Type": "application/json", "Accept": "application/json"},
        )
        try:
            with self._opener.open(request, timeout=timeout or self.timeout) as response:
                if not 200 <= response.status < 300:
                    raise BackendError("Ollama 请求失败，请检查本机服务。")
                raw = response.read(_MAX_RESPONSE_BYTES + 1)
            if len(raw) > _MAX_RESPONSE_BYTES:
                raise BackendError("Ollama 响应超过允许大小。")
            result = json.loads(raw)
            if not isinstance(result, dict) or "error" in result:
                raise BackendError("Ollama 返回无效响应，请检查本机服务。")
            return result
        except URLError, OSError, ValueError, http.client.HTTPException, RecursionError:
            raise BackendError("Ollama 不可用或响应无效，请检查本机服务。") from None

    def _status(self):
        result = self._request("GET", "/api/ps", timeout=5)
        models = result.get("models")
        if not isinstance(models, list):
            raise BackendError("Ollama 模型状态无效，不能确认模型是否卸载。")
        clean = []
        for item in models:
            if (
                not isinstance(item, dict)
                or not isinstance(item.get("name"), str)
                or not item["name"]
                or not _nonnegative_int(item.get("size_vram"))
            ):
                raise BackendError("Ollama 模型状态无效，不能确认模型是否卸载。")
            entry = {"name": _model_name(item["name"]), "size_vram": item["size_vram"]}
            for key in ("size", "context_length"):
                if key in item:
                    if not _nonnegative_int(item[key]):
                        raise BackendError("Ollama 模型状态无效，不能确认模型是否卸载。")
                    entry[key] = item[key]
            if isinstance(item.get("expires_at"), str):
                entry["expires_at"] = item["expires_at"]
            clean.append(entry)
        return clean

    async def get_model_status(self):
        return await asyncio.to_thread(self._status)

    def _installed_models(self):
        models = self._request("GET", "/api/tags", timeout=5).get("models")
        if not isinstance(models, list):
            raise BackendError("无法确认本地模型列表。")
        clean = set()
        for item in models:
            if not isinstance(item, dict):
                raise BackendError("本地模型列表无效。")
            name = _model_name(item.get("name"))
            if (
                not item.get("remote_host")
                and not item.get("remote_model")
                and not name.lower().endswith((":cloud", "-cloud"))
            ):
                clean.add(name)
        return sorted(clean)

    async def list_installed_models(self):
        """只读安装列表，排除Ollama远程代理；不下载、不加载。"""
        return await asyncio.to_thread(self._installed_models)

    def _ensure_local(self, model=None, *, timeout=None):
        model = _model_name(self.model if model is None else model)
        # Ollama 的云模型也可能出现在 tags 中；同时检查名称和远程元数据。
        if model.lower().endswith((":cloud", "-cloud")):
            raise BackendError("此入口仅允许已下载的本地模型，不启用云端调用。")
        models = self._request("GET", "/api/tags", timeout=timeout or 5).get("models")
        if not isinstance(models, list):
            raise BackendError("无法确认本地模型是否已安装。")
        item = next(
            (item for item in models if isinstance(item, dict) and item.get("name") == model),
            None,
        )
        if item is None:
            raise BackendError("指定模型尚未在本机安装；管理器不会自动下载。")
        if item.get("remote_host") or item.get("remote_model"):
            raise BackendError("此入口仅允许已下载的本地模型，不启用云端调用。")

    async def ensure_local_model(self, model):
        """在发送响应头前确认本地模型已安装；不会下载或加载。"""
        await asyncio.to_thread(self._ensure_local, model)

    @staticmethod
    def validate_runtime(runtime):
        _runtime_config(runtime)

    @staticmethod
    def validate_chat_messages(messages):
        if not isinstance(messages, list) or not 1 <= len(messages) <= 100:
            raise BackendError("聊天上下文必须为1至100条文本消息。")
        clean = []
        total = 0
        for item in messages:
            if (
                not isinstance(item, dict)
                or item.get("role") not in {"system", "user", "assistant"}
                or not isinstance(item.get("content"), str)
                or not item["content"].strip()
            ):
                raise BackendError("聊天消息必须包含有效角色和非空文本。")
            total += len(item["content"])
            if total > 24000:
                raise BackendError("聊天上下文过长，请缩短消息或建立新会话。")
            clean.append({"role": item["role"], "content": item["content"]})
        return clean

    def _stream_chat(self, model, messages, on_delta, *, runtime=None, control=None):
        started_at = time.monotonic()
        deadline = started_at + self.timeout
        model = _model_name(model)
        messages = self.validate_chat_messages(messages)
        keep_alive, options = _runtime_config(runtime)
        control = control if control is not None else StreamControl()
        control.check()
        self._ensure_local(model, timeout=min(5, self.timeout))
        control.check()
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise BackendError("本地聊天超过时限，请卸载复核后重试。")
        request = Request(
            self.base_url + "/api/chat",
            data=json.dumps(
                {
                    "model": model,
                    "messages": messages,
                    "stream": True,
                    "think": runtime.get("think", False) if runtime else False,
                    "keep_alive": keep_alive,
                    "options": options,
                }
            ).encode("utf-8"),
            method="POST",
            headers={"Content-Type": "application/json", "Accept": "application/x-ndjson"},
        )
        pieces = []
        consumed = 0
        filtered = AnswerFilter()
        first_activity = None
        first_token = None
        # Socket timeout 是每次 recv 的时限，无法限制持续缓慢发送的单行。
        # 到达总期限时只打断本请求的 I/O；上层仍须进行卸载复核。
        timer = threading.Timer(remaining, control.expire)
        timer.daemon = True
        timer.start()
        try:
            with self._opener.open(request, timeout=remaining) as response:
                control.bind(response)
                if not 200 <= response.status < 300:
                    raise BackendError("本地聊天连接失败。")
                for _ in range(8192):
                    control.check()
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise BackendError("本地聊天超过时限，请卸载复核后重试。")
                    # CPython 的 SocketIO 可缩短为剩余总时限，避免每行重新计时。
                    connection = getattr(
                        getattr(getattr(response, "fp", None), "raw", None), "_sock", None
                    )
                    if connection is not None:
                        connection.settimeout(remaining)
                    raw = response.readline(65537)
                    control.check()
                    if time.monotonic() >= deadline:
                        raise BackendError("本地聊天超过时限，请卸载复核后重试。")
                    consumed += len(raw)
                    if len(raw) > 65536 or consumed > _MAX_RESPONSE_BYTES:
                        raise BackendError("本地聊天响应超过允许大小。")
                    if not raw:
                        break
                    if not raw.strip():
                        continue
                    item = json.loads(raw)
                    if (
                        not isinstance(item, dict)
                        or "error" in item
                        or item.get("model") != model
                        or not isinstance(item.get("done"), bool)
                    ):
                        raise BackendError("本地聊天返回无效流。")
                    message = item.get("message")
                    if (
                        not isinstance(message, dict)
                        or message.get("role") != "assistant"
                        or not isinstance(message.get("content", ""), str)
                        or not isinstance(message.get("thinking", ""), str)
                    ):
                        raise BackendError("本地聊天返回无效消息。")
                    content = message.get("content", "")
                    if first_activity is None and (content or message.get("thinking")):
                        first_activity = (time.monotonic() - started_at) * 1000
                    # thinking 只参与首活动计时，不保存、回调或拼入最终文本。
                    delta = filtered.feed(content)
                    if delta:
                        if first_token is None:
                            first_token = (time.monotonic() - started_at) * 1000
                        pieces.append(delta)
                        on_delta(delta)
                    if item["done"]:
                        filtered.finish()
                        control.check()
                        text = "".join(pieces)
                        if not text.strip():
                            raise BackendError("本地聊天未返回最终正文。")
                        stats = {
                            "first_token_latency_ms": first_token,
                            "first_activity_latency_ms": first_activity,
                            "request_duration_ms": (time.monotonic() - started_at) * 1000,
                        }
                        for name in (
                            "load_duration",
                            "total_duration",
                            "eval_count",
                            "eval_duration",
                            "prompt_eval_count",
                            "prompt_eval_duration",
                        ):
                            if name in item:
                                if not _nonnegative_int(item[name]):
                                    raise BackendError("本地聊天统计无效。")
                                stats[name] = item[name]
                        if stats.get("eval_duration", 0) > 0 and "eval_count" in stats:
                            stats["tokens_per_second"] = (
                                stats["eval_count"] * 1_000_000_000 / stats["eval_duration"]
                            )
                        return {"model": model, "response": text, "done": True, "stats": stats}
            raise BackendError("本地聊天流未确认完成，不能视为成功。")
        except URLError, OSError, ValueError, http.client.HTTPException, RecursionError:
            control.check()
            raise BackendError("本地聊天连接失败、思考标记不完整或响应无效。") from None
        finally:
            timer.cancel()
            control.unbind()

    async def stream_chat(self, model, messages, on_delta, *, runtime=None, control=None):
        """on_delta在事件循环中调用；取消不能让仍运行的HTTP线程失去跟踪。"""
        loop = asyncio.get_running_loop()

        def emit(text):
            loop.call_soon_threadsafe(on_delta, text)

        kwargs = {} if runtime is None else {"runtime": runtime}
        if control is not None:
            kwargs["control"] = control
        task = asyncio.create_task(
            asyncio.to_thread(self._stream_chat, model, messages, emit, **kwargs)
        )
        try:
            return await asyncio.shield(task)
        except asyncio.CancelledError:
            # 调用方的安全锁仍须等上游结束，不能把取消当作显存已释放。
            try:
                await asyncio.shield(task)
            finally:
                raise

    def _generate(
        self,
        prompt,
        *,
        keep_alive,
        model,
        require_text=False,
        check_local=False,
        request_timeout=None,
        runtime=None,
    ):
        configured_keep_alive, options = _runtime_config(runtime, default_predict=128)
        if check_local:
            self._ensure_local()
        payload = {
            "model": model,
            "prompt": prompt,
            "stream": False,
            "think": runtime.get("think", False) if runtime else False,
            "keep_alive": keep_alive if runtime is None else configured_keep_alive,
            "options": options,
        }
        result = self._request("POST", "/api/generate", payload, timeout=request_timeout)
        if (
            result.get("done") is not True
            or result.get("model") != model
            or not isinstance(result.get("response", ""), str)
            or (require_text and not result.get("response", "").strip())
        ):
            raise BackendError("Ollama 未确认请求完成，请检查模型状态。")
        try:
            text = final_answer(result.get("response", ""))
        except ValueError:
            raise BackendError("本地模型思考标记不完整，未返回最终正文。") from None
        if require_text and not text.strip():
            raise BackendError("本地模型未返回最终正文。")
        clean = {"model": model, "done": True, "response": text}
        for key in (
            "load_duration",
            "total_duration",
            "eval_count",
            "eval_duration",
            "prompt_eval_count",
        ):
            if key in result:
                if not _nonnegative_int(result[key]):
                    raise BackendError("Ollama 返回无效的运行统计。")
                clean[key] = result[key]
        if clean.get("eval_duration", 0) > 0 and "eval_count" in clean:
            clean["tokens_per_second"] = (
                clean["eval_count"] * 1_000_000_000 / clean["eval_duration"]
            )
        if isinstance(result.get("done_reason"), str):
            clean["done_reason"] = result["done_reason"]
        return clean

    async def unload_model(self, name):
        return await asyncio.to_thread(
            self._generate, "", keep_alive=0, model=_model_name(name), request_timeout=10
        )

    async def sleep_model(self, name):
        return await self.unload_model(name)

    async def wake_model(self, *, runtime=None):
        kwargs = {} if runtime is None else {"runtime": runtime}
        return await asyncio.to_thread(
            self._generate, "", keep_alive=180, model=self.model, check_local=True, **kwargs
        )

    async def generate(self, prompt, *, runtime=None):
        if not isinstance(prompt, str) or not prompt.strip() or len(prompt) > 8192:
            raise BackendError("问题必须为 1 至 8192 个字符的非空文本。")
        kwargs = {} if runtime is None else {"runtime": runtime}
        return await asyncio.to_thread(
            self._generate,
            prompt,
            keep_alive=180,
            model=self.model,
            require_text=True,
            check_local=True,
            **kwargs,
        )

    @staticmethod
    def _gpu_memory():
        result = {"available": False, "gpus": [], "compute_processes": []}
        try:

            def query(fields, kind="gpu"):
                process = subprocess.run(
                    ["nvidia-smi", f"--query-{kind}={fields}", "--format=csv,noheader,nounits"],
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    timeout=5,
                    check=True,
                )
                return list(csv.reader(io.StringIO(process.stdout), skipinitialspace=True))

            rows = query("index,uuid,name,memory.total,memory.used")
            for row in rows:
                if not row:
                    continue
                if len(row) != 5:
                    raise ValueError
                index, uuid, name, total, used = [field.strip() for field in row]
                gpu = {
                    "index": int(index),
                    "uuid": uuid,
                    "name": name,
                    "total_mib": int(total),
                    "used_mib": int(used),
                }
                if (
                    gpu["index"] < 0
                    or not uuid
                    or not name
                    or gpu["total_mib"] <= 0
                    or not 0 <= gpu["used_mib"] <= gpu["total_mib"]
                ):
                    raise ValueError
                result["gpus"].append(gpu)
            if not result["gpus"]:
                raise ValueError
            for row in query("pid,process_name,used_gpu_memory", "compute-apps"):
                if not row:
                    continue
                if len(row) != 3:
                    raise ValueError
                pid, name, memory = [field.strip() for field in row]
                unknown = memory in {"N/A", "[N/A]", "Not Supported", "[Not Supported]", "-"}
                used = None if unknown else int(memory)
                if int(pid) <= 0 or (used is not None and used < 0):
                    raise ValueError
                result["compute_processes"].append(
                    {
                        "pid": int(pid),
                        "name": name.replace("\\", "/").rsplit("/", 1)[-1],
                        "used_mib": used,
                    }
                )
            result["available"] = True
        except OSError, ValueError, subprocess.SubprocessError:
            result["error"] = "NVIDIA_SMI_UNAVAILABLE_OR_INVALID"
        return result

    async def get_gpu_memory_usage(self):
        return await asyncio.to_thread(self._gpu_memory)
