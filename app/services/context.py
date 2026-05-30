"""Context selection for the TablePet agent runtime."""

from __future__ import annotations

from typing import Any

from .. import state
from ..memory import device_memory, get_user_profile


def load_profile(device_id: str, user_id: str) -> dict[str, Any]:
    profile = get_user_profile(device_id)
    if profile:
        return profile
    return {"name": "", "language": "auto", "bio": "", "city": ""}


def select_relevant_memory(device_id: str, user_text: str, intent: str, policy: dict[str, Any]) -> str:
    if not policy.get("should_use_memory") and intent not in {"memory_query", "memory_write"}:
        return ""
    memory = device_memory(device_id)
    profile = memory.get("profile", [])
    recent = memory.get("recent", [])
    lines: list[str] = []
    lowered = user_text.lower()

    for fact in profile[-12:]:
        fact_text = str(fact)
        if intent in {"memory_query", "memory_write", "emotional_support"} or any(
            token and token in fact_text.lower() for token in lowered.split()[:8]
        ):
            lines.append(f"- {fact_text[:120]}")
        if len(lines) >= 5:
            break

    if intent in {"memory_query", "chat", "casual_chat", "technical_help", "emotional_support"}:
        for item in recent[-3:]:
            user = str(item.get("user", ""))[:80]
            assistant = str(item.get("assistant", ""))[:80]
            if user or assistant:
                lines.append(f"- recent: user={user}; assistant={assistant}")

    return "Relevant memory:\n" + "\n".join(lines[:6]) if lines else ""


def select_sensor_context(device_id: str, user_text: str, intent: str, policy: dict[str, Any]) -> str:
    if not policy.get("should_use_sensor_context"):
        return ""
    telemetry = state.DEVICE_STATES.get(device_id, {})
    if not telemetry:
        return "Device status is not available yet."

    usb = state.USB_BRIDGE_STATE
    parts: list[str] = []
    if "transport" in telemetry:
        parts.append(f"transport {telemetry.get('transport')}")
    parts.append("USB connected" if usb.get("connected") else "USB not connected")
    if telemetry.get("wifi_ip"):
        parts.append(f"WiFi IP {telemetry['wifi_ip']}")
    if telemetry.get("output_volume") is not None:
        parts.append(f"volume {telemetry['output_volume']}%")
    if telemetry.get("mic_rms") is not None:
        parts.append(f"mic RMS about {round(float(telemetry['mic_rms']), 1)}")
    latency_bits = []
    for label, key in (("ASR", "last_asr_ms"), ("LLM", "last_chat_ms"), ("TTS", "last_tts_ms")):
        value = telemetry.get(key)
        if value is not None:
            latency_bits.append(f"{label} {value}ms")
    if latency_bits:
        parts.append("last latency: " + ", ".join(latency_bits))
    vision = telemetry.get("vision", {})
    if intent == "vision" and isinstance(vision, dict):
        parts.append(
            "vision: "
            + ("face detected" if vision.get("face") else "no stable face")
            + f", emotion {vision.get('emotion', 'unknown')}"
        )
    return "Selected sensor context: " + "; ".join(parts) + "."


def build_conversation_context(device_id: str, intent: str, policy: dict[str, Any]) -> str:
    if intent not in {"chat", "casual_chat", "technical_help", "emotional_support", "memory_query"}:
        return ""
    recent = device_memory(device_id).get("recent", [])
    lines: list[str] = []
    for item in recent[-4:]:
        user = str(item.get("user", ""))[:120]
        assistant = str(item.get("assistant", ""))[:120]
        if user or assistant:
            lines.append(f"User: {user}\nAssistant: {assistant}")
    return "Recent conversation:\n" + "\n".join(lines) if lines else ""


def build_anti_repetition_context(device_id: str) -> str:
    recent = device_memory(device_id).get("recent", [])
    openings: list[str] = []
    for item in recent[-3:]:
        assistant = str(item.get("assistant", "")).strip()
        if assistant:
            openings.append(assistant[:80])
    if not openings:
        return ""
    return "Avoid repeating these recent openings: " + " | ".join(openings)
