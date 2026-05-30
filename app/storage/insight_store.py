"""每日总结 + AI 用户画像 存储."""

from __future__ import annotations

import json
import time
from typing import Any

from .db import get_conn


# ---------------------------------------------------------------------------
# 每日总结 / Daily summary
# ---------------------------------------------------------------------------
def upsert_daily_summary(
    user_id: str,
    day: str,
    *,
    summary: str,
    bullet_facts: list[str],
    turn_count: int,
    token_count: int,
    mood_avg: float | None = None,
    mood: str = "",
) -> None:
    now = time.time()
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO daily_summary "
            "(user_id, day, summary, bullet_facts, turn_count, token_count, "
            " mood_avg, mood, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(user_id, day) DO UPDATE SET "
            "  summary = excluded.summary, "
            "  bullet_facts = excluded.bullet_facts, "
            "  turn_count = excluded.turn_count, "
            "  token_count = excluded.token_count, "
            "  mood_avg = excluded.mood_avg, "
            "  mood = excluded.mood, "
            "  created_at = excluded.created_at",
            (
                user_id,
                day,
                summary,
                json.dumps(bullet_facts, ensure_ascii=False),
                int(turn_count),
                int(token_count),
                mood_avg,
                mood[:20],
                now,
            ),
        )


def get_daily_summary(user_id: str, day: str) -> dict[str, Any] | None:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT day, summary, bullet_facts, turn_count, token_count, "
            "       mood_avg, COALESCE(mood, '') AS mood, created_at "
            "FROM daily_summary WHERE user_id = ? AND day = ?",
            (user_id, day),
        ).fetchone()
    if not row:
        return None
    d = dict(row)
    try:
        d["bullet_facts"] = json.loads(d["bullet_facts"])
    except json.JSONDecodeError:
        d["bullet_facts"] = []
    return d


def list_daily_summary_days(
    user_id: str,
    *,
    start_day: str | None = None,
    end_day: str | None = None,
) -> list[dict[str, Any]]:
    """日历视图：返回区间内"有总结的所有日期 + 简要预览"."""
    sql = (
        "SELECT day, turn_count, token_count, mood_avg, "
        "       COALESCE(mood, '') AS mood, created_at, "
        "       SUBSTR(summary, 1, 80) AS preview "
        "FROM daily_summary WHERE user_id = ?"
    )
    args: list[Any] = [user_id]
    if start_day:
        sql += " AND day >= ?"
        args.append(start_day)
    if end_day:
        sql += " AND day <= ?"
        args.append(end_day)
    sql += " ORDER BY day DESC LIMIT 366"
    with get_conn() as conn:
        rows = conn.execute(sql, args).fetchall()
    return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# AI 用户画像 / AI-derived user profile
# ---------------------------------------------------------------------------
def get_ai_profile(user_id: str) -> dict[str, Any]:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT description, traits, interests, relationship, "
            "       updated_at, source_window_end "
            "FROM ai_user_profile WHERE user_id = ?",
            (user_id,),
        ).fetchone()
    if not row:
        return {
            "description": "",
            "traits": [],
            "interests": [],
            "relationship": {},
            "updated_at": 0.0,
            "source_window_end": 0.0,
        }
    d = dict(row)
    try:
        d["traits"] = json.loads(d["traits"] or "[]")
        d["interests"] = json.loads(d["interests"] or "[]")
        d["relationship"] = json.loads(d["relationship"] or "{}")
    except json.JSONDecodeError:
        d["traits"], d["interests"], d["relationship"] = [], [], {}
    return d


def upsert_ai_profile(
    user_id: str,
    *,
    description: str,
    traits: list[str],
    interests: list[str],
    relationship: dict[str, Any],
    source_window_end: float = 0.0,
) -> dict[str, Any]:
    now = time.time()
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO ai_user_profile "
            "(user_id, description, traits, interests, relationship, "
            " updated_at, source_window_end) "
            "VALUES (?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(user_id) DO UPDATE SET "
            "  description = excluded.description, "
            "  traits = excluded.traits, "
            "  interests = excluded.interests, "
            "  relationship = excluded.relationship, "
            "  updated_at = excluded.updated_at, "
            "  source_window_end = excluded.source_window_end",
            (
                user_id,
                description,
                json.dumps(traits, ensure_ascii=False),
                json.dumps(interests, ensure_ascii=False),
                json.dumps(relationship, ensure_ascii=False),
                now,
                source_window_end,
            ),
        )
    return get_ai_profile(user_id)
