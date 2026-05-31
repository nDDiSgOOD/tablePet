"""把已启用的本地 Skill 目录 / MCP 配置转成 agent 能力上下文。"""

from __future__ import annotations

import json
import os
import re
import shutil
import shlex
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

from ..storage.agent_extension import list_enabled_extensions

MAX_SKILL_CHARS = 12_000
MAX_TOTAL_CHARS = 32_000
SKILLS_INDEX_MAX_CHARS = 4000
MAX_SCRIPT_OUTPUT_CHARS = 12_000
MAX_SKILL_FILE_CHARS = 20_000
MAX_SKILL_LIST_CHARS = 16_000
SKILL_SCRIPT_TIMEOUT_SECONDS = 30
SKILL_COMMAND_TIMEOUT_SECONDS = 20
SKILL_FILE = "SKILL.md"
VALID_SKILL_NAME = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9._-]{0,63}$")
COLLECTION_DIRS = ("skills", ".reasonix/skills")
SCRIPT_TOOL_NAME = "run_skill_script"
SCRIPT_RUNTIMES = {"python", "node", "bash", "sh"}
SKILL_WORKSPACE_TOOL_NAMES = {
    "list_skill_files",
    "read_skill_file",
    "search_skill_files",
    "run_skill_command",
}
SKIP_DIR_NAMES = {
    ".git",
    ".hg",
    ".svn",
    "__pycache__",
    ".venv",
    "venv",
    "node_modules",
    "dist",
    "build",
    ".next",
    "target",
    "__MACOSX",
}
SKIP_FILE_SUFFIXES = {
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
    ".webp",
    ".ico",
    ".pdf",
    ".zip",
    ".tar",
    ".gz",
    ".7z",
    ".mp3",
    ".mp4",
    ".mov",
    ".wav",
    ".woff",
    ".woff2",
    ".ttf",
}
SAFE_COMMAND_PREFIXES = (
    ("ls",),
    ("pwd",),
    ("cat",),
    ("head",),
    ("tail",),
    ("wc",),
    ("file",),
    ("find",),
    ("grep",),
    ("rg",),
    ("python", "--version"),
    ("python3", "--version"),
    ("node", "--version"),
    ("node", "-v"),
    ("npm", "--version"),
    ("npx", "--version"),
    ("pytest",),
    ("python", "-m", "pytest"),
    ("python3", "-m", "pytest"),
)
RISKY_COMMAND_ARGS = {
    "find": {"-delete", "-exec", "-execdir", "-ok", "-okdir", "-fprint", "-fprint0", "-fprintf", "-fls"},
    "rg": {"--replace", "-r", "--files-without-match"},
    "grep": {"--exclude-dir=*", "--include=*"},
}
SHELL_METACHARS = re.compile(r"(?:\|\||&&|[|;&`<>]|\$\(|\$\{|\n)")


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
        "script": (data.get("script") or "").strip(),
        "script_runtime": (data.get("script-runtime") or data.get("runtime") or "").strip().lower(),
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
    if _skill_allows_script(skill):
        tag += " [script]"
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
        "For multi-file skills, first call `run_skill`, then use `list_skill_files`, `read_skill_file`, or `search_skill_files` to inspect referenced files inside that Skill directory.",
        "Use `run_skill_command` only for safe allowlisted inspection/test commands inside the Skill directory. It does not run through a shell, does not persist cd, and rejects high-risk syntax.",
        "If a skill is tagged [script], first call `run_skill` to read its instructions. Only call `run_skill_script` when the skill explicitly asks for its declared local script to run.",
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
    if _skill_allows_script(skill):
        header += f"\n(script: {skill.get('script_runtime') or 'auto'} · {skill.get('script')})"
    args_block = f"\n\nArguments: {args}" if args else ""
    return _clip(f"{header}\n\n{skill['body']}{args_block}", MAX_SKILL_CHARS)


def _skill_allows_script(skill: dict[str, Any]) -> bool:
    allowed = {str(t).strip() for t in (skill.get("allowed_tools") or [])}
    return bool(skill.get("script")) and SCRIPT_TOOL_NAME in allowed


def _skill_root(skill: dict[str, Any]) -> Path:
    return Path(str(skill.get("local_path") or "")).expanduser().resolve()


