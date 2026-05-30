"""Tool routing for the TablePet interaction runtime."""

from __future__ import annotations

from typing import Any

from .. import state
from .weather import weather_reply


async def run_tool_if_needed(
    intent: str, user_text: str, device_id: str, runtime: dict[str, Any]
) -> tuple[str, dict[str, Any] | None, bool]:
    if intent == "music":
        return (
            "Music action prepared. Prefer Jay Chou endpoint if user asks for Jay Chou.",
            {"type": "play_audio", "url": "/music/default.wav"},
            True,
        )

    if intent == "weather":
        try:
            return await weather_reply(user_text), None, False
        except Exception as exc:
            return f"Weather tool not configured or failed: {exc}", None, False

    if intent == "vision":
        telemetry = state.DEVICE_STATES.get(device_id, {})
        vision = telemetry.get("vision", {}) if isinstance(telemetry, dict) else {}
        return f"Recent vision state: {vision or 'not available'}", None, False

    if intent == "device_control":
        lowered = user_text.lower()
        if "音量" in user_text or "volume" in lowered:
            return (
                "Device volume control requested.",
                {"type": "device_control", "target": "volume", "status": "needs_specific_value"},
                False,
            )
        return (
            "Device control requested, but the safe action is not specific enough.",
            {"type": "device_control", "status": "needs_clarification"},
            False,
        )

    return "", None, False
