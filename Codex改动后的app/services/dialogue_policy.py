"""Deterministic dialogue policy before model prompting."""

from __future__ import annotations

from typing import Any


def choose_dialogue_policy(
    user_text: str,
    intent: str,
    robot_state: dict[str, Any],
    relationship_state: dict[str, Any],
    conversation_context: str,
) -> dict[str, Any]:
    policy: dict[str, Any] = {
        "dialogue_act": "answer_directly",
        "emotional_tone": "warm",
        "response_length": "short",
        "should_ask_followup": False,
        "should_use_personality": True,
        "should_use_memory": intent in {"memory_query", "chat", "casual_chat", "emotional_support"},
        "should_use_sensor_context": False,
        "should_call_llm": True,
    }

    if intent == "technical_help":
        policy.update(
            dialogue_act="explain_step_by_step",
            emotional_tone="focused",
            response_length="medium_long",
            should_ask_followup=False,
            should_use_personality=False,
            should_use_memory=True,
        )
    elif intent == "emotional_support":
        policy.update(
            dialogue_act="comfort",
            emotional_tone="gentle",
            response_length="short_medium",
            should_ask_followup=True,
            should_use_personality=True,
            should_use_memory=True,
        )
    elif intent == "latency_debug":
        policy.update(
            dialogue_act="explain_step_by_step",
            emotional_tone="focused",
            response_length="medium",
            should_use_personality=False,
            should_use_sensor_context=True,
        )
    elif intent == "device_status":
        policy.update(
            dialogue_act="status_report",
            emotional_tone="warm",
            response_length="short_medium",
            should_use_sensor_context=True,
        )
    elif intent in {"music", "device_control"}:
        policy.update(
            dialogue_act="execute_action",
            emotional_tone="playful" if intent == "music" else "warm",
            response_length="short",
            should_call_llm=False,
            should_use_sensor_context=intent == "device_control",
        )
    elif intent == "memory_write":
        policy.update(
            dialogue_act="acknowledge_and_remember",
            emotional_tone="warm",
            response_length="short",
            should_call_llm=False,
            should_use_memory=True,
        )
    elif intent == "empty":
        policy.update(
            dialogue_act="ignore_empty",
            emotional_tone="neutral",
            response_length="none",
            should_call_llm=False,
            should_use_personality=False,
            should_use_memory=False,
        )
    elif intent == "force_wake":
        policy.update(
            dialogue_act="answer_directly",
            emotional_tone="warm",
            response_length="short",
            should_call_llm=False,
        )
    elif intent == "casual_chat":
        policy.update(
            dialogue_act="playful_reply" if not conversation_context else "answer_directly",
            emotional_tone="playful",
            response_length="natural",
            should_ask_followup=False,
        )
    elif intent == "weather":
        policy.update(
            dialogue_act="answer_directly",
            emotional_tone="focused",
            response_length="short_medium",
        )
    elif intent == "vision":
        policy.update(
            dialogue_act="answer_directly",
            emotional_tone="warm",
            response_length="short_medium",
            should_use_sensor_context=True,
        )
    return policy
