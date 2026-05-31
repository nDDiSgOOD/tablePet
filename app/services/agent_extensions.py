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
COLLECTION_DIRS = ("skills", ".reasonix/skills")


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


def _skill_roots(root: Path) -> list[Path]:
    roots = [root]
    for rel in COLLECTION_DIRS:
        candidate = root / rel
        if candidate.exists() and candidate.is_dir():
            roots.append(candidate)
    return roots


def _skill_files_for_root(root: Path, item_name: str = "") -> list[Path]:
    if root.is_file():
        return [root] if root.suffix.lower() == ".md" else []
    if not root.exists() or not root.is_dir():
        return []
    files: list[Path] = []
    seen: set[Path] = set()

    def add(path: Path) -> None:
        try:
            resolved = path.resolve()
        except OSError:
            return
        if resolved in seen or not path.exists() or not path.is_file():
            return
        seen.add(resolved)
        files.append(path)

    roots = _skill_roots(root)
    has_collection_root = len(roots) > 1
    for base in roots:
        add(base / SKILL_FILE)
        if item_name:
            add(base / f"{_safe_skill_name(item_name)}.md")
        # 仓库根目录常有 README/THIRD_PARTY_NOTICES；有 skills/ 集合目录时
        # 不把根目录普通 md 当 skill，避免误注册说明/版权文件。
        if not (has_collection_root and base == root):
            for path in sorted(base.glob("*.md")):
                if path.is_file() and not path.name.startswith("."):
                    add(path)
        for child in sorted(base.iterdir()):
            if child.is_dir() and VALID_SKILL_NAME.match(child.name):
                add(child / SKILL_FILE)
    return files


def _skill_files_for_item(item: dict[str, Any]) -> list[Path]:
    config = item.get("config") or {}
    local_path = config.get("local_path") or item.get("source_uri")
    if not local_path:
        return []
    root = Path(str(local_path)).expanduser()
    return _skill_files_for_root(root, str(item.get("name") or ""))


def _parse_skill_file(item: dict[str, Any], path: Path) -> dict[str, Any] | None:
    raw = _read_text(path)
    if not raw.strip():
        return None
    data, body = _parse_frontmatter(raw)
    fallback_name = path.parent.name if path.name == SKILL_FILE else path.stem
    if fallback_name in {"skills", ".reasonix"}:
        fallback_name = str(item.get("name") or path.stem)
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
        "id": item.get("id"),
        "enabled": item.get("enabled", True),
        "local_path": str(path.parent),
    }


