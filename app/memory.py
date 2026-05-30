"""长期记忆 facade / Long-term memory facade.

历史
----
本模块原本把所有记忆存进单个 ``memory.json`` 文件。重构后，**用户级数据**
（profile / 长期画像断言 / 最近对话）改成 SQLite，按 ``user_id`` 隔离，
使得 web / wifi / usb 三端共享同一份记忆。

兼容策略
--------
- ``load_memory_store`` / ``save_memory_store`` 仍然读写 ``memory.json``，
  专门给 ``services/agent_state`` / ``services/relationship_memory`` 用，
  里面存的是 agent 内部派生状态（robot_state / relationship_state），
  不直接暴露给前端，留待后续重构。
- 其余 API（``get_user_profile``、``add_profile_fact`` 等）改为直接走
  ``app.storage``，全部按 ``user_id`` 索引；``device_id`` 参数被映射成
  ``DEFAULT_USER_ID``（全局唯一用户）。
"""

from __future__ import annotations

import json
import time
from typing import Any

from .config import MEMORY_FILE
from .storage import (
    add_fact,
    clear_recent,
    delete_fact_by_index,
    get_profile,
    list_facts,
    list_recent,
    record_chat,
    update_profile,
)

# 你给定的设计：单用户。所有"按 device_id"的旧 API 都映射到这里。
DEFAULT_USER_ID = "tablepet"


# ---------------------------------------------------------------------------
# 旧 JSON 文件层：仅服务 robot_state / relationship_state 这类 agent 内部状态。
# 业务功能（profile / facts / recent）请走 SQLite 版本。
# ---------------------------------------------------------------------------
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


# ---------------------------------------------------------------------------
# 兼容层：device_id 维度的旧 API 映射到 DEFAULT_USER_ID
# ---------------------------------------------------------------------------
def device_memory(device_id: str) -> dict[str, Any]:
    """旧接口兼容：把 SQLite 数据按旧 shape 拼回去."""
    return {
        "profile": list_facts(DEFAULT_USER_ID),
        "recent": list_recent(DEFAULT_USER_ID, limit=30),
        "user_profile": get_profile(DEFAULT_USER_ID),
    }


def get_user_profile(device_id: str = DEFAULT_USER_ID) -> dict[str, Any]:
    return get_profile(DEFAULT_USER_ID)


def save_user_profile(profile: dict[str, Any], device_id: str = DEFAULT_USER_ID) -> dict[str, Any]:
    return update_profile(DEFAULT_USER_ID, profile)


def add_profile_fact(device_id: str, fact: str) -> None:
    add_fact(DEFAULT_USER_ID, fact)


def delete_profile_fact(device_id: str, idx: int) -> bool:
    return delete_fact_by_index(DEFAULT_USER_ID, idx)


def clear_short_term_memory(device_id: str) -> None:
    clear_recent(DEFAULT_USER_ID)


def update_memory_after_chat(device_id: str, user_text: str, assistant_text: str) -> None:
    record_chat(DEFAULT_USER_ID, user_text, assistant_text)

    useful_markers = ("我叫", "我是", "我的", "我喜欢", "我不喜欢", "记住", "以后叫我", "我想要")
    if any(marker in user_text for marker in useful_markers):
        add_fact(DEFAULT_USER_ID, user_text.strip())


# ---------------------------------------------------------------------------
# Prompt 注入用的字符串拼装（保留旧函数签名）
# ---------------------------------------------------------------------------
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
    custom = profile.get("custom") or {}
    if isinstance(custom, dict):
        for k, v in custom.items():
            text = str(v).strip()
            if text:
                lines.append(f"{k}：{text[:160]}")
    return lines


def user_profile_context(device_id: str, request_profile: dict[str, Any] | None = None) -> str:
    profile = get_profile(DEFAULT_USER_ID)
    if request_profile:
        profile = {**profile, **request_profile}
    lines = _profile_lines(profile)
    if not lines:
        return "用户简介：暂时没有可靠资料。"
    return "用户简介：\n" + "\n".join(f"- {line}" for line in lines)


def memory_context(device_id: str, request_profile: dict[str, Any] | None = None) -> str:
    facts = list_facts(DEFAULT_USER_ID)[-12:]
    recent = list_recent(DEFAULT_USER_ID, limit=8)
    lines = [user_profile_context(device_id, request_profile), "长期记忆："]
    if not facts and not recent:
        lines.append("暂时没有可靠记忆。")
        return "\n".join(lines)
    for item in facts:
        lines.append(f"- {item}")
    if recent:
        lines.append("最近互动摘要：")
        for item in recent:
            lines.append(
                f"- 用户：{str(item.get('user', ''))[:80]} / 回复：{str(item.get('assistant', ''))[:80]}"
            )
    return "\n".join(lines)


# 保留 time 导入避免未使用 lint（旧代码里 update_memory_after_chat 用过）
_ = time
