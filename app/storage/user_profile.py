"""User profile 存储 / User profile CRUD.

支持两类字段：

1. **固定字段**：name / language / bio / city / since 写在 ``user_profile`` 表。
2. **自定义字段**：写在 ``user_profile_custom`` 表（key/value 动态扩展），
   前端可以无限自由地往里加。
"""

from __future__ import annotations

import time
from typing import Any

from .db import get_conn

FIXED_FIELDS = ("name", "language", "bio", "city", "since")


def _ensure_row(conn, user_id: str) -> None:
    conn.execute(
        "INSERT OR IGNORE INTO user_profile (user_id, since, updated_at) "
        "VALUES (?, ?, ?)",
        (user_id, time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), time.time()),
    )


def get_profile(user_id: str) -> dict[str, Any]:
    """读取完整画像（固定 + 自定义）.

    返回结构::

        {
            "name": "...",
            "language": "zh-CN",
            "bio": "...",
            "city": "...",
            "since": "...",
            "custom": {"hobby": "钢琴", "pet_color": "橘"}
        }
    """
    with get_conn() as conn:
        _ensure_row(conn, user_id)
        row = conn.execute(
            "SELECT name, language, bio, city, since FROM user_profile "
            "WHERE user_id = ?",
            (user_id,),
        ).fetchone()
        custom_rows = conn.execute(
            "SELECT key, value FROM user_profile_custom WHERE user_id = ? ORDER BY key",
            (user_id,),
        ).fetchall()
    profile = dict(row) if row else {f: "" for f in FIXED_FIELDS}
    profile["custom"] = {r["key"]: r["value"] for r in custom_rows}
    return profile


def update_profile(user_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    """部分更新固定字段；未提供的字段保持原值."""
    cleaned = {k: str(payload.get(k) or "")[:500] for k in FIXED_FIELDS if k in payload}
    if not cleaned:
        return get_profile(user_id)
    with get_conn() as conn:
        _ensure_row(conn, user_id)
        sets = ", ".join(f"{k} = ?" for k in cleaned) + ", updated_at = ?"
        params = list(cleaned.values()) + [time.time(), user_id]
        conn.execute(f"UPDATE user_profile SET {sets} WHERE user_id = ?", params)
    return get_profile(user_id)


def list_custom_fields(user_id: str) -> dict[str, str]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT key, value FROM user_profile_custom WHERE user_id = ? ORDER BY key",
            (user_id,),
        ).fetchall()
    return {r["key"]: r["value"] for r in rows}


def set_custom_field(user_id: str, key: str, value: str) -> None:
    key = key.strip()[:60]
    if not key:
        raise ValueError("key is required")
    if key in FIXED_FIELDS:
        raise ValueError(f"'{key}' 与固定字段冲突，请使用 update_profile")
    value = str(value)[:1000]
    with get_conn() as conn:
        _ensure_row(conn, user_id)
        conn.execute(
            "INSERT INTO user_profile_custom (user_id, key, value, updated_at) "
            "VALUES (?, ?, ?, ?) "
            "ON CONFLICT(user_id, key) DO UPDATE SET value = excluded.value, "
            "updated_at = excluded.updated_at",
            (user_id, key, value, time.time()),
        )


def delete_custom_field(user_id: str, key: str) -> bool:
    with get_conn() as conn:
        cur = conn.execute(
            "DELETE FROM user_profile_custom WHERE user_id = ? AND key = ?",
            (user_id, key),
        )
        return cur.rowcount > 0
