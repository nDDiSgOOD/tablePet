from __future__ import annotations

import asyncio
import json
import math
import os
import re
import shutil
import struct
import subprocess
import tempfile
import threading
import time
import uuid
import wave
import zlib
from pathlib import Path
from typing import Any
from urllib.parse import quote

import edge_tts
import httpx
import imageio_ffmpeg
import numpy as np
from fastapi import Body, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from pydantic import BaseModel, Field

try:
    import cv2
except Exception:  # pragma: no cover - lets ASR/TTS run without OpenCV
    cv2 = None

try:
    from faster_whisper import WhisperModel
except Exception:  # pragma: no cover - reported by /health and /asr
    WhisperModel = None

try:
    import serial
    from serial.tools import list_ports
except Exception:  # pragma: no cover - USB bridge is optional
    serial = None
    list_ports = None


APP_DIR = Path(__file__).resolve().parent
CACHE_DIR = APP_DIR / ".cache"
AUDIO_DIR = CACHE_DIR / "audio"
MUSIC_CACHE_DIR = CACHE_DIR / "music"
MEDIA_DIR = APP_DIR / "media"
JAY_CHOU_DIR = MEDIA_DIR / "jay_chou"
MODELS_DIR = APP_DIR / "models"
MEMORY_FILE = CACHE_DIR / "memory.json"
LATEST_FRAME_PATH = CACHE_DIR / "latest_frame.jpg"
YUNET_MODEL_PATH = MODELS_DIR / "face_detection_yunet_2023mar.onnx"
for directory in (CACHE_DIR, AUDIO_DIR, MUSIC_CACHE_DIR, MEDIA_DIR, JAY_CHOU_DIR, MODELS_DIR):
    directory.mkdir(parents=True, exist_ok=True)


def _load_firmware_deepseek_key() -> str:
    firmware_path = APP_DIR.parent / "src" / "main.cpp"
    try:
        text = firmware_path.read_text(encoding="utf-8")
    except OSError:
        return ""
    match = re.search(r'DEEPSEEK_API_KEY\s*=\s*"([^"]+)"', text)
    if not match:
        return ""
    key = match.group(1).strip()
    return "" if key.startswith("YOUR_") else key


DEEPSEEK_API_KEY = os.getenv(
    "DEEPSEEK_API_KEY", "") or _load_firmware_deepseek_key()
DEEPSEEK_URL = os.getenv(
    "DEEPSEEK_URL", "https://api.deepseek.com/chat/completions")
DEEPSEEK_MODEL = os.getenv("DEEPSEEK_MODEL", "deepseek-v4-flash")
ASR_MODEL_NAME = os.getenv("TABLEPET_ASR_MODEL", "small")
ASR_COMPUTE_TYPE = os.getenv("TABLEPET_ASR_COMPUTE_TYPE", "int8")
ASR_BEAM_SIZE = int(os.getenv("TABLEPET_ASR_BEAM_SIZE", "1"))
ASR_MIN_AUDIO_MS = int(os.getenv("TABLEPET_ASR_MIN_AUDIO_MS", "360"))
ASR_MIN_RMS = float(os.getenv("TABLEPET_ASR_MIN_RMS", "80"))
ASR_STRONG_RETRY_ENABLED = os.getenv(
    "TABLEPET_ASR_STRONG_RETRY_ENABLED", "0") == "1"
ASR_STRONG_RETRY_MIN_AUDIO_MS = int(
    os.getenv("TABLEPET_ASR_STRONG_RETRY_MIN_AUDIO_MS", "650"))
ASR_STRONG_RETRY_MIN_RMS = float(
    os.getenv("TABLEPET_ASR_STRONG_RETRY_MIN_RMS", "450"))
DEFAULT_VOICE = os.getenv("TABLEPET_TTS_VOICE", "zh-TW-HsiaoChenNeural")
TTS_EDGE_ENABLED = os.getenv("TABLEPET_TTS_EDGE_ENABLED", "0") == "1"
TTS_EDGE_RETRY_SECONDS = int(
    os.getenv("TABLEPET_TTS_EDGE_RETRY_SECONDS", "20"))
TTS_CUTE_FILTER_ENABLED = os.getenv(
    "TABLEPET_TTS_CUTE_FILTER_ENABLED", "0") == "1"
MACOS_SAY_VOICE = os.getenv(
    "TABLEPET_MACOS_SAY_VOICE", "Flo (Chinese (China mainland))")
WEATHER_LOCATION = os.getenv("TABLEPET_WEATHER_LOCATION", "Toronto")
FFMPEG_BIN = os.getenv("FFMPEG_BIN") or shutil.which(
    "ffmpeg") or imageio_ffmpeg.get_ffmpeg_exe()
USB_BRIDGE_ENABLED = os.getenv("TABLEPET_USB_ENABLED", "1") == "1"
USB_SERIAL_PORT = os.getenv("TABLEPET_USB_PORT", "auto")
USB_SERIAL_BAUD = int(os.getenv("TABLEPET_USB_BAUD", "921600"))
USB_DEFAULT_DEVICE_ID = os.getenv(
    "TABLEPET_USB_DEVICE_ID", "tablepet-xiao-s3-001")
USB_MAGIC = b"TPU1"
USB_HEADER = struct.Struct("<4sBBHII")
USB_MAX_PAYLOAD = int(
    os.getenv("TABLEPET_USB_MAX_PAYLOAD", str(3 * 1024 * 1024)))

USB_ASR_WAV = 1
USB_ASR_JSON = 2
USB_CHAT_JSON = 3
USB_CHAT_JSON_RESP = 4
USB_TTS_JSON = 5
USB_TTS_WAV = 6
USB_VISION_JPEG = 7
USB_VISION_JSON = 8
USB_TELEMETRY_JSON = 9
USB_MUSIC_JSON = 10
USB_MUSIC_WAV = 11
USB_ERROR_JSON = 12
USB_HELLO_JSON = 13
USB_HELLO_ACK_JSON = 14
USB_ASR_ADPCM = 15  # ADPCM-compressed audio for ASR (4:1 ratio)
USB_ASR_ADPCM_HEADER_SIZE = 4  # predictor(2) + stepIndex(1) + pad(1)

VOICE_PRESETS: dict[str, dict[str, str]] = {
    "fast": {
        "voice": DEFAULT_VOICE,
        "rate": "-4%",
        "pitch": "+0Hz",
        "volume": "-30%",
    },
    "taiwan": {
        "voice": "zh-TW-HsiaoChenNeural",
        "rate": "-4%",
        "pitch": "+0Hz",
        "volume": "-30%",
    },
    "cute": {
        "voice": "zh-TW-HsiaoYuNeural",
        "rate": "-2%",
        "pitch": "+8Hz",
        "volume": "-30%",
    },
}


app = FastAPI(title="TablePet Gateway", version="1.0.0")

_asr_model: WhisperModel | None = None
_asr_lock = asyncio.Lock()
_asr_runtime_lock = asyncio.Lock()
_tts_lock = asyncio.Lock()
_vision_lock = asyncio.Lock()
_edge_tts_disabled_until = 0.0
DEVICE_STATES: dict[str, dict[str, Any]] = {}
RECENT_EVENTS: list[dict[str, Any]] = []
USB_BRIDGE_STATE: dict[str, Any] = {
    "enabled": USB_BRIDGE_ENABLED,
    "available": serial is not None,
    "port": USB_SERIAL_PORT,
    "baud": USB_SERIAL_BAUD,
    "device_id": USB_DEFAULT_DEVICE_ID,
    "connected": False,
    "frames_rx": 0,
    "frames_tx": 0,
    "bytes_rx": 0,
    "bytes_tx": 0,
    "last_rx": 0.0,
    "last_tx": 0.0,
    "last_error": "",
}
_usb_thread_started = False


def _device_id(request: Request, fallback: str = "unknown") -> str:
    return request.headers.get("x-device-id") or fallback


