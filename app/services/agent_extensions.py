"""把已启用的本地 Skill 目录 / MCP 配置转成 agent 可读上下文。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ..storage.agent_extension import list_enabled_extensions

MAX_SKILL_CHARS = 12_000
MAX_TOTAL_CHARS = 32_000
SKILL_ENTRY_FILES = ("SKILL.md", "skill.md", "README.md", "readme.md")
SKILL_EXTRA_SUFFIXES = (".md", ".txt")


def _clip(text: str, limit: int) -> str:
    text = (text or "").strip()
    if len(text) <= limit:
        return text
    return text[:limit] + "\n...[已截断]"


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return ""


def _skill_dir_content(item: dict[str, Any]) -> str:
    config = item.get("config") or {}
    local_path = config.get("local_path") or item.get("source_uri")
    if not local_path:
        return str(item.get("content") or "")
    root = Path(str(local_path)).expanduser()
    if root.is_file():
        return _read_text(root)
    if not root.exists() or not root.is_dir():
        return ""

    chunks: list[str] = []
    seen: set[Path] = set()
    for filename in SKILL_ENTRY_FILES:
        path = root / filename
        if path.exists() and path.is_file():
            text = _read_text(path)
            if text:
                chunks.append(f"# {path.name}\n{text}")
                seen.add(path.resolve())

    if not chunks:
        for path in sorted(root.rglob("*")):
            if len(chunks) >= 4:
                break
            if not path.is_file() or path.suffix.lower() not in SKILL_EXTRA_SUFFIXES:
                continue
            try:
                resolved = path.resolve()
            except OSError:
                continue
            if resolved in seen or any(part.startswith(".") for part in path.relative_to(root).parts):
                continue
            text = _read_text(path)
            if text:
                chunks.append(f"# {path.relative_to(root)}\n{text}")
    return "\n\n".join(chunks)


def build_agent_extension_context(user_id: str) -> str:
    """生成 prompt 注入块。

    Skill 以本地目录为唯一运行形态：安装时可以来自本地目录、GitHub/Git 或 ZIP，
    但 agent 使用时只从本地目录读取。MCP 先暴露已启用服务器配置，后续可在
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
            config = item.get("config") or {}
            local_path = config.get("local_path") or ""
            content = _clip(_skill_dir_content(item), MAX_SKILL_CHARS)
            if not content:
                continue
            desc = str(item.get("description") or "").strip()
            header = f"### {item.get('name') or 'Unnamed Skill'}"
            if desc:
                header += f"\nDescription: {desc}"
            lines.append(
                f"{header}\n"
                f"Installed directory: {local_path}\n"
                f"Original source: {item.get('source_type')} {item.get('source_uri') or ''}\n"
                f"{content}"
            )
        if len(lines) > 2:
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
