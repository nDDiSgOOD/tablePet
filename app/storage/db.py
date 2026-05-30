"""SQLite 存储模块 / SQLite-backed persistence.

设计原则
========
1. **单一数据库**：``data/tablepet.db`` 集中存所有用户级状态。
2. **user_id 是主键**：根据你的诉求"用户通过什么方式访问都能有一样的体验"，
   所有数据按 ``user_id`` 隔离；``device_id`` 仅用于设备 telemetry，不再作为
   记忆/画像/宠物状态的隔离维度。
3. **WAL 模式**：开 ``journal_mode=WAL``，让多线程读写更顺畅
   （uvicorn worker + USB 桥接线程并发安全）。
4. **接口分层**：
   - ``db.py`` 只负责连接 / schema 初始化 / 上下文管理；
   - ``user_profile.py`` / ``pet_state.py`` / ``memory_store.py`` 是业务表的
     CRUD 包装，外部模块只通过它们读写。

表结构
======

::

    user_profile         (user_id PK, name, language, bio, city, since,
                          updated_at)
        固定字段画像。

    user_profile_custom  (user_id, key, value, updated_at, PK(user_id, key))
        自定义画像 key/value，用户在前端可以自由扩展。

    pet_state            (user_id PK, name, mood, mood_score, energy, exp,
                          level, last_interact_ts, since_ts)
        宠物当前状态。所有数值由后端 ``pet_state.apply_event`` 计算，
        前端只读 + 推送 feed/pet/play/sleep 事件。

    memory_fact          (id PK, user_id, fact, created_at)
        长期画像事实（用户说过的有用断言），有顺序，有索引 (user_id, id)。

    memory_recent        (id PK, user_id, user_text, assistant_text, ts)
        最近对话摘要，用于注入 prompt。
"""

from __future__ import annotations

import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from ..config import APP_DIR

DB_PATH: Path = APP_DIR / "data" / "tablepet.db"
DB_PATH.parent.mkdir(parents=True, exist_ok=True)


_SCHEDULE_FIELDS_NOTE = """
WAL + 索引说明
=============
- ``conversation_turn``：所有原始对话记录的真理源。三层记忆都从它派生：
    * 临时记忆 = ``WHERE created_at >= now - 24h``（动态视图）
    * 短期记忆 = 已被 summarize_to_short_term 总结过的窗口
    * 长期记忆 = 已被 daily/forced summarize_to_long_term 沉淀的窗口
  通过 summarized_into_short_id / summarized_into_long_id 标记“是否已被消化”
  避免重复总结。

- ``memory_short_term`` / ``memory_long_term``：总结后的结构化条目，带 embedding。
  embedding 用 BLOB 存（float32 列表 → bytes），sqlite 不擅长向量但 ≤10k 条
  线性扫足够快（参考 Reasonix 的 SemanticStore）。

- ``daily_summary``：日历视图直接读这张表。
- ``ai_user_profile``：AI 自己维护的用户画像，与用户填写的 user_profile 解耦。
"""

