"""控制台日志：固定元数据、请求标识和不含异常原文的定位信息。"""

import json
import logging
import re
import sys
import traceback
from contextvars import ContextVar
from datetime import UTC, datetime
from pathlib import Path
from typing import TextIO

request_id_context: ContextVar[str] = ContextVar("nova_request_id", default="-")
_SENSITIVE_PAIR = re.compile(
    r"(?i)\b([a-z0-9_]*(?:api[_-]?key|password|token|secret|authorization))"
    r"([\"']?\s*[:=]\s*)(?:\"[^\"\r\n]*\"|'[^'\r\n]*'|[^\s,;&\"'}]+)"
)
_AUTHORIZATION = re.compile(
    r"(?i)\b(authorization[\"']?\s*[:=]\s*)"
    r"(?:\"[^\"\r\n]*\"|'[^'\r\n]*'|[^\r\n,;&]+)"
)
_BEARER = re.compile(r"(?i)\bbearer\s+[^\s,;\"']+")


class SafeFormatter(logging.Formatter):
    """不格式化异常消息、异常链或源代码行，避免堆栈夹带输入值。"""

    def __init__(self, secret_values: tuple[str, ...] = ()) -> None:
        super().__init__()
        self.secret_values = tuple(sorted(set(filter(None, secret_values)), key=len, reverse=True))

    def redact(self, message: str) -> str:
        for value in self.secret_values:
            message = message.replace(value, "[REDACTED]")
        message = _AUTHORIZATION.sub(r"\1[REDACTED]", message)
        message = _SENSITIVE_PAIR.sub(r"\1\2[REDACTED]", message)
        return _BEARER.sub("Bearer [REDACTED]", message)

    def format(self, record: logging.LogRecord) -> str:
        try:
            message = record.getMessage()
        except Exception:
            message = "unformattable_log_event"
        payload = {
            "timestamp": datetime.fromtimestamp(record.created, UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "request_id": getattr(record, "request_id", request_id_context.get()),
            "message": self.redact(message),
        }
        status = getattr(record, "status_code", None)
        if isinstance(status, int):
            payload["status_code"] = status
        if record.exc_info and record.exc_info[1] is not None:
            payload["exception_type"] = type(record.exc_info[1]).__name__
            payload["frames"] = [
                {"file": Path(frame.filename).name, "function": frame.name, "line": frame.lineno}
                for frame in traceback.extract_tb(record.exc_info[2])
            ]
        # 在序列化前处理字符串，避免正则破坏 JSON 的转义或引号结构。
        payload["request_id"] = self.redact(str(payload["request_id"]))
        return json.dumps(payload, ensure_ascii=False)


def configure_logging(
    level: str, secret_values: tuple[str, ...] = (), *, stream: TextIO | None = None
) -> None:
    """只管理 Nova 与 Uvicorn 的日志，不更改宿主根 logger。"""
    handler = logging.StreamHandler(stream if stream is not None else sys.stderr)
    handler.setFormatter(SafeFormatter(secret_values))
    for name in ("nova", "uvicorn"):
        logger = logging.getLogger(name)
        logger.handlers = [handler]
        logger.setLevel(level)
        logger.propagate = False
        logger.disabled = False
    for name in ("uvicorn.error", "uvicorn.asgi"):
        logger = logging.getLogger(name)
        logger.handlers = []
        logger.setLevel(level)
        logger.propagate = True
        logger.disabled = False
    # Uvicorn 的原始访问日志包含 URL，可能夹带查询参数凭据。
    access = logging.getLogger("uvicorn.access")
    access.handlers = []
    access.propagate = False
    access.disabled = True
