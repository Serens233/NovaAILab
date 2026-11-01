"""就绪检查：使用线程内短连接探测数据库，不返回内部配置。"""

from fastapi import APIRouter, Request

from nova.errors import ApplicationError
from nova.storage.database import DatabaseError

router = APIRouter()


@router.get("/health", tags=["运维"])
def health(request: Request) -> dict[str, str]:
    if not request.app.state.ready:
        raise ApplicationError("SERVICE_UNAVAILABLE", "服务尚未就绪", 503)
    try:
        request.app.state.database.probe()
    except DatabaseError:
        raise ApplicationError("DATABASE_UNAVAILABLE", "数据库暂时不可用", 503) from None
    return {"status": "ok", "database": "ok"}
