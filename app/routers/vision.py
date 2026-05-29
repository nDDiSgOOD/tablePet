"""视觉 HTTP 路由 + 最近帧快照 / Vision endpoint and latest snapshot."""

from __future__ import annotations

from fastapi import APIRouter, Body, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse

from .. import state
from ..config import LATEST_FRAME_PATH
from ..services.vision import process_vision_jpeg

router = APIRouter()


@router.post("/vision")
async def vision(
    request: Request, image: bytes = Body(..., media_type="image/jpeg")
) -> JSONResponse:
    return JSONResponse(
        await process_vision_jpeg(state.device_id_from_request(request), image, "wifi")
    )


@router.get("/snapshot/latest.jpg")
async def latest_snapshot() -> FileResponse:
    if not LATEST_FRAME_PATH.exists():
        raise HTTPException(status_code=404, detail="No camera frame has been received yet.")
    return FileResponse(LATEST_FRAME_PATH, media_type="image/jpeg")
