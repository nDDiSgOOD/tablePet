"""Agent Skill / MCP 管理接口。"""

from __future__ import annotations

import json
import base64
import asyncio
import re
import shutil
import subprocess
import tempfile
import time
import zipfile
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urljoin

import httpx
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from ..memory import DEFAULT_USER_ID
from ..config import SKILLS_DIR
from ..storage.agent_extension import (
    delete_extension,
    get_extension,
    list_extensions,
    set_extension_enabled,
    upsert_extension,
)
from ..services.agent_extensions import parse_skill_extensions

router = APIRouter(prefix="/api")

SkillSourceType = Literal["local_dir", "github_repo", "git_url", "zip_url"]
SkillUploadSourceType = Literal["local_dir_upload", "zip_upload"]
McpSourceType = Literal["inline", "local_file", "github_url", "url"]


class SkillPayload(BaseModel):
    name: str = Field(default="", max_length=120)
    description: str = Field(default="", max_length=500)
    source_type: SkillSourceType = "local_dir"
    source_uri: str = Field(default="", max_length=1200)
    enabled: bool = True


class SkillUploadFile(BaseModel):
    path: str = Field(max_length=1200)
    data: str = Field(max_length=8_000_000)


class SkillUploadPayload(BaseModel):
    source_type: SkillUploadSourceType
    name: str = Field(default="", max_length=120)
    description: str = Field(default="", max_length=500)
    files: list[SkillUploadFile] = Field(default_factory=list)
    zip_data: str = Field(default="", max_length=50_000_000)
    enabled: bool = True


class McpPayload(BaseModel):
    name: str = Field(default="", max_length=120)
    description: str = Field(default="", max_length=500)
    source_type: McpSourceType = "inline"
    source_uri: str = Field(default="", max_length=1200)
    transport: Literal["stdio", "http", "sse"] = "stdio"
    command: str = Field(default="", max_length=500)
    args: list[str] = Field(default_factory=list)
    url: str = Field(default="", max_length=1200)
    env: dict[str, str] = Field(default_factory=dict)
    config: dict[str, Any] = Field(default_factory=dict)
    content: str = Field(default="", max_length=200_000)
    enabled: bool = True


class TogglePayload(BaseModel):
    enabled: bool | None = None


class McpToolCallPayload(BaseModel):
    tool_name: str = Field(max_length=200)
    arguments: dict[str, Any] = Field(default_factory=dict)


