"""桌宠人格切换 API.

后端只做：
- 列出所有可选人格
- 读 / 写 当前选择（存到 ``ui_settings`` 的 key ``pet_persona_mode``）

人格的 prompt 真正注入是在 ``app/agent/graph.py: node_llm`` 里。
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException

from ..memory import DEFAULT_USER_ID
from ..services.personas import (
    DEFAULT_MODE,
    PERSONAS,
    RANDOM_MODE,
    list_personas,
    resolve_active_persona,
)
from ..storage import get_setting, set_setting

router = APIRouter()

PERSONA_KEY = "pet_persona_mode"


@router.get("/api/personas")
async def api_list_personas() -> dict[str, Any]:
    """列出所有可选人格 + 当前生效的选择.

    返回 ``mode`` 是用户的"原始选择"（可能是 ``random``）；``effective`` 是
    *当下* 实际生效的人格 id（如果选了随机会真的随机一次）。
    """
    items = list_personas()
    # 多塞一条"随机"伪选项，前端可以把它当成普通选项渲染
    items_with_random = [
        {
            "id": RANDOM_MODE,
            "label": "🎲 随机人格",
            "emoji": "🎲",
            "description": "每次启动对话时随机抽一个人格，给你点惊喜。",
        },
        *items,
    ]
    mode = get_setting(DEFAULT_USER_ID, PERSONA_KEY) or DEFAULT_MODE
    effective = resolve_active_persona(mode)
    return {
        "items": items_with_random,
        "mode": mode,
        "effective": {
            "id": effective["id"],
            "label": effective["label"],
            "emoji": effective["emoji"],
            "description": effective["description"],
        },
    }


@router.put("/api/personas/active")
async def api_set_persona(payload: dict[str, Any]) -> dict[str, Any]:
    """切换人格。请求体 ``{"mode": "<persona_id> | random"}``."""
    mode = str(payload.get("mode") or "").strip().lower()
    if mode != RANDOM_MODE and mode not in PERSONAS:
        raise HTTPException(status_code=400, detail=f"unknown persona mode: {mode}")
    set_setting(DEFAULT_USER_ID, PERSONA_KEY, mode)
    effective = resolve_active_persona(mode)
    return {
        "ok": True,
        "mode": mode,
        "effective": {
            "id": effective["id"],
            "label": effective["label"],
            "emoji": effective["emoji"],
        },
    }
