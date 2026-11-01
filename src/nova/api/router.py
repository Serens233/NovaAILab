"""集中注册路由；运维接口不随业务 API 版本改变。"""

from fastapi import APIRouter, FastAPI

from nova.api.chat import router as chat_router
from nova.api.gpu import router as gpu_router
from nova.api.health import router as health_router

API_V1_PREFIX = "/api/v1"


def register_routes(app: FastAPI) -> None:
    app.include_router(health_router)
    app.include_router(gpu_router)
    app.include_router(chat_router)
    # 本阶段没有业务端点，后续在此路由中逐步注册实际 v1 功能。
    app.include_router(APIRouter(prefix=API_V1_PREFIX))