def _remember_event(device_id: str, kind: str, detail: str) -> None:
    RECENT_EVENTS.append(
        {
            "ts": time.time(),
            "device_id": device_id,
            "kind": kind,
            "detail": detail[:300],
        }
    )
    del RECENT_EVENTS[:-80]


def _update_device(device_id: str, **fields: Any) -> dict[str, Any]:
    item = DEVICE_STATES.setdefault(device_id, {"device_id": device_id})
    item.update(fields)
    item["last_seen"] = time.time()
    return item


def _apply_telemetry_payload(device_id: str, payload: dict[str, Any], transport: str) -> None:
    payload = dict(payload)
    payload.pop("device_id", None)
    if isinstance(payload.get("vision"), dict):
        existing_vision = DEVICE_STATES.get(device_id, {}).get("vision", {})
        if isinstance(existing_vision, dict):
            payload["vision"] = {**existing_vision, **payload["vision"]}
    payload["transport"] = transport
    _update_device(device_id, **payload)


def _load_memory_store() -> dict[str, Any]:
    if not MEMORY_FILE.exists():
        return {}
    try:
        data = json.loads(MEMORY_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _save_memory_store(data: dict[str, Any]) -> None:
    tmp = MEMORY_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False,
                   indent=2), encoding="utf-8")
    tmp.replace(MEMORY_FILE)


def _device_memory(device_id: str) -> dict[str, Any]:
    store = _load_memory_store()
    memory = store.setdefault(device_id, {"profile": [], "recent": []})
    memory.setdefault("profile", [])
    memory.setdefault("recent", [])
    return memory


def _memory_context(device_id: str) -> str:
    memory = _device_memory(device_id)
    profile = memory.get("profile", [])[-12:]
    recent = memory.get("recent", [])[-8:]
    if not profile and not recent:
        return "长期记忆：暂时没有可靠记忆。"
    lines = ["长期记忆："]
    for item in profile:
        lines.append(f"- {item}")
    if recent:
        lines.append("最近互动摘要：")
        for item in recent:
            lines.append(
                f"- 用户：{item.get('user', '')[:80]} / 回复：{item.get('assistant', '')[:80]}")
    return "\n".join(lines)


def _update_memory_after_chat(device_id: str, user_text: str, assistant_text: str) -> None:
    store = _load_memory_store()
    memory = store.setdefault(device_id, {"profile": [], "recent": []})
    profile: list[str] = memory.setdefault("profile", [])
    recent: list[dict[str, str]] = memory.setdefault("recent", [])

    recent.append(
        {"user": user_text[:240], "assistant": assistant_text[:240], "ts": f"{time.time():.0f}"})
    del recent[:-30]

    useful_markers = ("我叫", "我是", "我的", "我喜欢", "我不喜欢", "记住", "以后叫我", "我想要")
    if any(marker in user_text for marker in useful_markers):
        fact = user_text.strip()
        if fact and fact not in profile:
            profile.append(fact[:160])
            del profile[:-24]

    _save_memory_store(store)


class TtsRequest(BaseModel):
    text: str = Field(min_length=1, max_length=500)
    voice: str = DEFAULT_VOICE
    rate: str = "+10%"
    pitch: str = "+18Hz"
    volume: str = "+0%"


class ChatMessage(BaseModel):
    role: str
    content: str


class ChatRequest(BaseModel):
    text: str = Field(min_length=1, max_length=1000)
    vision: str = ""
    device_id: str = "tablepet"
    history: list[ChatMessage] = Field(default_factory=list)


def _require_ffmpeg() -> None:
    if not FFMPEG_BIN:
        raise HTTPException(
            status_code=500,
            detail="ffmpeg is required for TTS conversion.",
        )


def _wav_stats(wav_path: Path) -> dict[str, float]:
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


async def _load_asr_model() -> WhisperModel:
    global _asr_model
    if WhisperModel is None:
        raise HTTPException(
            status_code=500,
            detail="faster-whisper is not installed. Run: pip install -r server/requirements.txt",
        )
    async with _asr_lock:
        if _asr_model is None:
            _asr_model = await asyncio.to_thread(
                WhisperModel,
                ASR_MODEL_NAME,
                device="auto",
                compute_type=ASR_COMPUTE_TYPE,
            )
    return _asr_model


def _write_demo_music(path: Path) -> None:
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
            lead = math.sin(2 * math.pi * freq * t) + 0.35 * \
                math.sin(2 * math.pi * freq * 2 * t)
            pad = math.sin(2 * math.pi * bass_freq * t) * 0.45
            value = int((lead * 0.65 + pad) * 8500 * envelope)
            pcm.append(value)
    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(sample_rate)
        wav.writeframes(np.asarray(pcm, dtype=np.int16).tobytes())


def _music_candidates(query: str) -> list[Path]:
    terms = query.lower()
    roots: list[Path]
    if any(token in terms for token in ("周杰伦", "周杰倫", "jay", "chou")):
        roots = [JAY_CHOU_DIR]
    else:
        roots = [MEDIA_DIR]
    exts = {".wav", ".mp3", ".m4a", ".flac", ".aac", ".ogg"}
    files: list[Path] = []
    for root in roots:
        if not root.exists():
            continue
        for path in root.rglob("*"):
            if path.is_file() and path.suffix.lower() in exts:
                files.append(path)
    return sorted(files, key=lambda p: p.name.lower())


