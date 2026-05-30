"""Unified interaction endpoint for text, voice, button, and system events."""

from __future__ import annotations

from fastapi import APIRouter

from ..schemas import InteractionRequest, InteractionResponse
from ..services.interaction import process_interaction

router = APIRouter()


@router.post("/api/interactions")
async def interactions(payload: InteractionRequest) -> InteractionResponse:
    return await process_interaction(payload)
