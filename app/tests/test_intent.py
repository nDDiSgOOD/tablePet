from app.services.intent import detect_intent


def test_intent_detects_technical_help():
    assert detect_intent("Explain this FastAPI router architecture error") == "technical_help"


def test_intent_detects_emotional_support():
    assert detect_intent("I feel tired and anxious today") == "emotional_support"
