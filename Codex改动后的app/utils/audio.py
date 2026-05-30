"""WAV / PCM 音频统计与生成 / WAV/PCM stats & generation."""

from __future__ import annotations

import math
import time
import wave
from pathlib import Path

import numpy as np

from ..config import AUDIO_DIR


def wav_stats(wav_path: Path) -> dict[str, float]:
    """计算 WAV 时长和 RMS。/ Return duration_ms + rms for the given WAV."""
    try:
        with wave.open(str(wav_path), "rb") as wav:
            sample_rate = wav.getframerate() or 16000
            frames = wav.getnframes()
            sample_width = wav.getsampwidth()
            raw = wav.readframes(frames)
    except wave.Error:
        return {"duration_ms": 0.0, "rms": 0.0}
    duration_ms = frames * 1000.0 / float(sample_rate)
    if not raw or sample_width != 2:
        return {"duration_ms": duration_ms, "rms": 0.0}
    samples = np.frombuffer(raw, dtype=np.int16).astype(np.float32)
    if samples.size == 0:
        return {"duration_ms": duration_ms, "rms": 0.0}
    return {
        "duration_ms": duration_ms,
        "rms": float(np.sqrt(np.mean(samples * samples))),
    }


def write_demo_music(path: Path) -> None:
    """生成一段 16 kHz 单声道示例旋律。/ Synthesize a fallback demo tune."""
    sample_rate = 16000
    melody = [
        523.25, 659.25, 783.99, 1046.5, 987.77, 783.99, 659.25, 523.25,
        587.33, 739.99, 880.0, 1174.66, 1046.5, 880.0, 739.99, 587.33,
    ]
    bass = [261.63, 392.0, 329.63, 440.0]
    pcm: list[int] = []
    for idx, freq in enumerate(melody * 3):
        duration = 0.26
        count = int(sample_rate * duration)
        bass_freq = bass[(idx // 4) % len(bass)]
        for i in range(count):
            t = i / sample_rate
            envelope = min(1.0, i / 320) * min(1.0, (count - i) / 600)
            lead = math.sin(2 * math.pi * freq * t) + 0.35 * math.sin(2 * math.pi * freq * 2 * t)
            pad = math.sin(2 * math.pi * bass_freq * t) * 0.45
            value = int((lead * 0.65 + pad) * 8500 * envelope)
            pcm.append(value)
    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(sample_rate)
        wav.writeframes(np.asarray(pcm, dtype=np.int16).tobytes())


def cleanup_old_audio(max_age_seconds: int = 900) -> None:
    """清理过期的 TTS 缓存。/ Drop stale TTS WAV files."""
    now = time.time()
    for file in AUDIO_DIR.glob("*.wav"):
        try:
            if now - file.stat().st_mtime > max_age_seconds:
                file.unlink(missing_ok=True)
        except OSError:
            pass
