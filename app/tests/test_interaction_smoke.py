import asyncio
from pathlib import Path

import app.memory as memory
import app.services.interaction as interaction
from app.schemas import InteractionRequest


def _use_temp_memory(tmp_path: Path) -> None:
    memory.MEMORY_FILE = tmp_path / "memory.json"


def test_process_interaction_empty_does_not_call_deepseek(tmp_path: Path):
    _use_temp_memory(tmp_path)

    async def fail_call(_messages):
        raise AssertionError("DeepSeek should not be called")

    original = interaction.call_deepseek_messages
    interaction.call_deepseek_messages = fail_call
    try:
        result = asyncio.run(
            interaction.process_interaction(
                InteractionRequest(source="web_text", text="", want_tts=False)
            )
        )
    finally:
        interaction.call_deepseek_messages = original

    assert result.ok is True
    assert result.intent == "empty"
    assert result.debug["called_llm"] is False


def test_process_interaction_music_returns_action_without_deepseek(tmp_path: Path):
    _use_temp_memory(tmp_path)

    async def fail_call(_messages):
        raise AssertionError("DeepSeek should not be called")

    original = interaction.call_deepseek_messages
    interaction.call_deepseek_messages = fail_call
    try:
        result = asyncio.run(
            interaction.process_interaction(
                InteractionRequest(source="web_text", text="play music", want_tts=False)
            )
        )
    finally:
        interaction.call_deepseek_messages = original

    assert result.ok is True
    assert result.intent == "music"
    assert result.device_action == {"type": "play_audio", "url": "/music/default.wav"}
    assert result.debug["called_llm"] is False
