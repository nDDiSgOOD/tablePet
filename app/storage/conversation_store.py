"""对话原始记录 / Conversation turn ledger.

所有 channel（web / wifi / usb）的真实对话都进 ``conversation_turn`` 表。
这是真理源；其他三层记忆（临时/短期/长期）都是它的派生视图。

特殊 channel 约定
-----------------
- ``"system_event"``：不是用户对话，而是 *系统事件叙述*（profile 变更、
  设置切换等）。注入 prompt 时会被渲染成 ``role="system"`` 而不是 user/assistant。
  这样 AI 不会把"用户改了名字"当成自己说错过话，反而获得连贯的因果意识。

为什么不像 Reasonix 那样写 JSONL？
  - JSONL 适合单进程开发助手；TablePet 是多线程网关（FastAPI worker + USB
    bridge thread），并发追加易冲突。
  - SQLite 自带 WAL，原生并发安全。
  - 派生视图（"未总结的最近 N 轮"、"24h 内的对话"）用 SQL 一行搞定。
"""

from __future__ import annotations

import time
from typing import Any, Iterable

from .db import get_conn

CHANNEL_SYSTEM_EVENT = "system_event"


def append_turn(
    user_id: str,
    user_text: str,
    assistant_text: str,
    *,
    channel: str = "",
    user_tokens: int = 0,
    assistant_tokens: int = 0,
    session_id: int | None = None,
) -> int:
    """记录一轮对话，返回 turn id.

    ``session_id`` 不传时自动挂到当前活跃 session（没有则新建一条）。
    """
    if session_id is None:
        # 局部 import 避免循环依赖
        from .session_store import get_or_create_active

        session_id = int(get_or_create_active(user_id)["id"])
    now = time.time()
    with get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO conversation_turn "
            "(user_id, session_id, channel, user_text, assistant_text, "
            " user_tokens, assistant_tokens, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                user_id,
                int(session_id),
                channel,
                user_text,
                assistant_text,
                int(user_tokens),
                int(assistant_tokens),
                now,
            ),
        )
        return cur.lastrowid or 0


def append_system_event(user_id: str, narrative: str) -> int:
    """记录一条 *系统事件*。``user_text`` 留空，叙述写在 ``assistant_text``
    （prompt 拼装时会再判断 channel）。
    """
    return append_turn(
        user_id,
        "",
        narrative,
        channel=CHANNEL_SYSTEM_EVENT,
        user_tokens=0,
        assistant_tokens=0,
    )


def list_turns_paged(
    user_id: str,
    *,
    limit: int = 50,
    before_id: int | None = None,
    include_system: bool = True,
) -> list[dict[str, Any]]:
    """按 id 倒序分页读 turn，可选过滤系统事件."""
    sql = (
        "SELECT id, channel, user_text, assistant_text, "
        "user_tokens, assistant_tokens, created_at "
        "FROM conversation_turn WHERE user_id = ?"
    )
    args: list[Any] = [user_id]
    if before_id is not None:
        sql += " AND id < ?"
        args.append(int(before_id))
    if not include_system:
        sql += " AND channel != ?"
        args.append(CHANNEL_SYSTEM_EVENT)
    sql += " ORDER BY id DESC LIMIT ?"
    args.append(int(limit))
    with get_conn() as conn:
        rows = conn.execute(sql, args).fetchall()
    return [dict(r) for r in rows]


def delete_turn(user_id: str, turn_id: int) -> bool:
    with get_conn() as conn:
        cur = conn.execute(
            "DELETE FROM conversation_turn WHERE user_id = ? AND id = ?",
            (user_id, int(turn_id)),
        )
        return cur.rowcount > 0


def delete_all_turns(user_id: str) -> int:
    with get_conn() as conn:
        cur = conn.execute(
            "DELETE FROM conversation_turn WHERE user_id = ?",
            (user_id,),
        )
        return cur.rowcount


