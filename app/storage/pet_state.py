"""Pet state 存储 / Pet state CRUD + 事件计算.

数值规则
========
- ``mood_score`` / ``energy`` / ``exp``: 0~100 之间的整数。
- ``level``: 由 ``exp`` 自动衍生（每 100 EXP 升一级，``exp`` 自动 mod 100）。
- ``mood``: happy / neutral / sleepy / hungry / excited / sick，
  按 ``mood_score`` 与 ``energy`` 自动推导。

事件
====
``apply_event(user_id, event)`` 接受预定义事件并计算新状态：

============  ====================================================
event         效果
============  ====================================================
feed          energy +5
pet           mood_score +3
play          energy -2（经验不再增加 —— 经验只能靠陪伴聊天）
sleep         energy +20, mood_score +1
chat          mood_score +1, exp = round(base * mood_factor * energy_factor)
============  ====================================================

陪伴系数
========
- ``base = 2``（一次有效对话基础经验）
- ``mood_factor``: 心情 ≥85 → 1.5；65~84 → 1.2；30~64 → 1.0；<30 → 0.6
- ``energy_factor``: 活力 ≥70 → 1.2；30~69 → 1.0；<30 → 0.5
- 最终 EXP = ``round(base * mood_factor * energy_factor)``，最低 1。
"""

from __future__ import annotations

import time
from typing import Any

from .db import get_conn

DEFAULT_PET: dict[str, Any] = {
    "name": "小桌",
    "persona": "",
    "tagline": "",
    "mood": "neutral",
    "mood_score": 60,
    "energy": 70,
    "exp": 0,
    "level": 1,
    "last_interact_ts": 0.0,
    "since_ts": 0.0,
    "ai_notes": "",
    "updated_at": 0.0,
}

_EVENT_DELTA = {
    "feed":  {"energy": 5},
    "pet":   {"mood_score": 3},
    "play":  {"energy": -2},
    "sleep": {"energy": 20, "mood_score": 1},
    # chat 的 exp 不在静态表里 —— 它是 mood/energy 的函数，apply_event 里动态算
    "chat":  {"mood_score": 1},
}

CHAT_EXP_BASE = 2


def _chat_exp_gain(mood_score: int, energy: int) -> int:
    """聊一次能涨多少经验：基础值 × 心情系数 × 活力系数，最少 1."""
    if mood_score >= 85:
        m_factor = 1.5
    elif mood_score >= 65:
        m_factor = 1.2
    elif mood_score >= 30:
        m_factor = 1.0
    else:
        m_factor = 0.6
    if energy >= 70:
        e_factor = 1.2
    elif energy >= 30:
        e_factor = 1.0
    else:
        e_factor = 0.5
    return max(1, round(CHAT_EXP_BASE * m_factor * e_factor))


def _clamp(value: int, lo: int = 0, hi: int = 100) -> int:
    return max(lo, min(hi, int(value)))


def _derive_mood(energy: int, mood_score: int) -> str:
    if energy < 20:
        return "sleepy"
    if mood_score >= 85:
        return "excited"
    if mood_score >= 65:
        return "happy"
    if mood_score < 30:
        return "sick"
    return "neutral"


def _ensure_row(conn, user_id: str) -> None:
    now = time.time()
    conn.execute(
        "INSERT OR IGNORE INTO pet_state "
        "(user_id, name, mood, mood_score, energy, exp, level, "
        " last_interact_ts, since_ts) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            user_id,
            DEFAULT_PET["name"],
            DEFAULT_PET["mood"],
            DEFAULT_PET["mood_score"],
            DEFAULT_PET["energy"],
            DEFAULT_PET["exp"],
            DEFAULT_PET["level"],
            now,
            now,
        ),
    )