def _skill_by_args(user_id: str, raw_args: dict[str, Any], *, tool_name: str) -> tuple[dict[str, Any] | None, str]:
    raw_name = str(raw_args.get("name") or raw_args.get("skill_name") or "").strip()
    raw_name = re.sub(r"\[[^\]]*\]", " ", raw_name).strip()
    name = next((t for t in raw_name.split() if t and t[0].isalnum()), "")
    if not name:
        return None, f"{tool_name} requires a skill name"
    skill = read_skill(user_id, name)
    if skill is None:
        available = [s["name"] for s in list_available_skills(user_id)]
        return None, json.dumps({"error": f"unknown skill: {name}", "available": available}, ensure_ascii=False)
    return skill, ""


def _safe_skill_path(skill: dict[str, Any], raw_path: str = ".") -> tuple[Path | None, str]:
    root = _skill_root(skill)
    value = str(raw_path or ".").strip() or "."
    while value.startswith("/") or value.startswith("\\"):
        value = value[1:]
    if value.startswith("~"):
        return None, "path 必须是 Skill 目录内的相对路径。"
    target = (root / value).resolve()
    try:
        target.relative_to(root)
    except ValueError:
        return None, "path 不能跳出 Skill 目录。"
    return target, ""


def _display_skill_rel(skill: dict[str, Any], path: Path) -> str:
    try:
        return str(path.relative_to(_skill_root(skill))).replace("\\", "/") or "."
    except ValueError:
        return path.name


def _is_probably_binary(path: Path) -> bool:
    if path.suffix.lower() in SKIP_FILE_SUFFIXES:
        return True
    try:
        with path.open("rb") as fh:
            chunk = fh.read(2048)
        return b"\0" in chunk
    except OSError:
        return True


def _walk_skill_files(skill: dict[str, Any], start: Path, *, include_deps: bool = False, max_depth: int = 5) -> list[Path]:
    root = _skill_root(skill)
    files: list[Path] = []
    if start.is_file():
        return [start]
    if not start.exists() or not start.is_dir():
        return []
    start_depth = len(start.relative_to(root).parts) if start != root else 0
    for current, dirs, names in os.walk(start):
        cur_path = Path(current)
        depth = len(cur_path.relative_to(root).parts) - start_depth
        if depth >= max_depth:
            dirs[:] = []
        if not include_deps:
            dirs[:] = [d for d in dirs if d not in SKIP_DIR_NAMES and not d.startswith(".")]
        for name in sorted(names):
            path = cur_path / name
            try:
                path.relative_to(root)
            except ValueError:
                continue
            files.append(path)
            if len(files) >= 600:
                return files
    return files


def list_skill_files_tool(user_id: str, raw_args: dict[str, Any]) -> str:
    skill, error = _skill_by_args(user_id, raw_args, tool_name="list_skill_files")
    if skill is None:
        return error if error.startswith("{") else json.dumps({"error": error}, ensure_ascii=False)
    path, error = _safe_skill_path(skill, str(raw_args.get("path") or "."))
    if path is None:
        return json.dumps({"error": error}, ensure_ascii=False)
    include_deps = raw_args.get("include_deps") is True
    max_depth = max(0, min(int(raw_args.get("max_depth") or 2), 8))
    if path.is_file():
        return _display_skill_rel(skill, path)
    if not path.exists() or not path.is_dir():
        return json.dumps({"error": "目录不存在", "path": str(raw_args.get("path") or ".")}, ensure_ascii=False)
    root = _skill_root(skill)
    lines: list[str] = []
    total = 0
    for item in _walk_skill_files(skill, path, include_deps=include_deps, max_depth=max_depth):
        rel = _display_skill_rel(skill, item)
        line = rel + ("/" if item.is_dir() else "")
        lines.append(line)
        total += len(line) + 1
        if total > MAX_SKILL_LIST_CHARS:
            lines.append("...[已截断，缩小 path 或 max_depth 后重试]")
            break
    if not lines and path == root:
        return "(empty skill directory)"
    return "\n".join(lines) or "(empty directory)"