def _jsonrpc_message(req_id: int, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
    msg: dict[str, Any] = {"jsonrpc": "2.0", "id": req_id, "method": method}
    if params is not None:
        msg["params"] = params
    return msg


def _tools_from_result(result: Any) -> list[dict[str, Any]]:
    raw_tools = (result or {}).get("tools") if isinstance(result, dict) else []
    tools: list[dict[str, Any]] = []
    if not isinstance(raw_tools, list):
        return tools
    for tool in raw_tools:
        if not isinstance(tool, dict):
            continue
        name = str(tool.get("name") or "").strip()
        if not name:
            continue
        tools.append({
            "name": name,
            "description": str(tool.get("description") or "").strip(),
            "input_schema": tool.get("inputSchema") or tool.get("input_schema") or {},
        })
    return tools


async def _mcp_http_rpc(url: str, req_id: int, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
    async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
        resp = await client.post(
            url,
            headers={
                "Accept": "application/json, text/event-stream",
                "Content-Type": "application/json",
            },
            json=_jsonrpc_message(req_id, method, params),
        )
    if resp.status_code >= 400:
        raise HTTPException(status_code=400, detail=f"MCP HTTP 请求失败：HTTP {resp.status_code} {resp.text[:200]}")
    try:
        data = resp.json()
    except ValueError:
        text = resp.text
        for line in text.splitlines():
            if line.startswith("data:"):
                payload = line[5:].strip()
                if payload and payload != "[DONE]":
                    data = json.loads(payload)
                    break
        else:
            raise HTTPException(status_code=400, detail="MCP HTTP 返回不是 JSON。")
    if isinstance(data, dict) and data.get("error"):
        raise HTTPException(status_code=400, detail=f"MCP 工具请求失败：{data['error']}")
    return data if isinstance(data, dict) else {}


async def _discover_http_tools(url: str) -> list[dict[str, Any]]:
    await _mcp_http_rpc(url, 1, "initialize", {
        "protocolVersion": "2024-11-05",
        "capabilities": {},
        "clientInfo": {"name": "TablePet", "version": "0.1"},
    })
    data = await _mcp_http_rpc(url, 2, "tools/list")
    return _tools_from_result(data.get("result"))


async def _read_sse_until(lines: Any, target_id: int, timeout: float = 20) -> dict[str, Any]:
    event_data: list[str] = []
    async def _loop() -> dict[str, Any]:
        async for line in lines:
            if line.startswith("data:"):
                event_data.append(line[5:].strip())
                continue
            if line.strip():
                continue
            if not event_data:
                continue
            payload = "\n".join(event_data).strip()
            event_data.clear()
            if not payload or payload == "[DONE]":
                continue
            try:
                data = json.loads(payload)
            except ValueError:
                continue
            if data.get("id") == target_id:
                if data.get("error"):
                    raise HTTPException(status_code=400, detail=f"MCP SSE 请求失败：{data['error']}")
                return data
        return {}
    return await asyncio.wait_for(_loop(), timeout=timeout)


async def _discover_sse_tools(url: str) -> list[dict[str, Any]]:
    async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
        async with client.stream("GET", url, headers={"Accept": "text/event-stream"}) as stream:
            lines = stream.aiter_lines()
            endpoint = ""
            async for line in lines:
                if line.startswith("data:"):
                    payload = line[5:].strip()
                    if payload.startswith(("http://", "https://", "/")):
                        endpoint = urljoin(url, payload)
                        break
            if not endpoint:
                raise HTTPException(status_code=400, detail="MCP SSE 未返回 message endpoint。")
            await client.post(endpoint, json=_jsonrpc_message(1, "initialize", {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "TablePet", "version": "0.1"},
            }))
            await _read_sse_until(lines, 1)
            await client.post(endpoint, json=_jsonrpc_message(2, "tools/list"))
            data = await _read_sse_until(lines, 2)
            return _tools_from_result(data.get("result"))


async def _stdio_rpc(command: str, messages: list[dict[str, Any]], timeout: float = 20) -> list[dict[str, Any]]:
    proc = await asyncio.create_subprocess_shell(
        command,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    assert proc.stdin is not None and proc.stdout is not None
    for msg in messages:
        proc.stdin.write((json.dumps(msg, ensure_ascii=False) + "\n").encode("utf-8"))
    await proc.stdin.drain()
    results: list[dict[str, Any]] = []
    ids = {m.get("id") for m in messages if m.get("id") is not None}
    try:
        while ids:
            line = await asyncio.wait_for(proc.stdout.readline(), timeout=timeout)
            if not line:
                break
            try:
                data = json.loads(line.decode("utf-8", errors="ignore").strip())
            except ValueError:
                continue
            if data.get("id") in ids:
                results.append(data)
                ids.discard(data.get("id"))
    finally:
        try:
            proc.terminate()
        except ProcessLookupError:
            pass
        try:
            await asyncio.wait_for(proc.wait(), timeout=2)
        except asyncio.TimeoutError:
            proc.kill()
    return results


async def _discover_stdio_tools(command: str) -> list[dict[str, Any]]:
    results = await _stdio_rpc(command, [
        _jsonrpc_message(1, "initialize", {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": "TablePet", "version": "0.1"},
        }),
        _jsonrpc_message(2, "tools/list"),
    ])
    data = next((r for r in results if r.get("id") == 2), {})
    if data.get("error"):
        raise HTTPException(status_code=400, detail=f"MCP stdio 发现工具失败：{data['error']}")
    return _tools_from_result(data.get("result"))


async def _discover_mcp_tools(config: dict[str, Any]) -> tuple[list[dict[str, Any]], str]:
    transport = config.get("transport")
    if transport == "stdio":
        tools = await _discover_stdio_tools(str(config.get("command") or ""))
    elif transport == "http":
        tools = await _discover_http_tools(str(config.get("url") or ""))
    elif transport == "sse":
        tools = await _discover_sse_tools(str(config.get("url") or ""))
    else:
        tools = []
    return tools, "connected" if tools else "connected_empty"


async def _call_mcp_tool(config: dict[str, Any], tool_name: str, arguments: dict[str, Any]) -> Any:
    params = {"name": tool_name, "arguments": arguments or {}}
    transport = config.get("transport")
    if transport == "http":
        data = await _mcp_http_rpc(str(config.get("url") or ""), 11, "tools/call", params)
        return data.get("result")
    if transport == "sse":
        # 调试调用走简化路径：部分 SSE 网关支持同 URL POST；不支持时会给出清晰报错。
        data = await _mcp_http_rpc(str(config.get("url") or ""), 11, "tools/call", params)
        return data.get("result")
    if transport == "stdio":
        results = await _stdio_rpc(str(config.get("command") or ""), [
            _jsonrpc_message(1, "initialize", {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "TablePet", "version": "0.1"},
            }),
            _jsonrpc_message(11, "tools/call", params),
        ])
        data = next((r for r in results if r.get("id") == 11), {})
        if data.get("error"):
            raise HTTPException(status_code=400, detail=f"MCP 工具调用失败：{data['error']}")
        return data.get("result")
    raise HTTPException(status_code=400, detail="不支持的 MCP transport。")


def _github_to_raw(url: str) -> str:
    if "raw.githubusercontent.com/" in url:
        return url
    marker = "github.com/"
    if marker not in url or "/blob/" not in url:
        return url
    prefix, rest = url.split(marker, 1)
    owner_repo, blob_path = rest.split("/blob/", 1)
    return f"{prefix}raw.githubusercontent.com/{owner_repo}/{blob_path}"


async def _load_content(source_type: str, source_uri: str, inline: str) -> str:
    if source_type == "inline":
        return inline.strip()
    if source_type == "local_file":
        if not source_uri.strip():
            raise HTTPException(status_code=400, detail="请填写本地文件路径。")
        path = Path(source_uri).expanduser()
        if not path.exists() or not path.is_file():
            raise HTTPException(status_code=400, detail="本地文件不存在。")
        data = path.read_text(encoding="utf-8")
        return data[:200_000]
    if source_type in {"github_url", "url"}:
        url = _github_to_raw(source_uri.strip()) if source_type == "github_url" else source_uri.strip()
        if not url.startswith(("http://", "https://")):
            raise HTTPException(status_code=400, detail="URL 必须以 http:// 或 https:// 开头。")
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(url)
        if resp.status_code >= 400:
            raise HTTPException(status_code=400, detail=f"读取远程内容失败：HTTP {resp.status_code}")
        return resp.text[:200_000]
    return inline.strip()


def _infer_name(source_uri: str, fallback: str) -> str:
    if fallback.strip():
        return fallback.strip()
    if source_uri.strip():
        return Path(source_uri.rstrip("/")).name or source_uri.strip()
    return "未命名扩展"


def _slug_name(value: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9._-]+", "-", value.strip()).strip("-._")
    return slug[:64] or "skill"


def _unique_skill_dir(name: str) -> Path:
    target = SKILLS_DIR / f"{_slug_name(name)}-{int(time.time() * 1000)}"
    target.mkdir(parents=True, exist_ok=False)
    return target


def _parsed_skills_from_root(root: Path, source_type: str, source_uri: str = "") -> list[dict[str, Any]]:
    return parse_skill_extensions([
        {
            "id": None,
            "kind": "skill",
            "name": root.name,
            "description": "",
            "source_type": source_type,
            "source_uri": source_uri,
            "config": {"local_path": str(root), "format": "skill_dir"},
            "enabled": True,
        }
    ])


def _safe_upload_path(raw: str, strip_prefix: str = "") -> Path:
    value = raw.replace("\\", "/").lstrip("/")
    if strip_prefix and value.startswith(strip_prefix + "/"):
        value = value[len(strip_prefix) + 1:]
    parts = [p for p in value.split("/") if p and p not in {".", ".."}]
    if not parts:
        raise HTTPException(status_code=400, detail="上传文件路径无效。")
    return Path(*parts)


def _decode_upload_data(raw: str) -> bytes:
    payload = raw.split(",", 1)[1] if raw.startswith("data:") and "," in raw else raw
    try:
        return base64.b64decode(payload, validate=True)
    except Exception as exc:
        raise HTTPException(status_code=400, detail="上传内容不是有效 base64。") from exc


def _write_uploaded_dir(files: list[SkillUploadFile], dst: Path) -> None:
    if not files:
        raise HTTPException(status_code=400, detail="请选择 Skill 文件夹。")
    shutil.rmtree(dst)
    dst.mkdir(parents=True, exist_ok=True)
    first_parts = [f.path.replace("\\", "/").lstrip("/").split("/", 1)[0] for f in files if "/" in f.path.replace("\\", "/").lstrip("/")]
    strip_prefix = first_parts[0] if first_parts and all(p == first_parts[0] for p in first_parts) else ""
    for item in files:
        rel = _safe_upload_path(item.path, strip_prefix=strip_prefix)
        if any(p.startswith(".") for p in rel.parts) or any(p in {"node_modules", "__pycache__", ".git"} for p in rel.parts):
            continue
        target = dst / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(_decode_upload_data(item.data))


def _extract_zip_bytes(raw: str, dst: Path) -> None:
    with tempfile.TemporaryDirectory() as td:
        zip_path = Path(td) / "skill.zip"
        zip_path.write_bytes(_decode_upload_data(raw))
        try:
            with zipfile.ZipFile(zip_path) as zf:
                zf.extractall(td)
        except zipfile.BadZipFile as exc:
            raise HTTPException(status_code=400, detail="上传内容不是有效 ZIP。") from exc
        roots = [p for p in Path(td).iterdir() if p.name != "skill.zip" and p.name != "__MACOSX"]
        root = roots[0] if len(roots) == 1 and roots[0].is_dir() else Path(td)
        shutil.rmtree(dst)
        shutil.copytree(root, dst, ignore=shutil.ignore_patterns("__MACOSX", ".DS_Store", "skill.zip"))


def _copy_dir(src: Path, dst: Path) -> None:
    if not src.exists() or not src.is_dir():
        raise HTTPException(status_code=400, detail="Skill 本地目录不存在。")
    ignore = shutil.ignore_patterns(".git", "__pycache__", ".venv", "node_modules", ".DS_Store")
    shutil.rmtree(dst)
    shutil.copytree(src, dst, ignore=ignore)


def _github_repo_to_git_url(raw: str) -> str:
    value = raw.strip()
    if re.fullmatch(r"[\w.-]+/[\w.-]+", value):
        return f"https://github.com/{value}.git"
    if value.startswith("https://github.com/") and not value.endswith(".git"):
        return value.rstrip("/") + ".git"
    return value


def _clone_repo(repo_url: str, dst: Path) -> None:
    shutil.rmtree(dst)
    cmd = ["git", "clone", "--depth", "1", repo_url, str(dst)]
    try:
        subprocess.run(cmd, check=True, capture_output=True, text=True, timeout=90)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=400, detail="本机没有 git，无法从 Git 仓库安装 Skill。") from exc
    except subprocess.CalledProcessError as exc:
        detail = (exc.stderr or exc.stdout or "git clone 失败").strip()[:500]
        raise HTTPException(status_code=400, detail=detail) from exc


