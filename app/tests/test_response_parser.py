from app.services.response_parser import parse_model_output


def test_response_parser_handles_plain_text():
    parsed = parse_model_output("hello there")
    assert parsed.assistant_text == "hello there"


def test_response_parser_handles_json_string():
    parsed = parse_model_output('{"assistant_text":"hi","emotional_tone":"warm"}')
    assert parsed.assistant_text == "hi"
    assert parsed.emotional_tone == "warm"


def test_response_parser_extracts_malformed_json_assistant_text():
    parsed = parse_model_output('{"assistant_text":"好问题！\\n第一点是职责分离')
    assert parsed.assistant_text.startswith("好问题！")
