"""High-level TablePet interaction orchestrator.

This is the only service that is allowed to compose intent, context, tools,
LLM, memory, state, and optional TTS.
"""

from __future__ import annotations

import time
import uuid
from typing import Any

from .. import state
from ..memory import add_profile_fact, update_memory_after_chat
from ..schemas import AgentRuntimeState, InteractionRequest, InteractionResponse, TtsRequest
from .agent_state import get_robot_state, update_robot_state, validate_state_update
from .chat import call_deepseek_messages
from .context import (
    build_anti_repetition_context,
    build_conversation_context,
    load_profile,
    select_relevant_memory,
    select_sensor_context,
)
from .dialogue_policy import choose_dialogue_policy
from .intent import detect_intent
from .observability import timed
from .prompt_builder import build_messages
from .relationship_memory import (
    format_relationship_context,
    get_relationship_state,
    update_relationship_state,
    validate_memory_update,
)
from .response_parser import parse_model_output
from .tool_registry import run_tool_if_needed
from .tts import build_tts_request, generate_tts_wav


def _normalize_text(text: str | None) -> str:
    return (text or "").strip()


def _direct_reply(runtime: AgentRuntimeState, text: str) -> None:
    runtime.assistant_text = text


def _build_runtime(req: InteractionRequest) -> AgentRuntimeState:
    return AgentRuntimeState(
        interaction_id=f"int_{uuid.uuid4().hex}",
        device_id=req.device_id,
        user_id=req.user_id,
        source=req.source,
        transport=req.transport,
        raw_text=req.text,
        user_text=_normalize_text(req.text),
        event=req.event,
        want_tts=req.want_tts,
    )


def _safe_debug(runtime: AgentRuntimeState, policy: dict[str, Any]) -> dict[str, Any]:
    return {
        "intent": runtime.intent,
        "source": runtime.source,
        "transport": runtime.transport,
        "dialogue_act": runtime.dialogue_act,
        "emotional_tone": runtime.emotional_tone,
        "used_memory": bool(runtime.relevant_memory),
        "used_sensor_context": bool(runtime.sensor_context),
        "used_tool": bool(runtime.tool_context or runtime.device_action),
        "called_llm": bool(runtime.raw_model_output is not None),
        "policy": {
            "should_call_llm": policy.get("should_call_llm"),
            "should_use_memory": policy.get("should_use_memory"),
            "should_use_sensor_context": policy.get("should_use_sensor_context"),
        },
    }