_SCHEMA = """
CREATE TABLE IF NOT EXISTS user_profile (
    user_id     TEXT PRIMARY KEY,
    name        TEXT DEFAULT '',
    language    TEXT DEFAULT 'zh-CN',
    bio         TEXT DEFAULT '',
    city        TEXT DEFAULT '',
    since       TEXT DEFAULT '',
    updated_at  REAL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS user_profile_custom (
    user_id     TEXT NOT NULL,
    key         TEXT NOT NULL,
    value       TEXT NOT NULL DEFAULT '',
    updated_at  REAL DEFAULT 0,
    PRIMARY KEY (user_id, key)
);

CREATE TABLE IF NOT EXISTS pet_state (
    user_id          TEXT PRIMARY KEY,
    name             TEXT DEFAULT '小桌',
    persona          TEXT DEFAULT '',                  -- 人设描述（用户编辑）
    tagline          TEXT DEFAULT '',                  -- 一句口头禅 / 自我介绍
    mood             TEXT DEFAULT 'neutral',
    mood_score       INTEGER DEFAULT 60,
    energy           INTEGER DEFAULT 70,
    exp              INTEGER DEFAULT 0,
    level            INTEGER DEFAULT 1,
    last_interact_ts REAL DEFAULT 0,
    since_ts         REAL DEFAULT 0,
    ai_notes         TEXT DEFAULT '',                  -- AI 总结写入的"最近主观感受"
    updated_at       REAL DEFAULT 0                    -- 用户最近一次编辑时间
);

-- 长期画像断言（用户手动添加 + agent 显式记忆）
CREATE TABLE IF NOT EXISTS memory_fact (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id     TEXT NOT NULL,
    fact        TEXT NOT NULL,
    created_at  REAL NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_memory_fact_user
    ON memory_fact(user_id, id);

-- 会话（session）：把零碎的 turn 按"一段连续对话"打包
-- - 三端共享同一个 active session
-- - Web 点清空时调 close_active_session() 总结并归档，然后开新 session
-- - USB / WiFi 不会主动关闭 session
CREATE TABLE IF NOT EXISTS conversation_session (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id     TEXT NOT NULL,
    started_at  REAL NOT NULL DEFAULT 0,
    closed_at   REAL DEFAULT NULL,                    -- NULL = 当前活跃会话
    title       TEXT NOT NULL DEFAULT '',             -- 由 AI 总结时填的简短标题
    summary     TEXT NOT NULL DEFAULT '',             -- 关闭时 LLM 写的会话级摘要
    turn_count  INTEGER NOT NULL DEFAULT 0,
    token_count INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_session_user_active
    ON conversation_session(user_id, closed_at);

-- 原始对话记录：所有 channel 的真实对话，带 channel + token 计数
-- 这是真理源，三层记忆都是它的派生视图
CREATE TABLE IF NOT EXISTS conversation_turn (
    id                       INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id                  TEXT    NOT NULL,
    session_id               INTEGER DEFAULT NULL,    -- 关联 conversation_session.id
    channel                  TEXT    NOT NULL DEFAULT '',
    user_text                TEXT    NOT NULL DEFAULT '',
    assistant_text           TEXT    NOT NULL DEFAULT '',
    user_tokens              INTEGER NOT NULL DEFAULT 0,
    assistant_tokens         INTEGER NOT NULL DEFAULT 0,
    created_at               REAL    NOT NULL DEFAULT 0,
    summarized_into_short_id INTEGER DEFAULT NULL,
    summarized_into_long_id  INTEGER DEFAULT NULL
);
CREATE INDEX IF NOT EXISTS idx_turn_user_time ON conversation_turn(user_id, created_at);
CREATE INDEX IF NOT EXISTS idx_turn_user_short_pending
    ON conversation_turn(user_id, summarized_into_short_id) WHERE summarized_into_short_id IS NULL;
-- idx_turn_session 在 ALTER TABLE 加 session_id 列之后再建（见 _ensure_schema）

-- 短期记忆：由 agent 总结产生，覆盖一段窗口
CREATE TABLE IF NOT EXISTS memory_short_term (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id       TEXT NOT NULL,
    summary       TEXT NOT NULL,
    bullet_facts  TEXT NOT NULL DEFAULT '[]',  -- JSON array
    window_start  REAL NOT NULL,
    window_end    REAL NOT NULL,
    token_count   INTEGER NOT NULL DEFAULT 0,
    created_at    REAL NOT NULL DEFAULT 0,
    embedding     BLOB DEFAULT NULL,
    embed_model   TEXT DEFAULT '',
    promoted      INTEGER NOT NULL DEFAULT 0   -- 1 表示已晋升到长期
);
CREATE INDEX IF NOT EXISTS idx_short_user_time
    ON memory_short_term(user_id, created_at);

-- 长期记忆：经过更深一轮总结的稳定记忆，可向量召回
CREATE TABLE IF NOT EXISTS memory_long_term (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id       TEXT NOT NULL,
    title         TEXT NOT NULL DEFAULT '',
    summary       TEXT NOT NULL,
    bullet_facts  TEXT NOT NULL DEFAULT '[]',
    importance    REAL NOT NULL DEFAULT 0.5,   -- 0~1
    window_start  REAL NOT NULL,
    window_end    REAL NOT NULL,
    created_at    REAL NOT NULL DEFAULT 0,
    last_recalled_at REAL DEFAULT 0,
    recall_count  INTEGER NOT NULL DEFAULT 0,
    embedding     BLOB DEFAULT NULL,
    embed_model   TEXT DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_long_user_time
    ON memory_long_term(user_id, created_at);
CREATE INDEX IF NOT EXISTS idx_long_user_imp
    ON memory_long_term(user_id, importance);

-- 每日总结（日历直接读）
CREATE TABLE IF NOT EXISTS daily_summary (
    user_id     TEXT NOT NULL,
    day         TEXT NOT NULL,                 -- YYYY-MM-DD（本地时间）
    summary     TEXT NOT NULL,
    bullet_facts TEXT NOT NULL DEFAULT '[]',
    turn_count  INTEGER NOT NULL DEFAULT 0,
    token_count INTEGER NOT NULL DEFAULT 0,
    mood_avg    REAL DEFAULT NULL,             -- 当日心情均值（来自 pet_state 趋势）
    mood        TEXT DEFAULT '',               -- 当日代表性心情标签（LLM 抽取）
    created_at  REAL NOT NULL DEFAULT 0,
    PRIMARY KEY (user_id, day)
);

-- AI 维护的用户画像：单行，由 update_ai_profile 周期性重写
CREATE TABLE IF NOT EXISTS ai_user_profile (
    user_id      TEXT PRIMARY KEY,
    description  TEXT NOT NULL DEFAULT '',
    traits       TEXT NOT NULL DEFAULT '[]',
    interests    TEXT NOT NULL DEFAULT '[]',
    relationship TEXT NOT NULL DEFAULT '{}',
    updated_at   REAL NOT NULL DEFAULT 0,
    source_window_end REAL DEFAULT 0
);

-- UI 设置（主题、背景图、布局偏好等）按 user_id + key 存
-- value 用 TEXT 存 JSON，前端读出来 JSON.parse 即可
CREATE TABLE IF NOT EXISTS ui_settings (
    user_id     TEXT NOT NULL,
    key         TEXT NOT NULL,
    value       TEXT NOT NULL DEFAULT '',
    updated_at  REAL NOT NULL DEFAULT 0,
    PRIMARY KEY (user_id, key)
);

-- LLM 账号 / Provider 配置：可以存多个，active=1 的那条是当前生效的
CREATE TABLE IF NOT EXISTS llm_account (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id      TEXT NOT NULL,
    provider     TEXT NOT NULL DEFAULT 'deepseek', -- deepseek / openai / 其它
    label        TEXT NOT NULL DEFAULT '',         -- 用户起的别名
    base_url     TEXT NOT NULL DEFAULT '',
    api_key      TEXT NOT NULL DEFAULT '',
    chat_model   TEXT NOT NULL DEFAULT '',
    summary_model TEXT NOT NULL DEFAULT '',
    balance      REAL NOT NULL DEFAULT 0,          -- 用户手动录入
    balance_currency TEXT NOT NULL DEFAULT 'CNY',
    is_active    INTEGER NOT NULL DEFAULT 0,
    created_at   REAL NOT NULL DEFAULT 0,
    updated_at   REAL NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_llm_account_user_active
    ON llm_account(user_id, is_active);

-- LLM 调用流水（用量统计 / 趋势图）
CREATE TABLE IF NOT EXISTS llm_usage (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id           TEXT NOT NULL,
    account_id        INTEGER DEFAULT NULL,
    purpose           TEXT NOT NULL DEFAULT 'chat',  -- chat / summary / greeting / pet_tick / ai_profile / daily
    model             TEXT NOT NULL DEFAULT '',
    prompt_tokens     INTEGER NOT NULL DEFAULT 0,
    completion_tokens INTEGER NOT NULL DEFAULT 0,
    total_tokens      INTEGER NOT NULL DEFAULT 0,
    elapsed_ms        INTEGER NOT NULL DEFAULT 0,
    cost              REAL NOT NULL DEFAULT 0,        -- 估算的费用（可空）
    turn_id           INTEGER DEFAULT NULL,
    session_id        INTEGER DEFAULT NULL,
    ok                INTEGER NOT NULL DEFAULT 1,
    err               TEXT NOT NULL DEFAULT '',
    created_at        REAL NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_usage_user_time ON llm_usage(user_id, created_at);
CREATE INDEX IF NOT EXISTS idx_usage_user_purpose ON llm_usage(user_id, purpose);
"""


