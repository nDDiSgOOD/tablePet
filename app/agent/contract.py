"""Agent 对外契约 / Agent IO contract.

这是 web / wifi / usb 三个传输层共同使用的统一数据结构。

设计哲学
--------
**入参极简**：只传"是谁、从哪来、说了什么、有什么临时上下文"。
**历史和画像由 agent 自己管**：history / profile / 关系记忆 等长期状态都从
``app.memory`` / ``services.relationship_memory`` 加载，**调用方不再传**。

任何新增的传输通道（蓝牙 / MQTT / WebSocket 等）都应只构造 ``AgentInput``，
不允许绕过 agent 直接调用底层 services。
"""

from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class Channel(str, Enum):
    """传输通道 / Transport channel.

    用于 agent 内部做差异化处理（如 USB 不返回 audio_url、web 默认不开 TTS 等）。
    """

    WEB = "web"
    WIFI = "wifi"
    USB = "usb"


class AgentInput(BaseModel):
    """Agent 入参 / Unified input across channels.

    Attributes:
        channel: 传输通道，必填。
        device_id: 设备/会话 ID，用于在 memory 中定位会话。
        user_id: 用户 ID，用于人格化记忆维度。
        text: 当前这一轮用户说的话。
        event: 设备按键 / 系统事件（与 text 二选一即可）。
        want_tts: 是否需要返回 audio_url。
        locale: 区域设置（影响 prompt / TTS 语种）。
        extra: 临时上下文（vision 描述、传感器读数、source/transport 等附加字段）。
               **不要往里塞 history / profile**——那些 agent 自己从 memory 取。
    """

    channel: Channel
    device_id: str = "default-device"
    user_id: str = "default-user"

    text: str | None = None
    event: str | None = None
    want_tts: bool = False
    locale: str | None = None

    extra: dict[str, Any] = Field(default_factory=dict)


class AgentOutput(BaseModel):
    """Agent 出参 / Unified output across channels.

    刻意 **不返回 history**：前端如需展示历史，请单独走
    ``GET /api/memory/{device_id}``（差异化职责）。
    """

    ok: bool = True
    reply: str = ""
    intent: str = "chat"
    dialogue_act: str | None = None
    emotional_tone: str | None = None
    audio_url: str | None = None
    device_action: dict[str, Any] | None = None
    state_update: dict[str, Any] | None = None
    memory_update: dict[str, Any] | None = None
    timing_ms: dict[str, float] = Field(default_factory=dict)
    debug: dict[str, Any] = Field(default_factory=dict)
    error: str | None = None
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0

