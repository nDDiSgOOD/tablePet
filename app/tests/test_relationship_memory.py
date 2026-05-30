from app.services.relationship_memory import validate_memory_update


def test_relationship_memory_rejects_dangerous_fields():
    update = validate_memory_update(
        {"important_preferences": ["clear docs"], "api_key": "secret", "hidden_rules": "x"}
    )
    assert update == {"important_preferences": ["clear docs"]}
