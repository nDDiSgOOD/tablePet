"""长期画像断言（memory_fact）/ User-asserted long-term facts.

注意：本模块只管 ``memory_fact`` 表（用户在前端"记忆管理"里手动加的事实，
agent 也可以显式追加）。**对话原始记录** 已迁移到
``app.storage.conversation_store``，新代码请用那边。
"""

from __future__ import annotations

import time

from .db import get_conn

MAX_FACTS = 24


def list_facts(user_id: str) -> list[str]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT fact FROM memory_fact WHERE user_id = ? ORDER BY id ASC",
            (user_id,),
        ).fetchall()
    return [r["fact"] for r in rows]


def add_fact(user_id: str, fact: str) -> None:
    fact = fact.strip()[:160]
    if not fact:
        return
    now = time.time()
    with get_conn() as conn:
        existing = conn.execute(
            "SELECT id FROM memory_fact WHERE user_id = ? AND fact = ?",
            (user_id, fact),
        ).fetchone()
        if existing:
            return
        conn.execute(
            "INSERT INTO memory_fact (user_id, fact, created_at) VALUES (?, ?, ?)",
            (user_id, fact, now),
        )
        conn.execute(
            "DELETE FROM memory_fact WHERE user_id = ? AND id NOT IN ("
            "  SELECT id FROM memory_fact WHERE user_id = ? "
            "  ORDER BY id DESC LIMIT ?"
            ")",
            (user_id, user_id, MAX_FACTS),
        )


def delete_fact_by_index(user_id: str, index: int) -> bool:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT id FROM memory_fact WHERE user_id = ? ORDER BY id ASC",
            (user_id,),
        ).fetchall()
        if index < 0 or index >= len(rows):
            return False
        conn.execute("DELETE FROM memory_fact WHERE id = ?", (rows[index]["id"],))
        return True