def parse_skill_extensions(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    parsed: list[dict[str, Any]] = []
    seen: set[tuple[Any, str]] = set()
    for item in items:
        for path in _skill_files_for_item(item):
            skill = _parse_skill_file(item, path)
            if not skill:
                continue
            key = (item.get("id"), skill["name"])
            if key in seen:
                continue
            seen.add(key)
            parsed.append(skill)
    return sorted(parsed, key=lambda s: str(s.get("name") or ""))


def list_available_skills(user_id: str) -> list[dict[str, Any]]:
    skills: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in list_enabled_extensions(user_id, "skill"):
        for parsed in parse_skill_extensions([item]):
            if parsed["name"] in seen:
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


def _schema_type(schema: Any) -> str:
    if not isinstance(schema, dict):
        return "string"
    raw = schema.get("type")
    if isinstance(raw, list):
        return str(raw[0] or "string")
    if raw:
        return str(raw)
    if schema.get("enum"):
        return "string"
    if isinstance(schema.get("properties"), dict):
        return "object"
    return "string"


def _schema_param_lines(tool: dict[str, Any], *, limit: int = 12) -> list[str]:
    schema = tool.get("input_schema") or tool.get("inputSchema") or {}
    if not isinstance(schema, dict):
        return []
    properties = schema.get("properties") or {}
    if not isinstance(properties, dict) or not properties:
        return []
    required = {str(v) for v in (schema.get("required") or []) if str(v)}
    lines: list[str] = []
    for name, prop in list(properties.items())[:limit]:
        if not isinstance(prop, dict):
            prop = {}
        desc = str(prop.get("description") or prop.get("title") or "").replace("\n", " ").strip()
        enum = prop.get("enum")
        enum_hint = ""
        if isinstance(enum, list) and enum:
            enum_hint = " enum=" + ",".join(str(v) for v in enum[:6])
        required_hint = "required" if str(name) in required else "optional"
        line = f"    - {name}: {_schema_type(prop)}, {required_hint}{enum_hint}"
        if desc:
            line += f" - {desc[:180]}"
        lines.append(line)
    if len(properties) > limit:
        lines.append(f"    - ... {len(properties) - limit} more parameters")
    return lines


def _tool_schema_for_name(server: dict[str, Any], tool_name: str) -> dict[str, Any]:
    tools = (server.get("config") or {}).get("tools") or []
    if not isinstance(tools, list):
        return {}
    for tool in tools:
        if isinstance(tool, dict) and str(tool.get("name") or "") == tool_name:
            schema = tool.get("input_schema") or tool.get("inputSchema") or {}
            return schema if isinstance(schema, dict) else {}
    return {}


def build_mcp_index_context(user_id: str) -> str:
    servers = list_enabled_extensions(user_id, "mcp")
    if not servers:
        return ""
    lines = [
        "## MCP servers",
        "",
        "Configured MCP servers are listed here with discovered tools. When a user asks for a capability provided by an MCP tool, call `call_mcp_tool` with server_id, tool_name, and arguments.",
        "The `arguments` object must follow each tool's parameter schema below. Never omit required parameters; if a required value is unknown, ask the user a brief clarification instead of calling the tool.",
    ]
    for item in servers:
        spec = _mcp_spec_for_item(item)
        if not spec:
            continue
        desc = str(item.get("description") or "").strip()
        config = item.get("config") or {}
        status = config.get("status") or "unknown"
        lines.append(f"- server_id={item.get('id')} {item.get('name') or 'mcp'}: `{spec}` status={status}" + (f" - {desc}" if desc else ""))
        tools = config.get("tools") or []
        if isinstance(tools, list) and tools:
            for tool in tools[:20]:
                if not isinstance(tool, dict):
                    continue
                t_name = str(tool.get("name") or "").strip()
                if not t_name:
                    continue
                t_desc = str(tool.get("description") or "").replace("\n", " ").strip()
                lines.append(f"  - tool: {t_name}" + (f" - {t_desc[:160]}" if t_desc else ""))
                lines.extend(_schema_param_lines(tool))
        elif config.get("last_error"):
            lines.append(f"  - tool discovery error: {str(config.get('last_error'))[:180]}")
    return "\n".join(lines) if len(lines) > 3 else ""


def build_local_music_context() -> str:
    return "\n".join([
        "## Local music control",
        "",
        "You can control local music apps on this macOS device with `local_music_control`.",
        "Supported apps: `qq_music` (QQ 音乐), `netease_music` (网易云音乐), or `default` for the current media app.",
        "Supported actions: `open`, `play_pause`, `next`, `previous`, `set_volume`, `volume_up`, `volume_down`, `list_apps`.",
        "This first version cannot search for a song or choose a playlist inside QQ 音乐/网易云. If the user asks for a specific song, open the requested app if needed, then explain that precise search/play needs a future app-specific adapter.",
        "If the tool reports macOS Accessibility permission is missing, tell the user to enable it in System Settings instead of retrying repeatedly.",
    ])


def _has_mcp_tools(user_id: str) -> bool:
    for item in list_enabled_extensions(user_id, "mcp"):
        tools = (item.get("config") or {}).get("tools") or []
        if isinstance(tools, list) and tools:
            return True
    return False


def build_agent_tools(user_id: str) -> list[dict[str, Any]]:
    tools: list[dict[str, Any]] = []
    skill_tool = skill_tool_spec(user_id)
    if skill_tool:
        tools.append(skill_tool)
    if _has_mcp_tools(user_id):
        tools.append({
            "type": "function",
            "function": {
                "name": "call_mcp_tool",
                "description": "Call a discovered tool from a configured MCP server by server_id and tool_name.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "server_id": {"type": "integer", "description": "MCP server id shown in the MCP servers index."},
                        "tool_name": {"type": "string", "description": "Exact MCP tool name."},
                        "arguments": {"type": "object", "description": "Tool arguments matching the discovered input schema. Include every required parameter listed in the MCP servers index."},
                    },
                    "required": ["server_id", "tool_name", "arguments"],
                },
            },
        })
    tools.append({
        "type": "function",
        "function": {
            "name": "local_music_control",
            "description": "Open and control local music apps on macOS, including QQ 音乐 and 网易云音乐. Supports app launch, play/pause, next, previous, and system volume.",
            "parameters": {
                "type": "object",
                "properties": {
                    "app": {
                        "type": "string",
                        "enum": ["default", "qq_music", "netease_music"],
                        "description": "Music app to control. Use default for the current media app when the user does not name QQ 音乐 or 网易云音乐.",
                    },
                    "action": {
                        "type": "string",
                        "enum": ["list_apps", "open", "play_pause", "next", "previous", "set_volume", "volume_up", "volume_down"],
                        "description": "Operation to perform. Use open to launch the app; use set_volume with level.",
                    },
                    "level": {
                        "type": "integer",
                        "description": "Volume level 0-100. Required only when action is set_volume.",
                    },
                },
                "required": ["action"],
            },
        },
    })
    return tools