async def _download_zip(url: str, dst: Path) -> None:
    if not url.startswith(("http://", "https://")):
        raise HTTPException(status_code=400, detail="ZIP URL 必须以 http:// 或 https:// 开头。")
    with tempfile.TemporaryDirectory() as td:
        zip_path = Path(td) / "skill.zip"
        async with httpx.AsyncClient(timeout=60, follow_redirects=True) as client:
            resp = await client.get(url)
        if resp.status_code >= 400:
            raise HTTPException(status_code=400, detail=f"下载 ZIP 失败：HTTP {resp.status_code}")
        zip_path.write_bytes(resp.content)
        try:
            with zipfile.ZipFile(zip_path) as zf:
                zf.extractall(td)
        except zipfile.BadZipFile as exc:
            raise HTTPException(status_code=400, detail="下载内容不是有效 ZIP。") from exc
        roots = [p for p in Path(td).iterdir() if p.name != "skill.zip"]
        root = roots[0] if len(roots) == 1 and roots[0].is_dir() else Path(td)
        shutil.rmtree(dst)
        shutil.copytree(root, dst, ignore=shutil.ignore_patterns("__MACOSX", ".DS_Store", "skill.zip"))


async def _install_skill_dir(payload: SkillPayload) -> Path:
    if not payload.source_uri.strip():
        raise HTTPException(status_code=400, detail="请填写 Skill 来源目录或仓库地址。")
    name = _infer_name(payload.source_uri, payload.name)
    target = _unique_skill_dir(name)
    try:
        if payload.source_type == "local_dir":
            _copy_dir(Path(payload.source_uri).expanduser(), target)
        elif payload.source_type == "github_repo":
            _clone_repo(_github_repo_to_git_url(payload.source_uri), target)
        elif payload.source_type == "git_url":
            _clone_repo(payload.source_uri.strip(), target)
        elif payload.source_type == "zip_url":
            await _download_zip(payload.source_uri.strip(), target)
        else:
            raise HTTPException(status_code=400, detail="不支持的 Skill 来源。")
        if not _parsed_skills_from_root(target, payload.source_type, payload.source_uri):
            raise HTTPException(
                status_code=400,
                detail="没有识别到合法 Skill。请确认目录包含 SKILL.md、<name>.md，或 skills/<skill-name>/SKILL.md。",
            )
    except Exception:
        if target.exists():
            shutil.rmtree(target, ignore_errors=True)
        raise
    return target