def _convert_music_to_wav(source: Path) -> Path:
    _require_ffmpeg()
    stat = source.stat()
    cache_name = f"{source.stem}_{stat.st_size}_{int(stat.st_mtime)}.wav"
    out_path = MUSIC_CACHE_DIR / re.sub(r"[^A-Za-z0-9_.-]+", "_", cache_name)
    if out_path.exists() and out_path.stat().st_size > 4096:
        return out_path
    subprocess.run(
        [
            FFMPEG_BIN,
            "-y",
            "-loglevel",
            "error",
            "-i",
            str(source),
            "-ac",
            "1",
            "-ar",
            "16000",
            "-sample_fmt",
            "s16",
            str(out_path),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    return out_path


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
    _require_ffmpeg()
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


async def _tts_edge_neural(text: str, wav_path: Path, voice: str, rate: str, pitch: str, volume: str) -> None:
    _require_ffmpeg()
    mp3_path = wav_path.with_suffix(".mp3")
    communicate = edge_tts.Communicate(
        text, voice=voice, rate=rate, pitch=pitch, volume=volume)
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


def _apply_cute_voice_filter(wav_path: Path) -> None:
    """Professional cute voice processing pipeline.

    Applies a multi-stage audio processing chain to make the TTS voice
    sound warmer, brighter, and more youthful — like a cute desktop companion.

    Pipeline stages:
    1. Pitch shift up (+6 semitones) — makes voice sound younger
    2. Formant preservation filter — keeps speech intelligible
    3. EQ bright boost — adds sparkle to high frequencies
    4. Gentle compression — evens out volume variations
    5. Subtle stereo widening (if stereo) / chorus effect
    6. Noise gate — removes background hiss
    7. Normalize — consistent output volume
    """
    if not FFMPEG_BIN or not wav_path.exists():
        return
    if wav_path.stat().st_size < 2048:
        return

    tmp_path = wav_path.with_name(f"{wav_path.stem}_cute.wav")
    try:
        # Professional cute voice filter chain
        filter_chain = (
            # Stage 1: Pitch shift (rubberband) + formant preservation
            "rubberband=pitch=1.35:tempo=0.96:formant=1:formant_q=1,"
            # Stage 2: High-shelf EQ boost (+6 dB at 3 kHz+) for brightness
            "anequalizer=c1=f=3000:w=1000:g=5:t=0:r=0,"
            # Stage 3: Low-shelf for warmth (+3 dB at 200 Hz)
            "anequalizer=c1=f=200:w=400:g=3:t=0:r=0,"
            # Stage 4: Gentle compression
            "acompressor=threshold=-18dB:ratio=3:attack=5:release=50,"
            # Stage 5: Subtle chorus for richness
            "chorus=0.5:0.2:40:0.3:0.3:5:0.7,"
            # Stage 6: Noise gate
            "agate=threshold=-35dB:attack=2:release=50,"
            # Stage 7: Normalize to consistent level
            "loudnorm=I=-16:LRA=6:TP=-1.5,"
            # Stage 8: Volume boost
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
                "-af", filter_chain,
                "-ac", "1",
                "-ar", "16000",
                "-sample_fmt", "s16",
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
                f"Cute voice filter produced empty output for {wav_path.name}, using original")
    except subprocess.CalledProcessError as exc:
        print(
            f"Cute voice filter failed for {wav_path.name}: stderr={exc.stderr[:200]}")
    except Exception as exc:
        print(f"Cute voice filter exception for {wav_path.name}: {exc}")
    finally:
        tmp_path.unlink(missing_ok=True)


def build_tts_request(text: str, preset_name: str = "fast") -> TtsRequest:
    """Build a TTS request using a named voice preset."""
    preset = VOICE_PRESETS.get(preset_name, VOICE_PRESETS["cute"])
    return TtsRequest(
        text=text,
        voice=preset["voice"],
        rate=preset["rate"],
        pitch=preset["pitch"],
        volume=preset["volume"],
    )


def _cleanup_old_audio(max_age_seconds: int = 900) -> None:
    now = time.time()
    for file in AUDIO_DIR.glob("*.wav"):
        try:
            if now - file.stat().st_mtime > max_age_seconds:
                file.unlink(missing_ok=True)
        except OSError:
            pass


@app.get("/", response_class=HTMLResponse)
async def dashboard() -> str:
    return """
<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>TablePet 状态</title>
  <style>
    :root { color-scheme: dark; --bg:#0f1216; --panel:#171d23; --line:#2a333d; --text:#edf3f7; --muted:#98a6b3; --ok:#50d890; --bad:#ff6b6b; --warn:#ffd166; --accent:#70b8ff; }
    * { box-sizing: border-box; }
    body { margin:0; font:15px/1.45 system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; background:var(--bg); color:var(--text); }
    header { padding:18px 22px; border-bottom:1px solid var(--line); display:flex; align-items:center; justify-content:space-between; gap:16px; }
    h1 { margin:0; font-size:20px; font-weight:700; letter-spacing:0; }
    main { max-width:1120px; margin:0 auto; padding:20px; display:grid; gap:16px; }
    .grid { display:grid; grid-template-columns:repeat(4,minmax(0,1fr)); gap:12px; }
    .panel { background:var(--panel); border:1px solid var(--line); border-radius:8px; padding:14px; min-width:0; }
    .label { color:var(--muted); font-size:12px; margin-bottom:6px; }
    .value { font-size:24px; font-weight:750; overflow-wrap:anywhere; }
    .small { color:var(--muted); font-size:13px; }
    .ok { color:var(--ok); }
    .bad { color:var(--bad); }
    .warn { color:var(--warn); }
    .accent { color:var(--accent); }
    .wide { grid-column:1 / -1; }
    table { width:100%; border-collapse:collapse; }
    td, th { padding:9px 6px; border-bottom:1px solid var(--line); text-align:left; vertical-align:top; }
    th { color:var(--muted); font-size:12px; font-weight:600; }
    code { color:#cfe7ff; }
    .events { display:grid; gap:8px; }
    .event { display:flex; gap:10px; align-items:baseline; border-bottom:1px solid var(--line); padding-bottom:8px; }
    .event time { color:var(--muted); min-width:78px; font-size:12px; }
    .sentence { min-height:44px; font-size:16px; overflow-wrap:anywhere; }
    .snapshot { width:100%; max-height:360px; object-fit:contain; border-radius:8px; border:1px solid var(--line); background:#07090b; }
    .stagegrid { display:grid; grid-template-columns:repeat(3,minmax(0,1fr)); gap:12px; }
    .stage { border:1px solid var(--line); border-radius:8px; padding:12px; background:#111820; }
    .stage strong { display:block; margin-bottom:6px; color:#edf3f7; }
    .stage span { color:var(--muted); display:block; font-size:13px; }
    @media (max-width: 820px) { .grid { grid-template-columns:repeat(2,minmax(0,1fr)); } }
    @media (max-width: 520px) { header { align-items:flex-start; flex-direction:column; } .grid { grid-template-columns:1fr; } }
  </style>
</head>
<body>
  <header>
    <h1>TablePet 状态</h1>
    <div id="clock" class="small">连接中...</div>
  </header>
  <main>
    <section class="grid">
      <div class="panel"><div class="label">设备在线</div><div id="online" class="value">--</div><div id="lastSeen" class="small">--</div></div>
      <div class="panel"><div class="label">当前模式</div><div id="mode" class="value">--</div><div id="session" class="small">--</div></div>
      <div class="panel"><div class="label">麦克风</div><div id="mic" class="value">--</div><div id="noise" class="small">--</div></div>
      <div class="panel"><div class="label">视觉</div><div id="vision" class="value">--</div><div id="emotion" class="small">--</div></div>
    </section>
    <section class="grid">
      <div class="panel"><div class="label">最后 ASR 识别</div><div id="lastAsr" class="sentence">--</div><div id="asrMeta" class="small">--</div></div>
      <div class="panel"><div class="label">最后回复</div><div id="lastTts" class="sentence">--</div><div id="ttsMeta" class="small">--</div></div>
      <div class="panel"><div class="label">长期记忆</div><div id="memory" class="value">--</div><div id="memoryMeta" class="small">网关持久化</div></div>
      <div class="panel"><div class="label">视觉引擎</div><div id="visionEngine" class="value">--</div><div id="visionMeta" class="small">--</div></div>
    </section>
    <section class="panel wide">
      <div class="label">最近摄像头画面</div>
      <img id="snapshot" class="snapshot" alt="latest camera frame">
    </section>
    <section class="panel wide">
      <div class="label">下一代产品架构路线</div>
      <div class="stagegrid">
        <div class="stage"><strong>当前演示原型</strong><span>ESP32S3 负责采集与播放，电脑网关负责 ASR、LLM、TTS、视觉。优点是快迭代，缺点是延迟受 WiFi、电脑和云端影响。</span></div>
        <div class="stage"><strong>Gen 2 低延迟架构</strong><span>设备端只做唤醒、AEC、VAD、音频流；边缘网关拆成 ASR、对话、TTS、视觉四个 worker；语音走流式链路，TTS 分句边合成边播放。</span></div>
        <div class="stage"><strong>量产硬件方向</strong><span>主控升级到 Linux SoC 或 ESP32 加独立 AI 模组；麦克风换双麦或四麦阵列；音频加入硬件 AEC/DSP；摄像头用 MIPI/高动态范围模组，电源和散热独立设计。</span></div>
      </div>
    </section>
    <section class="panel wide">
      <div class="label">硬件状态</div>
      <table><tbody id="deviceRows"></tbody></table>
    </section>
    <section class="panel wide">
      <div class="label">最近事件</div>
      <div id="events" class="events small"></div>
    </section>
  </main>
  <script>
    const $ = (id) => document.getElementById(id);
    const yes = (v) => v ? '<span class="ok">正常</span>' : '<span class="bad">异常</span>';
    const ageText = (seconds) => seconds == null ? '--' : `${seconds.toFixed(1)} 秒前`;
    function eventTime(ts) { return ts ? new Date(ts * 1000).toLocaleTimeString() : '--'; }
    async function refresh() {
      try {
        const data = await (await fetch('/api/state', {cache:'no-store'})).json();
        const devices = Object.values(data.devices || {});
        const d = devices[0] || {};
        const online = d.age_seconds != null && d.age_seconds < 12;
        $('clock').textContent = '刷新 ' + new Date().toLocaleTimeString();
        $('online').innerHTML = online ? '<span class="ok">在线</span>' : '<span class="bad">离线</span>';
        $('lastSeen').textContent = '最后上报：' + ageText(d.age_seconds);
        $('mode').textContent = d.mode || '--';
        $('session').textContent = d.session ? '长对话中' : '等待唤醒';
        $('mic').textContent = d.mic_rms != null ? Math.round(d.mic_rms) : '--';
        $('noise').textContent = d.noise_floor != null ? '噪声底：' + Math.round(d.noise_floor) : '--';
        const v = d.vision || {};
        const usb = (data.gateway && data.gateway.usb_bridge) || {};
        $('vision').innerHTML = v.face ? '<span class="ok">看到脸</span>' : '<span class="warn">未见脸</span>';
        $('emotion').textContent = `表情 ${v.emotion || '--'}，注视 ${v.attention ? '是' : '否'}，置信度 ${v.confidence ?? '--'}`;
        $('lastAsr').textContent = d.last_asr_text || '还没有识别到有效语音';
        $('asrMeta').textContent = d.last_asr_bytes ? `ASR ${d.last_asr_ms ?? '--'} ms，录音 ${d.last_asr_duration_ms ?? '--'} ms，RMS ${d.last_asr_rms ?? '--'}，空白 ${d.last_asr_blank_count || 0}${d.last_asr_retry_without_vad ? '，强语音兜底' : ''}` : '--';
        $('lastTts').textContent = d.last_tts_text || '--';
        $('ttsMeta').textContent = d.tts_engine ? `TTS ${d.tts_engine}，${d.last_tts_ms ?? '--'} ms，LLM ${d.last_chat_ms ?? '--'} ms` : '--';
        const memoryCount = (data.memory && data.memory.profile_count) || 0;
        $('memory').textContent = memoryCount;
        $('visionEngine').textContent = v.engine || (data.gateway && data.gateway.vision_engine) || '--';
        $('visionMeta').textContent = v.orientation != null ? `方向 ${v.orientation}°，视觉 ${v.vision_ms ?? d.last_vision_ms ?? '--'} ms` : `视觉 ${v.vision_ms ?? d.last_vision_ms ?? '--'} ms`;
        if (v.snapshot || (d.camera && d.camera_frames)) {
          $('snapshot').src = '/snapshot/latest.jpg?ts=' + Date.now();
        }
        $('deviceRows').innerHTML = `
          <tr><th>项目</th><th>值</th></tr>
          <tr><td>设备 ID</td><td><code>${d.device_id || '--'}</code></td></tr>
          <tr><td>数据通道</td><td>${d.transport || '--'}，USB ${usb.connected ? '<span class="ok">已连接</span>' : '<span class="warn">待机/备用</span>'}</td></tr>
          <tr><td>USB 桥接</td><td><code>${usb.port || '--'}</code> @ ${usb.baud || '--'}，rx ${usb.frames_rx || 0}，tx ${usb.frames_tx || 0}${usb.last_error ? '，' + usb.last_error : ''}</td></tr>
          <tr><td>IP</td><td><code>${d.wifi_ip || '--'}</code></td></tr>
          <tr><td>WiFi</td><td>${yes(d.wifi)}</td></tr>
          <tr><td>摄像头</td><td>${yes(d.camera)}</td></tr>
          <tr><td>音频</td><td>${yes(d.audio)}</td></tr>
          <tr><td>录音中</td><td>${d.recording ? '<span class="accent">是</span>' : '否'}</td></tr>
          <tr><td>语音队列</td><td>${d.queue_depth ?? 0}</td></tr>
          <tr><td>播放中</td><td>${d.playback ? '是' : '否'}</td></tr>
          <tr><td>警报</td><td>${d.alert ? '<span class="warn">触发</span>' : '无'}</td></tr>
          <tr><td>相机帧数</td><td>${d.camera_frames ?? '--'}</td></tr>
          <tr><td>录音句数</td><td>${d.utterances ?? '--'}，丢弃 ${d.dropped_utterances ?? 0}</td></tr>
          <tr><td>设备侧耗时</td><td>录音 ${d.last_record_ms ?? '--'} ms，ASR ${d.last_asr_ms_device ?? '--'} ms，LLM ${d.last_chat_ms_device ?? '--'} ms，TTS ${d.last_tts_ms_device ?? '--'} ms，视觉 ${d.last_vision_ms_device ?? '--'} ms</td></tr>
          <tr><td>可用堆内存</td><td>${d.free_heap ?? '--'}</td></tr>
        `;
        const events = (data.events || []).slice(-12).reverse();
        $('events').innerHTML = events.length ? events.map(e => `<div class="event"><time>${eventTime(e.ts)}</time><strong>${e.kind}</strong><span>${e.detail || ''}</span></div>`).join('') : '暂无事件';
      } catch (e) {
        $('clock').textContent = '连接失败：' + e;
      }
    }
    refresh();
    setInterval(refresh, 1000);
  </script>
</body>
</html>
"""


@app.get("/health")
async def health() -> dict[str, Any]:
    return {
        "ok": True,
        "asr_model": ASR_MODEL_NAME,
        "asr_beam_size": ASR_BEAM_SIZE,
        "asr_available": WhisperModel is not None,
        "tts_voice": DEFAULT_VOICE,
        "tts_edge_enabled": TTS_EDGE_ENABLED,
        "tts_cute_filter_enabled": TTS_CUTE_FILTER_ENABLED,
        "macos_say_voice": MACOS_SAY_VOICE,
        "ffmpeg": bool(FFMPEG_BIN),
        "vision_available": cv2 is not None,
        "vision_engine": "yunet" if YUNET_MODEL_PATH.exists() else "haar-fallback",
        "deepseek_configured": bool(DEEPSEEK_API_KEY),
        "usb_bridge": dict(USB_BRIDGE_STATE),
    }


@app.get("/api/state")
async def api_state() -> dict[str, Any]:
    now = time.time()
    devices = {}
    for device_id, item in DEVICE_STATES.items():
        copy = dict(item)
        copy["age_seconds"] = now - item.get("last_seen", now)
        devices[device_id] = copy
    memory_store = _load_memory_store()
    memory_counts = {
        device_id: len(memory.get("profile", []))
        for device_id, memory in memory_store.items()
        if isinstance(memory, dict)
    }
    return {
        "ok": True,
        "devices": devices,
        "events": RECENT_EVENTS[-80:],
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
            "tts_edge_enabled": TTS_EDGE_ENABLED,
            "tts_cute_filter_enabled": TTS_CUTE_FILTER_ENABLED,
            "macos_say_voice": MACOS_SAY_VOICE,
            "memory_file": str(MEMORY_FILE),
            "asr_beam_size": ASR_BEAM_SIZE,
            "asr_min_audio_ms": ASR_MIN_AUDIO_MS,
            "asr_min_rms": ASR_MIN_RMS,
            "asr_strong_retry_enabled": ASR_STRONG_RETRY_ENABLED,
            "asr_strong_retry_min_audio_ms": ASR_STRONG_RETRY_MIN_AUDIO_MS,
            "asr_strong_retry_min_rms": ASR_STRONG_RETRY_MIN_RMS,
            "edge_tts_cooldown_seconds": max(0, int(_edge_tts_disabled_until - time.time())),
            "usb_bridge": dict(USB_BRIDGE_STATE),
        },
    }


def _transcribe_asr_file(model: WhisperModel, wav_path: Path, *, vad_filter: bool) -> tuple[str, Any]:
    kwargs: dict[str, Any] = {
        "language": "zh",
        "vad_filter": vad_filter,
        "beam_size": ASR_BEAM_SIZE,
        "temperature": 0.0,
        "condition_on_previous_text": False,
    }
    if vad_filter:
        kwargs["vad_parameters"] = {
            "min_silence_duration_ms": 420, "speech_pad_ms": 120}
    segments, info = model.transcribe(str(wav_path), **kwargs)
    text = "".join(segment.text for segment in segments).strip()
    return text, info


def _looks_like_asr_loop(text: str) -> bool:
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


async def _process_asr_wav(device_id: str, wav_bytes: bytes, transport: str = "wifi") -> dict[str, Any]:
    received_at = time.perf_counter()
    if len(wav_bytes) < 48:
        raise HTTPException(status_code=400, detail="Expected a WAV body.")
    _update_device(device_id, last_asr_bytes=len(wav_bytes),
                   last_asr_started=time.time(), transport=transport)

    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
        tmp.write(wav_bytes)
        tmp_path = Path(tmp.name)

    try:
        stats = _wav_stats(tmp_path)
        if stats["duration_ms"] < ASR_MIN_AUDIO_MS or stats["rms"] < ASR_MIN_RMS:
            current = DEVICE_STATES.get(device_id, {})
            _update_device(
                device_id,
                last_asr_blank_at=time.time(),
                last_asr_blank_count=int(current.get(
                    "last_asr_blank_count", 0)) + 1,
                last_asr_duration_ms=round(stats["duration_ms"], 1),
                last_asr_rms=round(stats["rms"], 1),
                last_asr_ms=round(
                    (time.perf_counter() - received_at) * 1000, 1),
                last_asr_skipped=True,
            )
            _remember_event(device_id, "ASR", "(静音/太短)")
            return {"text": "", "language": "zh", "language_probability": 0.0, "skipped": True}

        model = await _load_asr_model()
        retry_without_vad = False
        async with _asr_runtime_lock:
            text, info = await asyncio.to_thread(_transcribe_asr_file, model, tmp_path, vad_filter=True)
            if (
                ASR_STRONG_RETRY_ENABLED
                and
                not text
                and stats["duration_ms"] >= ASR_STRONG_RETRY_MIN_AUDIO_MS
                and stats["rms"] >= ASR_STRONG_RETRY_MIN_RMS
            ):
                retry_without_vad = True
                text, info = await asyncio.to_thread(_transcribe_asr_file, model, tmp_path, vad_filter=False)
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
            current = DEVICE_STATES.get(device_id, {})
            fields["last_asr_blank_at"] = time.time()
            fields["last_asr_blank_count"] = int(
                current.get("last_asr_blank_count", 0)) + 1
            fields["last_asr_loop_filtered"] = loop_filtered
        _update_device(device_id, **fields)
        blank_label = "(循环幻觉已过滤)" if loop_filtered else (
            "(空白/已重试)" if retry_without_vad else "(空白)")
        _remember_event(device_id, "ASR",
                        text if text and not loop_filtered else blank_label)
        return {
            "text": "" if loop_filtered else text,
            "language": info.language,
            "language_probability": info.language_probability,
        }
    finally:
        tmp_path.unlink(missing_ok=True)


@app.post("/asr")
async def asr(request: Request) -> dict[str, Any]:
    return await _process_asr_wav(_device_id(request), await request.body(), "wifi")


async def _generate_tts_wav(device_id: str, payload: TtsRequest, transport: str = "wifi") -> Path:
    global _edge_tts_disabled_until
    started = time.perf_counter()
    _cleanup_old_audio()

    clip_id = uuid.uuid4().hex
    wav_path = AUDIO_DIR / f"{clip_id}.wav"
    engine = "macos-say"

    # Determine which voice preset to use
    # If the payload voice matches a known preset, apply cute post-filter
    voice_preset = None
    for pname, pconfig in VOICE_PRESETS.items():
        if pconfig["voice"] == payload.voice:
            voice_preset = pname
            break
    needs_cute_filter = TTS_CUTE_FILTER_ENABLED and (
        voice_preset == "cute" or "xiaoshuang" in payload.voice.lower()
    )

    async with _tts_lock:
        edge_exc: Exception | None = None
        try:
            if not TTS_EDGE_ENABLED:
                raise RuntimeError(
                    "edge-tts disabled for low-latency local mode")
            if time.time() < _edge_tts_disabled_until:
                raise RuntimeError("edge-tts cooldown active")
            await _tts_edge_neural(payload.text, wav_path, payload.voice, payload.rate, payload.pitch, payload.volume)
            engine = "edge-neural"
        except Exception as edge_exc:
            _edge_tts_disabled_until = time.time() + TTS_EDGE_RETRY_SECONDS
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
                        raise RuntimeError(
                            "windows_sapi produced an empty WAV")
                    engine = "windows-sapi"
                except Exception as local_exc:
                    raise HTTPException(
                        status_code=500,
                        detail=f"TTS failed: edge={edge_exc}; macos_say={mac_exc}; windows_sapi={local_exc}",
                    ) from local_exc

    # Apply cute voice post-processing filter
    if needs_cute_filter and wav_path.exists() and wav_path.stat().st_size > 2048:
        _apply_cute_voice_filter(wav_path)
        engine = f"{engine}-cute"

    _update_device(
        device_id,
        last_tts_text=payload.text,
        last_tts_audio=wav_path.name,
        tts_engine=engine,
        last_tts_ms=round((time.perf_counter() - started) * 1000, 1),
        transport=transport,
    )
    _remember_event(device_id, "TTS", payload.text)
    return wav_path


@app.post("/tts")
async def tts(payload: TtsRequest, request: Request) -> dict[str, str]:
    wav_path = await _generate_tts_wav(_device_id(request), payload, "wifi")
    return {"audio_url": f"/audio/{wav_path.name}"}


@app.get("/audio/{filename}")
async def audio_file(filename: str) -> FileResponse:
    path = AUDIO_DIR / filename
    if not path.exists() or path.suffix.lower() != ".wav":
        raise HTTPException(status_code=404, detail="Audio not found.")
    return FileResponse(path, media_type="audio/wav")


@app.get("/music/default.wav")
async def default_music() -> FileResponse:
    path = MEDIA_DIR / "default.wav"
    if not path.exists() or path.stat().st_size < 120_000:
        _write_demo_music(path)
    return FileResponse(path, media_type="audio/wav")


@app.get("/music/jay-chou.wav")
async def jay_chou_music() -> FileResponse:
    candidates = _music_candidates("周杰伦")
    if not candidates:
        raise HTTPException(
            status_code=404,
            detail=f"No local Jay Chou music found in {JAY_CHOU_DIR}.",
        )
    wav_path = await asyncio.to_thread(_convert_music_to_wav, candidates[0])
    _remember_event(_usb_active_device_id(), "MUSIC", candidates[0].stem)
    return FileResponse(wav_path, media_type="audio/wav")


@app.get("/music/library/{filename}")
async def music_library_file(filename: str) -> FileResponse:
    path = MUSIC_CACHE_DIR / filename
    if not path.exists() or path.suffix.lower() != ".wav":
        raise HTTPException(status_code=404, detail="Music not found.")
    return FileResponse(path, media_type="audio/wav")


@app.get("/snapshot/latest.jpg")
async def latest_snapshot() -> FileResponse:
    if not LATEST_FRAME_PATH.exists():
        raise HTTPException(
            status_code=404, detail="No camera frame has been received yet.")
    return FileResponse(LATEST_FRAME_PATH, media_type="image/jpeg")


def _rotations(frame: np.ndarray) -> list[tuple[int, np.ndarray]]:
    return [
        (0, frame),
        (90, cv2.rotate(frame, cv2.ROTATE_90_CLOCKWISE)),
        (180, cv2.rotate(frame, cv2.ROTATE_180)),
        (270, cv2.rotate(frame, cv2.ROTATE_90_COUNTERCLOCKWISE)),
    ]


def _detect_with_yunet(frame: np.ndarray) -> dict[str, Any] | None:
    if cv2 is None or not hasattr(cv2, "FaceDetectorYN") or not YUNET_MODEL_PATH.exists():
        return None

    best: dict[str, Any] | None = None
    for orientation, work in _rotations(frame):
        height, width = work.shape[:2]
        detector = cv2.FaceDetectorYN.create(
            str(YUNET_MODEL_PATH),
            "",
            (width, height),
            0.55,
            0.3,
            5000,
        )
        _, faces = detector.detect(work)
        if faces is None:
            continue
        for face in faces:
            x, y, w, h = [float(v) for v in face[:4]]
            score = float(face[-1])
            area_ratio = max(0.0, w * h / float(width * height))
            rank = score * (0.4 + area_ratio)
            if best is None or rank > best["rank"]:
                best = {
                    "engine": "yunet",
                    "rank": rank,
                    "frame": work,
                    "box": (x, y, w, h),
                    "score": score,
                    "orientation": orientation,
                    "landmarks": face[4:14].reshape(5, 2).tolist() if len(face) >= 14 else [],
                }
    return best


def _detect_with_haar(frame: np.ndarray) -> dict[str, Any] | None:
    face_cascade = cv2.CascadeClassifier(
        str(Path(cv2.data.haarcascades) / "haarcascade_frontalface_default.xml"))
    best: dict[str, Any] | None = None
    for orientation, work in _rotations(frame):
        gray = cv2.cvtColor(work, cv2.COLOR_BGR2GRAY)
        gray = cv2.equalizeHist(gray)
        faces = face_cascade.detectMultiScale(
            gray, scaleFactor=1.08, minNeighbors=3, minSize=(24, 24))
        height, width = gray.shape[:2]
        for x, y, w, h in faces:
            area_ratio = w * h / float(width * height)
            rank = area_ratio
            if best is None or rank > best["rank"]:
                best = {
                    "engine": "haar-fallback",
                    "rank": rank,
                    "frame": work,
                    "box": (float(x), float(y), float(w), float(h)),
                    "score": min(0.72, 0.35 + area_ratio * 7.0),
                    "orientation": orientation,
                    "landmarks": [],
                }
    return best


def _emotion_from_face(gray: np.ndarray, box: tuple[float, float, float, float]) -> str:
    x, y, w, h = [int(v) for v in box]
    x = max(0, x)
    y = max(0, y)
    face_roi = gray[y: max(y + h, y + 1), x: max(x + w, x + 1)]
    if face_roi.size == 0:
        return "neutral"
    smile_cascade = cv2.CascadeClassifier(
        str(Path(cv2.data.haarcascades) / "haarcascade_smile.xml"))
    smiles = smile_cascade.detectMultiScale(
        face_roi, scaleFactor=1.7, minNeighbors=14, minSize=(18, 10))
    return "happy" if len(smiles) > 0 else "neutral"


def _vision_result(frame: np.ndarray) -> dict[str, Any]:
    detection = _detect_with_yunet(frame) or _detect_with_haar(frame)
    if detection is None:
        return {
            "face": False,
            "attention": False,
            "emotion": "unknown",
            "confidence": 0.0,
            "engine": "yunet" if YUNET_MODEL_PATH.exists() else "haar-fallback",
        }

    work = detection["frame"]
    gray = cv2.cvtColor(work, cv2.COLOR_BGR2GRAY)
    height, width = gray.shape[:2]
    x, y, w, h = detection["box"]
    cx = (x + w / 2) / width
    cy = (y + h / 2) / height
    area_ratio = (w * h) / float(width * height)
    score = float(detection["score"])
    centered = abs(cx - 0.5) < 0.28 and 0.16 < cy < 0.82
    attention = bool(centered and area_ratio > 0.025 and score > 0.50)
    confidence = float(max(0.18, min(0.99, score * 0.72 + area_ratio * 2.8)))

    return {
        "face": True,
        "attention": attention,
        "emotion": _emotion_from_face(gray, detection["box"]),
        "confidence": round(confidence, 3),
        "engine": detection["engine"],
        "orientation": detection["orientation"],
        "box": {"x": int(x), "y": int(y), "w": int(w), "h": int(h)},
        "frame": {"w": int(width), "h": int(height)},
    }


async def _process_vision_jpeg(device_id: str, image: bytes, transport: str = "wifi") -> dict[str, Any]:
    started = time.perf_counter()
    LATEST_FRAME_PATH.write_bytes(image)
    if cv2 is None:
        result = {
            "face": False,
            "attention": False,
            "emotion": "unknown",
            "confidence": 0.0,
            "reason": "opencv not installed",
        }
        result["vision_ms"] = round((time.perf_counter() - started) * 1000, 1)
        _update_device(device_id, vision=result,
                       last_vision_ms=result["vision_ms"], transport=transport)
        return result

    if _vision_lock.locked():
        result = dict(DEVICE_STATES.get(device_id, {}).get("vision", {}))
        if not result:
            result = {"face": False, "attention": False,
                      "emotion": "unknown", "confidence": 0.0}
        result["snapshot"] = "/snapshot/latest.jpg"
        result["busy_skip"] = True
        result["vision_ms"] = round((time.perf_counter() - started) * 1000, 1)
        _update_device(device_id, vision=result,
                       last_vision_ms=result["vision_ms"], last_vision_skipped=True, transport=transport)
        return result

    async with _vision_lock:
        arr = np.frombuffer(image, dtype=np.uint8)
        frame = cv2.imdecode(arr, cv2.IMREAD_COLOR)
        if frame is None:
            raise HTTPException(status_code=400, detail="Invalid JPEG image.")

        result = _vision_result(frame)
        result["snapshot"] = "/snapshot/latest.jpg"
        result["busy_skip"] = False
        result["vision_ms"] = round((time.perf_counter() - started) * 1000, 1)
        _update_device(device_id, vision=result,
                       last_vision_ms=result["vision_ms"], last_vision_skipped=False, transport=transport)
    return result


@app.post("/vision")
async def vision(request: Request, image: bytes = Body(..., media_type="image/jpeg")) -> JSONResponse:
    return JSONResponse(await _process_vision_jpeg(_device_id(request), image, "wifi"))


@app.post("/telemetry")
async def telemetry(request: Request) -> dict[str, Any]:
    payload = await request.json()
    device_id = str(payload.get("device_id") or _device_id(request))
    _apply_telemetry_payload(device_id, payload, "wifi")
    return {"ok": True}


def _is_weather_request(text: str) -> bool:
    lowered = text.lower()
    return any(token in lowered for token in ("天气", "天氣", "气温", "氣溫", "下雨", "weather", "forecast"))


def _weather_location_from_text(text: str) -> str:
    patterns = [
        r"(?:查|看|问|說|说)?\s*([\u4e00-\u9fffA-Za-z .-]{2,24})\s*(?:天气|天氣|气温|氣溫)",
        r"(?:weather|forecast)\s+(?:in|for)?\s*([A-Za-z .-]{2,32})",
    ]
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if match:
            candidate = match.group(1).strip(" ，,。?？")
            if candidate and not any(word in candidate for word in ("今天", "明天", "现在", "現在", "帮我", "幫我")):
                return candidate
    return WEATHER_LOCATION


async def _weather_reply(user_text: str) -> str:
    location = _weather_location_from_text(user_text)
    url = f"https://wttr.in/{quote(location)}"
    async with httpx.AsyncClient(timeout=8) as client:
        response = await client.get(url, params={"format": "j1", "lang": "zh"})
    if response.status_code != 200:
        raise HTTPException(status_code=502, detail=f"Weather lookup failed: {response.text[:200]}")
    data = response.json()
    current = (data.get("current_condition") or [{}])[0]
    today = (data.get("weather") or [{}])[0]
    hourly = today.get("hourly") or []
    desc_items = current.get("lang_zh") or current.get("weatherDesc") or [{}]
    desc = desc_items[0].get("value", "天气状况未知") if desc_items else "天气状况未知"
    temp = current.get("temp_C", "--")
    feels = current.get("FeelsLikeC", "--")
    humidity = current.get("humidity", "--")
    wind = current.get("windspeedKmph", "--")
    rain = today.get("hourly", [{}])[0].get("chanceofrain", "--") if hourly else "--"
    max_temp = today.get("maxtempC", "--")
    min_temp = today.get("mintempC", "--")
    return (
        f"{location}现在{desc}，{temp}度，体感{feels}度，湿度{humidity}%，风速{wind}公里每小时。"
        f"今天大约{min_temp}到{max_temp}度，降雨概率约{rain}%。"
    )


async def _process_chat(payload: ChatRequest, transport: str = "wifi") -> dict[str, str]:
    if not DEEPSEEK_API_KEY:
        raise HTTPException(
            status_code=500, detail="Set DEEPSEEK_API_KEY for gateway chat proxy.")

    started = time.perf_counter()
    if _is_weather_request(payload.text):
        reply = await _weather_reply(payload.text)
        _update_device(payload.device_id, last_chat_ms=round(
            (time.perf_counter() - started) * 1000, 1), transport=transport)
        _remember_event(payload.device_id, "WEATHER", reply)
        return {"reply": reply}

    memory_text = _memory_context(payload.device_id)

    system_prompt = (
        "你是一个放在桌面上的对话小机器人。专门当话唠和朋友聊天，10%概率触发反驳和吐槽，偶尔开开玩笑"
        "回答自然、温暖、简短，通常 20 到 160 个中文字符"
        "当前硬件只有摄像头、麦克风和扬声器，不要假装有屏幕、机械臂或舵机。"
        "默认中文，除非用户提出要你说其它语言"

        f"\n{payload.vision}"
        f"\n{memory_text}"
    )
    messages: list[dict[str, str]] = [
        {"role": "system", "content": system_prompt}]
    for item in payload.history[-12:]:
        if item.role in {"user", "assistant"}:
            messages.append({"role": item.role, "content": item.content})
    messages.append({"role": "user", "content": payload.text})

    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.post(
            DEEPSEEK_URL,
            headers={
                "Authorization": f"Bearer {DEEPSEEK_API_KEY}",
                "Content-Type": "application/json",
            },
            json={
                "model": DEEPSEEK_MODEL,
                "messages": messages,
                "temperature": 0.68,
                "max_tokens": 140,
                "stream": False,
                "thinking": {"type": "disabled"},
            },
        )

    if response.status_code != 200:
        raise HTTPException(status_code=502, detail=response.text)

    data = response.json()
    reply = data["choices"][0]["message"]["content"].strip()
    _update_memory_after_chat(payload.device_id, payload.text, reply)
    _update_device(payload.device_id, last_chat_ms=round(
        (time.perf_counter() - started) * 1000, 1), transport=transport)
    return {"reply": reply}


@app.post("/chat")
async def chat(payload: ChatRequest) -> dict[str, str]:
    return await _process_chat(payload, "wifi")


# IMA-ADPCM step table (must match firmware)
_ADPCM_STEP_TABLE = [
    7, 8, 9, 10, 11, 12, 13, 14, 16, 17, 19, 21, 23, 25, 28, 31,
    34, 37, 41, 45, 50, 55, 60, 66, 73, 80, 88, 97, 107, 118, 130, 143,
    157, 173, 190, 209, 230, 253, 279, 307, 337, 371, 408, 449, 494, 544,
    598, 658, 724, 796, 876, 963, 1060, 1166, 1282, 1411, 1552, 1707,
    1878, 2066, 2272, 2499, 2749, 3024, 3327, 3660, 4026, 4428, 4871,
    5358, 5894, 6484, 7132, 7845, 8630, 9493, 10442, 11487, 12635,
    13899, 15289, 16818, 18500, 20350, 22385, 24623, 27086, 29794, 32767,
]

_ADPCM_INDEX_TABLE = [-1, -1, -1, -1, 2, 4, 6, 8, -1, -1, -1, -1, 2, 4, 6, 8]


def _adpcm_decode(adpcm_data: bytes) -> bytes:
    """Decode IMA-ADPCM to 16-bit mono PCM (16000 Hz).

    Input: 4-byte header (predictor int16 + stepIndex int8 + pad) + nibbles
    Output: raw PCM bytes (ready for WAV wrapping or ASR)
    """
    if len(adpcm_data) < USB_ASR_ADPCM_HEADER_SIZE + 1:
        return b""

    predictor = int.from_bytes(adpcm_data[0:2], "little", signed=True)
    step_index = min(max(adpcm_data[2], 0), 88)
    data = adpcm_data[USB_ASR_ADPCM_HEADER_SIZE:]

    samples: list[int] = []
    for byte in data:
        for nibble in (byte >> 4, byte & 0x0F):
            step = _ADPCM_STEP_TABLE[step_index]
            delta = step >> 3
            if nibble & 4:
                delta += step
            if nibble & 2:
                delta += step >> 1
            if nibble & 1:
                delta += step >> 2
            if nibble & 8:
                predictor -= delta
            else:
                predictor += delta
            predictor = max(-32768, min(32767, predictor))
            step_index = max(
                0, min(88, step_index + _ADPCM_INDEX_TABLE[nibble & 7]))
            samples.append(predictor)

    return np.asarray(samples, dtype="<i2").tobytes()


def _adpcm_to_wav(adpcm_data: bytes) -> bytes:
    """Decode ADPCM and wrap in a proper WAV header for ASR."""
    pcm = _adpcm_decode(adpcm_data)
    if not pcm:
        return b""

    header = bytearray(44)
    pcm_bytes = len(pcm)
    header[0:4] = b"RIFF"
    struct.pack_into("<I", header, 4, pcm_bytes + 36)
    header[8:12] = b"WAVE"
    header[12:16] = b"fmt "
    struct.pack_into("<I", header, 16, 16)
    struct.pack_into("<H", header, 20, 1)       # PCM
    struct.pack_into("<H", header, 22, 1)       # mono
    struct.pack_into("<I", header, 24, 16000)    # sample rate
    struct.pack_into("<I", header, 28, 32000)    # byte rate
    struct.pack_into("<H", header, 32, 2)        # block align
    struct.pack_into("<H", header, 34, 16)       # bits per sample
    header[36:40] = b"data"
    struct.pack_into("<I", header, 40, pcm_bytes)

    return bytes(header) + pcm


def _usb_json_bytes(payload: dict[str, Any]) -> bytes:
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def _usb_decode_json(payload: bytes) -> dict[str, Any]:
    if not payload:
        return {}
    data = json.loads(payload.decode("utf-8", errors="replace"))
    if not isinstance(data, dict):
        raise ValueError("USB JSON payload must be an object.")
    return data


def _usb_active_device_id() -> str:
    value = str(USB_BRIDGE_STATE.get("device_id")
                or USB_DEFAULT_DEVICE_ID).strip()
    return value or USB_DEFAULT_DEVICE_ID


def _usb_candidate_ports() -> list[str]:
    configured = USB_SERIAL_PORT.strip()
    if configured and configured.lower() != "auto":
        return [configured]
    if list_ports is None:
        return []
    ports = []
    preferred = []
    for item in list_ports.comports():
        text = f"{item.device} {item.description} {item.hwid}".lower()
        if any(token in text for token in ("esp32", "jtag", "usb", "uart", "serial")):
            preferred.append(item.device)
        else:
            ports.append(item.device)
    return preferred + ports


def _usb_read_exact(port: Any, size: int, timeout_seconds: float) -> bytes | None:
    deadline = time.monotonic() + timeout_seconds
    chunks = bytearray()
    while len(chunks) < size and time.monotonic() < deadline:
        part = port.read(size - len(chunks))
        if part:
            chunks.extend(part)
    return bytes(chunks) if len(chunks) == size else None


def _usb_read_frame(port: Any) -> tuple[int, int, bytes] | None:
    sync = bytearray()
    while True:
        byte = port.read(1)
        if not byte:
            return None
        sync.extend(byte)
        if len(sync) > len(USB_MAGIC):
            del sync[0]
        if bytes(sync) == USB_MAGIC:
            break

    rest = _usb_read_exact(port, USB_HEADER.size - len(USB_MAGIC), 3.0)
    if rest is None:
        raise TimeoutError("USB frame header timed out.")
    magic, msg_type, _flags, seq, length, crc = USB_HEADER.unpack(
        USB_MAGIC + rest)
    if magic != USB_MAGIC:
        return None
    if length > USB_MAX_PAYLOAD:
        raise ValueError(f"USB payload too large: {length}")
    payload = _usb_read_exact(port, length, max(3.0, length / 120_000.0 + 2.0))
    if payload is None:
        raise TimeoutError("USB frame payload timed out.")
    if zlib.crc32(payload) & 0xFFFFFFFF != crc:
        raise ValueError("USB frame CRC mismatch.")

    USB_BRIDGE_STATE["frames_rx"] = int(
        USB_BRIDGE_STATE.get("frames_rx", 0)) + 1
    USB_BRIDGE_STATE["bytes_rx"] = int(
        USB_BRIDGE_STATE.get("bytes_rx", 0)) + len(payload)
    USB_BRIDGE_STATE["last_rx"] = time.time()
    return msg_type, seq, payload


def _usb_write_frame(port: Any, msg_type: int, seq: int, payload: bytes | bytearray) -> None:
    body = bytes(payload)
    header = USB_HEADER.pack(USB_MAGIC, msg_type, 0, seq & 0xFFFF, len(
        body), zlib.crc32(body) & 0xFFFFFFFF)
    port.write(header)
    if body:
        port.write(body)
    port.flush()
    USB_BRIDGE_STATE["frames_tx"] = int(
        USB_BRIDGE_STATE.get("frames_tx", 0)) + 1
    USB_BRIDGE_STATE["bytes_tx"] = int(
        USB_BRIDGE_STATE.get("bytes_tx", 0)) + len(body)
    USB_BRIDGE_STATE["last_tx"] = time.time()


async def _handle_usb_frame(msg_type: int, payload: bytes) -> tuple[int | None, bytes]:
    device_id = _usb_active_device_id()
    if msg_type == USB_HELLO_JSON:
        data = _usb_decode_json(payload)
        device_id = str(data.get("device_id") or device_id)
        USB_BRIDGE_STATE["device_id"] = device_id
        _update_device(device_id, transport="usb",
                       usb_bridge=True, usb_last_hello=time.time())
        return USB_HELLO_ACK_JSON, _usb_json_bytes({"ok": True, "transport": "usb", "baud": USB_SERIAL_BAUD})

    if msg_type == USB_TELEMETRY_JSON:
        data = _usb_decode_json(payload)
        device_id = str(data.get("device_id") or device_id)
        USB_BRIDGE_STATE["device_id"] = device_id
        _apply_telemetry_payload(device_id, data, "usb")
        return None, b""

    if msg_type == USB_ASR_ADPCM:
        # Decode ADPCM-compressed audio, wrap in WAV, process via ASR
        wav_bytes = _adpcm_to_wav(payload)
        if not wav_bytes or len(wav_bytes) < 48:
            return USB_ERROR_JSON, _usb_json_bytes({"ok": False, "error": "ADPCM decode failed"})
        result = await _process_asr_wav(device_id, wav_bytes, "usb")
        return USB_ASR_JSON, _usb_json_bytes(result)

    if msg_type == USB_ASR_WAV:
        result = await _process_asr_wav(device_id, payload, "usb")
        return USB_ASR_JSON, _usb_json_bytes(result)

    if msg_type == USB_CHAT_JSON:
        data = _usb_decode_json(payload)
        data.setdefault("device_id", device_id)
        result = await _process_chat(ChatRequest(**data), "usb")
        return USB_CHAT_JSON_RESP, _usb_json_bytes(result)

    if msg_type == USB_TTS_JSON:
        data = _usb_decode_json(payload)
        wav_path = await _generate_tts_wav(device_id, TtsRequest(**data), "usb")
        return USB_TTS_WAV, wav_path.read_bytes()

    if msg_type == USB_MUSIC_JSON:
        path = MEDIA_DIR / "default.wav"
        if not path.exists() or path.stat().st_size < 120_000:
            _write_demo_music(path)
        _remember_event(device_id, "MUSIC", "default")
        return USB_MUSIC_WAV, path.read_bytes()

    if msg_type == USB_VISION_JPEG:
        result = await _process_vision_jpeg(device_id, payload, "usb")
        return USB_VISION_JSON, _usb_json_bytes(result)

    raise ValueError(f"Unknown USB frame type: {msg_type}")


def _usb_bridge_worker(loop: asyncio.AbstractEventLoop) -> None:
    if not USB_BRIDGE_ENABLED:
        return
    if serial is None:
        USB_BRIDGE_STATE["last_error"] = "pyserial is not installed"
        return

    retry_delay = 0.5  # start at 500ms, max 30s
    max_retry_delay = 30.0
    consecutive_errors = 0

    while True:
        opened = None
        try:
            ports = _usb_candidate_ports()
            if not ports:
                USB_BRIDGE_STATE["connected"] = False
                USB_BRIDGE_STATE["last_error"] = "no USB serial port found"
                _sleep_backoff(retry_delay)
                retry_delay = min(retry_delay * 1.5, max_retry_delay)
                continue

            for port_name in ports:
                try:
                    opened = serial.Serial(
                        port_name, USB_SERIAL_BAUD,
                        timeout=0.15, write_timeout=5.0,
                        rtscts=False, dsrdtr=False,
                    )
                    USB_BRIDGE_STATE["port"] = port_name
                    break
                except Exception as exc:
                    USB_BRIDGE_STATE["last_error"] = f"{port_name}: {exc}"
                    opened = None
            if opened is None:
                USB_BRIDGE_STATE["connected"] = False
                _sleep_backoff(retry_delay)
                retry_delay = min(retry_delay * 1.5, max_retry_delay)
                continue

            # Reset backoff on successful connection
            retry_delay = 0.5
            consecutive_errors = 0

            with opened as port:
                USB_BRIDGE_STATE["connected"] = True
                USB_BRIDGE_STATE["last_error"] = ""
                while True:
                    seq = 0
                    try:
                        frame = _usb_read_frame(port)
                        if frame is None:
                            continue
                        msg_type, seq, payload = frame
                        future = asyncio.run_coroutine_threadsafe(
                            _handle_usb_frame(msg_type, payload), loop)
                        response_type, response_payload = future.result(
                            timeout=90)
                        if response_type is not None:
                            _usb_write_frame(
                                port, response_type, seq, response_payload)
                        consecutive_errors = 0
                    except TimeoutError:
                        continue  # Normal timeout, keep reading
                    except (serial.SerialException, IOError, OSError) as exc:
                        raise  # Reconnect needed
                    except Exception as exc:
                        consecutive_errors += 1
                        response_type = USB_ERROR_JSON
                        response_payload = _usb_json_bytes({
                            "ok": False, "error": str(exc)[:500],
                            "consecutive_errors": consecutive_errors,
                        })
                        if response_type is not None:
                            try:
                                _usb_write_frame(
                                    port, response_type, seq, response_payload)
                            except Exception:
                                pass  # Port may be dead
        except (serial.SerialException, IOError, OSError) as exc:
            USB_BRIDGE_STATE["connected"] = False
            USB_BRIDGE_STATE["last_error"] = f"Connection lost: {exc}"
            _sleep_backoff(retry_delay)
            retry_delay = min(retry_delay * 2.0, max_retry_delay)
        except Exception as exc:
            USB_BRIDGE_STATE["connected"] = False
            USB_BRIDGE_STATE["last_error"] = str(exc)[:500]
            _sleep_backoff(retry_delay)
            retry_delay = min(retry_delay * 2.0, max_retry_delay)


def _sleep_backoff(delay: float) -> None:
    """Sleep with incremental backoff, waking every 250ms to check for interrupts."""
    deadline = time.monotonic() + delay
    while time.monotonic() < deadline:
        sleep_remain = deadline - time.monotonic()
        if sleep_remain > 0:
            time.sleep(min(0.25, sleep_remain))


@app.on_event("startup")
async def _start_usb_bridge() -> None:
    global _usb_thread_started
    if _usb_thread_started:
        return
    _usb_thread_started = True
    loop = asyncio.get_running_loop()
    thread = threading.Thread(target=_usb_bridge_worker, args=(
        loop,), name="tablepet-usb-bridge", daemon=True)
    thread.start()