async def dispatch_agent_tool(user_id: str, name: str, arguments: dict[str, Any]) -> str:
    if name == "run_skill":
        return run_skill_tool(user_id, arguments)
    if name == "local_music_control":
        from .local_music_control import control_music

        return control_music(arguments)
    if name == "call_mcp_tool":
        try:
            from ..routers.agent_extensions import _call_mcp_tool
            from ..storage.agent_extension import get_extension

            server_id = int(arguments.get("server_id") or 0)
            tool_name = str(arguments.get("tool_name") or "").strip()
            server = get_extension(user_id, server_id, "mcp")
            if server is None:
                return json.dumps({"error": f"unknown MCP server_id: {server_id}"}, ensure_ascii=False)
            tool_args = arguments.get("arguments") or {}
            if not isinstance(tool_args, dict):
                return json.dumps({"error": "call_mcp_tool.arguments must be an object"}, ensure_ascii=False)
            schema = _tool_schema_for_name(server, tool_name)
            required = [str(v) for v in (schema.get("required") or []) if str(v)] if schema else []
            missing = [name for name in required if name not in tool_args or tool_args.get(name) in (None, "")]
            if missing:
                return json.dumps(
                    {
                        "error": "missing required MCP arguments",
                        "tool_name": tool_name,
                        "missing": missing,
                        "hint": "Ask the user for these values before calling the tool again.",
                    },
                    ensure_ascii=False,
                )
            result = await _call_mcp_tool(server.get("config") or {}, tool_name, tool_args)
            return json.dumps({"result": result}, ensure_ascii=False)
        except Exception as exc:
            return json.dumps({"error": str(getattr(exc, "detail", exc))[:500]}, ensure_ascii=False)
    return json.dumps({"error": f"unknown tool: {name}"}, ensure_ascii=False)


def build_agent_extension_context(user_id: str) -> str:
    """生成 prompt 注入块。

    Skill 以本地目录为唯一运行形态：安装时可以来自本地目录、GitHub/Git 或 ZIP，
    但系统 prompt 只放短索引；正文通过 run_skill 按需读取。
    """
    parts = [
        build_skills_index_context(user_id),
        build_mcp_index_context(user_id),
        build_local_music_context(),
    ]
    return _clip("\n\n".join(parts), MAX_TOTAL_CHARS)
