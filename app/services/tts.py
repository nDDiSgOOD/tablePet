"""文本转语音服务 / TTS with edge-tts → say → SAPI fallback chain."""

from __future__ import annotations

import shutil
import subprocess
import tempfile
import time
import uuid
from pathlib import Path

import edge_tts  # type: ignore
from fastapi import HTTPException

from .. import state
from ..config import (
    AUDIO_DIR,
    CACHE_DIR,
    FFMPEG_BIN,
    MACOS_SAY_VOICE,
    TTS_CUTE_FILTER_ENABLED,
    TTS_EDGE_ENABLED,
    TTS_EDGE_RETRY_SECONDS,
    VOICE_PRESETS,
)
from ..schemas import TtsRequest
from ..utils.audio import cleanup_old_audio
from ..utils.ffmpeg import require_ffmpeg


# ---------------------------------------------------------------------------
# Windows SAPI / macOS say / edge-tts 三条回退路径
# ---------------------------------------------------------------------------
def _tts_windows_sapi(text: str, wav_path: Path) -> None:
    with tempfile.NamedTemporaryFile("w", suffix=".txt", encoding="utf-8", delete=False) as tmp:
        tmp.write(text)
        text_path = Path(tmp.name)

    script_path = CACHE_DIR / f"tts_{uuid.uuid4().hex}.ps1"
    script = r"""
param([string]$TextPath, [string]$OutPath)
Add-Type -AssemblyName System.Speech
$text = Get-Content -LiteralPath $TextPath -Raw -Encoding UTF8
  $synth = New-Object System.Speech.Synthesis.SpeechSynthesizer
try {
  $voice = $synth.GetInstalledVoices() |
    Where-Object { $_.VoiceInfo.Culture.Name -eq 'zh-CN' } |
    Select-Object -First 1
  if ($voice) { $synth.SelectVoice($voice.VoiceInfo.Name) }
  $synth.Rate = 2
  $synth.Volume = 100
  $format = New-Object System.Speech.AudioFormat.SpeechAudioFormatInfo(
    16000,
    [System.Speech.AudioFormat.AudioBitsPerSample]::Sixteen,
    [System.Speech.AudioFormat.AudioChannel]::Mono
  )
  $synth.SetOutputToWaveFile($OutPath, $format)
  $synth.Speak($text)
} finally {
  $synth.Dispose()
}
"""
    script_path.write_text(script, encoding="utf-8")
    try:
        subprocess.run(
            [
                "powershell.exe",
                "-NoProfile",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                str(script_path),
                str(text_path),
                str(wav_path),
            ],
            check=True,
            capture_output=True,
            text=True,
        )
    finally:
        text_path.unlink(missing_ok=True)
        script_path.unlink(missing_ok=True)