_lock = threading.Lock()
_initialized = False


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, timeout=10, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def _ensure_schema(conn: sqlite3.Connection) -> None:
    global _initialized
    if _initialized:
        return
    with _lock:
        if _initialized:
            return
        conn.executescript(_SCHEMA)
        # 老库迁移：缺列就补（CREATE TABLE IF NOT EXISTS 不会自动加新列）
        _migrate_add_columns(conn, "pet_state", {
            "persona": "TEXT DEFAULT ''",
            "tagline": "TEXT DEFAULT ''",
            "updated_at": "REAL DEFAULT 0",
        })
        _migrate_add_columns(conn, "conversation_turn", {
            "session_id": "INTEGER DEFAULT NULL",
            "latency_ms": "INTEGER DEFAULT 0",
            "model": "TEXT DEFAULT ''",
        })
        _migrate_add_columns(conn, "daily_summary", {
            "mood": "TEXT DEFAULT ''",  # 当日代表性心情：happy/neutral/sleepy/excited/sick/hungry
        })
        # session_id 列保证存在后再建索引
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_turn_session "
            "ON conversation_turn(session_id, id)"
        )
        conn.commit()
        _initialized = True


def _migrate_add_columns(
    conn: sqlite3.Connection,
    table: str,
    columns: dict[str, str],
) -> None:
    """给已存在的表补字段；已有则跳过."""
    existing = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}
    for col, type_def in columns.items():
        if col not in existing:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {type_def}")


@contextmanager
def get_conn() -> Iterator[sqlite3.Connection]:
    """获取一个 sqlite 连接，离开作用域时自动 commit + close."""
    conn = _connect()
    try:
        _ensure_schema(conn)
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
