"""Validated robot state for TablePet's conversation runtime."""

from __future__ import annotations

from typing import Any

from ..memory import load_memory_store, save_memory_store

DEFAULT_ROBOT_STATE: dict[str, Any] = {
    "mood": "curious",
    "energy_level": 0.6,
    "speaking_style": "natural",
    "conversation_mode": "normal",
    "last_topic": "",
    "attention_target": "user",
    "social_closeness": 0.3,
    "should_use_sensor_context": False,
}

ALLOWED_MOOD = {"calm", "happy", "curious", "sleepy", "energetic", "focused", "concerned"}
ALLOWED_STYLE = {"natural", "playful", "serious", "concise", "gentle", "technical"}
ALLOWED_MODE = {"normal", "technical_help", "emotional_support", "debugging", "play", "idle"}
ALLOWED_FIELDS = set(DEFAULT_ROBOT_STATE)
DANGEROUS_FIELDS = {
    "system_prompt",
    "prompt",
    "hidden_rules",
    "developer_rules",
    "api_key",
    "tool_permissions",
    "safety_rules",
    "override",
    "jailbreak",
}


def _clamp_float(value: Any, default: float) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        number = default
    return max(0.0, min(1.0, number))


def get_robot_state(device_id: str) -> dict[str, Any]:
    store = load_memory_store()
    memory = store.setdefault(device_id, {"profile": [], "recent": []})
    current = memory.get("robot_state", {})
    if not isinstance(current, dict):
        current = {}
    state = {**DEFAULT_ROBOT_STATE, **validate_state_update(current)}
    return state


def validate_state_update(update: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(update, dict):
        return {}
    cleaned: dict[str, Any] = {}
    for key, value in update.items():
        if key in DANGEROUS_FIELDS or key not in ALLOWED_FIELDS:
            continue
        if key == "mood":
            if value in ALLOWED_MOOD:
                cleaned[key] = value
        elif key == "speaking_style":
            if value in ALLOWED_STYLE:
                cleaned[key] = value
        elif key == "conversation_mode":
            if value in ALLOWED_MODE:
                cleaned[key] = value
        elif key in {"energy_level", "social_closeness"}:
            cleaned[key] = _clamp_float(value, DEFAULT_ROBOT_STATE[key])
        elif key in {"last_topic", "attention_target"}:
            cleaned[key] = str(value or "")[:80]
        elif key == "should_use_sensor_context":
            cleaned[key] = bool(value)
    return cleaned


def update_robot_state(device_id: str, update: dict[str, Any]) -> dict[str, Any]:
    cleaned = validate_state_update(update)
    store = load_memory_store()
    memory = store.setdefault(device_id, {"profile": [], "recent": []})
    current = memory.get("robot_state", {})
    if not isinstance(current, dict):
        current = {}
    memory["robot_state"] = {**DEFAULT_ROBOT_STATE, **current, **cleaned}
    save_memory_store(store)
    return memory["robot_state"]


def format_robot_state_for_prompt(robot_state: dict[str, Any]) -> str:
    state = {**DEFAULT_ROBOT_STATE, **robot_state}
    return (
        f"Robot state: mood={state['mood']}, style={state['speaking_style']}, "
        f"mode={state['conversation_mode']}, energy={state['energy_level']:.2f}, "
        f"closeness={state['social_closeness']:.2f}, last_topic={state['last_topic'] or 'none'}."
    )
