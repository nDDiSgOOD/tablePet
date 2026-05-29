"""请求/响应数据模型 / Pydantic request/response schemas."""

from __future__ import annotations

from pydantic import BaseModel, Field

from .config import DEFAULT_VOICE


class TtsRequest(BaseModel):
    text: str = Field(min_length=1, max_length=500)
    voice: str = DEFAULT_VOICE
    rate: str = "+10%"
    pitch: str = "+18Hz"
    volume: str = "+0%"


class ChatMessage(BaseModel):
    role: str
    content: str


class ChatRequest(BaseModel):
    text: str = Field(min_length=1, max_length=1000)
    vision: str = ""
    device_id: str = "tablepet"
    history: list[ChatMessage] = Field(default_factory=list)
