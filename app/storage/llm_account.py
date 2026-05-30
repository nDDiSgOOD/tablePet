"""LLM 账号 + 用量流水 storage.

设计
====
- ``llm_account``：可以存多个 provider/api_key 配置，``is_active=1`` 那条
  是当前生效的；切换是"把另一条置 1，其他置 0"。
- ``llm_usage``：每次模型调用都写一行（无论成功失败），用于：
  * 单 turn 旁边显示 token / 耗时
  * 日历每天用量
  * 账户页趋势图
"""

from __future__ import annotations

import time
from typing import Any

from .db import get_conn


# ---------------------------------------------------------------------------
# llm_account
# ---------------------------------------------------------------------------
def list_accounts(user_id: str) -> list[dict[str, Any]]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT id, provider, label, base_url, api_key, chat_model, "
            "       summary_model, balance, balance_currency, is_active, "
            "       created_at, updated_at "
            "FROM llm_account WHERE user_id = ? "
            "ORDER BY is_active DESC, id DESC",
            (user_id,),
        ).fetchall()
    items = []
    for r in rows:
        d = dict(r)
        # 显示用：api_key 部分脱敏
        d["api_key_masked"] = _mask(d.get("api_key") or "")
        items.append(d)
    return items


def get_active_account(user_id: str) -> dict[str, Any] | None:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT id, provider, label, base_url, api_key, chat_model, "
            "       summary_model, balance, balance_currency, is_active, "
            "       created_at, updated_at "
            "FROM llm_account WHERE user_id = ? AND is_active = 1 LIMIT 1",
            (user_id,),
        ).fetchone()
    return dict(row) if row else None


def get_account(user_id: str, account_id: int) -> dict[str, Any] | None:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM llm_account WHERE user_id = ? AND id = ?",
            (user_id, int(account_id)),
        ).fetchone()
    return dict(row) if row else None


def upsert_account(user_id: str, payload: dict[str, Any], *, account_id: int | None = None) -> int:
    now = time.time()
    fields = {
        "provider": str(payload.get("provider") or "deepseek")[:40],
        "label": str(payload.get("label") or "")[:60],
        "base_url": str(payload.get("base_url") or "")[:300],
        "api_key": str(payload.get("api_key") or "")[:300],
        "chat_model": str(payload.get("chat_model") or "")[:80],
        "summary_model": str(payload.get("summary_model") or "")[:80],
        "balance": float(payload.get("balance") or 0),
        "balance_currency": str(payload.get("balance_currency") or "CNY")[:10],
    }
    with get_conn() as conn:
        if account_id:
            conn.execute(
                "UPDATE llm_account SET "
                "provider=?, label=?, base_url=?, api_key=?, chat_model=?, "
                "summary_model=?, balance=?, balance_currency=?, updated_at=? "
                "WHERE id=? AND user_id=?",
                (*fields.values(), now, int(account_id), user_id),
            )
            return int(account_id)
        cur = conn.execute(
            "INSERT INTO llm_account "
            "(user_id, provider, label, base_url, api_key, chat_model, "
            " summary_model, balance, balance_currency, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (user_id, *fields.values(), now, now),
        )
        new_id = cur.lastrowid or 0
        # 第一条自动激活
        active = conn.execute(
            "SELECT COUNT(*) AS c FROM llm_account WHERE user_id = ? AND is_active = 1",
            (user_id,),
        ).fetchone()
        if (active["c"] or 0) == 0:
            conn.execute("UPDATE llm_account SET is_active = 1 WHERE id = ?", (new_id,))
        return new_id


def set_active_account(user_id: str, account_id: int) -> bool:
    with get_conn() as conn:
        owner = conn.execute(
            "SELECT id FROM llm_account WHERE user_id = ? AND id = ?",
            (user_id, int(account_id)),
        ).fetchone()
        if not owner:
            return False
        conn.execute("UPDATE llm_account SET is_active = 0 WHERE user_id = ?", (user_id,))
        conn.execute("UPDATE llm_account SET is_active = 1 WHERE id = ?", (int(account_id),))
        return True