async def _install_uploaded_skill(payload: SkillUploadPayload) -> Path:
    target = _unique_skill_dir("skill")
    try:
        if payload.source_type == "local_dir_upload":
            _write_uploaded_dir(payload.files, target)
        elif payload.source_type == "zip_upload":
            if not payload.zip_data:
                raise HTTPException(status_code=400, detail="请选择 ZIP 文件。")
            _extract_zip_bytes(payload.zip_data, target)
        else:
            raise HTTPException(status_code=400, detail="不支持的 Skill 上传方式。")
        if not _parsed_skills_from_root(target, payload.source_type):
            raise HTTPException(
                status_code=400,
                detail="没有识别到合法 Skill。请确认目录包含 SKILL.md、<name>.md，或 skills/<skill-name>/SKILL.md。",
            )
        return target
    except Exception:
        if target.exists():
            shutil.rmtree(target, ignore_errors=True)
        raise


def _split_legacy_collection_items(items: list[dict[str, Any]]) -> bool:
    changed = False
    existing_keys = {
        (
            str((item.get("config") or {}).get("collection_path") or ""),
            str(item.get("name") or ""),
        )
        for item in items
    }
    for item in items:
        config = item.get("config") or {}
        if config.get("skill_path"):
            continue
        parsed = parse_skill_extensions([item])
        if len(parsed) <= 1:
            continue
        collection_path = str(config.get("local_path") or item.get("source_uri") or "")
        for skill in parsed:
            key = (collection_path, skill["name"])
            if key in existing_keys:
                continue
            upsert_extension(
                DEFAULT_USER_ID,
                kind="skill",
                name=skill["name"],
                description=skill["description"],
                source_type=item.get("source_type") or "local_dir_upload",
                source_uri=item.get("source_uri") or "",
                content="",
                config={
                    "local_path": skill["local_path"],
                    "skill_path": skill["path"],
                    "collection_path": collection_path,
                    "collection_name": config.get("collection_name") or item.get("name") or "",
                    "collection_description": config.get("collection_description") or item.get("description") or "",
                    "format": "skill_dir",
                    "run_as": skill["run_as"],
                    "allowed_tools": skill["allowed_tools"],
                },
                enabled=bool(item.get("enabled")),
            )
            existing_keys.add(key)
            changed = True
        delete_extension(DEFAULT_USER_ID, int(item["id"]), "skill")
        changed = True
    return changed


