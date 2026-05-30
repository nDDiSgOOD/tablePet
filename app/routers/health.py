"""健康检查 & 网关状态 / Health & gateway state endpoints."""

from __future__ import annotations

import time
from typing import Any

from fastapi import APIRouter

from .. import state
from ..config import (
    ASR_BEAM_SIZE,
    ASR_MIN_AUDIO_MS,
    ASR_MIN_RMS,
    ASR_MODEL_NAME,
    ASR_STRONG_RETRY_ENABLED,
    ASR_STRONG_RETRY_MIN_AUDIO_MS,
    ASR_STRONG_RETRY_MIN_RMS,
    DEEPSEEK_API_KEY,
    DEFAULT_VOICE,
    FFMPEG_BIN,
    MACOS_SAY_VOICE,
    MEMORY_FILE,
    MLX_TTS_COMMAND,
    MLX_TTS_MODEL,
    TTS_CUTE_FILTER_ENABLED,
    TTS_EDGE_ENABLED,
    TTS_ENGINE,
    YUNET_MODEL_PATH,
)
from ..memory import load_memory_store
from ..services.asr import WhisperModel
from ..services.vision import cv2

router = APIRouter()


@router.get("/health")
async def health() -> dict[str, Any]:
    return {
        "ok": True,
        "asr_model": ASR_MODEL_NAME,
        "asr_beam_size": ASR_BEAM_SIZE,
        "asr_available": WhisperModel is not None,
        "tts_voice": DEFAULT_VOICE,
        "tts_engine": TTS_ENGINE,
        "tts_edge_enabled": TTS_EDGE_ENABLED,
        "tts_cute_filter_enabled": TTS_CUTE_FILTER_ENABLED,
        "macos_say_voice": MACOS_SAY_VOICE,
        "mlx_tts_configured": bool(MLX_TTS_COMMAND),
        "mlx_tts_model": MLX_TTS_MODEL,
        "ffmpeg": bool(FFMPEG_BIN),
        "vision_available": cv2 is not None,
        "vision_engine": "yunet" if YUNET_MODEL_PATH.exists() else "haar-fallback",
        "deepseek_configured": bool(DEEPSEEK_API_KEY),
        "usb_bridge": dict(state.USB_BRIDGE_STATE),
    }


@router.get("/api/state")
async def api_state() -> dict[str, Any]:
    now = time.time()
    devices: dict[str, dict[str, Any]] = {}
    for device_id, item in state.DEVICE_STATES.items():
        copy = dict(item)
        copy["age_seconds"] = now - item.get("last_seen", now)
        devices[device_id] = copy
    memory_store = load_memory_store()
    memory_counts = {
        device_id: len(memory.get("profile", []))
        for device_id, memory in memory_store.items()
        if isinstance(memory, dict)
    }
    return {
        "ok": True,
        "devices": devices,
        "events": state.RECENT_EVENTS[-80:],
        "memory": {
            "profile_count": max(memory_counts.values(), default=0),
            "by_device": memory_counts,
        },
        "gateway": {
            "asr_available": WhisperModel is not None,
            "vision_available": cv2 is not None,
            "vision_engine": "yunet" if YUNET_MODEL_PATH.exists() else "haar-fallback",
            "ffmpeg": bool(FFMPEG_BIN),
            "deepseek_configured": bool(DEEPSEEK_API_KEY),
            "tts_voice": DEFAULT_VOICE,
            "tts_engine": TTS_ENGINE,
            "tts_edge_enabled": TTS_EDGE_ENABLED,
            "tts_cute_filter_enabled": TTS_CUTE_FILTER_ENABLED,
            "macos_say_voice": MACOS_SAY_VOICE,
            "mlx_tts_configured": bool(MLX_TTS_COMMAND),
            "mlx_tts_model": MLX_TTS_MODEL,
            "memory_file": str(MEMORY_FILE),
            "asr_beam_size": ASR_BEAM_SIZE,
            "asr_min_audio_ms": ASR_MIN_AUDIO_MS,
            "asr_min_rms": ASR_MIN_RMS,
            "asr_strong_retry_enabled": ASR_STRONG_RETRY_ENABLED,
            "asr_strong_retry_min_audio_ms": ASR_STRONG_RETRY_MIN_AUDIO_MS,
            "asr_strong_retry_min_rms": ASR_STRONG_RETRY_MIN_RMS,
            "edge_tts_cooldown_seconds": max(
                0, int(state.EDGE_TTS_DISABLED_UNTIL - time.time())
            ),
            "usb_bridge": dict(state.USB_BRIDGE_STATE),
        },
    }
