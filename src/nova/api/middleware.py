"""为每次 HTTP 请求生成服务端标识；不记录 URL、查询参数或请求体。"""

import logging
from uuid import uuid4

from starlette.types import ASGIApp, Message, Receive, Scope, Send

from nova.logging_config import request_id_context

logger = logging.getLogger("nova.requests")


class RequestContextMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        request_id = uuid4().hex
        scope.setdefault("state", {})["request_id"] = request_id
        token = request_id_context.set(request_id)
        status_code = 500

        async def send_with_id(message: Message) -> None:
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = message["status"]
                headers = [
                    (key, value)
                    for key, value in message.get("headers", [])
                    if key.lower() != b"x-request-id"
                ]
                message = {
                    **message,
                    "headers": [*headers, (b"x-request-id", request_id.encode("ascii"))],
                }
            await send(message)

        try:
            await self.app(scope, receive, send_with_id)
        finally:
            logger.info("http_request_finished", extra={"status_code": status_code})
            request_id_context.reset(token)