def get_pet_state(user_id: str) -> dict[str, Any]:
    with get_conn() as conn:
        _ensure_row(conn, user_id)
        row = conn.execute(
            "SELECT name, "
            "       COALESCE(persona, '') AS persona, "
            "       COALESCE(tagline, '') AS tagline, "
            "       mood, mood_score, energy, exp, level, "
            "       last_interact_ts, since_ts, "
            "       COALESCE(ai_notes, '') AS ai_notes, "
            "       COALESCE(updated_at, 0) AS updated_at "
            "FROM pet_state WHERE user_id = ?",
            (user_id,),
        ).fetchone()
    state = dict(row)
    # "已陪伴 N 天" —— 用日历日跨度而不是 86400 整除。
    # 老逻辑 (now - since_ts) // 86400 在不满 24h 时为 0，会让用户
    # 第一天打开 dashboard 永远看到 "0 天"，违反中文常识（首日 = 第 1 天）。
    # 现在按本地日期跨度算：首次创建当天 = 第 1 天，明天 = 第 2 天，依此类推。
    since_ts = float(state.get("since_ts") or time.time())
    try:
        since_date = time.localtime(since_ts)
        today_date = time.localtime(time.time())
        # struct_time → date ordinal（年 * 366 + yday 粗算就够）
        since_ord = since_date.tm_year * 366 + since_date.tm_yday
        today_ord = today_date.tm_year * 366 + today_date.tm_yday
        days = max(1, today_ord - since_ord + 1)
    except (TypeError, ValueError):
        days = 1
    state["days"] = days
    return state


def update_pet_state(user_id: str, **fields: Any) -> dict[str, Any]:
    """直接覆盖字段。一般业务调用 ``apply_event``，这里给管理面板 / AI tick 用.

    若涉及 *用户编辑* 类字段（name / persona / tagline），会顺手把
    ``updated_at`` 刷成当前时间，让 prompt 能引用"主人最近修改了我的简介…"。
    """
    allowed = {"name", "persona", "tagline", "mood", "mood_score",
               "energy", "exp", "level", "ai_notes"}
    cleaned = {k: v for k, v in fields.items() if k in allowed}
    if not cleaned:
        return get_pet_state(user_id)
    user_edit_fields = {"name", "persona", "tagline"}
    bump_updated_at = any(k in cleaned for k in user_edit_fields)
    if bump_updated_at:
        cleaned["updated_at"] = time.time()
    with get_conn() as conn:
        _ensure_row(conn, user_id)
        sets = ", ".join(f"{k} = ?" for k in cleaned)
        params = list(cleaned.values()) + [user_id]
        conn.execute(f"UPDATE pet_state SET {sets} WHERE user_id = ?", params)
    return get_pet_state(user_id)


def apply_event(user_id: str, event: str) -> dict[str, Any]:
    """根据预定义事件更新数值并返回新状态."""
    delta = _EVENT_DELTA.get(event)
    if delta is None:
        raise ValueError(f"unknown pet event: {event}")
    now = time.time()
    with get_conn() as conn:
        _ensure_row(conn, user_id)
        row = conn.execute(
            "SELECT mood_score, energy, exp, level FROM pet_state "
            "WHERE user_id = ?",
            (user_id,),
        ).fetchone()
        mood_score = _clamp(row["mood_score"] + delta.get("mood_score", 0))
        energy = _clamp(row["energy"] + delta.get("energy", 0))
        # exp 只对 "chat" 事件生效，且按"互动前"的 mood/energy 决定收益
        # 这样 mood/energy 越好，每次聊天经验越多 —— 真正的"陪伴"
        exp_gain = (
            _chat_exp_gain(int(row["mood_score"]), int(row["energy"]))
            if event == "chat"
            else int(delta.get("exp", 0))
        )
        exp = row["exp"] + exp_gain
        level = row["level"]
        while exp >= 100:
            exp -= 100
            level += 1
        mood = _derive_mood(energy, mood_score)
        conn.execute(
            "UPDATE pet_state SET mood_score = ?, energy = ?, exp = ?, "
            "level = ?, mood = ?, last_interact_ts = ? WHERE user_id = ?",
            (mood_score, energy, exp, level, mood, now, user_id),
        )
    return get_pet_state(user_id)
