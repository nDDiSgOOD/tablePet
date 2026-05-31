"""把已启用的 Skill / MCP 转成 agent 可读上下文。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ..storage.agent_extension import list_enabled_extensions

MAX_SKILL_CHARS = 12_000
MAX_TOTAL_CHARS = 32_000


def _clip(text: str, limit: int) -> str:
    text = (text or "").strip()
    if len(text) <= limit:
        return text
    return text[:limit] + "\n...[已截断]"


def _latest_local_content(item: dict[str, Any]) -> str:
    if item.get("source_type") != "local_file" or not item.get("source_uri"):
        return str(item.get("content") or "")
    try:
        path = Path(str(item["source_uri"])).expanduser()
        if path.exists() and path.is_file():
            return path.read_text(encoding="utf-8")
    except OSError:
        pass
    return str(item.get("content") or "")


def build_agent_extension_context(user_id: str) -> str:
    """生成 prompt 注入块。

    Skill 目前以"可执行说明 / 能力说明"接入：本地文件会在每次对话前重新读取，
    GitHub/URL 内容则使用安装时缓存。MCP 先暴露已启用服务器配置，后续可在
    tool_call 节点把同一份配置接到真实 MCP client。
    """
    skills = list_enabled_extensions(user_id, "skill")
    servers = list_enabled_extensions(user_id, "mcp")
    parts: list[str] = []
    if skills:
        lines = [
            "## Enabled Skills",
            "Use these installed skills as additional instructions when they match the user's request.",
        ]
        for item in skills:
            content = _clip(_latest_local_content(item), MAX_SKILL_CHARS)
            desc = str(item.get("description") or "").strip()
            header = f"### {item.get('name') or 'Unnamed Skill'}"
            if desc:
                header += f"\nDescription: {desc}"
            lines.append(f"{header}\nSource: {item.get('source_type')} {item.get('source_uri') or ''}\n{content}")
        parts.append("\n\n".join(lines))
    if servers:
        lines = [
            "## Enabled MCP Servers",
            "These MCP servers are configured for the agent. If a task requires one, prefer its declared capability and be explicit when an action needs a live tool call.",
        ]
        for item in servers:
            config = item.get("config") or {}
            safe_config = {
                k: v for k, v in config.items()
                if k not in {"env"} and v not in ("", [], {}, None)
            }
            env_keys = sorted((config.get("env") or {}).keys())
            if env_keys:
                safe_config["env_keys"] = env_keys
            lines.append(
                "### {name}\nDescription: {desc}\nConfig: {config}".format(
                    name=item.get("name") or "Unnamed MCP",
                    desc=item.get("description") or "",
                    config=json.dumps(safe_config, ensure_ascii=False),
                )
            )
        parts.append("\n\n".join(lines))
    return _clip("\n\n".join(parts), MAX_TOTAL_CHARS)
