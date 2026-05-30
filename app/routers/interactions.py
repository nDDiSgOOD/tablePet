"""统一交互入口 / Unified interaction endpoint (WiFi / device JSON).

设备端通过 WiFi 直连网关时使用本路由。所有逻辑都收口到 ``run_agent``，
路由层只做契约映射。
"""

from __future__ import annotations

from fastapi import APIRouter

from ..agent import AgentInput, Channel, run_agent
from ..memory import DEFAULT_USER_ID
from ..schemas import InteractionRequest, InteractionResponse

router = APIRouter()


@router.post("/api/interactions")
async def interactions(payload: InteractionRequest) -> InteractionResponse:
    result = await run_agent(
        AgentInput(
            channel=Channel.WIFI,
            device_id=payload.device_id,
            # 单用户系统：忽略设备传的 user_id，统一使用 DEFAULT_USER_ID 让
            # 三端共享同一份 profile / memory / pet_state。
            user_id=DEFAULT_USER_ID,
            text=payload.text,
            event=payload.event,
            want_tts=payload.want_tts,
            locale=payload.locale,
            extra={
                **(payload.extra or {}),
                "source": payload.source,
                "transport": payload.transport,
            },
        )
    )
    return InteractionResponse(
        ok=result.ok,
        interaction_id=result.debug.get("interaction_id", ""),
        device_id=payload.device_id,
        user_text=(payload.text or "").strip() or None,
        assistant_text=result.reply or None,
        intent=result.intent,
        dialogue_act=result.dialogue_act,
        emotional_tone=result.emotional_tone,
        audio_url=result.audio_url,
        device_action=result.device_action,
        state_update=result.state_update,
        memory_update=result.memory_update,
        timing_ms=result.timing_ms,
        debug=result.debug,
        error=result.error,
    )
