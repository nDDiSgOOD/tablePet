"""ASR HTTP 路由 / ASR HTTP route."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request

from .. import state
from ..services.asr import process_asr_adpcm, process_asr_wav

router = APIRouter()


@router.post("/asr")
async def asr(request: Request) -> dict[str, Any]:
    return await process_asr_wav(state.device_id_from_request(request), await request.body(), "wifi")


@router.post("/asr/adpcm")
async def asr_adpcm(request: Request) -> dict[str, Any]:
    return await process_asr_adpcm(
        state.device_id_from_request(request), await request.body(), "wifi-adpcm"
    )
