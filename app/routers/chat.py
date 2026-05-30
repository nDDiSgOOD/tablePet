"""聊天 HTTP 路由 / Chat HTTP route.

Web 前端的 /chat 入口已收口到统一 agent。本路由只做协议转换，不再
直接调 services。业务逻辑请到 ``app.agent.graph`` 中维护。
"""

from __future__ import annotations

from fastapi import APIRouter

from ..agent import AgentInput, Channel, run_agent
from ..memory import DEFAULT_USER_ID
from ..schemas import ChatRequest

router = APIRouter()


@router.post("/chat")
async def chat(payload: ChatRequest) -> dict[str, str]:
    result = await run_agent(
        AgentInput(
            channel=Channel.WEB,
            device_id=payload.device_id,
            # 单用户系统：user_id 永远是 DEFAULT_USER_ID（"tablepet"），
            # 这样 web/wifi/usb 三端共享同一份 profile / memory / pet_state。
            # 之前误把 device_id 当 user_id，导致前端填的画像在 prompt 里读不到。
            user_id=DEFAULT_USER_ID,
            text=payload.text,
            want_tts=False,
        )
    )
    return {"reply": result.reply or (result.error or "")}
