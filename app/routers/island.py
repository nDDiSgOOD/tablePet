"""灵动岛伴生 App 中转 / Dynamic Island companion bridge.

设计：
  - 浏览器（dashboard.html）通过 ``POST /api/island/event`` 推送事件
    （sent / received / mood_change / energy_low），事件入内存环形队列。
  - 原生伴生 App（TablePetIsland.app）通过 ``GET /api/island/events`` 长轮询
    （或定时 1s 轮询）拉走事件 + 当前状态镜像；不需要任何外部依赖。
  - ``GET /api/island/state`` 返回最新状态快照（avatar / mood / name /
    energy / pending_text），伴生 App 启动时先拉一次做兜底。

故意不放 SQLite：岛只反映"实时"状态，重启即丢，行为和 macOS 通知一致；
持久化的"宠物状态"仍在 ``/api/pet/status`` 走原表。
"""

from __future__ import annotations

import asyncio
import time
from collections import deque
from typing import Any, Deque

from fastapi import APIRouter
from pydantic import BaseModel, Field

router = APIRouter()


# ---------------------------------------------------------------------------
# 内存状态 / In-memory state
# ---------------------------------------------------------------------------
_LOCK = asyncio.Lock()

# 事件环形缓冲（最多保留最近 64 条；伴生 App 每秒拉一次足够）
_EVENTS: Deque[dict[str, Any]] = deque(maxlen=64)
_NEXT_SEQ: int = 1

# 实时状态镜像（浏览器 push 来；伴生 App 用作 fallback）
_STATE: dict[str, Any] = {
    "name": "桌宠",
    "avatar": "",            # data: URL 或 http URL
    "mood": "neutral",       # happy / neutral / sleepy / hungry / excited / sick
    "mood_label": "在线",
    "energy": 100,
    "level": 1,
    "pending_text": "",      # 中间区域："待回复消息"
    "scene": "idle",         # idle | thinking | replying | alert
    "updated_at": 0.0,
}


class IslandStatePayload(BaseModel):
    name: str | None = None
    avatar: str | None = None
    mood: str | None = None
    mood_label: str | None = None
    energy: int | None = None
    level: int | None = None
    pending_text: str | None = None
    scene: str | None = None


class IslandEventPayload(BaseModel):
    """浏览器推送的事件。

    type 约定：
      - ``sent``         : 用户刚发了消息，岛上抖一下 + 显示"已发送"
      - ``thinking``     : LLM 思考中（可选，给 scene 染色用）
      - ``received``     : 收到 AI 回复，弹一段简短预览
      - ``mood_change``  : 宠物心情变化（happy → sleepy 等）
      - ``energy_low``   : 能量低于阈值
    """

    type: str
    text: str = ""
    mood: str | None = None
    energy: int | None = None
    extra: dict[str, Any] = Field(default_factory=dict)


@router.post("/api/island/state")
async def update_state(payload: IslandStatePayload) -> dict[str, Any]:
    """浏览器周期性把宠物当前状态推过来；非必填字段会忽略。"""
    async with _LOCK:
        for k, v in payload.model_dump(exclude_none=True).items():
            _STATE[k] = v
        _STATE["updated_at"] = time.time()
        return {"ok": True, "state": dict(_STATE)}


@router.get("/api/island/state")
async def get_state() -> dict[str, Any]:
    """伴生 App 启动时拉一次，作为 UI 初值。"""
    return dict(_STATE)


@router.post("/api/island/event")
async def push_event(payload: IslandEventPayload) -> dict[str, Any]:
    """浏览器推送一条岛事件到事件队列。同时按事件 hint 更新 state。"""
    global _NEXT_SEQ
    async with _LOCK:
        seq = _NEXT_SEQ
        _NEXT_SEQ += 1
        ev = {
            "seq": seq,
            "ts": time.time(),
            "type": payload.type,
            "text": payload.text,
            "extra": payload.extra,
        }
        if payload.mood:
            ev["mood"] = payload.mood
            _STATE["mood"] = payload.mood
        if payload.energy is not None:
            ev["energy"] = payload.energy
            _STATE["energy"] = payload.energy

        # 事件 → scene 映射
        # 关键语义：只有 received 才写 pending_text（=未读 AI 回复）。
        # sent / thinking 仅切 scene 让小呼吸点变色，岛保持折叠态，
        # 避免"用户刚发就把岛弹大"。
        if payload.type == "thinking":
            _STATE["scene"] = "thinking"
        elif payload.type == "sent":
            _STATE["scene"] = "thinking"
        elif payload.type == "received":
            _STATE["scene"] = "replying"
            _STATE["pending_text"] = (payload.text or "").strip()[:200]
        elif payload.type == "energy_low":
            _STATE["scene"] = "alert"
        elif payload.type == "mood_change":
            _STATE["scene"] = "idle"
        _STATE["updated_at"] = ev["ts"]

        _EVENTS.append(ev)
        return {"ok": True, "seq": seq}


@router.post("/api/island/read")
async def mark_read() -> dict[str, Any]:
    """把当前 pending_text 标记为已读 → 岛收回到折叠态。

    三个调用方对应"三种收回情况"：
      1. web 端用户在 chat 视图看到回复 / 切到 chat → island.markRead()
      2. 伴生 App 鼠标悬停在岛范围内 → APIPoller.markRead()
      3. 伴生 App 5s 未读自动超时 → APIPoller.markRead()
    """
    async with _LOCK:
        _STATE["pending_text"] = ""
        if _STATE.get("scene") in ("thinking", "replying"):
            _STATE["scene"] = "idle"
        _STATE["updated_at"] = time.time()
        return {"ok": True}


@router.get("/api/island/events")
async def get_events(since: int = 0) -> dict[str, Any]:
    """伴生 App 轮询：拿走 ``seq > since`` 的所有事件 + 当前 state。

    伴生 App 应记住最后看到的 ``last_seq``，下次带过去即可避免重复消费。
    """
    async with _LOCK:
        items = [e for e in _EVENTS if e["seq"] > since]
        last_seq = _EVENTS[-1]["seq"] if _EVENTS else 0
        return {
            "events": items,
            "last_seq": last_seq,
            "state": dict(_STATE),
        }


@router.post("/api/island/clear")
async def clear_pending() -> dict[str, Any]:
    """伴生 App 也可以反向通知后端：用户在岛上点掉了消息。"""
    async with _LOCK:
        _STATE["pending_text"] = ""
        _STATE["scene"] = "idle"
        _STATE["updated_at"] = time.time()
        return {"ok": True}
