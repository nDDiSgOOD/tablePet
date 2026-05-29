"""聊天 HTTP 路由 / Chat HTTP route."""

from __future__ import annotations

from fastapi import APIRouter

from ..schemas import ChatRequest
from ..services.chat import process_chat

router = APIRouter()


@router.post("/chat")
async def chat(payload: ChatRequest) -> dict[str, str]:
    return await process_chat(payload, "wifi")