@router.get("/skills")
async def api_list_skills() -> dict[str, Any]:
    items = list_extensions(DEFAULT_USER_ID, "skill")
    if _split_legacy_collection_items(items):
        items = list_extensions(DEFAULT_USER_ID, "skill")
    parsed_by_id: dict[Any, list[dict[str, Any]]] = {}
    for skill in parse_skill_extensions(items):
        parsed_by_id.setdefault(skill.get("id"), []).append(skill)
    enriched: list[dict[str, Any]] = []
    for item in items:
        parsed = parsed_by_id.get(item.get("id")) or []
        if not parsed:
            enriched.append({**item, "parse_error": "未识别到 SKILL.md"})
            continue
        for skill in parsed:
            enriched.append({
                **item,
                "name": skill["name"],
                "description": skill["description"],
                "run_as": skill["run_as"],
                "allowed_tools": skill["allowed_tools"],
                "skill_path": skill["path"],
                "local_path": skill["local_path"],
            })
    return {"skills": enriched}


def _register_parsed_skills(
    *,
    root: Path,
    source_type: str,
    source_uri: str,
    enabled: bool,
    display_name: str = "",
    display_description: str = "",
) -> list[dict[str, Any]]:
    parsed = _parsed_skills_from_root(root, source_type, source_uri)
    if not parsed:
        raise HTTPException(
            status_code=400,
            detail="没有识别到合法 Skill。请确认目录包含 SKILL.md、<name>.md，或 skills/<skill-name>/SKILL.md。",
        )
    registered: list[dict[str, Any]] = []
    collection_name = display_name.strip()
    collection_description = display_description.strip()
    for skill in parsed:
        registered.append(upsert_extension(
            DEFAULT_USER_ID,
            kind="skill",
            name=skill["name"],
            description=skill["description"],
            source_type=source_type,
            source_uri=source_uri,
            content="",
            config={
                "local_path": skill["local_path"],
                "skill_path": skill["path"],
                "collection_path": str(root),
                "collection_name": collection_name,
                "collection_description": collection_description,
                "format": "skill_dir",
                "run_as": skill["run_as"],
                "allowed_tools": skill["allowed_tools"],
            },
            enabled=enabled,
        ))
    return registered


