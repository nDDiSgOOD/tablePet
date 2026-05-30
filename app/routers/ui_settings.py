"""UI 设置 / Per-user UI settings.

主题 / 背景图 / 布局偏好都走这里，按 ``key`` 命名空间存。
单一真理源在 SQLite ``ui_settings`` 表，前端可以保留 localStorage
作为离线缓存，但不再依赖它做持久化。
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException

from ..memory import DEFAULT_USER_ID
from ..storage import (
    delete_setting,
    get_setting,
    list_settings,
    set_setting,
)

router = APIRouter()


@router.get("/api/ui-settings")
async def api_list_settings() -> dict[str, Any]:
    """一次拿全部 UI 设置（前端启动用）."""
    return {"settings": list_settings(DEFAULT_USER_ID)}


@router.get("/api/ui-settings/{key}")
async def api_get_setting(key: str) -> dict[str, Any]:
    value = get_setting(DEFAULT_USER_ID, key)
    if value is None:
        raise HTTPException(status_code=404, detail="setting not found")
    return {"key": key, "value": value}


@router.put("/api/ui-settings/{key}")
async def api_set_setting(key: str, payload: dict[str, Any]) -> dict[str, Any]:
    """payload 必须含 ``value`` 字段，可以是任意 JSON-serializable 值."""
    if "value" not in payload:
        raise HTTPException(status_code=400, detail="value is required")
    try:
        set_setting(DEFAULT_USER_ID, key, payload["value"])
    except ValueError as exc:
        raise HTTPException(status_code=413, detail=str(exc)) from exc
    return {"ok": True}


@router.delete("/api/ui-settings/{key}")
async def api_delete_setting(key: str) -> dict[str, bool]:
    delete_setting(DEFAULT_USER_ID, key)
    return {"ok": True}
