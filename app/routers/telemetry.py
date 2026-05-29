"""设备遥测上报 / Device telemetry intake."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request

from .. import state

router = APIRouter()


@router.post("/telemetry")
async def telemetry(request: Request) -> dict[str, Any]:
    payload = await request.json()
    device_id = str(payload.get("device_id") or state.device_id_from_request(request))
    state.apply_telemetry_payload(device_id, payload, "wifi")
    return {"ok": True}
