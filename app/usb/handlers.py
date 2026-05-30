"""USB 帧路由 / Dispatch incoming USB frames to services."""

from __future__ import annotations

import time

from .. import state
from ..config import (
    MEDIA_DIR,
    USB_ASR_ADPCM,
    USB_ASR_JSON,
    USB_ASR_WAV,
    USB_CHAT_JSON,
    USB_CHAT_JSON_RESP,
    USB_ERROR_JSON,
    USB_HELLO_ACK_JSON,
    USB_HELLO_JSON,
    USB_MUSIC_JSON,
    USB_MUSIC_WAV,
    USB_SERIAL_BAUD,
    USB_TELEMETRY_JSON,
    USB_TTS_JSON,
    USB_TTS_WAV,
    USB_VISION_JPEG,
    USB_VISION_JSON,
)
from ..agent import AgentInput, Channel, run_agent
from ..memory import DEFAULT_USER_ID
from ..schemas import TtsRequest
from ..services.asr import process_asr_wav
from ..services.tts import generate_tts_wav
from ..services.vision import process_vision_jpeg
from ..utils.adpcm import adpcm_to_wav
from ..utils.audio import write_demo_music
from .protocol import usb_active_device_id, usb_decode_json, usb_json_bytes


async def handle_usb_frame(msg_type: int, payload: bytes) -> tuple[int | None, bytes]:
    """根据帧类型分发到对应的业务服务。"""
    device_id = usb_active_device_id()

    if msg_type == USB_HELLO_JSON:
        data = usb_decode_json(payload)
        device_id = str(data.get("device_id") or device_id)
        state.USB_BRIDGE_STATE["device_id"] = device_id
        state.update_device(
            device_id, transport="usb", usb_bridge=True, usb_last_hello=time.time()
        )
        return USB_HELLO_ACK_JSON, usb_json_bytes(
            {"ok": True, "transport": "usb", "baud": USB_SERIAL_BAUD}
        )

    if msg_type == USB_TELEMETRY_JSON:
        data = usb_decode_json(payload)
        device_id = str(data.get("device_id") or device_id)
        state.USB_BRIDGE_STATE["device_id"] = device_id
        state.apply_telemetry_payload(device_id, data, "usb")
        return None, b""

    if msg_type == USB_ASR_ADPCM:
        wav_bytes = adpcm_to_wav(payload)
        if not wav_bytes or len(wav_bytes) < 48:
            return USB_ERROR_JSON, usb_json_bytes(
                {"ok": False, "error": "ADPCM decode failed"}
            )
        result = await process_asr_wav(device_id, wav_bytes, "usb")
        return USB_ASR_JSON, usb_json_bytes(result)

    if msg_type == USB_ASR_WAV:
        result = await process_asr_wav(device_id, payload, "usb")
        return USB_ASR_JSON, usb_json_bytes(result)

    if msg_type == USB_CHAT_JSON:
        data = usb_decode_json(payload)
        result = await run_agent(
            AgentInput(
                channel=Channel.USB,
                device_id=str(data.get("device_id") or device_id),
                # 单用户系统：忽略设备 payload 里的 user_id，统一用 DEFAULT_USER_ID。
                user_id=DEFAULT_USER_ID,
                text=str(data.get("text") or ""),
                want_tts=False,
                extra={
                    "vision": data.get("vision", ""),
                    "source": "device_voice",
                    "transport": "usb",
                },
            )
        )
        return USB_CHAT_JSON_RESP, usb_json_bytes({"reply": result.reply or ""})

    if msg_type == USB_TTS_JSON:
        data = usb_decode_json(payload)
        wav_path = await generate_tts_wav(device_id, TtsRequest(**data), "usb")
        return USB_TTS_WAV, wav_path.read_bytes()

    if msg_type == USB_MUSIC_JSON:
        path = MEDIA_DIR / "default.wav"
        if not path.exists() or path.stat().st_size < 120_000:
            write_demo_music(path)
        state.remember_event(device_id, "MUSIC", "default")
        return USB_MUSIC_WAV, path.read_bytes()

    if msg_type == USB_VISION_JPEG:
        result = await process_vision_jpeg(device_id, payload, "usb")
        return USB_VISION_JSON, usb_json_bytes(result)

    raise ValueError(f"Unknown USB frame type: {msg_type}")
