from app.services.dialogue_policy import choose_dialogue_policy


def test_dialogue_policy_technical_help_is_focused():
    policy = choose_dialogue_policy("debug fastapi", "technical_help", {}, {}, "")
    assert policy["dialogue_act"] == "explain_step_by_step"
    assert policy["emotional_tone"] == "focused"
    assert policy["should_use_personality"] is False


def test_dialogue_policy_emotional_support_is_gentle():
    policy = choose_dialogue_policy("I feel sad", "emotional_support", {}, {}, "")
    assert policy["dialogue_act"] == "comfort"
    assert policy["emotional_tone"] == "gentle"
    assert policy["should_ask_followup"] is True