@router.post("/skills")
async def api_create_skill(payload: SkillPayload) -> dict[str, Any]:
    local_dir = await _install_skill_dir(payload)
    skills = _register_parsed_skills(
        root=local_dir,
        source_type=payload.source_type,
        source_uri=payload.source_uri.strip(),
        enabled=payload.enabled,
        display_name=payload.name,
        display_description=payload.description,
    )
    return {"ok": True, "skills": skills, "count": len(skills)}


@router.post("/skills/upload")
async def api_upload_skill(payload: SkillUploadPayload) -> dict[str, Any]:
    local_dir = await _install_uploaded_skill(payload)
    skills = _register_parsed_skills(
        root=local_dir,
        source_type=payload.source_type,
        source_uri="",
        enabled=payload.enabled,
        display_name=payload.name,
        display_description=payload.description,
    )
    return {"ok": True, "skills": skills, "count": len(skills)}


@router.delete("/skills/{skill_id}")
async def api_delete_skill(skill_id: int) -> dict[str, Any]:
    skill = get_extension(DEFAULT_USER_ID, skill_id, "skill")
    ok = delete_extension(DEFAULT_USER_ID, skill_id, "skill")
    if ok and skill:
        local_path = (skill.get("config") or {}).get("local_path")
        if local_path:
            path = Path(str(local_path)).expanduser()
            try:
                if path.exists() and path.is_dir() and SKILLS_DIR.resolve() in path.resolve().parents:
                    shutil.rmtree(path, ignore_errors=True)
            except OSError:
                pass
    return {"ok": ok}


@router.post("/skills/{skill_id}/toggle")
async def api_toggle_skill(skill_id: int, payload: TogglePayload | None = None) -> dict[str, Any]:
    skill = set_extension_enabled(
        DEFAULT_USER_ID,
        skill_id,
        enabled=(payload.enabled if payload else None),
        kind="skill",
    )
    if skill is None:
        raise HTTPException(status_code=404, detail="Skill 不存在。")
    return {"ok": True, "enabled": skill["enabled"], "skill": skill}


@router.get("/mcp")
async def api_list_mcp() -> dict[str, Any]:
    return {"servers": list_extensions(DEFAULT_USER_ID, "mcp")}


