"""Relationship-level memory for stable user preferences."""

from __future__ import annotations

from typing import Any

from ..memory import load_memory_store, save_memory_store

DEFAULT_RELATIONSHIP_STATE: dict[str, Any] = {
    "known_name": None,
    "preferred_language": "auto",
    "preferred_explanation_style": "clear",
    "preferred_pet_style": "balanced",
    "important_preferences": [],
    "recent_topics": [],
    "trust_level": 0.3,
}

ALLOWED_FIELDS = set(DEFAULT_RELATIONSHIP_STATE)
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


def _relationship_key(user_id: str, device_id: str) -> str:
    return f"relationship::{user_id}::{device_id}"


def _short_text(value: Any, limit: int = 120) -> str:
    return str(value or "").strip()[:limit]


def _short_list(value: Any, limit: int = 8) -> list[str]:
    if not isinstance(value, list):
        return []
    items: list[str] = []
    for item in value:
        text = _short_text(item, 120)
        if text and text not in items:
            items.append(text)
        if len(items) >= limit:
            break
    return items


def _clamp(value: Any, default: float = 0.3) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        number = default
    return max(0.0, min(1.0, number))


def get_relationship_state(user_id: str, device_id: str) -> dict[str, Any]:
    store = load_memory_store()
    key = _relationship_key(user_id, device_id)
    current = store.get(key, {})
    if not isinstance(current, dict):
        current = {}
    return {**DEFAULT_RELATIONSHIP_STATE, **validate_memory_update(current)}


def validate_memory_update(update: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(update, dict):
        return {}
    cleaned: dict[str, Any] = {}
    for key, value in update.items():
        if key in DANGEROUS_FIELDS or key not in ALLOWED_FIELDS:
            continue
        if key in {"known_name", "preferred_language", "preferred_explanation_style", "preferred_pet_style"}:
            text = _short_text(value, 80)
            cleaned[key] = (text or None) if key == "known_name" else text
        elif key in {"important_preferences", "recent_topics"}:
            cleaned[key] = _short_list(value)
        elif key == "trust_level":
            cleaned[key] = _clamp(value)
    return cleaned


def update_relationship_state(user_id: str, device_id: str, update: dict[str, Any]) -> dict[str, Any]:
    cleaned = validate_memory_update(update)
    store = load_memory_store()
    key = _relationship_key(user_id, device_id)
    current = store.get(key, {})
    if not isinstance(current, dict):
        current = {}
    merged = {**DEFAULT_RELATIONSHIP_STATE, **current}
    for list_key in ("important_preferences", "recent_topics"):
        if list_key in cleaned:
            existing = merged.get(list_key, [])
            if not isinstance(existing, list):
                existing = []
            merged[list_key] = _short_list(existing + cleaned[list_key])
    for key2, value in cleaned.items():
        if key2 not in {"important_preferences", "recent_topics"}:
            merged[key2] = value
    store[key] = merged
    save_memory_store(store)
    return merged


def format_relationship_context(relationship_state: dict[str, Any]) -> str:
    state = {**DEFAULT_RELATIONSHIP_STATE, **relationship_state}
    parts: list[str] = []
    if state.get("known_name"):
        parts.append(f"user name is {state['known_name']}")
    parts.append(f"prefers {state['preferred_explanation_style']} explanations")
    parts.append(f"pet style: {state['preferred_pet_style']}")
    prefs = state.get("important_preferences") or []
    if prefs:
        parts.append("stable preferences: " + "; ".join(prefs[:4]))
    topics = state.get("recent_topics") or []
    if topics:
        parts.append("recent topics: " + "; ".join(topics[:4]))
    return "User " + ", ".join(parts) + "."
