"""长期记忆存储 + 召回 / Long-term memory store with vector recall.

召回策略
========
1. **关键词预过滤**：用 SQLite ``LIKE`` 粗筛降低候选量（≤200 条）；
2. **向量重排**：对候选条目计算余弦相似度，取 topK；
3. **近期加权**：``last_recalled_at`` 越新分数加成越多（避免同样的高分条
   永远被召回，新记忆没机会出头）；
4. **重要度提权**：``importance`` 高的优先。

为什么不直接 ANN？
  - tablepet 数据规模小（一年估计 ≤5k 条长期记忆），线性扫够用；
  - 引入 faiss / hnswlib 会破坏 "零外部依赖（纯 SQLite）" 的简洁性；
  - 等真的 >50k 条再考虑迁移到 sqlite-vec / pgvector / qdrant。
"""

from __future__ import annotations

import json
import time
from typing import Any

from .db import get_conn
from .vector import cosine, decode_vector, encode_vector


def insert_long_term(
    user_id: str,
    *,
    title: str,
    summary: str,
    bullet_facts: list[str],
    importance: float,
    window_start: float,
    window_end: float,
    embedding: list[float] | None = None,
    embed_model: str = "",
) -> int:
    now = time.time()
    blob = encode_vector(embedding) if embedding else None
    with get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO memory_long_term "
            "(user_id, title, summary, bullet_facts, importance, "
            " window_start, window_end, created_at, embedding, embed_model) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                user_id,
                title,
                summary,
                json.dumps(bullet_facts, ensure_ascii=False),
                max(0.0, min(1.0, float(importance))),
                window_start,
                window_end,
                now,
                blob,
                embed_model,
            ),
        )
        return cur.lastrowid or 0


def _row_to_dict(row: Any) -> dict[str, Any]:
    d = dict(row)
    try:
        d["bullet_facts"] = json.loads(d.get("bullet_facts") or "[]")
    except json.JSONDecodeError:
        d["bullet_facts"] = []
    if "embedding" in d:
        d["embedding"] = decode_vector(d["embedding"])
    return d


def list_long_term(user_id: str, limit: int = 50) -> list[dict[str, Any]]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT id, title, summary, bullet_facts, importance, "
            "       window_start, window_end, created_at, recall_count, "
            "       last_recalled_at "
            "FROM memory_long_term "
            "WHERE user_id = ? "
            "ORDER BY id DESC LIMIT ?",
            (user_id, limit),
        ).fetchall()
    return [_row_to_dict(r) for r in rows]


def recall_by_vector(
    user_id: str,
    query_vec: list[float],
    *,
    keyword_hint: str = "",
    top_k: int = 5,
    candidate_limit: int = 200,
) -> list[dict[str, Any]]:
    """向量召回主入口。"""
    if not query_vec:
        return []
    with get_conn() as conn:
        if keyword_hint:
            like = f"%{keyword_hint[:40]}%"
            rows = conn.execute(
                "SELECT id, title, summary, bullet_facts, importance, "
                "       window_start, window_end, created_at, recall_count, "
                "       last_recalled_at, embedding "
                "FROM memory_long_term "
                "WHERE user_id = ? AND (summary LIKE ? OR title LIKE ?) "
                "ORDER BY importance DESC, id DESC LIMIT ?",
                (user_id, like, like, candidate_limit),
            ).fetchall()
            if len(rows) < 5:
                rows = conn.execute(
                    "SELECT id, title, summary, bullet_facts, importance, "
                    "       window_start, window_end, created_at, recall_count, "
                    "       last_recalled_at, embedding "
                    "FROM memory_long_term "
                    "WHERE user_id = ? "
                    "ORDER BY importance DESC, id DESC LIMIT ?",
                    (user_id, candidate_limit),
                ).fetchall()
        else:
            rows = conn.execute(
                "SELECT id, title, summary, bullet_facts, importance, "
                "       window_start, window_end, created_at, recall_count, "
                "       last_recalled_at, embedding "
                "FROM memory_long_term "
                "WHERE user_id = ? "
                "ORDER BY importance DESC, id DESC LIMIT ?",
                (user_id, candidate_limit),
            ).fetchall()

    now = time.time()
    scored: list[tuple[float, dict[str, Any]]] = []
    for r in rows:
        d = _row_to_dict(r)
        emb = d.pop("embedding", None)
        if not emb:
            continue
        sim = cosine(query_vec, emb)
        recency = 0.0
        if d.get("last_recalled_at"):
            age_days = max(0.0, (now - d["last_recalled_at"]) / 86400)
            recency = max(0.0, 0.1 - 0.005 * age_days)
        score = sim * 0.7 + d.get("importance", 0.5) * 0.2 + recency
        scored.append((score, d))

    scored.sort(key=lambda x: x[0], reverse=True)
    return [d for _s, d in scored[:top_k]]


def bump_recall(long_ids: list[int]) -> None:
    if not long_ids:
        return
    now = time.time()
    with get_conn() as conn:
        conn.executemany(
            "UPDATE memory_long_term "
            "SET recall_count = recall_count + 1, last_recalled_at = ? "
            "WHERE id = ?",
            [(now, int(i)) for i in long_ids],
        )