def list_turns_since(user_id: str, since_ts: float, *, limit: int = 1000,
                     session_id: int | None = None) -> list[dict[str, Any]]:
    """读 since_ts 之后的所有 turn（升序）.

    传了 ``session_id`` 时会再过滤为只属于该 session 的 turn。
    ``limit`` 是兜底，正常 24h 内不会超过几百条。
    """
    sql = (
        "SELECT id, channel, user_text, assistant_text, "
        "       user_tokens, assistant_tokens, created_at, session_id "
        "FROM conversation_turn "
        "WHERE user_id = ? AND created_at >= ?"
    )
    args: list[Any] = [user_id, since_ts]
    if session_id is not None:
        sql += " AND session_id = ?"
        args.append(int(session_id))
    sql += " ORDER BY id ASC LIMIT ?"
    args.append(int(limit))
    with get_conn() as conn:
        rows = conn.execute(sql, args).fetchall()
    return [dict(r) for r in rows]


def list_turns_unsummarized_short(user_id: str, limit: int = 200,
                                  *, session_id: int | None = None) -> list[dict[str, Any]]:
    """获取尚未被短期总结消化的 turn（可选限定 session）."""
    sql = (
        "SELECT id, channel, user_text, assistant_text, "
        "       user_tokens, assistant_tokens, created_at, session_id "
        "FROM conversation_turn "
        "WHERE user_id = ? AND summarized_into_short_id IS NULL"
    )
    args: list[Any] = [user_id]
    if session_id is not None:
        sql += " AND session_id = ?"
        args.append(int(session_id))
    sql += " ORDER BY id ASC LIMIT ?"
    args.append(int(limit))
    with get_conn() as conn:
        rows = conn.execute(sql, args).fetchall()
    return [dict(r) for r in rows]


def list_turns_for_day(user_id: str, day_start_ts: float, day_end_ts: float) -> list[dict[str, Any]]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT id, channel, user_text, assistant_text, created_at "
            "FROM conversation_turn "
            "WHERE user_id = ? AND created_at >= ? AND created_at < ? "
            "ORDER BY id ASC",
            (user_id, day_start_ts, day_end_ts),
        ).fetchall()
    return [dict(r) for r in rows]


def mark_turns_short_summarized(turn_ids: Iterable[int], short_id: int) -> None:
    ids = [int(i) for i in turn_ids]
    if not ids:
        return
    with get_conn() as conn:
        conn.executemany(
            "UPDATE conversation_turn SET summarized_into_short_id = ? WHERE id = ?",
            [(short_id, i) for i in ids],
        )


def mark_turns_long_summarized(turn_ids: Iterable[int], long_id: int) -> None:
    ids = [int(i) for i in turn_ids]
    if not ids:
        return
    with get_conn() as conn:
        conn.executemany(
            "UPDATE conversation_turn SET summarized_into_long_id = ? WHERE id = ?",
            [(long_id, i) for i in ids],
        )


def total_unsummarized_tokens(user_id: str) -> int:
    """估算尚未被短期总结消化的总 token 数（用于阈值判断）."""
    with get_conn() as conn:
        row = conn.execute(
            "SELECT COALESCE(SUM(user_tokens + assistant_tokens), 0) AS t "
            "FROM conversation_turn "
            "WHERE user_id = ? AND summarized_into_short_id IS NULL",
            (user_id,),
        ).fetchone()
    return int(row["t"] or 0)


def clear_recent(user_id: str) -> None:
    """兼容旧 API：物理删除 24h 内的 turn（前端"清空最近对话"按钮用）."""
    cutoff = time.time() - 86400
    with get_conn() as conn:
        conn.execute(
            "DELETE FROM conversation_turn WHERE user_id = ? AND created_at >= ?",
            (user_id, cutoff),
        )


# 兼容旧 API：list_recent / record_chat
def list_recent(user_id: str, limit: int = 12) -> list[dict[str, Any]]:
    """按时间倒序返回最近 N 轮（前端记忆面板用）."""
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT user_text, assistant_text, created_at AS ts "
            "FROM conversation_turn "
            "WHERE user_id = ? "
            "ORDER BY id DESC LIMIT ?",
            (user_id, limit),
        ).fetchall()
    items = [
        {"user": r["user_text"], "assistant": r["assistant_text"], "ts": r["ts"]}
        for r in rows
    ]
    items.reverse()
    return items


def record_chat(user_id: str, user_text: str, assistant_text: str) -> None:
    """旧 API 兼容入口；新代码请直接用 ``append_turn``."""
    append_turn(user_id, user_text, assistant_text)