async def process_interaction(req: InteractionRequest) -> InteractionResponse:
    total_started = time.perf_counter()
    runtime = _build_runtime(req)
    policy: dict[str, Any] = {}

    try:
        with timed(runtime.timing_ms, "intent"):
            runtime.intent = detect_intent(runtime.user_text, runtime.event, runtime.source)

        with timed(runtime.timing_ms, "state"):
            runtime.profile = load_profile(runtime.device_id, runtime.user_id)
            if req.extra and isinstance(req.extra.get("profile"), dict):
                runtime.profile = {**runtime.profile, **req.extra["profile"]}
            runtime.robot_state = get_robot_state(runtime.device_id)
            runtime.relationship_state = get_relationship_state(runtime.user_id, runtime.device_id)
            runtime.relationship_context = format_relationship_context(runtime.relationship_state)
            runtime.telemetry_state = state.DEVICE_STATES.get(runtime.device_id, {})

        with timed(runtime.timing_ms, "policy"):
            policy = choose_dialogue_policy(
                runtime.user_text or "",
                runtime.intent,
                runtime.robot_state,
                runtime.relationship_state,
                "",
            )
            runtime.dialogue_act = policy.get("dialogue_act")
            runtime.emotional_tone = policy.get("emotional_tone")

        with timed(runtime.timing_ms, "direct"):
            if runtime.intent == "empty":
                _direct_reply(runtime, "")
            elif runtime.intent == "force_wake":
                state.update_device(
                    runtime.device_id,
                    session=True,
                    mode="listening",
                    force_wake=True,
                    transport=runtime.transport or runtime.source,
                )
                runtime.device_action = {"type": "force_wake", "session": "listening"}
                _direct_reply(runtime, "我在，直接说就好。")
            elif runtime.intent == "memory_write":
                if runtime.user_text:
                    add_profile_fact(runtime.device_id, runtime.user_text)
                runtime.memory_update = {"important_preferences": [runtime.user_text or ""]}
                _direct_reply(runtime, "我记住了。")

        should_call_llm = bool(policy.get("should_call_llm")) and runtime.assistant_text is None

        with timed(runtime.timing_ms, "context"):
            runtime.conversation_context = build_conversation_context(
                runtime.device_id, runtime.intent, policy
            )
            policy = choose_dialogue_policy(
                runtime.user_text or "",
                runtime.intent,
                runtime.robot_state,
                runtime.relationship_state,
                runtime.conversation_context,
            )
            runtime.dialogue_act = policy.get("dialogue_act")
            runtime.emotional_tone = policy.get("emotional_tone")
            runtime.relevant_memory = select_relevant_memory(
                runtime.device_id, runtime.user_text or "", runtime.intent, policy
            )
            runtime.sensor_context = select_sensor_context(
                runtime.device_id, runtime.user_text or "", runtime.intent, policy
            )
            runtime.anti_repetition_context = build_anti_repetition_context(runtime.device_id)

        with timed(runtime.timing_ms, "tools"):
            runtime.tool_context, runtime.device_action, skip_llm = await run_tool_if_needed(
                runtime.intent, runtime.user_text or "", runtime.device_id, runtime.model_dump()
            )
            if skip_llm:
                should_call_llm = False
                if runtime.intent == "music":
                    _direct_reply(runtime, "好，我给你放一段音乐。")

        if should_call_llm:
            with timed(runtime.timing_ms, "prompt"):
                runtime.messages = build_messages(
                    user_text=runtime.user_text or "",
                    intent=runtime.intent,
                    policy=policy,
                    profile=runtime.profile,
                    robot_state=runtime.robot_state,
                    relationship_context=runtime.relationship_context,
                    relevant_memory=runtime.relevant_memory,
                    sensor_context=runtime.sensor_context,
                    conversation_context=runtime.conversation_context,
                    anti_repetition_context=runtime.anti_repetition_context,
                    tool_context=runtime.tool_context,
                )
            with timed(runtime.timing_ms, "llm"):
                runtime.raw_model_output = await call_deepseek_messages(runtime.messages)
            with timed(runtime.timing_ms, "parse"):
                parsed = parse_model_output(runtime.raw_model_output)
                runtime.assistant_text = parsed.assistant_text
                runtime.state_update = parsed.state_update
                runtime.memory_update = parsed.memory_update
                runtime.device_action = parsed.device_action or runtime.device_action
                runtime.dialogue_act = parsed.dialogue_act or runtime.dialogue_act
                runtime.emotional_tone = parsed.emotional_tone or runtime.emotional_tone

        with timed(runtime.timing_ms, "validate_state"):
            runtime.state_update = validate_state_update(runtime.state_update)
            if runtime.state_update:
                runtime.robot_state = update_robot_state(runtime.device_id, runtime.state_update)

        with timed(runtime.timing_ms, "validate_memory"):
            runtime.memory_update = validate_memory_update(runtime.memory_update)
            if runtime.memory_update:
                runtime.relationship_state = update_relationship_state(
                    runtime.user_id, runtime.device_id, runtime.memory_update
                )

        with timed(runtime.timing_ms, "tts"):
            if runtime.want_tts and runtime.assistant_text:
                try:
                    tts_req = build_tts_request(runtime.assistant_text, "taiwan")
                    wav_path = await generate_tts_wav(runtime.device_id, tts_req, runtime.transport or "interaction")
                    runtime.audio_url = f"/audio/{wav_path.name}"
                except Exception as exc:
                    runtime.debug["tts_error"] = str(exc)[:200]

        with timed(runtime.timing_ms, "save"):
            if runtime.user_text and runtime.assistant_text:
                update_memory_after_chat(runtime.device_id, runtime.user_text, runtime.assistant_text)
            state.update_device(
                runtime.device_id,
                last_interaction_id=runtime.interaction_id,
                last_interaction_intent=runtime.intent,
                last_dialogue_act=runtime.dialogue_act,
                last_emotional_tone=runtime.emotional_tone,
                last_chat_ms=runtime.timing_ms.get("llm", 0),
                transport=runtime.transport or runtime.source,
            )
            state.remember_event(
                runtime.device_id, "INTERACTION", f"{runtime.intent}: {(runtime.user_text or '')[:80]}"
            )

        runtime.timing_ms["total"] = round((time.perf_counter() - total_started) * 1000, 2)
        runtime.debug = {**runtime.debug, **_safe_debug(runtime, policy)}
        return InteractionResponse(
            ok=True,
            interaction_id=runtime.interaction_id,
            device_id=runtime.device_id,
            user_text=runtime.user_text,
            assistant_text=runtime.assistant_text,
            intent=runtime.intent,
            dialogue_act=runtime.dialogue_act,
            emotional_tone=runtime.emotional_tone,
            audio_url=runtime.audio_url,
            device_action=runtime.device_action,
            state_update=runtime.state_update or None,
            memory_update=runtime.memory_update or None,
            timing_ms=runtime.timing_ms,
            debug=runtime.debug,
        )
    except Exception as exc:
        runtime.timing_ms["total"] = round((time.perf_counter() - total_started) * 1000, 2)
        runtime.errors.append(str(exc))
        return InteractionResponse(
            ok=False,
            interaction_id=runtime.interaction_id,
            device_id=runtime.device_id,
            user_text=runtime.user_text,
            assistant_text=runtime.assistant_text,
            intent=runtime.intent,
            dialogue_act=runtime.dialogue_act,
            emotional_tone=runtime.emotional_tone,
            device_action=runtime.device_action,
            timing_ms=runtime.timing_ms,
            debug=_safe_debug(runtime, policy),
            error=str(exc)[:500],
        )
