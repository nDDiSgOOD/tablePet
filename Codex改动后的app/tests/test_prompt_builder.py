from app.services.prompt_builder import build_messages


def test_prompt_builder_does_not_include_empty_sensor_context():
    messages = build_messages(
        user_text="hello",
        intent="chat",
        policy={"dialogue_act": "answer_directly"},
        profile={},
        robot_state={},
        relationship_context="",
        relevant_memory="",
        sensor_context="",
        conversation_context="",
        anti_repetition_context="",
        tool_context="",
    )
    assert "selected sensor context" not in messages[1]["content"]