def _slice_lines(text: str, raw_args: dict[str, Any]) -> str:
    lines = text.splitlines()
    total = len(lines)
    raw_range = str(raw_args.get("range") or "").strip()
    if re.fullmatch(r"\d+\s*-\s*\d+", raw_range):
        start_s, end_s = re.split(r"\s*-\s*", raw_range, maxsplit=1)
        start = max(1, int(start_s))
        end = min(total, max(start, int(end_s)))
        return f"[range {start}-{end} of {total} lines]\n" + "\n".join(lines[start - 1:end])
    head = raw_args.get("head")
    tail = raw_args.get("tail")
    if isinstance(head, int) and head > 0:
        count = min(head, total)
        suffix = f"\n\n...[head {count} of {total} lines]" if count < total else ""
        return "\n".join(lines[:count]) + suffix
    if isinstance(tail, int) and tail > 0:
        count = min(tail, total)
        prefix = f"...[tail {count} of {total} lines]\n\n" if count < total else ""
        return prefix + "\n".join(lines[-count:])
    if len(text) > MAX_SKILL_FILE_CHARS:
        return text[:MAX_SKILL_FILE_CHARS] + "\n...[已截断，请用 head/tail/range 缩小读取范围]"
    return text


def read_skill_file_tool(user_id: str, raw_args: dict[str, Any]) -> str:
    skill, error = _skill_by_args(user_id, raw_args, tool_name="read_skill_file")
    if skill is None:
        return error if error.startswith("{") else json.dumps({"error": error}, ensure_ascii=False)
    path, error = _safe_skill_path(skill, str(raw_args.get("path") or ""))
    if path is None:
        return json.dumps({"error": error}, ensure_ascii=False)
    if not path.exists() or not path.is_file():
        return json.dumps({"error": "文件不存在", "path": str(raw_args.get("path") or "")}, ensure_ascii=False)
    if _is_probably_binary(path):
        return json.dumps({"error": "拒绝读取二进制文件", "path": _display_skill_rel(skill, path)}, ensure_ascii=False)
    try:
        text = path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return json.dumps({"error": "文件不是 UTF-8 文本", "path": _display_skill_rel(skill, path)}, ensure_ascii=False)
    return _slice_lines(text, raw_args)


def search_skill_files_tool(user_id: str, raw_args: dict[str, Any]) -> str:
    skill, error = _skill_by_args(user_id, raw_args, tool_name="search_skill_files")
    if skill is None:
        return error if error.startswith("{") else json.dumps({"error": error}, ensure_ascii=False)
    pattern = str(raw_args.get("pattern") or "").strip()
    if not pattern:
        return json.dumps({"error": "search_skill_files requires pattern"}, ensure_ascii=False)
    path, error = _safe_skill_path(skill, str(raw_args.get("path") or "."))
    if path is None:
        return json.dumps({"error": error}, ensure_ascii=False)
    include_deps = raw_args.get("include_deps") is True
    case_sensitive = raw_args.get("case_sensitive") is True
    needle = pattern if case_sensitive else pattern.lower()
    lines: list[str] = []
    for file_path in _walk_skill_files(skill, path, include_deps=include_deps, max_depth=8):
        if not file_path.is_file() or _is_probably_binary(file_path):
            continue
        try:
            content = file_path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        for idx, line in enumerate(content.splitlines(), start=1):
            haystack = line if case_sensitive else line.lower()
            if needle in haystack:
                lines.append(f"{_display_skill_rel(skill, file_path)}:{idx}: {line[:240]}")
                if len("\n".join(lines)) > MAX_SKILL_LIST_CHARS:
                    lines.append("...[已截断，缩小 path 或 pattern 后重试]")
                    return "\n".join(lines)
    return "\n".join(lines) or "(no matches)"


def _resolve_skill_script(skill: dict[str, Any]) -> tuple[Path, str] | tuple[None, str]:
    if not _skill_allows_script(skill):
        return None, "Skill 没有声明允许运行脚本。需要在 SKILL.md frontmatter 中设置 script 和 allowed-tools: run_skill_script。"
    root = Path(str(skill.get("local_path") or "")).expanduser().resolve()
    raw_script = str(skill.get("script") or "").strip()
    if not raw_script or raw_script.startswith(("/", "~")):
        return None, "script 必须是 Skill 目录内的相对路径。"
    script = (root / raw_script).resolve()
    try:
        script.relative_to(root)
    except ValueError:
        return None, "script 不能跳出 Skill 目录。"
    if not script.exists() or not script.is_file():
        return None, f"脚本不存在：{raw_script}"
    return script, ""


