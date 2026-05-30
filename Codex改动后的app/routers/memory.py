"""用户简介与记忆管理路由 / User profile and memory management routes."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException

from ..config import USB_DEFAULT_DEVICE_ID
from ..memory import (
    add_profile_fact,
    clear_short_term_memory,
    delete_profile_fact,
    device_memory,
    get_user_profile,
    save_user_profile,
)

router = APIRouter()


@router.get("/api/user/profile")
async def get_default_profile() -> dict[str, Any]:
    return get_user_profile(USB_DEFAULT_DEVICE_ID)


@router.put("/api/user/profile")
async def put_default_profile(payload: dict[str, Any]) -> dict[str, Any]:
    return {"ok": True, "profile": save_user_profile(payload, USB_DEFAULT_DEVICE_ID)}


@router.get("/api/memory/{device_id}")
async def get_memory(device_id: str) -> dict[str, Any]:
    memory = device_memory(device_id)
    return {
        "profile": memory.get("profile", []),
        "recent": memory.get("recent", []),
        "user_profile": memory.get("user_profile", {}),
    }


@router.post("/api/memory/{device_id}")
async def add_memory(device_id: str, payload: dict[str, Any]) -> dict[str, bool]:
    fact = str(payload.get("fact") or "").strip()
    if not fact:
        raise HTTPException(status_code=400, detail="fact is required")
    add_profile_fact(device_id, fact)
    return {"ok": True}


@router.delete("/api/memory/{device_id}/profile/{idx}")
async def delete_memory(device_id: str, idx: int) -> dict[str, bool]:
    if not delete_profile_fact(device_id, idx):
        raise HTTPException(status_code=404, detail="profile fact not found")
    return {"ok": True}


@router.post("/api/memory/{device_id}/clear-short-term")
async def clear_recent_memory(device_id: str) -> dict[str, bool]:
    clear_short_term_memory(device_id)
    return {"ok": True}
