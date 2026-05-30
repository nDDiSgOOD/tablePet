"""会话（session）存储 / Session lifecycle management.

设计
====
- ``conversation_session``：一段连续对话的容器，``closed_at IS NULL`` 表示
  当前活跃 session。同一个 user_id 任意时刻只允许一条 active。
- ``conversation_turn.session_id``：每轮对话挂到一个 session 上。
- 三端共享当前 session：USB / WiFi 不会主动关闭，由 Web 点"清空"触发关闭。

API
===
- ``get_or_create_active(user_id)``：拿当前活跃 session，没有就开一条
- ``close_active_session(user_id, *, summary, title)``：归档当前活跃 session
- ``list_sessions(user_id, ...)``：分页拉所有 session（含已关闭的）
- ``list_turns_by_session(session_id)``：拉 session 下的全部 turn
- ``delete_session(user_id, session_id)``：连同其所有 turn 一起物理删除
"""

from __future__ import annotations

import time
from typing import Any

from .db import get_conn


def get_or_create_active(user_id: str) -> dict[str, Any]:
    """拿当前活跃 session，没有则新建一条 (closed_at IS NULL)."""
    now = time.time()
    with get_conn() as conn:
        row = conn.execute(
            "SELECT id, started_at, closed_at, title, summary, "
            "       turn_count, token_count "
            "FROM conversation_session "
            "WHERE user_id = ? AND closed_at IS NULL "
            "ORDER BY id DESC LIMIT 1",
            (user_id,),
        ).fetchone()
        if row:
            return dict(row)
        cur = conn.execute(
            "INSERT INTO conversation_session "
            "(user_id, started_at) VALUES (?, ?)",
            (user_id, now),
        )
        sid = cur.lastrowid or 0
        return {
            "id": sid,
            "started_at": now,
            "closed_at": None,
            "title": "",
            "summary": "",
            "turn_count": 0,
            "token_count": 0,
        }


def close_active_session(
    user_id: str,
    *,
    summary: str = "",
    title: str = "",
) -> int | None:
    """关闭当前活跃 session 并写入 summary。返回被关闭的 session id。"""
    now = time.time()
    with get_conn() as conn:
        row = conn.execute(
            "SELECT id FROM conversation_session "
            "WHERE user_id = ? AND closed_at IS NULL "
            "ORDER BY id DESC LIMIT 1",
            (user_id,),
        ).fetchone()
        if not row:
            return None
        sid = int(row["id"])
        # 同时把 turn_count / token_count 算好回填
        agg = conn.execute(
            "SELECT COUNT(*) AS c, "
            "       COALESCE(SUM(user_tokens + assistant_tokens), 0) AS t "
            "FROM conversation_turn WHERE session_id = ?",
            (sid,),
        ).fetchone()
        conn.execute(
            "UPDATE conversation_session "
            "SET closed_at = ?, summary = ?, title = ?, "
            "    turn_count = ?, token_count = ? "
            "WHERE id = ?",
            (now, summary[:2000], title[:60], int(agg["c"]), int(agg["t"]), sid),
        )
    return sid


def update_session_meta(session_id: int, *, title: str | None = None,
                        summary: str | None = None) -> None:
    sets: list[str] = []
    args: list[Any] = []
    if title is not None:
        sets.append("title = ?")
        args.append(title[:60])
    if summary is not None:
        sets.append("summary = ?")
        args.append(summary[:2000])
    if not sets:
        return
    args.append(int(session_id))
    with get_conn() as conn:
        conn.execute(
            f"UPDATE conversation_session SET {', '.join(sets)} WHERE id = ?",
            args,
        )


def list_sessions(
    user_id: str,
    *,
    limit: int = 30,
    before_id: int | None = None,
) -> list[dict[str, Any]]:
    """按 id 倒序分页（最新的在前）。返回每条 session 的 meta + 最新 turn_count。"""
    sql = (
        "SELECT s.id, s.started_at, s.closed_at, s.title, s.summary, "
        "       s.turn_count, s.token_count, "
        "       (SELECT COUNT(*) FROM conversation_turn t "
        "        WHERE t.session_id = s.id "
        "          AND COALESCE(t.channel,'') != 'system_event') AS live_turn_count, "
        "       (SELECT MAX(t.created_at) FROM conversation_turn t "
        "        WHERE t.session_id = s.id) AS last_turn_at "
        "FROM conversation_session s "
        "WHERE s.user_id = ?"
    )
    args: list[Any] = [user_id]
    if before_id is not None:
        sql += " AND s.id < ?"
        args.append(int(before_id))
    sql += " ORDER BY s.id DESC LIMIT ?"
    args.append(int(limit))
    with get_conn() as conn:
        rows = conn.execute(sql, args).fetchall()
    return [dict(r) for r in rows]


def list_turns_by_session(session_id: int, *, include_system: bool = True) -> list[dict[str, Any]]:
    sql = (
        "SELECT id, channel, user_text, assistant_text, "
        "       user_tokens, assistant_tokens, "
        "       COALESCE(latency_ms, 0) AS latency_ms, "
        "       COALESCE(model, '') AS model, "
        "       created_at "
        "FROM conversation_turn WHERE session_id = ?"
    )
    args: list[Any] = [int(session_id)]
    if not include_system:
        sql += " AND COALESCE(channel,'') != 'system_event'"
    sql += " ORDER BY id ASC"
    with get_conn() as conn:
        rows = conn.execute(sql, args).fetchall()
    return [dict(r) for r in rows]


def delete_session(user_id: str, session_id: int) -> bool:
    """删除整个 session 及其下属 turn。短期/长期记忆和日记不动."""
    with get_conn() as conn:
        owner = conn.execute(
            "SELECT id FROM conversation_session WHERE id = ? AND user_id = ?",
            (int(session_id), user_id),
        ).fetchone()
        if not owner:
            return False
        conn.execute("DELETE FROM conversation_turn WHERE session_id = ?", (int(session_id),))
        conn.execute("DELETE FROM conversation_session WHERE id = ?", (int(session_id),))
        return True


def reopen_session(user_id: str, session_id: int) -> dict[str, Any] | None:
    """把一条已归档 session 重新设为活跃（``closed_at = NULL``）。

    使用前提：调用方已经先通过 ``close_session_with_summary`` 关闭了当前活跃
    session（避免出现两条 active）。返回被重开的 session dict；找不到 / 不属于
    该用户返回 ``None``.
    """
    with get_conn() as conn:
        row = conn.execute(
            "SELECT id, started_at, closed_at, title, summary, "
            "       turn_count, token_count "
            "FROM conversation_session WHERE id = ? AND user_id = ?",
            (int(session_id), user_id),
        ).fetchone()
        if not row:
            return None
        # 兜底：如果还有别的 active session（理论上不该有），先关掉
        conn.execute(
            "UPDATE conversation_session SET closed_at = COALESCE(closed_at, ?) "
            "WHERE user_id = ? AND closed_at IS NULL AND id != ?",
            (time.time(), user_id, int(session_id)),
        )
        conn.execute(
            "UPDATE conversation_session SET closed_at = NULL WHERE id = ?",
            (int(session_id),),
        )
        # 拿最新 row 返回
        row2 = conn.execute(
            "SELECT id, started_at, closed_at, title, summary, "
            "       turn_count, token_count "
            "FROM conversation_session WHERE id = ?",
            (int(session_id),),
        ).fetchone()
    return dict(row2) if row2 else None
