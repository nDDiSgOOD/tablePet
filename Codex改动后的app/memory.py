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


def _profile_lines(profile: dict[str, Any]) -> list[str]:
    labels = {
        "name": "昵称",
        "language": "偏好语言",
        "bio": "性格/兴趣",
        "city": "所在城市",
        "since": "注册时间",
    }
    lines: list[str] = []
    for key, label in labels.items():
        value = str(profile.get(key) or "").strip()
        if value:
            lines.append(f"{label}：{value[:160]}")
    return lines


def user_profile_context(device_id: str, request_profile: dict[str, Any] | None = None) -> str:
    memory = device_memory(device_id)
    stored = memory.get("user_profile", {})
    profile = stored if isinstance(stored, dict) else {}
    if request_profile:
        profile = {**profile, **request_profile}
    lines = _profile_lines(profile)
    if not lines:
        return "用户简介：暂时没有可靠资料。"
    return "用户简介：\n" + "\n".join(f"- {line}" for line in lines)


def memory_context(device_id: str, request_profile: dict[str, Any] | None = None) -> str:
    memory = device_memory(device_id)
    profile = memory.get("profile", [])[-12:]
    recent = memory.get("recent", [])[-8:]
    lines = [user_profile_context(device_id, request_profile), "长期记忆："]
    if not profile and not recent:
        lines.append("暂时没有可靠记忆。")
        return "\n".join(lines)
    for item in profile:
        lines.append(f"- {item}")
    if recent:
        lines.append("最近互动摘要：")
        for item in recent:
            lines.append(
                f"- 用户：{item.get('user', '')[:80]} / 回复：{item.get('assistant', '')[:80]}"
            )
    return "\n".join(lines)


def get_user_profile(device_id: str = "tablepet") -> dict[str, Any]:
    memory = device_memory(device_id)
    profile = memory.get("user_profile", {})
    return profile if isinstance(profile, dict) else {}


def save_user_profile(profile: dict[str, Any], device_id: str = "tablepet") -> dict[str, Any]:
    store = load_memory_store()
    memory = store.setdefault(device_id, {"profile": [], "recent": []})
    memory.setdefault("profile", [])
    memory.setdefault("recent", [])
    current = memory.get("user_profile", {})
    if not isinstance(current, dict):
        current = {}
    cleaned = {
        "name": str(profile.get("name") or current.get("name") or "")[:80],
        "language": str(profile.get("language") or current.get("language") or "zh-CN")[:40],
        "bio": str(profile.get("bio") or current.get("bio") or "")[:500],
        "city": str(profile.get("city") or current.get("city") or "")[:120],
        "since": str(profile.get("since") or current.get("since") or "")[:80],
    }
    memory["user_profile"] = cleaned
    save_memory_store(store)
    return cleaned


def add_profile_fact(device_id: str, fact: str) -> None:
    fact = fact.strip()
    if not fact:
        return
    store = load_memory_store()
    memory = store.setdefault(device_id, {"profile": [], "recent": []})
    profile: list[str] = memory.setdefault("profile", [])
    if fact not in profile:
        profile.append(fact[:160])
        del profile[:-24]
    save_memory_store(store)


def delete_profile_fact(device_id: str, idx: int) -> bool:
    store = load_memory_store()
    memory = store.setdefault(device_id, {"profile": [], "recent": []})
    profile: list[str] = memory.setdefault("profile", [])
    if idx < 0 or idx >= len(profile):
        return False
    del profile[idx]
    save_memory_store(store)
    return True


def clear_short_term_memory(device_id: str) -> None:
    store = load_memory_store()
    memory = store.setdefault(device_id, {"profile": [], "recent": []})
    memory["recent"] = []
    save_memory_store(store)


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
