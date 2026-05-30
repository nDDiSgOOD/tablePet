"""UI 设置存储 / Per-user UI settings (theme, layout etc.).

Why server-side?
================
原本主题（含上传的 base64 图片）存在浏览器 localStorage，单 origin 配额
通常只有 5–10MB，一张图片就能让 ``setItem`` 抛 ``QuotaExceededError``，
导致下次启动读不到。把它挪到 SQLite，配额上不封顶，多端也能同步。

字段约定
--------
``key`` 是命名空间（例如 ``theme``、``chat_layout``），``value`` 是 JSON 字符串
——前端 ``JSON.stringify`` 进来、``JSON.parse`` 出去。

体积上限
--------
单条 ``value`` ≤ ``MAX_VALUE_BYTES``（默认 32MB，足够装一张 24MB 的高清图
做 base64 后的 dataURL）。SQLite 单 row 默认上限是 1GB，瓶颈不是数据库
而是 HTTP body 大小——uvicorn 默认就放过 32MB 没问题。
"""

from __future__ import annotations

import json
import time
from typing import Any

from .db import get_conn

# 32MB：base64 之后约对应 24MB 的原图，桌面壁纸用绰绰有余。
# 真要上传 4K 高清原图，建议先在前端压缩；这里再大就该走对象存储了。
MAX_VALUE_BYTES = 32 * 1024 * 1024


def get_setting(user_id: str, key: str) -> Any:
    """读取一项设置；没存过返回 None."""
    with get_conn() as conn:
        row = conn.execute(
            "SELECT value FROM ui_settings WHERE user_id = ? AND key = ?",
            (user_id, key),
        ).fetchone()
    if not row:
        return None
    raw = row["value"]
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return raw


def set_setting(user_id: str, key: str, value: Any) -> None:
    """整体覆盖式写入 (upsert)."""
    raw = json.dumps(value, ensure_ascii=False)
    if len(raw.encode("utf-8")) > MAX_VALUE_BYTES:
        raise ValueError(
            f"UI 设置值过大（>{MAX_VALUE_BYTES // 1024 // 1024}MB）。"
            f"如果是图片请先在前端压缩或换更小的素材。"
        )
    now = time.time()
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO ui_settings (user_id, key, value, updated_at) "
            "VALUES (?, ?, ?, ?) "
            "ON CONFLICT(user_id, key) DO UPDATE SET "
            "  value = excluded.value, updated_at = excluded.updated_at",
            (user_id, key, raw, now),
        )


def delete_setting(user_id: str, key: str) -> bool:
    with get_conn() as conn:
        cur = conn.execute(
            "DELETE FROM ui_settings WHERE user_id = ? AND key = ?",
            (user_id, key),
        )
        return cur.rowcount > 0


def list_settings(user_id: str) -> dict[str, Any]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT key, value FROM ui_settings WHERE user_id = ?",
            (user_id,),
        ).fetchall()
    out: dict[str, Any] = {}
    for r in rows:
        try:
            out[r["key"]] = json.loads(r["value"])
        except json.JSONDecodeError:
            out[r["key"]] = r["value"]
    return out
