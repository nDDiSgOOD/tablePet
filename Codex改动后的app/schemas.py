"""请求/响应数据模型 / Pydantic request/response schemas."""

from __future__ import annotations

from typing import Any

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
    profile: dict[str, Any] = Field(default_factory=dict)


class InteractionRequest(BaseModel):
    device_id: str = "default-device"
    user_id: str = "default-user"
    source: str
    transport: str | None = None
    text: str | None = None
    event: str | None = None
    want_tts: bool = False
    locale: str | None = None
    extra: dict[str, Any] | None = None


class InteractionResponse(BaseModel):
    ok: bool
    interaction_id: str
    device_id: str
    user_text: str | None = None
    assistant_text: str | None = None
    intent: str
    dialogue_act: str | None = None
    emotional_tone: str | None = None
    audio_url: str | None = None
    device_action: dict[str, Any] | None = None
    state_update: dict[str, Any] | None = None
    memory_update: dict[str, Any] | None = None
    timing_ms: dict[str, float] = Field(default_factory=dict)
    debug: dict[str, Any] | None = None
    error: str | None = None


class AgentRuntimeState(BaseModel):
    interaction_id: str
    device_id: str
    user_id: str
    source: str
    transport: str | None = None
    raw_text: str | None = None
    user_text: str | None = None
    event: str | None = None
    want_tts: bool = False
    intent: str = "chat"
    dialogue_act: str | None = None
    emotional_tone: str | None = None
    profile: dict[str, Any] = Field(default_factory=dict)
    robot_state: dict[str, Any] = Field(default_factory=dict)
    relationship_state: dict[str, Any] = Field(default_factory=dict)
    session_state: dict[str, Any] = Field(default_factory=dict)
    telemetry_state: dict[str, Any] = Field(default_factory=dict)
    relevant_memory: str = ""
    relationship_context: str = ""
    sensor_context: str = ""
    conversation_context: str = ""
    anti_repetition_context: str = ""
    tool_context: str = ""
    tool_result: dict[str, Any] | None = None
    messages: list[dict[str, str]] = Field(default_factory=list)
    raw_model_output: Any = None
    assistant_text: str | None = None
    state_update: dict[str, Any] = Field(default_factory=dict)
    memory_update: dict[str, Any] = Field(default_factory=dict)
    audio_url: str | None = None
    device_action: dict[str, Any] | None = None
    timing_ms: dict[str, float] = Field(default_factory=dict)
    debug: dict[str, Any] = Field(default_factory=dict)
    errors: list[str] = Field(default_factory=list)


class ModelOutput(BaseModel):
    assistant_text: str
    state_update: dict[str, Any] = Field(default_factory=dict)
    memory_update: dict[str, Any] = Field(default_factory=dict)
    device_action: dict[str, Any] | None = None
    dialogue_act: str | None = None
    emotional_tone: str | None = None
