"""USB 帧协议 (TPU1) / USB frame protocol (TPU1).

帧格式 / Frame format:
  magic(4) = b"TPU1" | type(1) | flags(1) | seq(2) | length(4) | crc32(4) | payload(length)
"""

from __future__ import annotations

import json
import time
import zlib
from typing import Any

from .. import state
from ..config import (
    USB_DEFAULT_DEVICE_ID,
    USB_HEADER,
    USB_MAGIC,
    USB_MAX_PAYLOAD,
    USB_SERIAL_PORT,
)

try:
    from serial.tools import list_ports
except Exception:  # pragma: no cover - pyserial optional
    list_ports = None  # type: ignore[assignment]


def usb_json_bytes(payload: dict[str, Any]) -> bytes:
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def usb_decode_json(payload: bytes) -> dict[str, Any]:
    if not payload:
        return {}
    data = json.loads(payload.decode("utf-8", errors="replace"))
    if not isinstance(data, dict):
        raise ValueError("USB JSON payload must be an object.")
    return data


def usb_active_device_id() -> str:
    value = str(state.USB_BRIDGE_STATE.get("device_id") or USB_DEFAULT_DEVICE_ID).strip()
    return value or USB_DEFAULT_DEVICE_ID


def usb_candidate_ports() -> list[str]:
    """枚举可能的 USB CDC 端口（优先含 esp32/usb 关键字的）。"""
    configured = USB_SERIAL_PORT.strip()
    if configured and configured.lower() != "auto":
        return [configured]
    if list_ports is None:
        return []
    ports: list[str] = []
    preferred: list[str] = []
    for item in list_ports.comports():
        text = f"{item.device} {item.description} {item.hwid}".lower()
        if any(token in text for token in ("esp32", "jtag", "usb", "uart", "serial")):
            preferred.append(item.device)
        else:
            ports.append(item.device)
    return preferred + ports


def usb_read_exact(port: Any, size: int, timeout_seconds: float) -> bytes | None:
    deadline = time.monotonic() + timeout_seconds
    chunks = bytearray()
    while len(chunks) < size and time.monotonic() < deadline:
        part = port.read(size - len(chunks))
        if part:
            chunks.extend(part)
    return bytes(chunks) if len(chunks) == size else None


def usb_read_frame(port: Any) -> tuple[int, int, bytes] | None:
    """阻塞读取一个完整帧；超时/错误返回 None 或抛异常。"""
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

    rest = usb_read_exact(port, USB_HEADER.size - len(USB_MAGIC), 3.0)
    if rest is None:
        raise TimeoutError("USB frame header timed out.")
    magic, msg_type, _flags, seq, length, crc = USB_HEADER.unpack(USB_MAGIC + rest)
    if magic != USB_MAGIC:
        return None
    if length > USB_MAX_PAYLOAD:
        raise ValueError(f"USB payload too large: {length}")
    payload = usb_read_exact(port, length, max(3.0, length / 120_000.0 + 2.0))
    if payload is None:
        raise TimeoutError("USB frame payload timed out.")
    if zlib.crc32(payload) & 0xFFFFFFFF != crc:
        raise ValueError("USB frame CRC mismatch.")

    state.USB_BRIDGE_STATE["frames_rx"] = int(state.USB_BRIDGE_STATE.get("frames_rx", 0)) + 1
    state.USB_BRIDGE_STATE["bytes_rx"] = int(state.USB_BRIDGE_STATE.get("bytes_rx", 0)) + len(payload)
    state.USB_BRIDGE_STATE["last_rx"] = time.time()
    return msg_type, seq, payload


def usb_write_frame(port: Any, msg_type: int, seq: int, payload: bytes | bytearray) -> None:
    body = bytes(payload)
    header = USB_HEADER.pack(
        USB_MAGIC, msg_type, 0, seq & 0xFFFF, len(body), zlib.crc32(body) & 0xFFFFFFFF
    )
    port.write(header)
    if body:
        port.write(body)
    port.flush()
    state.USB_BRIDGE_STATE["frames_tx"] = int(state.USB_BRIDGE_STATE.get("frames_tx", 0)) + 1
    state.USB_BRIDGE_STATE["bytes_tx"] = int(state.USB_BRIDGE_STATE.get("bytes_tx", 0)) + len(body)
    state.USB_BRIDGE_STATE["last_tx"] = time.time()
