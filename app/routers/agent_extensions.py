"""Agent Skill / MCP 管理接口。"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import tempfile
import time
import zipfile
from pathlib import Path
from typing import Any, Literal

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

router = APIRouter(prefix="/api")

SkillSourceType = Literal["local_dir", "github_repo", "git_url", "zip_url"]
McpSourceType = Literal["inline", "local_file", "github_url", "url"]


class SkillPayload(BaseModel):
    name: str = Field(default="", max_length=120)
    description: str = Field(default="", max_length=500)
    source_type: SkillSourceType = "local_dir"
    source_uri: str = Field(default="", max_length=1200)
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


async def _install_skill_dir(payload: SkillPayload) -> tuple[str, Path]:
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
    except Exception:
        if target.exists():
            shutil.rmtree(target, ignore_errors=True)
        raise
    return name, target


@router.get("/skills")
async def api_list_skills() -> dict[str, Any]:
    return {"skills": list_extensions(DEFAULT_USER_ID, "skill")}


@router.post("/skills")
async def api_create_skill(payload: SkillPayload) -> dict[str, Any]:
    name, local_dir = await _install_skill_dir(payload)
    skill = upsert_extension(
        DEFAULT_USER_ID,
        kind="skill",
        name=name,
        description=payload.description.strip(),
        source_type=payload.source_type,
        source_uri=payload.source_uri.strip(),
        content="",
        config={"local_path": str(local_dir), "format": "skill_dir"},
        enabled=payload.enabled,
    )
    return {"ok": True, "skill": skill}


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
