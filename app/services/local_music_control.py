"""Local music app control for macOS.

This module intentionally exposes a small whitelist of actions. It does not run
arbitrary user-provided shell commands.
"""

from __future__ import annotations

import json
import platform
import subprocess
import sys
from typing import Any


MUSIC_APPS: dict[str, dict[str, str]] = {
    "qq_music": {
        "label": "QQ 音乐",
        "bundle_id": "com.tencent.QQMusicMac",
        "scheme": "qqmusicmac://",
    },
    "netease_music": {
        "label": "网易云音乐",
        "bundle_id": "com.netease.163music",
        "scheme": "orpheus://",
    },
}

MEDIA_KEY_CODES = {
    "play_pause": 100,  # F8 / media play-pause on macOS keyboards
    "previous": 98,    # F7 / previous
    "next": 101,       # F9 / next
}


class LocalMusicError(RuntimeError):
    pass


def _host_hint() -> str:
    return (
        "请给启动 TablePet 后端的本地应用授予辅助功能权限。"
        "如果你从 Trae 启动，授权对象会是 Trae；如果你用 scripts/run_tablepet_local.command "
        "或 Terminal 启动，授权对象会是 Terminal。"
        f" 当前 Python：{sys.executable}"
    )


def _is_permission_error(message: str) -> bool:
    lowered = message.lower()
    return any(
        token in lowered
        for token in (
            "not allowed assistive access",
            "is not allowed",
            "not authorized",
            "not authorised",
            "权限违例",
            "未获授权",
            "不被允许",
            "-10004",
        )
    )


def _run(args: list[str], *, timeout: float = 5) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            args,
            text=True,
            capture_output=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise LocalMusicError("本地软件响应超时。") from exc
    except OSError as exc:
        raise LocalMusicError(str(exc)) from exc


def _osascript(script: str, *, timeout: float = 5) -> str:
    proc = _run(["osascript", "-e", script], timeout=timeout)
    if proc.returncode != 0:
        msg = (proc.stderr or proc.stdout or "AppleScript 执行失败").strip()
        if _is_permission_error(msg):
            msg += "。" + _host_hint()
        raise LocalMusicError(msg)
    return (proc.stdout or "").strip()


def _resolve_app(app: str) -> tuple[str, dict[str, str] | None]:
    normalized = (app or "default").strip()
    if normalized in {"", "default", "current"}:
        return "default", None
    if normalized not in MUSIC_APPS:
        raise LocalMusicError(f"未知音乐应用：{normalized}")
    return normalized, MUSIC_APPS[normalized]


def _open_app(app_key: str, app_info: dict[str, str]) -> dict[str, Any]:
    # URL schemes wake these apps reliably on the user's machine; bundle id is a fallback.
    proc = _run(["open", "-g", app_info["scheme"]], timeout=5)
    if proc.returncode != 0:
        proc = _run(["open", "-b", app_info["bundle_id"]], timeout=5)
    if proc.returncode != 0:
        raise LocalMusicError((proc.stderr or proc.stdout or "打开应用失败").strip())
    return {"ok": True, "app": app_key, "app_label": app_info["label"], "action": "open"}


def _activate_app(app_key: str, app_info: dict[str, str]) -> None:
    try:
        _osascript(f'tell application id "{app_info["bundle_id"]}" to activate', timeout=5)
    except LocalMusicError:
        _open_app(app_key, app_info)


def _send_media_key(action: str) -> dict[str, Any]:
    key_code = MEDIA_KEY_CODES[action]
    _osascript(f'tell application "System Events" to key code {key_code}', timeout=5)
    return {
        "ok": True,
        "action": action,
        "verified": False,
        "note": "已向 macOS 发送媒体键，但 macOS 不提供稳定的播放器状态回读，不能确认 QQ 音乐/网易云是否真的开始播放。",
    }


def _get_volume() -> int:
    raw = _osascript("output volume of (get volume settings)", timeout=3)
    try:
        return int(raw.strip())
    except ValueError as exc:
        raise LocalMusicError(f"读取系统音量失败：{raw}") from exc


def _set_volume(level: Any) -> dict[str, Any]:
    try:
        volume = int(level)
    except (TypeError, ValueError) as exc:
        raise LocalMusicError("set_volume 需要 level 参数，范围 0 到 100。") from exc
    volume = max(0, min(100, volume))
    _osascript(f"set volume output volume {volume}", timeout=3)
    return {"ok": True, "action": "set_volume", "level": volume}


def _adjust_volume(delta: int) -> dict[str, Any]:
    return _set_volume(_get_volume() + delta)


def available_music_apps() -> list[dict[str, str]]:
    return [
        {"id": key, "label": value["label"], "bundle_id": value["bundle_id"]}
        for key, value in MUSIC_APPS.items()
    ]


def control_music(raw_args: dict[str, Any]) -> str:
    if platform.system() != "Darwin":
        return json.dumps({"error": "当前只实现了 macOS 音乐软件控制。"}, ensure_ascii=False)

    action = str(raw_args.get("action") or "").strip()
    app_key, app_info = _resolve_app(str(raw_args.get("app") or "default"))
    try:
        if action == "list_apps":
            return json.dumps({"ok": True, "apps": available_music_apps()}, ensure_ascii=False)

        if action == "open":
            if app_info is None:
                raise LocalMusicError("open 需要指定 app：qq_music 或 netease_music。")
            return json.dumps(_open_app(app_key, app_info), ensure_ascii=False)

        if action in MEDIA_KEY_CODES:
            if app_info is not None:
                _activate_app(app_key, app_info)
            result = _send_media_key(action)
            if app_info is not None:
                result.update({"app": app_key, "app_label": app_info["label"]})
            return json.dumps(result, ensure_ascii=False)

        if action == "set_volume":
            return json.dumps(_set_volume(raw_args.get("level")), ensure_ascii=False)
        if action == "volume_up":
            return json.dumps(_adjust_volume(10), ensure_ascii=False)
        if action == "volume_down":
            return json.dumps(_adjust_volume(-10), ensure_ascii=False)

        return json.dumps(
            {
                "error": "unknown action",
                "available_actions": [
                    "list_apps",
                    "open",
                    "play_pause",
                    "next",
                    "previous",
                    "set_volume",
                    "volume_up",
                    "volume_down",
                ],
            },
            ensure_ascii=False,
        )
    except LocalMusicError as exc:
        return json.dumps({"error": str(exc)}, ensure_ascii=False)