def _tts_macos_say(text: str, wav_path: Path) -> None:
    say_bin = shutil.which("say")
    if not say_bin:
        raise RuntimeError("macOS say command is not available")
    require_ffmpeg()
    aiff_path = wav_path.with_suffix(".aiff")
    cmd = [say_bin]
    if MACOS_SAY_VOICE:
        cmd.extend(["-v", MACOS_SAY_VOICE])
    cmd.extend(["-o", str(aiff_path), text])
    try:
        subprocess.run(cmd, check=True, capture_output=True, text=True)
        subprocess.run(
            [
                FFMPEG_BIN,
                "-y",
                "-loglevel",
                "error",
                "-i",
                str(aiff_path),
                "-ac",
                "1",
                "-ar",
                "16000",
                "-sample_fmt",
                "s16",
                str(wav_path),
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        if not wav_path.exists() or wav_path.stat().st_size < 2048:
            raise RuntimeError("macOS say produced an empty WAV")
    finally:
        aiff_path.unlink(missing_ok=True)


async def _tts_edge_neural(
    text: str, wav_path: Path, voice: str, rate: str, pitch: str, volume: str
) -> None:
    require_ffmpeg()
    mp3_path = wav_path.with_suffix(".mp3")
    communicate = edge_tts.Communicate(text, voice=voice, rate=rate, pitch=pitch, volume=volume)
    await communicate.save(str(mp3_path))
    try:
        subprocess.run(
            [
                FFMPEG_BIN,
                "-y",
                "-loglevel",
                "error",
                "-i",
                str(mp3_path),
                "-ac",
                "1",
                "-ar",
                "16000",
                "-sample_fmt",
                "s16",
                str(wav_path),
            ],
            check=True,
        )
        if not wav_path.exists() or wav_path.stat().st_size < 2048:
            raise RuntimeError("edge-tts produced an empty WAV")
    finally:
        mp3_path.unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# 萌系语音后处理 / Cute voice post-processing pipeline
# ---------------------------------------------------------------------------
def _apply_cute_voice_filter(wav_path: Path) -> None:
    """变调 + EQ + 压缩 + chorus + 噪声门 + 响度归一化。"""
    if not FFMPEG_BIN or not wav_path.exists():
        return
    if wav_path.stat().st_size < 2048:
        return

    tmp_path = wav_path.with_name(f"{wav_path.stem}_cute.wav")
    try:
        filter_chain = (
            "rubberband=pitch=1.35:tempo=0.96:formant=1:formant_q=1,"
            "anequalizer=c1=f=3000:w=1000:g=5:t=0:r=0,"
            "anequalizer=c1=f=200:w=400:g=3:t=0:r=0,"
            "acompressor=threshold=-18dB:ratio=3:attack=5:release=50,"
            "chorus=0.5:0.2:40:0.3:0.3:5:0.7,"
            "agate=threshold=-35dB:attack=2:release=50,"
            "loudnorm=I=-16:LRA=6:TP=-1.5,"
            "volume=1.15"
        )
        subprocess.run(
            [
                FFMPEG_BIN,
                "-y",
                "-loglevel",
                "error",
                "-i",
                str(wav_path),
                "-af",
                filter_chain,
                "-ac",
                "1",
                "-ar",
                "16000",
                "-sample_fmt",
                "s16",
                str(tmp_path),
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        if tmp_path.exists() and tmp_path.stat().st_size > 2048:
            tmp_path.replace(wav_path)
        else:
            print(
                f"Cute voice filter produced empty output for {wav_path.name}, using original"
            )
    except subprocess.CalledProcessError as exc:
        print(f"Cute voice filter failed for {wav_path.name}: stderr={exc.stderr[:200]}")
    except Exception as exc:
        print(f"Cute voice filter exception for {wav_path.name}: {exc}")
    finally:
        tmp_path.unlink(missing_ok=True)


def build_tts_request(text: str, preset_name: str = "fast") -> TtsRequest:
    preset = VOICE_PRESETS.get(preset_name, VOICE_PRESETS["cute"])
    return TtsRequest(
        text=text,
        voice=preset["voice"],
        rate=preset["rate"],
        pitch=preset["pitch"],
        volume=preset["volume"],
    )


# ---------------------------------------------------------------------------
# 业务入口 / Public entry point
# ---------------------------------------------------------------------------
async def generate_tts_wav(device_id: str, payload: TtsRequest, transport: str = "wifi") -> Path:
    started = time.perf_counter()
    cleanup_old_audio()

    clip_id = uuid.uuid4().hex
    wav_path = AUDIO_DIR / f"{clip_id}.wav"
    engine = "macos-say"

    voice_preset = None
    for pname, pconfig in VOICE_PRESETS.items():
        if pconfig["voice"] == payload.voice:
            voice_preset = pname
            break
    needs_cute_filter = TTS_CUTE_FILTER_ENABLED and (
        voice_preset == "cute" or "xiaoshuang" in payload.voice.lower()
    )

    async with state.TTS_LOCK:
        try:
            if not TTS_EDGE_ENABLED:
                raise RuntimeError("edge-tts disabled for low-latency local mode")
            if time.time() < state.EDGE_TTS_DISABLED_UNTIL:
                raise RuntimeError("edge-tts cooldown active")
            await _tts_edge_neural(
                payload.text,
                wav_path,
                payload.voice,
                payload.rate,
                payload.pitch,
                payload.volume,
            )
            engine = "edge-neural"
        except Exception as edge_exc:
            state.EDGE_TTS_DISABLED_UNTIL = time.time() + TTS_EDGE_RETRY_SECONDS
            wav_path.unlink(missing_ok=True)
            try:
                _tts_macos_say(payload.text, wav_path)
                if not wav_path.exists() or wav_path.stat().st_size < 2048:
                    raise RuntimeError("macos_say produced an empty WAV")
                engine = "macos-say"
            except Exception as mac_exc:
                wav_path.unlink(missing_ok=True)
                try:
                    _tts_windows_sapi(payload.text, wav_path)
                    if not wav_path.exists() or wav_path.stat().st_size < 2048:
                        raise RuntimeError("windows_sapi produced an empty WAV")
                    engine = "windows-sapi"
                except Exception as local_exc:
                    raise HTTPException(
                        status_code=500,
                        detail=(
                            f"TTS failed: edge={edge_exc}; "
                            f"macos_say={mac_exc}; windows_sapi={local_exc}"
                        ),
                    ) from local_exc

    if needs_cute_filter and wav_path.exists() and wav_path.stat().st_size > 2048:
        _apply_cute_voice_filter(wav_path)
        engine = f"{engine}-cute"

    state.update_device(
        device_id,
        last_tts_text=payload.text,
        last_tts_audio=wav_path.name,
        tts_engine=engine,
        last_tts_ms=round((time.perf_counter() - started) * 1000, 1),
        transport=transport,
    )
    state.remember_event(device_id, "TTS", payload.text)
    return wav_path