def _runtime_command(runtime: str, script: Path) -> list[str] | None:
    runtime = (runtime or "").strip().lower()
    if not runtime:
        suffix = script.suffix.lower()
        if suffix == ".py":
            runtime = "python"
        elif suffix in {".js", ".mjs", ".cjs"}:
            runtime = "node"
        elif suffix in {".sh", ".bash"}:
            runtime = "bash"
    if runtime not in SCRIPT_RUNTIMES:
        return None
    if runtime == "python":
        return [sys.executable, str(script)]
    if runtime == "node":
        return ["node", str(script)]
    if runtime == "bash":
        return ["bash", str(script)]
    if runtime == "sh":
        return ["sh", str(script)]
    return None


def _sandboxed_command(command: list[str], cwd: Path) -> tuple[list[str] | None, str]:
    sandbox_exec = shutil.which("sandbox-exec")
    if not sandbox_exec:
        return None, "当前系统没有 sandbox-exec，无法使用 macOS best-effort 沙箱。"
    profile = "\n".join([
        "(version 1)",
        "(deny default)",
        "(allow process*)",
        "(allow sysctl-read)",
        "(allow file-read*)",
        f"(allow file-write* (subpath {json.dumps(str(cwd))}) (subpath {json.dumps(tempfile.gettempdir())}))",
    ])
    return [sandbox_exec, "-p", profile, *command], ""


