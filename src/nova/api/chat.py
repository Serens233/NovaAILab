"""版本化聊天API；沿用回环与Bearer鉴权，由Next同源服务端代理调用。"""

import asyncio
import json
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, Field, StrictBool

from nova.api.gpu import require_gpu
from nova.chat.service import read_events
from nova.errors import ApplicationError

router = APIRouter(prefix="/api/v1", tags=["chat"])


async def require_chat(request: Request, manager: Annotated[object, Depends(require_gpu)]):
    if not request.app.state.settings.chat_enabled:
        raise ApplicationError("CHAT_DISABLED", "Nova聊天尚未启用", 503)
    if not request.app.state.ready:
        raise ApplicationError("CHAT_NOT_READY", "聊天服务尚未就绪", 503)
    return request.app.state.chat_service


Chat = Annotated[object, Depends(require_chat)]


class CreateThread(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    title: str = Field(default="新对话", min_length=1, max_length=100)
    model: str = Field(default="qwen3.5:9b", min_length=1, max_length=128)


class RenameThread(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    title: str = Field(min_length=1, max_length=100)


class ChatRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    thread_id: UUID
    model: str = Field(min_length=1, max_length=128)
    content: str = Field(min_length=1, max_length=4000)
    request_id: UUID
    cloud_consent: bool = False
    stream: bool = True
    think: StrictBool | None = None


class CancelChat(BaseModel):
    model_config = ConfigDict(extra="forbid")
    thread_id: UUID
    request_id: UUID


@router.get("/models")
async def models(chat: Chat):
    return await chat.registry.list()


@router.get("/threads")
async def threads(chat: Chat):
    return {"threads": await asyncio.to_thread(chat.store.list_threads)}


@router.post("/threads", status_code=201)
async def create_thread(body: CreateThread, chat: Chat):
    try:
        chat.registry.get(body.model)
    except ApplicationError as error:
        if error.code != "MODEL_UNKNOWN":
            raise
        await chat.registry.resolve(body.model)
    return await asyncio.to_thread(chat.store.create_thread, body.title, body.model)


@router.get("/threads/{thread_id}")
async def thread(thread_id: UUID, chat: Chat):
    identity = str(thread_id)
    return {
        "thread": await asyncio.to_thread(chat.store.get_thread, identity),
        "messages": await asyncio.to_thread(chat.store.get_messages, identity),
    }


@router.patch("/threads/{thread_id}")
async def rename(thread_id: UUID, body: RenameThread, chat: Chat):
    return await asyncio.to_thread(chat.store.rename_thread, str(thread_id), body.title)


@router.delete("/threads/{thread_id}", status_code=204)
async def delete(thread_id: UUID, chat: Chat):
    await asyncio.to_thread(chat.store.delete_thread, str(thread_id))
    return Response(status_code=204)


@router.post("/chat")
async def generate(body: ChatRequest, chat: Chat):
    queue = await chat.open(
        str(body.thread_id),
        body.model,
        body.content,
        str(body.request_id),
        body.cloud_consent,
        think=body.think,
    )
    if not body.stream:
        async for event in read_events(queue):
            if event["type"] == "error":
                raise ApplicationError(event["code"], event["message"], 503)
            if event["type"] == "done":
                return {"thread_id": str(body.thread_id), "message": event["message"]}

    async def stream():
        async for event in read_events(queue):
            yield "data: " + json.dumps(event, ensure_ascii=False) + "\n\n"

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache, no-store", "X-Accel-Buffering": "no"},
    )


@router.post("/chat/cancel")
async def cancel(body: CancelChat, chat: Chat):
    return await chat.cancel(str(body.thread_id), str(body.request_id))
