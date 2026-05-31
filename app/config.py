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
SKILLS_DIR = APP_DIR / "data" / "skills"
MEMORY_FILE = CACHE_DIR / "memory.json"
LATEST_FRAME_PATH = CACHE_DIR / "latest_frame.jpg"
YUNET_MODEL_PATH = MODELS_DIR / "face_detection_yunet_2023mar.onnx"

for directory in (CACHE_DIR, AUDIO_DIR, MUSIC_CACHE_DIR, MEDIA_DIR, JAY_CHOU_DIR, MODELS_DIR, SKILLS_DIR):
    directory.mkdir(parents=True, exist_ok=True)


def _load_dotenv_file(path: Path) -> None:
    if not path.exists():
        return
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


_load_dotenv_file(APP_DIR / ".env")


# ---------------------------------------------------------------------------
# 固件 main.cpp 中嵌入的 DeepSeek key（兼容老 demo）—— 已废弃
# ---------------------------------------------------------------------------
def _load_firmware_deepseek_key() -> str:
    """老逻辑会从 src/main.cpp 提取 DEEPSEEK_API_KEY；现在改为前端账户页配置，
    所以这里固定返回空字符串，避免误触发任何 fallback。
    """
    return ""


# ---------------------------------------------------------------------------
# DeepSeek
# ---------------------------------------------------------------------------
# ⚠️ 重要：API Key 不再从环境变量 / 固件文件读取。所有调用必须走「账户情况」页面
#         配置的账号（SQLite ``llm_account`` 表）。env 里的 DEEPSEEK_API_KEY 即使
#         设置了也会被忽略，避免泄露 / 误用。
DEEPSEEK_API_KEY = ""
DEEPSEEK_URL = os.getenv("DEEPSEEK_URL", "https://api.deepseek.com/chat/completions")
DEEPSEEK_MODEL = os.getenv("DEEPSEEK_MODEL", "deepseek-chat")

# 主对话模型 vs 总结模型分开配置 / Chat model vs summarization model separation.
# 总结类任务（短期/长期/AI 画像/宠物状态）一律走更便宜的模型，避免烧钱。
DEEPSEEK_CHAT_MODEL = os.getenv("DEEPSEEK_CHAT_MODEL", DEEPSEEK_MODEL)
DEEPSEEK_SUMMARY_MODEL = os.getenv("DEEPSEEK_SUMMARY_MODEL", "deepseek-chat")


# ---------------------------------------------------------------------------
# 记忆体系 / Memory system
# ---------------------------------------------------------------------------
# 上下文 token 预算
# - deepseek-v4 上下文 ~128k tokens
# - 60k 触发总结，给 prompt 附加内容 + 输出留 50% 余裕
MEMORY_CONTEXT_BUDGET_TOKENS = int(
    os.getenv("TABLEPET_CONTEXT_BUDGET_TOKENS", str(60_000))
)
# 临时记忆窗口（小时）
MEMORY_EPHEMERAL_WINDOW_HOURS = int(os.getenv("TABLEPET_EPHEMERAL_HOURS", "24"))
# 长期记忆召回 topK
MEMORY_LONG_TERM_TOPK = int(os.getenv("TABLEPET_LONG_TERM_TOPK", "5"))
# 短期记忆 prompt 注入条数上限
MEMORY_SHORT_TERM_INJECT_MAX = int(os.getenv("TABLEPET_SHORT_TERM_INJECT_MAX", "8"))

# tiktoken 编码器：deepseek 与 GPT 系列 token 切分相近，用 cl100k_base 估计
MEMORY_TOKEN_ENCODING = os.getenv("TABLEPET_TOKEN_ENCODING", "cl100k_base")


# ---------------------------------------------------------------------------
# 向量嵌入 / Embedding (Ollama)
# ---------------------------------------------------------------------------
OLLAMA_BASE_URL = os.getenv("OLLAMA_URL", "http://localhost:11434")
OLLAMA_EMBED_MODEL = os.getenv("TABLEPET_EMBED_MODEL", "nomic-embed-text")
OLLAMA_EMBED_TIMEOUT_SECONDS = int(os.getenv("TABLEPET_EMBED_TIMEOUT", "30"))


# ---------------------------------------------------------------------------
# APScheduler 后台任务
# ---------------------------------------------------------------------------
SCHEDULER_ENABLED = os.getenv("TABLEPET_SCHEDULER_ENABLED", "1") == "1"
# 每天几点跑日记总结（24h）
SCHEDULER_DAILY_SUMMARY_HOUR = int(os.getenv("TABLEPET_DAILY_SUMMARY_HOUR", "3"))
SCHEDULER_DAILY_SUMMARY_MINUTE = int(os.getenv("TABLEPET_DAILY_SUMMARY_MINUTE", "30"))
# 宠物状态多久重算一次（分钟）
SCHEDULER_PET_TICK_MINUTES = int(os.getenv("TABLEPET_PET_TICK_MINUTES", "60"))


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
TTS_ENGINE = os.getenv("TABLEPET_TTS_ENGINE", "edge").strip().lower()
TTS_EDGE_ENABLED = os.getenv("TABLEPET_TTS_EDGE_ENABLED", "0") == "1"
TTS_EDGE_RETRY_SECONDS = int(os.getenv("TABLEPET_TTS_EDGE_RETRY_SECONDS", "20"))
TTS_CUTE_FILTER_ENABLED = os.getenv("TABLEPET_TTS_CUTE_FILTER_ENABLED", "0") == "1"
MACOS_SAY_VOICE = os.getenv("TABLEPET_MACOS_SAY_VOICE", "Flo (Chinese (China mainland))")
MLX_TTS_MODEL = os.getenv("TABLEPET_MLX_TTS_MODEL", "")
MLX_TTS_COMMAND = os.getenv("TABLEPET_MLX_TTS_COMMAND", "")

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
ITUNES_SEARCH_URL = os.getenv("TABLEPET_ITUNES_SEARCH_URL", "https://itunes.apple.com/search")
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