@router.post("/mcp")
async def api_create_mcp(payload: McpPayload) -> dict[str, Any]:
    loaded = await _load_content(payload.source_type, payload.source_uri, payload.content)
    config = dict(payload.config or {})
    if loaded:
        try:
            loaded_config = json.loads(loaded)
            if isinstance(loaded_config, dict):
                config.update(loaded_config)
        except json.JSONDecodeError:
            config["raw"] = loaded
    config.update(
        {
            "transport": payload.transport,
            "command": payload.command.strip(),
            "args": [str(a) for a in payload.args if str(a).strip()],
            "url": payload.url.strip(),
            "env": {str(k): str(v) for k, v in payload.env.items()},
        }
    )
    if payload.transport == "stdio" and not config.get("command"):
        raise HTTPException(status_code=400, detail="stdio MCP 需要填写启动命令。")
    if payload.transport in {"http", "sse"} and not config.get("url"):
        raise HTTPException(status_code=400, detail="HTTP/SSE MCP 需要填写服务地址。")
    try:
        tools, status = await _discover_mcp_tools(config)
        config["tools"] = tools
        config["status"] = status
        config["last_error"] = ""
        config["checked_at"] = time.time()
    except Exception as exc:
        config["tools"] = []
        config["status"] = "error"
        config["last_error"] = str(getattr(exc, "detail", exc))[:500]
        config["checked_at"] = time.time()
    server = upsert_extension(
        DEFAULT_USER_ID,
        kind="mcp",
        name=_infer_name(payload.source_uri or payload.url or payload.command, payload.name),
        description=payload.description.strip(),
        source_type=payload.source_type,
        source_uri=payload.source_uri.strip(),
        content=loaded,
        config=config,
        enabled=payload.enabled,
    )
    return {"ok": True, "server": server}


@router.post("/mcp/{server_id}/refresh-tools")
async def api_refresh_mcp_tools(server_id: int) -> dict[str, Any]:
    server = get_extension(DEFAULT_USER_ID, server_id, "mcp")
    if server is None:
        raise HTTPException(status_code=404, detail="MCP 服务器不存在。")
    config = dict(server.get("config") or {})
    try:
        tools, status = await _discover_mcp_tools(config)
        config.update({
            "tools": tools,
            "status": status,
            "last_error": "",
            "checked_at": time.time(),
        })
    except Exception as exc:
        config.update({
            "tools": [],
            "status": "error",
            "last_error": str(getattr(exc, "detail", exc))[:500],
            "checked_at": time.time(),
        })
    updated = upsert_extension(
        DEFAULT_USER_ID,
        kind="mcp",
        name=server.get("name") or "",
        description=server.get("description") or "",
        source_type=server.get("source_type") or "inline",
        source_uri=server.get("source_uri") or "",
        content=server.get("content") or "",
        config=config,
        enabled=bool(server.get("enabled")),
        extension_id=server_id,
    )
    return {"ok": config.get("status") != "error", "server": updated, "tools": config.get("tools") or [], "error": config.get("last_error") or ""}


@router.post("/mcp/{server_id}/debug-tool")
async def api_debug_mcp_tool(server_id: int, payload: McpToolCallPayload) -> dict[str, Any]:
    server = get_extension(DEFAULT_USER_ID, server_id, "mcp")
    if server is None:
        raise HTTPException(status_code=404, detail="MCP 服务器不存在。")
    result = await _call_mcp_tool(server.get("config") or {}, payload.tool_name, payload.arguments)
    return {"ok": True, "result": result}


@router.delete("/mcp/{server_id}")
async def api_delete_mcp(server_id: int) -> dict[str, Any]:
    return {"ok": delete_extension(DEFAULT_USER_ID, server_id, "mcp")}


@router.post("/mcp/{server_id}/toggle")
async def api_toggle_mcp(server_id: int, payload: TogglePayload | None = None) -> dict[str, Any]:
    server = set_extension_enabled(
        DEFAULT_USER_ID,
        server_id,
        enabled=(payload.enabled if payload else None),
        kind="mcp",
    )
    if server is None:
        raise HTTPException(status_code=404, detail="MCP 服务器不存在。")
    return {"ok": True, "enabled": server["enabled"], "server": server}
