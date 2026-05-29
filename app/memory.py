"""长期记忆持久化 / Long-term memory persistence on disk."""

from __future__ import annotations

import json
import time
from typing import Any

from .config import MEMORY_FILE


def load_memory_store() -> dict[str, Any]:
    if not MEMORY_FILE.exists():
        return {}
    try:
        data = json.loads(MEMORY_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def save_memory_store(data: dict[str, Any]) -> None:
    tmp = MEMORY_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(MEMORY_FILE)


def device_memory(device_id: str) -> dict[str, Any]:
    store = load_memory_store()
    memory = store.setdefault(device_id, {"profile": [], "recent": []})
    memory.setdefault("profile", [])
    memory.setdefault("recent", [])
    return memory


def memory_context(device_id: str) -> str:
    memory = device_memory(device_id)
    profile = memory.get("profile", [])[-12:]
    recent = memory.get("recent", [])[-8:]
    if not profile and not recent:
        return "长期记忆：暂时没有可靠记忆。"
    lines = ["长期记忆："]
    for item in profile:
        lines.append(f"- {item}")
    if recent:
        lines.append("最近互动摘要：")
        for item in recent:
            lines.append(
                f"- 用户：{item.get('user', '')[:80]} / 回复：{item.get('assistant', '')[:80]}"
            )
    return "\n".join(lines)


def update_memory_after_chat(device_id: str, user_text: str, assistant_text: str) -> None:
    store = load_memory_store()
    memory = store.setdefault(device_id, {"profile": [], "recent": []})
    profile: list[str] = memory.setdefault("profile", [])
    recent: list[dict[str, str]] = memory.setdefault("recent", [])

    recent.append(
        {
            "user": user_text[:240],
            "assistant": assistant_text[:240],
            "ts": f"{time.time():.0f}",
        }
    )
    del recent[:-30]

    useful_markers = ("我叫", "我是", "我的", "我喜欢", "我不喜欢", "记住", "以后叫我", "我想要")
    if any(marker in user_text for marker in useful_markers):
        fact = user_text.strip()
        if fact and fact not in profile:
            profile.append(fact[:160])
            del profile[:-24]

    save_memory_store(store)