def run_skill_script_tool(user_id: str, raw_args: dict[str, Any]) -> str:
    raw_name = str(raw_args.get("name") or raw_args.get("skill_name") or "").strip()
    raw_name = re.sub(r"\[[^\]]*\]", " ", raw_name).strip()
    name = next((t for t in raw_name.split() if t and t[0].isalnum()), "")
    if not name:
        return json.dumps({"error": "run_skill_script requires a skill name"}, ensure_ascii=False)
    skill = read_skill(user_id, name)
    if skill is None:
        available = [s["name"] for s in list_available_skills(user_id)]
        return json.dumps({"error": f"unknown skill: {name}", "available": available}, ensure_ascii=False)

    script, error = _resolve_skill_script(skill)
    if script is None:
        return json.dumps({"error": error}, ensure_ascii=False)
    root = Path(str(skill.get("local_path") or "")).expanduser().resolve()
    runtime = str(skill.get("script_runtime") or "").strip().lower()
    command = _runtime_command(runtime, script)
    if command is None:
        return json.dumps(
            {
                "error": "不支持的脚本运行时",
                "runtime": runtime or "auto",
                "supported": sorted(SCRIPT_RUNTIMES),
            },
            ensure_ascii=False,
        )

    mode = str(raw_args.get("mode") or os.getenv("TABLEPET_SKILL_SCRIPT_MODE") or "local").strip().lower()
    if mode not in {"local", "sandbox"}:
        mode = "local"
    if mode == "sandbox":
        command, sandbox_error = _sandboxed_command(command, root)
        if command is None:
            return json.dumps({"error": sandbox_error, "mode": "sandbox"}, ensure_ascii=False)

    extra_args = raw_args.get("args") or []
    if not isinstance(extra_args, list):
        extra_args = []
    safe_args = [str(v) for v in extra_args[:20]]
    input_text = str(raw_args.get("input") or "")
    env = {
        "PATH": os.getenv("PATH", ""),
        "HOME": str(root),
        "TABLEPET_SKILL_NAME": str(skill.get("name") or name),
        "TABLEPET_SKILL_DIR": str(root),
        "TABLEPET_SCRIPT_MODE": mode,
    }
    try:
        proc = subprocess.run(
            [*command, *safe_args],
            cwd=str(root),
            input=input_text if input_text else None,
            capture_output=True,
            text=True,
            timeout=SKILL_SCRIPT_TIMEOUT_SECONDS,
            env=env,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return json.dumps(
            {
                "error": "Skill 脚本执行超时",
                "timeout_seconds": SKILL_SCRIPT_TIMEOUT_SECONDS,
                "mode": mode,
            },
            ensure_ascii=False,
        )
    except Exception as exc:
        return json.dumps({"error": f"Skill 脚本启动失败：{exc}", "mode": mode}, ensure_ascii=False)

    return json.dumps(
        {
            "ok": proc.returncode == 0,
            "skill": skill.get("name") or name,
            "script": str(script.relative_to(root)),
            "mode": mode,
            "returncode": proc.returncode,
            "stdout": _clip(proc.stdout or "", MAX_SCRIPT_OUTPUT_CHARS),
            "stderr": _clip(proc.stderr or "", MAX_SCRIPT_OUTPUT_CHARS),
        },
        ensure_ascii=False,
    )


def _command_prefix_allowed(argv: list[str]) -> bool:
    if not argv:
        return False
    for prefix in SAFE_COMMAND_PREFIXES:
        if len(argv) < len(prefix):
            continue
        if tuple(argv[:len(prefix)]) != prefix:
            continue
        risky = RISKY_COMMAND_ARGS.get(prefix[0], set())
        tail = argv[len(prefix):]
        for arg in tail:
            if arg in risky or any(arg.startswith(r + "=") for r in risky):
                return False
        return True
    return False


def _command_paths_stay_in_skill(skill: dict[str, Any], argv: list[str], cwd: Path) -> tuple[bool, str]:
    root = _skill_root(skill)
    for arg in argv[1:]:
        if not arg or arg.startswith("-"):
            continue
        if "://" in arg:
            return False, "命令参数不能包含 URL。"
        if arg.startswith(("~", "/")):
            return False, "命令参数不能使用绝对路径或 HOME 路径。"
        # Only path-like arguments need sandbox resolution.
        if "/" not in arg and "\\" not in arg and arg not in {".", ".."}:
            continue
        candidate = (cwd / arg).resolve()
        try:
            candidate.relative_to(root)
        except ValueError:
            return False, f"命令参数不能跳出 Skill 目录：{arg}"
    return True, ""


def run_skill_command_tool(user_id: str, raw_args: dict[str, Any]) -> str:
    skill, error = _skill_by_args(user_id, raw_args, tool_name="run_skill_command")
    if skill is None:
        return error if error.startswith("{") else json.dumps({"error": error}, ensure_ascii=False)
    command = str(raw_args.get("command") or "").strip()
    if not command:
        return json.dumps({"error": "run_skill_command requires command"}, ensure_ascii=False)
    if SHELL_METACHARS.search(command):
        return json.dumps(
            {
                "error": "命令包含不支持或高危 shell 语法",
                "hint": "不要使用管道、重定向、&&、;、反引号、命令替换或环境变量展开。需要切目录时使用 cwd 参数。",
            },
            ensure_ascii=False,
        )
    try:
        argv = shlex.split(command)
    except ValueError as exc:
        return json.dumps({"error": f"命令解析失败：{exc}"}, ensure_ascii=False)
    if not _command_prefix_allowed(argv):
        return json.dumps(
            {
                "error": "命令不在安全 allowlist 中",
                "command": command,
                "allowed_examples": [
                    "ls",
                    "cat README.md",
                    "grep pattern file.txt",
                    "rg pattern",
                    "find . -name '*.md'",
                    "python --version",
                    "python -m pytest",
                ],
            },
            ensure_ascii=False,
        )
    cwd, path_error = _safe_skill_path(skill, str(raw_args.get("cwd") or "."))
    if cwd is None:
        return json.dumps({"error": path_error}, ensure_ascii=False)
    if not cwd.exists() or not cwd.is_dir():
        return json.dumps({"error": "cwd 不存在或不是目录", "cwd": str(raw_args.get("cwd") or ".")}, ensure_ascii=False)
    ok, path_error = _command_paths_stay_in_skill(skill, argv, cwd)
    if not ok:
        return json.dumps({"error": path_error}, ensure_ascii=False)
    timeout = max(1, min(int(raw_args.get("timeout_seconds") or SKILL_COMMAND_TIMEOUT_SECONDS), 60))
    env = {
        "PATH": os.getenv("PATH", ""),
        "HOME": str(_skill_root(skill)),
        "TABLEPET_SKILL_NAME": str(skill.get("name") or ""),
        "TABLEPET_SKILL_DIR": str(_skill_root(skill)),
    }
    try:
        proc = subprocess.run(
            argv,
            cwd=str(cwd),
            capture_output=True,
            text=True,
            timeout=timeout,
            env=env,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return json.dumps({"error": "Skill 命令执行超时", "timeout_seconds": timeout}, ensure_ascii=False)
    except FileNotFoundError:
        return json.dumps({"error": f"命令不存在：{argv[0]}"}, ensure_ascii=False)
    except Exception as exc:
        return json.dumps({"error": f"Skill 命令启动失败：{exc}"}, ensure_ascii=False)
    output = "\n".join(part for part in (proc.stdout, proc.stderr) if part)
    return json.dumps(
        {
            "ok": proc.returncode == 0,
            "skill": skill.get("name"),
            "cwd": _display_skill_rel(skill, cwd),
            "command": command,
            "returncode": proc.returncode,
            "output": _clip(output, MAX_SCRIPT_OUTPUT_CHARS),
        },
        ensure_ascii=False,
    )


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
        "For play/pause/next/previous, the result may include `verified=false`; in that case say the media key command was sent, but do not claim playback definitely started.",
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
        tools.extend([
            {
                "type": "function",
                "function": {
                    "name": "list_skill_files",
                    "description": "List files inside an installed Skill directory. Paths are relative to the Skill root and cannot escape it.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "name": {"type": "string", "description": "Bare Skill identifier from the Skills index."},
                            "path": {"type": "string", "description": "Relative directory path inside the Skill. Defaults to root."},
                            "max_depth": {"type": "integer", "description": "Max traversal depth. Defaults to 2."},
                            "include_deps": {"type": "boolean", "description": "Include dependency/build/hidden directories. Default false."},
                        },
                        "required": ["name"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "read_skill_file",
                    "description": "Read a text file inside an installed Skill directory. Prefer head/tail/range for large files.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "name": {"type": "string", "description": "Bare Skill identifier from the Skills index."},
                            "path": {"type": "string", "description": "Relative file path inside the Skill."},
                            "head": {"type": "integer", "description": "Return first N lines."},
                            "tail": {"type": "integer", "description": "Return last N lines."},
                            "range": {"type": "string", "description": "Inclusive 1-indexed line range, for example 20-80."},
                        },
                        "required": ["name", "path"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "search_skill_files",
                    "description": "Search text content inside files in an installed Skill directory. Returns path:line: text matches.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "name": {"type": "string", "description": "Bare Skill identifier from the Skills index."},
                            "pattern": {"type": "string", "description": "Literal text to search for."},
                            "path": {"type": "string", "description": "Relative directory or file path to search. Defaults to root."},
                            "case_sensitive": {"type": "boolean", "description": "Match case exactly. Default false."},
                            "include_deps": {"type": "boolean", "description": "Search dependency/build/hidden directories. Default false."},
                        },
                        "required": ["name", "pattern"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "run_skill_command",
                    "description": "Run a safe allowlisted command inside a Skill directory, using shell=false. Use for ls/cat/grep/rg/find/version probes/tests. Use cwd instead of cd. High-risk shell syntax and path escapes are rejected.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "name": {"type": "string", "description": "Bare Skill identifier from the Skills index."},
                            "command": {"type": "string", "description": "Command line without pipes, redirects, &&, ;, command substitution, or env expansion."},
                            "cwd": {"type": "string", "description": "Relative working directory inside the Skill. Defaults to root."},
                            "timeout_seconds": {"type": "integer", "description": "1-60 seconds. Defaults to 20."},
                        },
                        "required": ["name", "command"],
                    },
                },
            },
        ])
    if any(_skill_allows_script(skill) for skill in list_available_skills(user_id)):
        tools.append({
            "type": "function",
            "function": {
                "name": SCRIPT_TOOL_NAME,
                "description": "Run the declared local script for an installed Skill. Only use this after reading the skill with run_skill and only when the Skill index/body indicates script execution is allowed.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string", "description": "Bare Skill identifier from the Skills index."},
                        "args": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "Optional command-line arguments for the declared script. Do not pass shell syntax.",
                        },
                        "input": {"type": "string", "description": "Optional stdin text for the script."},
                        "mode": {
                            "type": "string",
                            "enum": ["local", "sandbox"],
                            "description": "Execution mode. Use local by default. Use sandbox only when the user asks for best-effort macOS sandboxing.",
                        },
                    },
                    "required": ["name"],
                },
            },
        })
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
    if name == "list_skill_files":
        return list_skill_files_tool(user_id, arguments)
    if name == "read_skill_file":
        return read_skill_file_tool(user_id, arguments)
    if name == "search_skill_files":
        return search_skill_files_tool(user_id, arguments)
    if name == "run_skill_command":
        return run_skill_command_tool(user_id, arguments)
    if name == SCRIPT_TOOL_NAME:
        return run_skill_script_tool(user_id, arguments)
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
