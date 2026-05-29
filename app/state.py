"""运行时全局状态 / Runtime in-process state."""

from __future__ import annotations

import asyncio
import time
from typing import Any

from fastapi import Request

from .config import USB_BRIDGE_ENABLED, USB_DEFAULT_DEVICE_ID, USB_SERIAL_BAUD, USB_SERIAL_PORT

try:
    import serial  # noqa: F401  (仅用于检测可用性 / availability check)
except Exception:
    serial = None  # type: ignore[assignment]


# ---------------------------------------------------------------------------
# 设备状态 / Device states
# ---------------------------------------------------------------------------
DEVICE_STATES: dict[str, dict[str, Any]] = {}
RECENT_EVENTS: list[dict[str, Any]] = []


# ---------------------------------------------------------------------------
# USB 桥状态 / USB bridge state
# ---------------------------------------------------------------------------
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


# ---------------------------------------------------------------------------
# 全局锁 & 模型句柄 / Global locks & model handles
# 这些在 asr/tts/vision service 里复用，集中放这里避免循环依赖。
# ---------------------------------------------------------------------------
ASR_MODEL: Any | None = None
ASR_LOCK = asyncio.Lock()
ASR_RUNTIME_LOCK = asyncio.Lock()
TTS_LOCK = asyncio.Lock()
VISION_LOCK = asyncio.Lock()

# edge-tts 故障冷却时间戳 / cooldown deadline for edge-tts failures
EDGE_TTS_DISABLED_UNTIL: float = 0.0

# USB 工作线程是否已启动 / whether USB worker thread has started
USB_THREAD_STARTED: bool = False


# ---------------------------------------------------------------------------
# 设备 / 事件辅助函数 / Helpers
# ---------------------------------------------------------------------------
def device_id_from_request(request: Request, fallback: str = "unknown") -> str:
    return request.headers.get("x-device-id") or fallback


def remember_event(device_id: str, kind: str, detail: str) -> None:
    RECENT_EVENTS.append(
        {
            "ts": time.time(),
            "device_id": device_id,
            "kind": kind,
            "detail": detail[:300],
        }
    )
    del RECENT_EVENTS[:-80]


def update_device(device_id: str, **fields: Any) -> dict[str, Any]:
    item = DEVICE_STATES.setdefault(device_id, {"device_id": device_id})
    item.update(fields)
    item["last_seen"] = time.time()
    return item


def apply_telemetry_payload(device_id: str, payload: dict[str, Any], transport: str) -> None:
    payload = dict(payload)
    payload.pop("device_id", None)
    if isinstance(payload.get("vision"), dict):
        existing_vision = DEVICE_STATES.get(device_id, {}).get("vision", {})
        if isinstance(existing_vision, dict):
            payload["vision"] = {**existing_vision, **payload["vision"]}
    payload["transport"] = transport
    update_device(device_id, **payload)
