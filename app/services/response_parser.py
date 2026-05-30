"""Parse model output into validated TablePet response fields."""

from __future__ import annotations

import json
import re
from typing import Any

from ..schemas import ModelOutput


def _extract_json_candidate(text: str) -> str | None:
    stripped = text.strip()
    if stripped.startswith("{") and stripped.endswith("}"):
        return stripped
    match = re.search(r"\{.*\}", stripped, flags=re.DOTALL)
    return match.group(0) if match else None


def _clean_text(value: Any) -> str:
    text = str(value or "").strip()
    if len(text) > 1200:
        text = text[:1190].rstrip() + "..."
    return text


def _extract_assistant_text_from_malformed_json(text: str) -> str | None:
    match = re.search(r'"assistant_text"\s*:\s*"((?:\\.|[^"\\])*)', text, flags=re.DOTALL)
    if not match:
        return None
    raw_value = match.group(1)
    try:
        return json.loads(f'"{raw_value}"')
    except Exception:
        return raw_value.replace("\\n", "\n").replace('\\"', '"')


def parse_model_output(raw: Any) -> ModelOutput:
    try:
        if isinstance(raw, dict):
            if "assistant_text" in raw:
                return ModelOutput(
                    assistant_text=_clean_text(raw.get("assistant_text")),
                    state_update=raw.get("state_update") if isinstance(raw.get("state_update"), dict) else {},
                    memory_update=raw.get("memory_update") if isinstance(raw.get("memory_update"), dict) else {},
                    device_action=raw.get("device_action") if isinstance(raw.get("device_action"), dict) else None,
                    dialogue_act=raw.get("dialogue_act"),
                    emotional_tone=raw.get("emotional_tone"),
                )
            choices = raw.get("choices")
            if isinstance(choices, list) and choices:
                content = choices[0].get("message", {}).get("content", "")
                return parse_model_output(content)
            return ModelOutput(assistant_text=_clean_text(raw))

        if isinstance(raw, str):
            candidate = _extract_json_candidate(raw)
            if candidate:
                try:
                    parsed = json.loads(candidate)
                    return parse_model_output(parsed)
                except json.JSONDecodeError:
                    extracted = _extract_assistant_text_from_malformed_json(candidate)
                    if extracted:
                        return ModelOutput(assistant_text=_clean_text(extracted))
            extracted = _extract_assistant_text_from_malformed_json(raw)
            if extracted:
                return ModelOutput(assistant_text=_clean_text(extracted))
            return ModelOutput(assistant_text=_clean_text(raw))
    except Exception:
        return ModelOutput(assistant_text=_clean_text(raw))
    return ModelOutput(assistant_text="")