def delete_account(user_id: str, account_id: int) -> bool:
    with get_conn() as conn:
        cur = conn.execute(
            "DELETE FROM llm_account WHERE user_id = ? AND id = ?",
            (user_id, int(account_id)),
        )
        return cur.rowcount > 0


def _mask(key: str) -> str:
    if not key:
        return ""
    if len(key) <= 10:
        return key[:2] + "***"
    return key[:6] + "***" + key[-4:]


# ---------------------------------------------------------------------------
# llm_usage
# ---------------------------------------------------------------------------
def record_usage(
    user_id: str,
    *,
    purpose: str,
    model: str,
    prompt_tokens: int = 0,
    completion_tokens: int = 0,
    elapsed_ms: int = 0,
    cost: float = 0.0,
    turn_id: int | None = None,
    session_id: int | None = None,
    account_id: int | None = None,
    ok: bool = True,
    err: str = "",
) -> int:
    total = int(prompt_tokens) + int(completion_tokens)
    now = time.time()
    with get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO llm_usage "
            "(user_id, account_id, purpose, model, prompt_tokens, completion_tokens, "
            " total_tokens, elapsed_ms, cost, turn_id, session_id, ok, err, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                user_id,
                int(account_id) if account_id else None,
                purpose,
                model,
                int(prompt_tokens),
                int(completion_tokens),
                total,
                int(elapsed_ms),
                float(cost),
                int(turn_id) if turn_id else None,
                int(session_id) if session_id else None,
                1 if ok else 0,
                err[:300],
                now,
            ),
        )
        return cur.lastrowid or 0


def usage_today(user_id: str) -> dict[str, Any]:
    """今天 0 点至今的累计."""
    import datetime as _dt
    now = time.time()
    today_start = _dt.datetime.fromtimestamp(now).replace(
        hour=0, minute=0, second=0, microsecond=0
    ).timestamp()
    with get_conn() as conn:
        row = conn.execute(
            "SELECT COALESCE(SUM(total_tokens),0) AS t, "
            "       COALESCE(SUM(prompt_tokens),0) AS pt, "
            "       COALESCE(SUM(completion_tokens),0) AS ct, "
            "       COALESCE(SUM(cost),0) AS c, "
            "       COUNT(*) AS n "
            "FROM llm_usage WHERE user_id = ? AND created_at >= ?",
            (user_id, today_start),
        ).fetchone()
    return {
        "tokens": int(row["t"] or 0),
        "prompt_tokens": int(row["pt"] or 0),
        "completion_tokens": int(row["ct"] or 0),
        "cost": float(row["c"] or 0),
        "calls": int(row["n"] or 0),
    }


def usage_daily(user_id: str, days: int = 30) -> list[dict[str, Any]]:
    """近 N 天每日 token 用量."""
    cutoff = time.time() - days * 86400
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT strftime('%Y-%m-%d', created_at, 'unixepoch', 'localtime') AS day, "
            "       SUM(total_tokens) AS tokens, SUM(cost) AS cost, COUNT(*) AS calls "
            "FROM llm_usage WHERE user_id = ? AND created_at >= ? "
            "GROUP BY day ORDER BY day ASC",
            (user_id, cutoff),
        ).fetchall()
    return [
        {
            "day": r["day"],
            "tokens": int(r["tokens"] or 0),
            "cost": float(r["cost"] or 0),
            "calls": int(r["calls"] or 0),
        }
        for r in rows
    ]


def usage_by_day(user_id: str, day: str) -> dict[str, Any]:
    """指定 YYYY-MM-DD（本地）的用量."""
    with get_conn() as conn:
        row = conn.execute(
            "SELECT COALESCE(SUM(total_tokens),0) AS t, "
            "       COALESCE(SUM(cost),0) AS c, COUNT(*) AS n "
            "FROM llm_usage WHERE user_id = ? "
            "AND strftime('%Y-%m-%d', created_at, 'unixepoch', 'localtime') = ?",
            (user_id, day),
        ).fetchone()
    return {
        "day": day,
        "tokens": int(row["t"] or 0),
        "cost": float(row["c"] or 0),
        "calls": int(row["n"] or 0),
    }
