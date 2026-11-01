"""预期应用错误与系统错误的统一 HTTP 响应。"""

import logging
from uuid import uuid4

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException

logger = logging.getLogger("nova.errors")
_HTTP_ERRORS = {
    400: ("BAD_REQUEST", "请求不合法"),
    401: ("UNAUTHORIZED", "需要身份验证"),
    403: ("FORBIDDEN", "没有访问权限"),
    404: ("NOT_FOUND", "请求的资源不存在"),
    405: ("METHOD_NOT_ALLOWED", "请求方法不支持"),
    422: ("VALIDATION_ERROR", "请求参数不合法"),
    429: ("RATE_LIMITED", "请求过于频繁"),
    503: ("SERVICE_UNAVAILABLE", "服务暂时不可用"),
}


class ApplicationError(Exception):
    """message 仅接受开发者定义的公开提示，不能放入原始异常或用户输入。"""

    def __init__(self, code: str, message: str, status_code: int = 400) -> None:
        super().__init__(message)
        self.code = code
        self.public_message = message
        self.status_code = status_code


def error_response(request: Request, code: str, message: str, status_code: int) -> JSONResponse:
    request_id = getattr(request.state, "request_id", None) or uuid4().hex
    return JSONResponse(
        status_code=status_code,
        content={"error": {"code": code, "message": message, "request_id": request_id}},
        headers={"X-Request-ID": request_id},
    )


def register_exception_handlers(app: FastAPI) -> None:
    async def application_error(request: Request, exc: ApplicationError) -> JSONResponse:
        return error_response(request, exc.code, exc.public_message, exc.status_code)

    async def http_error(request: Request, exc: HTTPException) -> JSONResponse:
        code, message = _HTTP_ERRORS.get(exc.status_code, ("HTTP_ERROR", "请求无法完成"))
        response = error_response(request, code, message, exc.status_code)
        # 仅保留协议要求的安全响应头，不透传任意错误详情或响应头。
        if exc.status_code == 405 and exc.headers and "Allow" in exc.headers:
            response.headers["Allow"] = exc.headers["Allow"]
        return response

    async def validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
        return error_response(request, "VALIDATION_ERROR", "请求参数不合法", 422)

    async def unexpected_error(request: Request, exc: Exception) -> JSONResponse:
        request_id = getattr(request.state, "request_id", "-")
        logger.error("unhandled_error", exc_info=exc, extra={"request_id": request_id})
        return error_response(request, "INTERNAL_ERROR", "服务内部错误", 500)

    app.add_exception_handler(ApplicationError, application_error)
    app.add_exception_handler(HTTPException, http_error)
    app.add_exception_handler(RequestValidationError, validation_error)
    app.add_exception_handler(Exception, unexpected_error)
