"""Storage 层 / Persistent storage facade.

记忆体系分层一览
================
- ``user_profile`` / ``user_profile_custom``：用户填写的画像（前端可编辑）
- ``ai_user_profile``                       ：AI 自己总结的用户画像
- ``pet_state``                             ：宠物状态（前端 + AI 共同维护）
- ``conversation_turn``                     ：原始对话（真理源）
- ``memory_fact``                           ：长期画像断言
- ``memory_short_term``                     ：短期记忆（窗口总结）
- ``memory_long_term``                      ：长期记忆（向量召回）
- ``daily_summary``                         ：每日总结（日历用）
"""

from __future__ import annotations

# user profile (用户填写)
from .user_profile import (
    delete_custom_field,
    get_profile,
    list_custom_fields,
    set_custom_field,
    update_profile,
)

# pet state
from .pet_state import (
    DEFAULT_PET,
    apply_event,
    get_pet_state,
    update_pet_state,
)

# 长期画像断言（用户/agent 显式记忆的事实）
from .memory_store import (
    add_fact,
    delete_fact_by_index,
    list_facts,
)

# 原始对话
from .conversation_store import (
    CHANNEL_SYSTEM_EVENT,
    append_system_event,
    append_turn,
    clear_recent,
    delete_all_turns,
    delete_turn,
    list_recent,
    list_turns_for_day,
    list_turns_paged,
    list_turns_since,
    list_turns_unsummarized_short,
    mark_turns_long_summarized,
    mark_turns_short_summarized,
    record_chat,
    total_unsummarized_tokens,
)

# 短期 / 长期记忆
from .short_term_store import (
    insert_short_term,
    list_recent_short_term,
    list_unpromoted_short_term,
    mark_short_term_promoted,
)
from .long_term_store import (
    bump_recall,
    insert_long_term,
    list_long_term,
    recall_by_vector,
)

# 每日总结 + AI 画像
from .insight_store import (
    get_ai_profile,
    get_daily_summary,
    list_daily_summary_days,
    upsert_ai_profile,
    upsert_daily_summary,
)

# UI 设置
from .ui_settings import (
    delete_setting,
    get_setting,
    list_settings,
    set_setting,
)

# 会话
from .session_store import (
    close_active_session,
    delete_session,
    get_or_create_active,
    list_sessions,
    list_turns_by_session,
    reopen_session,
    update_session_meta,
)

# LLM 账号 + 用量
from .llm_account import (
    delete_account,
    get_account,
    get_active_account,
    list_accounts,
    record_usage,
    set_active_account,
    upsert_account,
    usage_by_day,
    usage_daily,
    usage_today,
)

__all__ = [
    # user profile
    "get_profile",
    "update_profile",
    "list_custom_fields",
    "set_custom_field",
    "delete_custom_field",
    # pet state
    "DEFAULT_PET",
    "get_pet_state",
    "update_pet_state",
    "apply_event",
    # facts
    "list_facts",
    "add_fact",
    "delete_fact_by_index",
    # conversation
    "CHANNEL_SYSTEM_EVENT",
    "append_turn",
    "append_system_event",
    "list_turns_since",
    "list_turns_unsummarized_short",
    "list_turns_for_day",
    "list_turns_paged",
    "delete_turn",
    "delete_all_turns",
    "mark_turns_short_summarized",
    "mark_turns_long_summarized",
    "total_unsummarized_tokens",
    "list_recent",
    "record_chat",
    "clear_recent",
    # short term
    "insert_short_term",
    "list_recent_short_term",
    "list_unpromoted_short_term",
    "mark_short_term_promoted",
    # long term
    "insert_long_term",
    "list_long_term",
    "recall_by_vector",
    "bump_recall",
    # insight
    "get_daily_summary",
    "list_daily_summary_days",
    "upsert_daily_summary",
    "get_ai_profile",
    "upsert_ai_profile",
    # ui settings
    "get_setting",
    "set_setting",
    "delete_setting",
    "list_settings",
    # session
    "get_or_create_active",
    "close_active_session",
    "list_sessions",
    "list_turns_by_session",
    "delete_session",
    "reopen_session",
    "update_session_meta",
    # llm account / usage
    "list_accounts",
    "get_active_account",
    "get_account",
    "upsert_account",
    "set_active_account",
    "delete_account",
    "record_usage",
    "usage_today",
    "usage_daily",
    "usage_by_day",
]
