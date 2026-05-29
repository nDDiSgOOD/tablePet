"""语音识别服务 / Speech-to-text service powered by faster-whisper."""

from __future__ import annotations

import asyncio
import re
import tempfile
import time
from pathlib import Path
from typing import Any

from fastapi import HTTPException

from .. import state
from ..config import (
    ASR_BEAM_SIZE,
    ASR_COMPUTE_TYPE,
    ASR_MIN_AUDIO_MS,
    ASR_MIN_RMS,
    ASR_MODEL_NAME,
    ASR_STRONG_RETRY_ENABLED,
    ASR_STRONG_RETRY_MIN_AUDIO_MS,
    ASR_STRONG_RETRY_MIN_RMS,
)
from ..utils.audio import wav_stats

try:
    from faster_whisper import WhisperModel
except Exception:  # pragma: no cover - 让 /health 反馈缺失
    WhisperModel = None  # type: ignore[assignment]


async def load_asr_model() -> "WhisperModel":
    """懒加载 faster-whisper 模型。/ Lazily load the faster-whisper model."""
    if WhisperModel is None:
        raise HTTPException(
            status_code=500,
            detail="faster-whisper is not installed. Run: pip install -r requirements.txt",
        )
    async with state.ASR_LOCK:
        if state.ASR_MODEL is None:
            state.ASR_MODEL = await asyncio.to_thread(
                WhisperModel,
                ASR_MODEL_NAME,
                device="auto",
                compute_type=ASR_COMPUTE_TYPE,
            )
    return state.ASR_MODEL


def _transcribe_asr_file(model: "WhisperModel", wav_path: Path, *, vad_filter: bool) -> tuple[str, Any]:
    kwargs: dict[str, Any] = {
        "language": "zh",
        "vad_filter": vad_filter,
        "beam_size": ASR_BEAM_SIZE,
        "temperature": 0.0,
        "condition_on_previous_text": False,
    }
    if vad_filter:
        kwargs["vad_parameters"] = {
            "min_silence_duration_ms": 420,
            "speech_pad_ms": 120,
        }
    segments, info = model.transcribe(str(wav_path), **kwargs)
    text = "".join(segment.text for segment in segments).strip()
    return text, info


def _looks_like_asr_loop(text: str) -> bool:
    """检测 ASR 复读幻觉。/ Detect repetition hallucinations."""
    compact = re.sub(r"[\s，。！？,.!?~～…]+", "", text)
    if len(compact) < 24:
        return False
    counts: dict[str, int] = {}
    for char in compact:
        counts[char] = counts.get(char, 0) + 1
    if counts and max(counts.values()) / len(compact) > 0.55:
        return True
    for size in (1, 2, 3):
        unit = compact[:size]
        if unit and unit * (len(compact) // size) == compact[: len(unit) * (len(compact) // size)]:
            return True
    return False


async def process_asr_wav(device_id: str, wav_bytes: bytes, transport: str = "wifi") -> dict[str, Any]:
    """对单段 WAV 跑 ASR，返回识别文本 + 元数据。"""
    received_at = time.perf_counter()
    if len(wav_bytes) < 48:
        raise HTTPException(status_code=400, detail="Expected a WAV body.")
    state.update_device(
        device_id,
        last_asr_bytes=len(wav_bytes),
        last_asr_started=time.time(),
        transport=transport,
    )

    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
        tmp.write(wav_bytes)
        tmp_path = Path(tmp.name)

    try:
        stats = wav_stats(tmp_path)
        if stats["duration_ms"] < ASR_MIN_AUDIO_MS or stats["rms"] < ASR_MIN_RMS:
            current = state.DEVICE_STATES.get(device_id, {})
            state.update_device(
                device_id,
                last_asr_blank_at=time.time(),
                last_asr_blank_count=int(current.get("last_asr_blank_count", 0)) + 1,
                last_asr_duration_ms=round(stats["duration_ms"], 1),
                last_asr_rms=round(stats["rms"], 1),
                last_asr_ms=round((time.perf_counter() - received_at) * 1000, 1),
                last_asr_skipped=True,
            )
            state.remember_event(device_id, "ASR", "(静音/太短)")
            return {"text": "", "language": "zh", "language_probability": 0.0, "skipped": True}

        model = await load_asr_model()
        retry_without_vad = False
        async with state.ASR_RUNTIME_LOCK:
            text, info = await asyncio.to_thread(
                _transcribe_asr_file, model, tmp_path, vad_filter=True
            )
            if (
                ASR_STRONG_RETRY_ENABLED
                and not text
                and stats["duration_ms"] >= ASR_STRONG_RETRY_MIN_AUDIO_MS
                and stats["rms"] >= ASR_STRONG_RETRY_MIN_RMS
            ):
                retry_without_vad = True
                text, info = await asyncio.to_thread(
                    _transcribe_asr_file, model, tmp_path, vad_filter=False
                )
        elapsed_ms = round((time.perf_counter() - received_at) * 1000, 1)
        fields: dict[str, Any] = {
            "last_asr_language": info.language,
            "last_asr_duration_ms": round(stats["duration_ms"], 1),
            "last_asr_rms": round(stats["rms"], 1),
            "last_asr_ms": elapsed_ms,
            "last_asr_skipped": False,
            "last_asr_retry_without_vad": retry_without_vad,
        }
        loop_filtered = bool(text and _looks_like_asr_loop(text))
        if text and not loop_filtered:
            fields["last_asr_text"] = text
            fields["last_asr_valid_at"] = time.time()
        else:
            current = state.DEVICE_STATES.get(device_id, {})
            fields["last_asr_blank_at"] = time.time()
            fields["last_asr_blank_count"] = int(current.get("last_asr_blank_count", 0)) + 1
            fields["last_asr_loop_filtered"] = loop_filtered
        state.update_device(device_id, **fields)
        blank_label = (
            "(循环幻觉已过滤)"
            if loop_filtered
            else ("(空白/已重试)" if retry_without_vad else "(空白)")
        )
        state.remember_event(
            device_id, "ASR", text if text and not loop_filtered else blank_label
        )
        return {
            "text": "" if loop_filtered else text,
            "language": info.language,
            "language_probability": info.language_probability,
        }
    finally:
        tmp_path.unlink(missing_ok=True)
