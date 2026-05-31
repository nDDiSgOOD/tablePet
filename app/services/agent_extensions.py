"""把已启用的本地 Skill 目录 / MCP 配置转成 agent 能力上下文。"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from ..storage.agent_extension import list_enabled_extensions

MAX_SKILL_CHARS = 12_000
MAX_TOTAL_CHARS = 32_000
SKILLS_INDEX_MAX_CHARS = 4000
SKILL_FILE = "SKILL.md"
VALID_SKILL_NAME = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9._-]{0,63}$")


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


def _parse_frontmatter(raw: str) -> tuple[dict[str, str], str]:
    lines = raw.splitlines()
    if not lines or lines[0] != "---":
        return {}, raw
    try:
        end = lines.index("---", 1)
    except ValueError:
        return {}, raw
    data: dict[str, str] = {}
    for line in lines[1:end]:
        match = re.match(r"^([a-zA-Z_][a-zA-Z0-9_-]*):\s*(.*)$", line)
        if match:
            data[match.group(1)] = match.group(2).strip()
    return data, "\n".join(lines[end + 1:]).lstrip()


def _safe_skill_name(raw: str) -> str:
    name = re.sub(r"[^a-zA-Z0-9._-]+", "-", raw.strip()).strip("-._")
    if not name or not VALID_SKILL_NAME.match(name):
        return "skill"
    return name[:64]


def _skill_file_for_item(item: dict[str, Any]) -> Path | None:
    config = item.get("config") or {}
    local_path = config.get("local_path") or item.get("source_uri")
    if not local_path:
        return None
    root = Path(str(local_path)).expanduser()
    if root.is_file():
        return root
    if not root.exists() or not root.is_dir():
        return None
    preferred = root / SKILL_FILE
    if preferred.exists() and preferred.is_file():
        return preferred
    flat = root / f"{_safe_skill_name(str(item.get('name') or root.name))}.md"
    if flat.exists() and flat.is_file():
        return flat
    for path in sorted(root.glob("*.md")):
        if path.is_file() and not path.name.startswith("."):
            return path
    return None


def _parse_skill_item(item: dict[str, Any]) -> dict[str, Any] | None:
    path = _skill_file_for_item(item)
    if path is None:
        return None
    raw = _read_text(path)
    if not raw.strip():
        return None
    data, body = _parse_frontmatter(raw)
    fallback_name = str(item.get("name") or path.parent.name or path.stem)
    name = data.get("name", "").strip()
    if not VALID_SKILL_NAME.match(name):
        name = _safe_skill_name(fallback_name)
    return {
        "name": name,
        "description": (data.get("description") or item.get("description") or "").strip(),
        "body": body.strip(),
        "run_as": "subagent" if data.get("runAs", "").strip() == "subagent" else "inline",
        "allowed_tools": [
            t.strip() for t in (data.get("allowed-tools") or "").split(",") if t.strip()
        ],
        "path": str(path),
        "source_type": item.get("source_type") or "",
        "source_uri": item.get("source_uri") or "",
    }


def list_available_skills(user_id: str) -> list[dict[str, Any]]:
    skills: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in list_enabled_extensions(user_id, "skill"):
        parsed = _parse_skill_item(item)
        if not parsed or parsed["name"] in seen:
            continue
        seen.add(parsed["name"])
        skills.append(parsed)
    return sorted(skills, key=lambda s: s["name"])


def read_skill(user_id: str, name: str) -> dict[str, Any] | None:
    if not VALID_SKILL_NAME.match(name):
        return None
    for skill in list_available_skills(user_id):
        if skill["name"] == name:
            return skill
    return None


def _skill_index_line(skill: dict[str, Any]) -> str:
    tag = " [subagent]" if skill.get("run_as") == "subagent" else ""
    desc = str(skill.get("description") or "").replace("\n", " ").strip()
    max_desc = max(16, 130 - len(str(skill.get("name") or "")) - len(tag))
    if len(desc) > max_desc:
        desc = desc[: max_desc - 1] + "..."
    return f"- {skill['name']}{tag}" + (f" - {desc}" if desc else "")


def build_skills_index_context(user_id: str) -> str:
    skills = [s for s in list_available_skills(user_id) if s.get("description")]
    if not skills:
        return ""
    joined = "\n".join(_skill_index_line(s) for s in skills)
    if len(joined) > SKILLS_INDEX_MAX_CHARS:
        joined = joined[:SKILLS_INDEX_MAX_CHARS] + "\n... (truncated)"
    return "\n".join([
        "## Skills - playbooks you can invoke",
        "",
        "Only a short index is pinned here. When a skill matches the user's request, call the `run_skill` tool with the bare skill name and a concise arguments string. The tool will read the local SKILL.md body on demand. Do not copy the [subagent] tag into the name.",
        "",
        "```",
        joined,
        "```",
    ])


def skill_tool_spec(user_id: str) -> dict[str, Any] | None:
    if not list_available_skills(user_id):
        return None
    return {
        "type": "function",
        "function": {
            "name": "run_skill",
            "description": "Invoke an installed local Skill playbook by name. The skill body is loaded from its local SKILL.md on demand.",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "Bare skill identifier from the Skills index, without tags.",
                    },
                    "arguments": {
                        "type": "string",
                        "description": "Concrete task or request for the skill.",
                    },
                },
                "required": ["name"],
            },
        },
    }


def run_skill_tool(user_id: str, raw_args: dict[str, Any]) -> str:
    raw_name = str(raw_args.get("name") or "").strip()
    raw_name = re.sub(r"\[[^\]]*\]", " ", raw_name).strip()
    name = next((t for t in raw_name.split() if t and t[0].isalnum()), "")
    if not name:
        return json.dumps({"error": "run_skill requires a skill name"}, ensure_ascii=False)
    skill = read_skill(user_id, name)
    if skill is None:
        available = [s["name"] for s in list_available_skills(user_id)]
        return json.dumps(
            {"error": f"unknown skill: {name}", "available": available},
            ensure_ascii=False,
        )
    args = str(raw_args.get("arguments") or "").strip()
    header = f"# Skill: {skill['name']}"
    if skill.get("description"):
        header += f"\n> {skill['description']}"
    header += f"\n(scope: local · {skill['path']})"
    args_block = f"\n\nArguments: {args}" if args else ""
    return _clip(f"{header}\n\n{skill['body']}{args_block}", MAX_SKILL_CHARS)


def _mcp_spec_for_item(item: dict[str, Any]) -> str:
    config = item.get("config") or {}
    name = str(item.get("name") or "").strip()
    prefix = f"{_safe_skill_name(name)}=" if name else ""
    transport = config.get("transport")
    if transport == "stdio":
        command = str(config.get("command") or "").strip()
        args = " ".join(str(a) for a in (config.get("args") or []) if str(a).strip())
        return f"{prefix}{command}{(' ' + args) if args else ''}".strip()
    url = str(config.get("url") or "").strip()
    if transport == "http":
        return f"{prefix}streamable+{url}" if url else ""
    if transport == "sse":
        return f"{prefix}{url}" if url else ""
    return ""


def build_mcp_index_context(user_id: str) -> str:
    servers = list_enabled_extensions(user_id, "mcp")
    if not servers:
        return ""
    lines = [
        "## MCP servers",
        "",
        "Configured MCP specs are listed here. In Reasonix these specs are bridged into callable tools after the client initializes. TablePet keeps the same spec shape so the next step can hot-bridge each server into function tools.",
    ]
    for item in servers:
        spec = _mcp_spec_for_item(item)
        if not spec:
            continue
        desc = str(item.get("description") or "").strip()
        lines.append(f"- {item.get('name') or 'mcp'}: `{spec}`" + (f" - {desc}" if desc else ""))
    return "\n".join(lines) if len(lines) > 3 else ""


def build_agent_tools(user_id: str) -> list[dict[str, Any]]:
    tools: list[dict[str, Any]] = []
    skill_tool = skill_tool_spec(user_id)
    if skill_tool:
        tools.append(skill_tool)
    return tools


def dispatch_agent_tool(user_id: str, name: str, arguments: dict[str, Any]) -> str:
    if name == "run_skill":
        return run_skill_tool(user_id, arguments)
    return json.dumps({"error": f"unknown tool: {name}"}, ensure_ascii=False)


def build_agent_extension_context(user_id: str) -> str:
    """生成 prompt 注入块。

    Skill 以本地目录为唯一运行形态：安装时可以来自本地目录、GitHub/Git 或 ZIP，
    但系统 prompt 只放短索引；正文通过 run_skill 按需读取。
    """
    parts = [
        build_skills_index_context(user_id),
        build_mcp_index_context(user_id),
    ]
    return _clip("\n\n".join(parts), MAX_TOTAL_CHARS)
