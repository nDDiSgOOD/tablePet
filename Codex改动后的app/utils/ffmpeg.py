"""ffmpeg 相关辅助 / ffmpeg helpers."""

from __future__ import annotations

from fastapi import HTTPException

from ..config import FFMPEG_BIN


def require_ffmpeg() -> None:
    if not FFMPEG_BIN:
        raise HTTPException(
            status_code=500,
            detail="ffmpeg is required for TTS conversion.",
        )
