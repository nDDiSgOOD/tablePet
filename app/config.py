"""集中管理环境变量、路径和协议常量 / Centralized env vars, paths and protocol constants."""

from __future__ import annotations

import os
import re
import shutil
import struct
from pathlib import Path

try:
    import imageio_ffmpeg
except Exception:  # pragma: no cover - optional fallback
    imageio_ffmpeg = None  # type: ignore[assignment]


# ---------------------------------------------------------------------------
# 路径 / Paths
# ---------------------------------------------------------------------------
APP_DIR = Path(__file__).resolve().parent.parent
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


# ---------------------------------------------------------------------------
# 固件 main.cpp 中嵌入的 DeepSeek key（兼容老 demo）
# ---------------------------------------------------------------------------
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


# ---------------------------------------------------------------------------
# DeepSeek
# ---------------------------------------------------------------------------
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY", "") or _load_firmware_deepseek_key()
DEEPSEEK_URL = os.getenv("DEEPSEEK_URL", "https://api.deepseek.com/chat/completions")
DEEPSEEK_MODEL = os.getenv("DEEPSEEK_MODEL", "deepseek-v4-flash")


# ---------------------------------------------------------------------------
# ASR (faster-whisper)
# ---------------------------------------------------------------------------
ASR_MODEL_NAME = os.getenv("TABLEPET_ASR_MODEL", "small")
ASR_COMPUTE_TYPE = os.getenv("TABLEPET_ASR_COMPUTE_TYPE", "int8")
ASR_BEAM_SIZE = int(os.getenv("TABLEPET_ASR_BEAM_SIZE", "1"))
ASR_MIN_AUDIO_MS = int(os.getenv("TABLEPET_ASR_MIN_AUDIO_MS", "360"))
ASR_MIN_RMS = float(os.getenv("TABLEPET_ASR_MIN_RMS", "80"))
ASR_STRONG_RETRY_ENABLED = os.getenv("TABLEPET_ASR_STRONG_RETRY_ENABLED", "0") == "1"
ASR_STRONG_RETRY_MIN_AUDIO_MS = int(os.getenv("TABLEPET_ASR_STRONG_RETRY_MIN_AUDIO_MS", "650"))
ASR_STRONG_RETRY_MIN_RMS = float(os.getenv("TABLEPET_ASR_STRONG_RETRY_MIN_RMS", "450"))


# ---------------------------------------------------------------------------
# TTS
# ---------------------------------------------------------------------------
DEFAULT_VOICE = os.getenv("TABLEPET_TTS_VOICE", "zh-TW-HsiaoChenNeural")
TTS_EDGE_ENABLED = os.getenv("TABLEPET_TTS_EDGE_ENABLED", "0") == "1"
TTS_EDGE_RETRY_SECONDS = int(os.getenv("TABLEPET_TTS_EDGE_RETRY_SECONDS", "20"))
TTS_CUTE_FILTER_ENABLED = os.getenv("TABLEPET_TTS_CUTE_FILTER_ENABLED", "0") == "1"
MACOS_SAY_VOICE = os.getenv("TABLEPET_MACOS_SAY_VOICE", "Flo (Chinese (China mainland))")

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


# ---------------------------------------------------------------------------
# Weather / ffmpeg
# ---------------------------------------------------------------------------
WEATHER_LOCATION = os.getenv("TABLEPET_WEATHER_LOCATION", "Toronto")
FFMPEG_BIN = (
    os.getenv("FFMPEG_BIN")
    or shutil.which("ffmpeg")
    or (imageio_ffmpeg.get_ffmpeg_exe() if imageio_ffmpeg is not None else "")
)


# ---------------------------------------------------------------------------
# USB 串口桥 / USB serial bridge
# ---------------------------------------------------------------------------
USB_BRIDGE_ENABLED = os.getenv("TABLEPET_USB_ENABLED", "1") == "1"
USB_SERIAL_PORT = os.getenv("TABLEPET_USB_PORT", "auto")
USB_SERIAL_BAUD = int(os.getenv("TABLEPET_USB_BAUD", "921600"))
USB_DEFAULT_DEVICE_ID = os.getenv("TABLEPET_USB_DEVICE_ID", "tablepet-xiao-s3-001")
USB_MAGIC = b"TPU1"
USB_HEADER = struct.Struct("<4sBBHII")
USB_MAX_PAYLOAD = int(os.getenv("TABLEPET_USB_MAX_PAYLOAD", str(3 * 1024 * 1024)))

# 帧类型 / Frame types
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
