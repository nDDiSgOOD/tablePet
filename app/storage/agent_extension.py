"""Agent Skill / MCP 扩展存储。"""

from __future__ import annotations

import json
import time
from typing import Any

from .db import get_conn


def _row_to_dict(row: Any) -> dict[str, Any]:
    item = dict(row)
    item["enabled"] = bool(item.get("enabled"))
    try:
        item["config"] = json.loads(item.get("config_json") or "{}")
    except json.JSONDecodeError:
        item["config"] = {}
    item.pop("config_json", None)
    return item


def list_extensions(user_id: str, kind: str | None = None) -> list[dict[str, Any]]:
    sql = "SELECT * FROM agent_extension WHERE user_id = ?"
    params: list[Any] = [user_id]
    if kind:
        sql += " AND kind = ?"
        params.append(kind)
    sql += " ORDER BY enabled DESC, updated_at DESC, id DESC"
    with get_conn() as conn:
        rows = conn.execute(sql, params).fetchall()
    return [_row_to_dict(r) for r in rows]


def list_enabled_extensions(user_id: str, kind: str | None = None) -> list[dict[str, Any]]:
    sql = "SELECT * FROM agent_extension WHERE user_id = ? AND enabled = 1"
    params: list[Any] = [user_id]
    if kind:
        sql += " AND kind = ?"
        params.append(kind)
    sql += " ORDER BY updated_at DESC, id DESC"
    with get_conn() as conn:
        rows = conn.execute(sql, params).fetchall()
    return [_row_to_dict(r) for r in rows]


def upsert_extension(
    user_id: str,
    *,
    kind: str,
    name: str,
    description: str = "",
    source_type: str = "inline",
    source_uri: str = "",
    content: str = "",
    config: dict[str, Any] | None = None,
    enabled: bool = True,
    extension_id: int | None = None,
) -> dict[str, Any]:
    now = time.time()
    config_json = json.dumps(config or {}, ensure_ascii=False)
    with get_conn() as conn:
        if extension_id:
            conn.execute(
                """
                UPDATE agent_extension
                   SET kind = ?, name = ?, description = ?, source_type = ?,
                       source_uri = ?, content = ?, config_json = ?,
                       enabled = ?, updated_at = ?
                 WHERE id = ? AND user_id = ?
                """,
                (
                    kind,
                    name,
                    description,
                    source_type,
                    source_uri,
                    content,
                    config_json,
                    1 if enabled else 0,
                    now,
                    extension_id,
                    user_id,
                ),
            )
            row_id = extension_id
        else:
            cur = conn.execute(
                """
                INSERT INTO agent_extension
                    (user_id, kind, name, description, source_type, source_uri,
                     content, config_json, enabled, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    user_id,
                    kind,
                    name,
                    description,
                    source_type,
                    source_uri,
                    content,
                    config_json,
                    1 if enabled else 0,
                    now,
                    now,
                ),
            )
            row_id = int(cur.lastrowid)
        row = conn.execute(
            "SELECT * FROM agent_extension WHERE id = ? AND user_id = ?",
            (row_id, user_id),
        ).fetchone()
    return _row_to_dict(row)


def delete_extension(user_id: str, extension_id: int, kind: str | None = None) -> bool:
    sql = "DELETE FROM agent_extension WHERE user_id = ? AND id = ?"
    params: list[Any] = [user_id, extension_id]
    if kind:
        sql += " AND kind = ?"
        params.append(kind)
    with get_conn() as conn:
        cur = conn.execute(sql, params)
        return cur.rowcount > 0


def set_extension_enabled(
    user_id: str,
    extension_id: int,
    *,
    enabled: bool | None = None,
    kind: str | None = None,
) -> dict[str, Any] | None:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM agent_extension WHERE user_id = ? AND id = ?"
            + (" AND kind = ?" if kind else ""),
            (user_id, extension_id, kind) if kind else (user_id, extension_id),
        ).fetchone()
        if row is None:
            return None
        next_enabled = (not bool(row["enabled"])) if enabled is None else bool(enabled)
        conn.execute(
            "UPDATE agent_extension SET enabled = ?, updated_at = ? WHERE user_id = ? AND id = ?",
            (1 if next_enabled else 0, time.time(), user_id, extension_id),
        )
        row = conn.execute(
            "SELECT * FROM agent_extension WHERE user_id = ? AND id = ?",
            (user_id, extension_id),
        ).fetchone()
    return _row_to_dict(row)
