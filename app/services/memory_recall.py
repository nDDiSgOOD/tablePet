"""记忆召回 / Memory recall.

负责把"临时 / 短期 / 长期 / 画像 / 状态"五个维度的上下文打包给 prompt。

布局
====
返回的 ``MemoryContext`` 中:

  ephemeral:        最近 24h 全部 turn（用户允许的话尽量全量）
  short_term:       最近 N 条短期记忆摘要
  long_term:        基于 query 召回的 topK 长期记忆
  user_profile:     用户填写 + AI 总结合并
  pet_state:        宠物状态
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

from ..config import (
    MEMORY_EPHEMERAL_WINDOW_HOURS,
    MEMORY_LONG_TERM_TOPK,
    MEMORY_SHORT_TERM_INJECT_MAX,
)
from ..storage import (
    get_ai_profile,
    get_pet_state,
    get_profile,
    list_facts,
    list_recent_short_term,
    list_turns_since,
    recall_by_vector,
)
from .embedding import embed_text_safe


def _humanize_ts(ts: float) -> str:
    """把 unix 时间戳渲染成 '今天 14:32' / '昨天 21:05' / '2026-05-30 09:07'."""
    if not ts or ts <= 0:
        return "未知"
    now = time.time()
    diff = now - ts
    try:
        local = time.localtime(ts)
        local_now = time.localtime(now)
    except (TypeError, ValueError):
        return "未知"
    same_day = (
        local.tm_year == local_now.tm_year
        and local.tm_yday == local_now.tm_yday
    )
    if same_day:
        return time.strftime("今天 %H:%M", local)
    if 0 < diff < 2 * 86400 and local.tm_yday == (local_now.tm_yday - 1):
        return time.strftime("昨天 %H:%M", local)
    return time.strftime("%Y-%m-%d %H:%M", local)


@dataclass
class MemoryContext:
    ephemeral_turns: list[dict[str, Any]] = field(default_factory=list)
    short_term: list[dict[str, Any]] = field(default_factory=list)
    long_term: list[dict[str, Any]] = field(default_factory=list)
    facts: list[str] = field(default_factory=list)
    user_profile: dict[str, Any] = field(default_factory=dict)
    ai_profile: dict[str, Any] = field(default_factory=dict)
    pet_state: dict[str, Any] = field(default_factory=dict)

    def to_messages(self, *, include_ephemeral: bool = True) -> list[dict[str, str]]:
        """把上下文打包成 OpenAI messages 形式（除 system 之外的 user/assistant 历史）。

        特殊处理：channel == "system_event" 的 turn 表示一条由网关代为
        叙述的系统事件（如"用户刚把名字从 X 改成 Y"），渲染成 ``role="system"``
        让 AI "亲历"变更，避免它把现在的资料当成自己以前认错。
        """
        out: list[dict[str, str]] = []
        if include_ephemeral:
            for t in self.ephemeral_turns:
                if t.get("channel") == "system_event":
                    note = t.get("assistant_text") or ""
                    if note:
                        out.append({"role": "system", "content": note})
                    continue
                if t.get("user_text"):
                    out.append({"role": "user", "content": t["user_text"]})
                if t.get("assistant_text"):
                    out.append({"role": "assistant", "content": t["assistant_text"]})
        return out

    def to_system_block(self) -> str:
        """折叠成一段 system 文本，用作 prompt 上半部分."""
        sections: list[str] = []

        # 【你的当前身份】放最前面，是 LLM 自我认同的真理来源。
        # 改名时路由会切换新 session（见 api_put_pet_status），所以这里
        # 不再需要"忽略历史里的旧名字"那种硬声明 —— ephemeral_turns
        # 在新 session 下就是干净的。
        ps = self.pet_state
        if ps:
            pet_name = ps.get("name") or "小桌"
            persona  = ps.get("persona")  or ""
            tagline  = ps.get("tagline")  or ""
            identity_lines = [
                f"你叫「{pet_name}」。这是主人最新设定。",
            ]
            if persona:
                identity_lines.append(f"人设：{persona}")
            if tagline:
                identity_lines.append(f"口头禅：{tagline}（自然出现，不要每句都说）")
            identity_lines.append(
                f"当前心情 {ps.get('mood', 'neutral')}（{ps.get('mood_score', 60)}/100），"
                f"活力 {ps.get('energy', 70)}/100，等级 Lv.{ps.get('level', 1)}"
            )
            if ps.get("ai_notes"):
                identity_lines.append(f"AI 主观感受：{ps['ai_notes']}")
            if ps.get("updated_at"):
                try:
                    ts = float(ps["updated_at"])
                    if ts > 0:
                        identity_lines.append(
                            "（你的设定最近一次修改：" + _humanize_ts(ts) + "）"
                        )
                except (TypeError, ValueError):
                    pass
            sections.append("【你的当前身份】\n" + "\n".join(f"- {l}" for l in identity_lines))

        # 用户画像（用户填 + AI 总结）
        u = self.user_profile or {}
        ai = self.ai_profile or {}
        profile_lines: list[str] = []
        if u.get("name"):
            profile_lines.append(f"昵称：{u['name']}")
        if u.get("language"):
            profile_lines.append(f"偏好语言：{u['language']}")
        if u.get("bio"):
            profile_lines.append(f"性格/兴趣：{u['bio']}")
        if u.get("city"):
            profile_lines.append(f"所在城市：{u['city']}")
        custom = u.get("custom") or {}
        for k, v in custom.items():
            if v:
                profile_lines.append(f"{k}：{v}")
        if u.get("updated_at"):
            try:
                ts = float(u["updated_at"])
                profile_lines.append(
                    "（资料最近一次修改：" + _humanize_ts(ts) + "）"
                )
            except (TypeError, ValueError):
                pass
        if ai.get("description"):
            profile_lines.append(f"AI 观察到的画像：{ai['description']}")
        if ai.get("traits"):
            profile_lines.append(f"性格标签：{', '.join(ai['traits'][:6])}")
        if ai.get("interests"):
            profile_lines.append(f"兴趣点：{', '.join(ai['interests'][:6])}")
        if profile_lines:
            sections.append("【用户画像】\n" + "\n".join(f"- {l}" for l in profile_lines))

        # 宠物状态在最顶部已注入【你的当前身份】，这里不再重复输出，
        # 避免 LLM 被同一段信息出现两次混淆。

        # 长期画像断言
        if self.facts:
            lines = "\n".join(f"- {f}" for f in self.facts[-12:])
            sections.append("【关于用户的事实】\n" + lines)

        # 长期记忆
        if self.long_term:
            lines = []
            for m in self.long_term:
                bullets = m.get("bullet_facts") or []
                bullet_text = "; ".join(bullets[:3]) if bullets else ""
                title = m.get("title") or ""
                lines.append(
                    f"- [{title}] {m.get('summary', '')[:200]}"
                    + (f"（要点：{bullet_text}）" if bullet_text else "")
                )
            sections.append("【召回的长期记忆】\n" + "\n".join(lines))

        # 短期记忆
        if self.short_term:
            lines = []
            for m in self.short_term:
                lines.append(f"- {m.get('summary', '')[:200]}")
            sections.append("【最近的短期记忆摘要】\n" + "\n".join(lines))

        return "\n\n".join(sections)


async def load_memory_context(
    user_id: str,
    *,
    query_text: str = "",
    enable_long_term_recall: bool = True,
    ephemeral_limit: int = 200,
) -> MemoryContext:
    """组装 MemoryContext。所有失败都吞掉，只返回能拿到的部分.

    临时记忆 = 当前 active session 的 turn（不会跨 session 串味）。
    """
    cutoff = time.time() - MEMORY_EPHEMERAL_WINDOW_HOURS * 3600
    ctx = MemoryContext()

    # 拿当前 active session id（没有则会自动创建一条空的）
    active_id: int | None = None
    try:
        from ..storage import get_or_create_active

        active_id = int(get_or_create_active(user_id)["id"])
    except Exception:
        active_id = None

    try:
        ctx.ephemeral_turns = list_turns_since(
            user_id, cutoff, limit=ephemeral_limit, session_id=active_id
        )
    except Exception:
        ctx.ephemeral_turns = []

    try:
        ctx.short_term = list_recent_short_term(user_id, limit=MEMORY_SHORT_TERM_INJECT_MAX)
    except Exception:
        ctx.short_term = []

    if enable_long_term_recall and query_text:
        try:
            vec = await embed_text_safe(query_text)
        except Exception:
            vec = []
        try:
            ctx.long_term = recall_by_vector(
                user_id,
                vec,
                keyword_hint=query_text,
                top_k=MEMORY_LONG_TERM_TOPK,
            )
        except Exception:
            ctx.long_term = []

    try:
        ctx.facts = list_facts(user_id)
    except Exception:
        ctx.facts = []
    try:
        ctx.user_profile = get_profile(user_id)
    except Exception:
        ctx.user_profile = {}
    try:
        ctx.ai_profile = get_ai_profile(user_id)
    except Exception:
        ctx.ai_profile = {}
    try:
        ctx.pet_state = get_pet_state(user_id)
    except Exception:
        ctx.pet_state = {}

    return ctx
