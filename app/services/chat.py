"""DeepSeek 对话代理 / DeepSeek chat proxy with memory + vision context."""

from __future__ import annotations

import time

import httpx
from fastapi import HTTPException

from .. import state
from ..config import DEEPSEEK_API_KEY, DEEPSEEK_MODEL, DEEPSEEK_URL
from ..memory import memory_context, update_memory_after_chat
from ..schemas import ChatRequest
from .weather import is_weather_request, weather_reply


async def process_chat(payload: ChatRequest, transport: str = "wifi") -> dict[str, str]:
    if not DEEPSEEK_API_KEY:
        raise HTTPException(
            status_code=500, detail="Set DEEPSEEK_API_KEY for gateway chat proxy."
        )

    started = time.perf_counter()

    # 直接命中天气意图，无需走 LLM。/ Short-circuit obvious weather intents.
    if is_weather_request(payload.text):
        reply = await weather_reply(payload.text)
        state.update_device(
            payload.device_id,
            last_chat_ms=round((time.perf_counter() - started) * 1000, 1),
            transport=transport,
        )
        state.remember_event(payload.device_id, "WEATHER", reply)
        return {"reply": reply}

    memory_text = memory_context(payload.device_id)

    system_prompt = (
        "你是一个放在桌面上的对话小机器人。专门当话唠和朋友聊天，10%概率触发反驳和吐槽，偶尔开开玩笑"
        "回答自然、温暖、简短，通常 20 到 160 个中文字符"
        "当前硬件只有摄像头、麦克风和扬声器，不要假装有屏幕、机械臂或舵机。"
        "默认中文，除非用户提出要你说其它语言"
        f"\n{payload.vision}"
        f"\n{memory_text}"
    )
    messages: list[dict[str, str]] = [{"role": "system", "content": system_prompt}]
    for item in payload.history[-12:]:
        if item.role in {"user", "assistant"}:
            messages.append({"role": item.role, "content": item.content})
    messages.append({"role": "user", "content": payload.text})

    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.post(
            DEEPSEEK_URL,
            headers={
                "Authorization": f"Bearer {DEEPSEEK_API_KEY}",
                "Content-Type": "application/json",
            },
            json={
                "model": DEEPSEEK_MODEL,
                "messages": messages,
                "temperature": 0.68,
                "max_tokens": 140,
                "stream": False,
                "thinking": {"type": "disabled"},
            },
        )

    if response.status_code != 200:
        raise HTTPException(status_code=502, detail=response.text)

    data = response.json()
    reply = data["choices"][0]["message"]["content"].strip()
    update_memory_after_chat(payload.device_id, payload.text, reply)
    state.update_device(
        payload.device_id,
        last_chat_ms=round((time.perf_counter() - started) * 1000, 1),
        transport=transport,
    )
    return {"reply": reply}
