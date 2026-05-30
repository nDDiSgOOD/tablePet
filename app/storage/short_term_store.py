"""短期记忆存储 / Short-term memory.

由 agent 在以下两种情况下生成：
1. **token 阈值触发**：``conversation_turn`` 中未总结的 token 数 ≥
   ``MEMORY_CONTEXT_BUDGET_TOKENS`` 时主动调 summarize。
2. **每日定时**：每天凌晨把当日剩余的 turn 也总结一遍。

短期记忆条目带 embedding（用于召回），但召回主要是长期记忆，
短期记忆的注入通常按"最近 N 条"全量加进 prompt。
"""

from __future__ import annotations

import json
import time
from typing import Any

from .db import get_conn
from .vector import decode_vector, encode_vector


def insert_short_term(
    user_id: str,
    *,
    summary: str,
    bullet_facts: list[str],
    window_start: float,
    window_end: float,
    token_count: int,
    embedding: list[float] | None = None,
    embed_model: str = "",
) -> int:
    now = time.time()
    blob = encode_vector(embedding) if embedding else None
    with get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO memory_short_term "
            "(user_id, summary, bullet_facts, window_start, window_end, "
            " token_count, created_at, embedding, embed_model) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                user_id,
                summary,
                json.dumps(bullet_facts, ensure_ascii=False),
                window_start,
                window_end,
                int(token_count),
                now,
                blob,
                embed_model,
            ),
        )
        return cur.lastrowid or 0


def list_recent_short_term(user_id: str, limit: int = 8) -> list[dict[str, Any]]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT id, summary, bullet_facts, window_start, window_end, "
            "       token_count, created_at, promoted "
            "FROM memory_short_term "
            "WHERE user_id = ? "
            "ORDER BY id DESC LIMIT ?",
            (user_id, limit),
        ).fetchall()
    items = []
    for r in rows:
        d = dict(r)
        try:
            d["bullet_facts"] = json.loads(d["bullet_facts"])
        except json.JSONDecodeError:
            d["bullet_facts"] = []
        items.append(d)
    items.reverse()
    return items


def list_unpromoted_short_term(user_id: str) -> list[dict[str, Any]]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT id, summary, bullet_facts, window_start, window_end, "
            "       token_count, created_at, embedding, embed_model "
            "FROM memory_short_term "
            "WHERE user_id = ? AND promoted = 0 "
            "ORDER BY id ASC",
            (user_id,),
        ).fetchall()
    items = []
    for r in rows:
        d = dict(r)
        d["embedding"] = decode_vector(d["embedding"])
        try:
            d["bullet_facts"] = json.loads(d["bullet_facts"])
        except json.JSONDecodeError:
            d["bullet_facts"] = []
        items.append(d)
    return items


def mark_short_term_promoted(short_ids: list[int]) -> None:
    if not short_ids:
        return
    with get_conn() as conn:
        conn.executemany(
            "UPDATE memory_short_term SET promoted = 1 WHERE id = ?",
            [(int(i),) for i in short_ids],
        )
