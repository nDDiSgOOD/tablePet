"""TTS HTTP 路由 + 音频文件分发 / TTS endpoint and generated WAV serving."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse

from .. import state
from ..config import AUDIO_DIR
from ..schemas import TtsRequest
from ..services.tts import generate_tts_wav

router = APIRouter()


@router.post("/tts")
async def tts(payload: TtsRequest, request: Request) -> dict[str, str]:
    wav_path = await generate_tts_wav(state.device_id_from_request(request), payload, "wifi")
    return {"audio_url": f"/audio/{wav_path.name}"}


@router.get("/audio/{filename}")
async def audio_file(filename: str) -> FileResponse:
    path = AUDIO_DIR / filename
    if not path.exists() or path.suffix.lower() != ".wav":
        raise HTTPException(status_code=404, detail="Audio not found.")
    return FileResponse(path, media_type="audio/wav")
