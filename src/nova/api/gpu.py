"""CLI 首版受保护接口；所有本地加载都经统一 admission，不提供云端回退。"""

import hmac
import ipaddress
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict, Field, StrictBool

from nova.errors import ApplicationError
from nova.gpu.manager import GpuManager

router = APIRouter(prefix="/api/gpu", tags=["gpu"])


async def require_gpu(request: Request):
    settings = request.app.state.settings
    if not settings.gpu_enabled:
        raise ApplicationError("GPU_DISABLED", "GPU 管理未启用", 503)
    if request.headers.get("origin"):
        raise ApplicationError("GPU_LOCAL_CLI_ONLY", "本轮管理接口仅供本机命令行调用", 403)
    host = request.client.host if request.client else ""
    try:
        local = ipaddress.ip_address(host).is_loopback
    except ValueError:
        local = host == "testclient" and settings.environment == "test"
    if not local:
        raise ApplicationError("GPU_LOCAL_ONLY", "GPU 管理仅允许本机访问", 403)
    expected = "Bearer " + settings.gpu_token.get_secret_value()
    received = request.headers.get("authorization", "")
    if not hmac.compare_digest(expected.encode("utf-8"), received.encode("utf-8")):
        raise ApplicationError("GPU_UNAUTHORIZED", "需要 GPU 管理令牌", 401)
    return request.app.state.gpu_manager


class ModeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    mode: str = Field(pattern="^(AUTO|GAMING)$")


class GenerateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    prompt: str = Field(min_length=1, max_length=8000)


class TrainingClaim(BaseModel):
    model_config = ConfigDict(extra="forbid")
    job_id: UUID
    required_mib: int = Field(ge=1, le=65536, strict=True)


class TrainingRelease(BaseModel):
    model_config = ConfigDict(extra="forbid")
    lease_id: UUID
    stopped: StrictBool


ManagedGpu = Annotated[GpuManager, Depends(require_gpu)]


@router.get("/status")
async def status(manager: ManagedGpu):
    return await manager.status()


@router.post("/mode")
async def mode(body: ModeRequest, manager: ManagedGpu):
    return await manager.set_mode(body.mode)


@router.post("/unload")
async def unload(manager: ManagedGpu):
    return await manager.unload()


@router.post("/wake")
async def wake(manager: ManagedGpu):
    return await manager.wake()


@router.post("/generate")
async def generate(body: GenerateRequest, manager: ManagedGpu):
    return await manager.generate(body.prompt)


@router.post("/training/claim")
async def claim_training(body: TrainingClaim, manager: ManagedGpu):
    return await manager.claim_training(str(body.job_id), body.required_mib)


@router.post("/training/release")
async def release_training(body: TrainingRelease, manager: ManagedGpu):
    return await manager.release_training(str(body.lease_id), stopped=body.stopped)
