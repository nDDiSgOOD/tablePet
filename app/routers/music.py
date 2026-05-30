"""音乐资源 HTTP 路由 / Music resource HTTP routes."""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

from .. import state
from ..config import JAY_CHOU_DIR, MEDIA_DIR, MUSIC_CACHE_DIR
from ..services.music import convert_music_to_wav, jay_chou_wav, music_candidates
from ..usb.protocol import usb_active_device_id
from ..utils.audio import write_demo_music

router = APIRouter()


@router.get("/music/default.wav")
async def default_music() -> FileResponse:
    path = MEDIA_DIR / "default.wav"
    if not path.exists() or path.stat().st_size < 120_000:
        write_demo_music(path)
    return FileResponse(path, media_type="audio/wav")


@router.get("/music/jay-chou.wav")
async def jay_chou_music() -> FileResponse:
    try:
        wav_path, label = await asyncio.to_thread(jay_chou_wav)
    except Exception as exc:
        raise HTTPException(
            status_code=404,
            detail=(
                f"No local Jay Chou music found in {JAY_CHOU_DIR}, "
                f"and official preview API failed: {exc}"
            ),
        ) from exc
    state.remember_event(usb_active_device_id(), "MUSIC", label)
    return FileResponse(wav_path, media_type="audio/wav")


@router.get("/music/library/{filename}")
async def music_library_file(filename: str) -> FileResponse:
    path = MUSIC_CACHE_DIR / filename
    if not path.exists() or path.suffix.lower() != ".wav":
        raise HTTPException(status_code=404, detail="Music not found.")
    return FileResponse(path, media_type="audio/wav")
