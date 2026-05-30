from pathlib import Path

import app.memory as memory
from app.services.context import select_sensor_context


def test_sensor_context_empty_when_policy_disables_it(tmp_path: Path):
    memory.MEMORY_FILE = tmp_path / "memory.json"
    context = select_sensor_context("test-device", "status?", "device_status", {"should_use_sensor_context": False})
    assert context == ""
