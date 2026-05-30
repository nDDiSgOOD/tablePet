"""聊天 HTTP 路由 / Chat HTTP route."""

from __future__ import annotations

from fastapi import APIRouter

from ..schemas import ChatRequest, InteractionRequest
from ..services.interaction import process_interaction

router = APIRouter()


@router.post("/chat")
async def chat(payload: ChatRequest) -> dict[str, str]:
    result = await process_interaction(
        InteractionRequest(
            device_id=payload.device_id,
            user_id=payload.device_id,
            source="web_text",
            transport="wifi",
            text=payload.text,
            want_tts=False,
            extra={"profile": payload.profile, "history": [item.model_dump() for item in payload.history]},
        )
    )
    return {"reply": result.assistant_text or (result.error or "")}
