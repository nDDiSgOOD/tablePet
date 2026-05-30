"""记忆总结 / Memory summarization.

四类总结任务（都用 ``DEEPSEEK_SUMMARY_MODEL``）：

1. ``summarize_to_short_term``
   - 触发：未消化 token 数 ≥ MEMORY_CONTEXT_BUDGET_TOKENS，或被定时任务调用
   - 输出：把"未总结的 turn"压缩成一条 short_term 摘要，自动 embedding
   - 副作用：标记原始 turn 已消化

2. ``summarize_daily``
   - 触发：APScheduler 每天凌晨；前端日历点空白也允许 on-demand
   - 输出：daily_summary 一行；同时挑出"重要"的内容晋升到 long_term
   - 副作用：mark_short_term_promoted + mark_turns_long_summarized

3. ``update_ai_profile``
   - 触发：每天总结后；或被前端手动触发
   - 输出：ai_user_profile 一行
   - 副作用：覆盖式重写

4. ``update_pet_state_hourly``
   - 触发：APScheduler 每小时
   - 输出：调整 pet_state.mood_score / energy / mood / ai_notes
"""

from __future__ import annotations

import json
import logging
import re
import time
from datetime import datetime
from typing import Any

from ..config import (
    DEEPSEEK_SUMMARY_MODEL,
    MEMORY_CONTEXT_BUDGET_TOKENS,
)
from ..storage import (
    apply_event,
    get_ai_profile,
    get_pet_state,
    insert_long_term,
    insert_short_term,
    list_recent_short_term,
    list_turns_for_day,
    list_turns_unsummarized_short,
    list_unpromoted_short_term,
    mark_short_term_promoted,
    mark_turns_long_summarized,
    mark_turns_short_summarized,
    total_unsummarized_tokens,
    update_pet_state,
    upsert_ai_profile,
    upsert_daily_summary,
)
from .chat import call_deepseek_messages, extract_text
from .embedding import embed_text_safe
from .tokens import count_tokens

logger = logging.getLogger(__name__)


_JSON_BLOCK_RE = re.compile(r"\{[\s\S]*\}")


def _parse_json_loose(text: str) -> dict[str, Any]:
    """从 LLM 文本里把 JSON 块抠出来，宽容解析."""
    if not text:
        return {}
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    m = _JSON_BLOCK_RE.search(text)
    if not m:
        return {}
    try:
        return json.loads(m.group(0))
    except json.JSONDecodeError:
        return {}


# ---------------------------------------------------------------------------
# 1) 短期总结
# ---------------------------------------------------------------------------
SHORT_TERM_SYSTEM = (
    "你是 TablePet 桌宠的记忆整理助手。你将看到一段用户与桌宠的最近对话，"
    "请把它压缩成一条短期记忆。严格按以下 JSON 格式输出，不要任何额外文本：\n"
    "{\n"
    '  "summary": "<200 字内中文摘要，写清话题、情绪和用户透露的关键信息>",\n'
    '  "bullet_facts": ["<≤10 字的事实点 1>", "..."],\n'
    '  "important": true|false  // 是否值得长期记忆\n'
    "}"
)


async def summarize_to_short_term(
    user_id: str,
    *,
    force: bool = False,
    session_id: int | None = None,
) -> dict[str, Any] | None:
    """把指定 session（或全部）未总结的 turn 压成一条短期记忆。

    - ``session_id=None``：跨 session 兜底总结，主要给后台定时任务用
    - ``session_id`` 指定时：只压该 session 的内容（典型场景：会话关闭时）
    """
    if not force:
        budget = total_unsummarized_tokens(user_id)
        if budget < MEMORY_CONTEXT_BUDGET_TOKENS:
            return None

    turns = list_turns_unsummarized_short(user_id, limit=400, session_id=session_id)
    # 过滤纯系统事件，避免摘要里只剩 system_event 噪声
    real_turns = [t for t in turns if t.get("channel") != "system_event"]
    if len(real_turns) < 2:
        return None

    convo_lines: list[str] = []
    for t in turns:
        if t.get("channel") == "system_event":
            note = (t.get("assistant_text") or "").strip()
            if note:
                convo_lines.append(f"[SYSTEM] {note}")
            continue
        if t.get("user_text"):
            convo_lines.append(f"User: {t['user_text']}")
        if t.get("assistant_text"):
            convo_lines.append(f"Assistant: {t['assistant_text']}")
    convo = "\n".join(convo_lines)

    messages = [
        {"role": "system", "content": SHORT_TERM_SYSTEM},
        {"role": "user", "content": f"以下是最近的对话窗口：\n\n{convo}\n\n请输出 JSON。"},
    ]
    try:
        resp = await call_deepseek_messages(
            messages, model=DEEPSEEK_SUMMARY_MODEL, max_tokens=600, temperature=0.3,
            user_id=user_id, purpose="summary.short_term",
        )
    except Exception as exc:
        logger.warning("short-term summary call failed: %s", exc)
        return None

    parsed = _parse_json_loose(extract_text(resp))
    summary = str(parsed.get("summary") or "").strip()
    if not summary:
        return None
    bullet = parsed.get("bullet_facts")
    if not isinstance(bullet, list):
        bullet = []
    bullet = [str(x)[:60] for x in bullet if str(x).strip()][:10]
    important = bool(parsed.get("important"))

    window_start = min(t["created_at"] for t in turns)
    window_end = max(t["created_at"] for t in turns)
    token_count = count_tokens(summary) + sum(count_tokens(b) for b in bullet)

    embedding = await embed_text_safe(summary + "\n" + " ".join(bullet))

    short_id = insert_short_term(
        user_id,
        summary=summary,
        bullet_facts=bullet,
        window_start=window_start,
        window_end=window_end,
        token_count=token_count,
        embedding=embedding or None,
        embed_model="ollama" if embedding else "",
    )
    mark_turns_short_summarized([t["id"] for t in turns], short_id)
    logger.info(
        "short-term summary user=%s session=%s turns=%d important=%s",
        user_id, session_id, len(turns), important,
    )
    return {"id": short_id, "summary": summary, "important": important}


# ---------------------------------------------------------------------------
# 2) 日总结 + 重要片段晋升到长期
# ---------------------------------------------------------------------------
DAILY_SYSTEM = (
    "你是 TablePet 桌宠的日记整理助手。请基于一整天的对话与短期记忆，"
    "为这一天写一段总结。严格按以下 JSON 格式输出：\n"
    "{\n"
    '  "summary": "<300 字内中文，像日记一样回顾这一天>",\n'
    '  "bullet_facts": ["<关键事件或事实点>", ...],\n'
    '  "mood": "<这一天桌宠的代表性心情，从下面六选一：happy / neutral / sleepy / excited / sick / hungry>",\n'
    '  "long_term": [\n'
    '    {"title":"<≤12字>","summary":"<≤200字>","bullets":["..."],"importance":0.0~1.0}\n'
    "  ]  // 0~3 条值得长期记忆的片段\n"
    "}"
)


def _local_day_str(ts: float) -> str:
    return datetime.fromtimestamp(ts).strftime("%Y-%m-%d")


def _day_bounds(day: str) -> tuple[float, float]:
    start = datetime.strptime(day, "%Y-%m-%d").timestamp()
    return start, start + 86400


async def summarize_daily(user_id: str, day: str | None = None) -> dict[str, Any] | None:
    """生成 day 的日总结，并把"重要"片段插入长期记忆."""
    if not day:
        day = datetime.now().strftime("%Y-%m-%d")

    # 先把当天剩余未总结 turn 都压一下短期，以免漏掉
    await summarize_to_short_term(user_id, force=True)

    day_start, day_end = _day_bounds(day)
    turns = list_turns_for_day(user_id, day_start, day_end)
    short_terms = [
        s for s in list_recent_short_term(user_id, limit=20)
        if day_start <= s.get("window_end", 0) < day_end
    ]
    if not turns and not short_terms:
        return None

    turn_text = "\n".join(
        f"[{datetime.fromtimestamp(t['created_at']).strftime('%H:%M')}] "
        f"User: {t.get('user_text', '')}\nAssistant: {t.get('assistant_text', '')}"
        for t in turns
    )
    short_text = "\n".join(f"- {s.get('summary', '')}" for s in short_terms)

    messages = [
        {"role": "system", "content": DAILY_SYSTEM},
        {
            "role": "user",
            "content": (
                f"日期：{day}\n\n"
                f"短期记忆摘要：\n{short_text or '(无)'}\n\n"
                f"原始对话：\n{turn_text or '(无)'}"
            ),
        },
    ]
    try:
        resp = await call_deepseek_messages(
            messages, model=DEEPSEEK_SUMMARY_MODEL, max_tokens=900, temperature=0.4,
            user_id=user_id, purpose="summary.daily",
        )
    except Exception as exc:
        logger.warning("daily summary call failed: %s", exc)
        return None

    parsed = _parse_json_loose(extract_text(resp))
    summary = str(parsed.get("summary") or "").strip()
    bullet = parsed.get("bullet_facts") or []
    if not isinstance(bullet, list):
        bullet = []
    bullet = [str(x)[:80] for x in bullet if str(x).strip()][:12]
    # 当日心情：LLM 自由选；非法值则 fallback 到当前 pet_state.mood
    valid_moods = {"happy", "neutral", "sleepy", "excited", "sick", "hungry"}
    mood_pick = str(parsed.get("mood") or "").strip().lower()
    if mood_pick not in valid_moods:
        try:
            from ..storage import get_pet_state
            mood_pick = (get_pet_state(user_id).get("mood") or "").strip()
        except Exception:
            mood_pick = ""
        if mood_pick not in valid_moods:
            mood_pick = "neutral" if (turns or short_terms) else ""

    upsert_daily_summary(
        user_id,
        day,
        summary=summary,
        bullet_facts=bullet,
        turn_count=len(turns),
        token_count=sum(int(t.get("user_tokens", 0)) + int(t.get("assistant_tokens", 0)) for t in turns),
        mood_avg=None,
        mood=mood_pick,
    )

    long_specs = parsed.get("long_term") or []
    promoted_short_ids: list[int] = []
    for spec in long_specs[:3] if isinstance(long_specs, list) else []:
        if not isinstance(spec, dict):
            continue
        lt_summary = str(spec.get("summary") or "").strip()
        if not lt_summary:
            continue
        title = str(spec.get("title") or "")[:40]
        bullets = spec.get("bullets") or []
        if not isinstance(bullets, list):
            bullets = []
        bullets = [str(x)[:80] for x in bullets if str(x).strip()][:8]
        try:
            importance = float(spec.get("importance") or 0.5)
        except (TypeError, ValueError):
            importance = 0.5
        embedding = await embed_text_safe(lt_summary + "\n" + " ".join(bullets))
        insert_long_term(
            user_id,
            title=title or lt_summary[:20],
            summary=lt_summary,
            bullet_facts=bullets,
            importance=importance,
            window_start=day_start,
            window_end=day_end,
            embedding=embedding or None,
            embed_model="ollama" if embedding else "",
        )

    promoted_short_ids = [s["id"] for s in short_terms]
    if promoted_short_ids:
        mark_short_term_promoted(promoted_short_ids)
    if turns:
        mark_turns_long_summarized([t["id"] for t in turns], 0)

    logger.info(
        "daily summary user=%s day=%s turns=%d longs=%d",
        user_id, day, len(turns), len(long_specs) if isinstance(long_specs, list) else 0,
    )
    return {"day": day, "summary": summary, "bullet_facts": bullet}


# ---------------------------------------------------------------------------
# 3) AI 用户画像
# ---------------------------------------------------------------------------
AI_PROFILE_SYSTEM = (
    "你是 TablePet 桌宠的画像分析师。请综合下面三类信息，更新你对用户的认知：\n"
    "1) 用户主动填写的最新简介（事实层面，比如昵称 / 城市 / 自定义字段）—— 必须以此为准\n"
    "2) AI 自己之前生成的画像（你上一版的认知，可以参考但不要照搬）\n"
    "3) 短期 + 长期记忆里观察到的行为、对话、偏好\n"
    "注意：\n"
    "- description 里的姓名 / 城市 / 关键属性必须与「用户主动填写」完全一致；不要用旧值。\n"
    "- 如果用户填写的内容与历史记忆冲突，以填写为准，并在描述里自然地反映「最新状态」。\n"
    "- 不要复述系统事件原文（如「系统事件 · 资料同步」），也不要解释你之前认错。\n"
    "严格按以下 JSON 格式输出，不要任何额外文本：\n"
    "{\n"
    '  "description": "<200 字内中文整体描述，融合最新事实+性格观察>",\n'
    '  "traits": ["性格标签", ...],\n'
    '  "interests": ["兴趣点", ...],\n'
    '  "relationship": {"closeness": 0.0~1.0, "trust": 0.0~1.0, "tone": "..."}\n'
    "}"
)


def _format_user_profile_for_ai(profile: dict[str, Any]) -> str:
    """把 user_profile 表 + custom 字段拼成给画像分析师看的「事实简介」。"""
    bits: list[str] = []
    fixed_labels = {
        "name": "昵称",
        "language": "偏好语言",
        "bio": "性格/兴趣（用户自述）",
        "city": "所在城市",
    }
    for k, label in fixed_labels.items():
        v = str(profile.get(k) or "").strip()
        if v:
            bits.append(f"- {label}：{v}")
    custom = profile.get("custom") or {}
    for k, v in custom.items():
        v = str(v or "").strip()
        if v:
            bits.append(f"- {k}：{v}")
    if not bits:
        return "（用户暂未填写任何资料）"
    return "\n".join(bits)


async def update_ai_profile(user_id: str) -> dict[str, Any] | None:
    short_terms = list_recent_short_term(user_id, limit=20)
    from ..storage import list_long_term as _list_long_term, get_profile, get_ai_profile
    long_terms = _list_long_term(user_id, limit=30)

    # 用户主动填写的最新资料 —— 这是事实真理源，必须放在 prompt 最显眼位置
    user_profile = {}
    try:
        user_profile = get_profile(user_id) or {}
    except Exception:
        user_profile = {}

    # AI 自己上一版的画像（参考，不照搬）
    prev_ai = {}
    try:
        prev_ai = get_ai_profile(user_id) or {}
    except Exception:
        prev_ai = {}

    if not short_terms and not long_terms and not (user_profile.get("name") or user_profile.get("bio")):
        # 真没任何信息可以总结
        return None

    sections: list[str] = []
    sections.append("# 用户主动填写的最新简介（事实层 / 必须以此为准）\n" + _format_user_profile_for_ai(user_profile))

    if prev_ai.get("description"):
        sections.append(
            "# 你（AI）上一版的画像（仅供参考，如与最新简介冲突以最新简介为准）\n"
            f"description: {prev_ai.get('description', '')}\n"
            f"traits: {', '.join(prev_ai.get('traits') or [])}\n"
            f"interests: {', '.join(prev_ai.get('interests') or [])}"
        )

    if short_terms:
        sections.append(
            "# 近期短期记忆\n"
            + "\n".join(f"- {s.get('summary', '')}" for s in short_terms)
        )
    if long_terms:
        sections.append(
            "# 长期记忆\n"
            + "\n".join(
                f"- [{m.get('title') or '-'}] {m.get('summary', '')}" for m in long_terms
            )
        )

    text = "\n\n".join(sections)
    messages = [
        {"role": "system", "content": AI_PROFILE_SYSTEM},
        {"role": "user", "content": text},
    ]
    try:
        resp = await call_deepseek_messages(
            messages, model=DEEPSEEK_SUMMARY_MODEL, max_tokens=700, temperature=0.4,
            user_id=user_id, purpose="summary.ai_profile",
        )
    except Exception as exc:
        logger.warning("ai-profile call failed: %s", exc)
        return None

    parsed = _parse_json_loose(extract_text(resp))
    desc = str(parsed.get("description") or "").strip()
    traits = parsed.get("traits") or []
    interests = parsed.get("interests") or []
    relationship = parsed.get("relationship") or {}
    if not isinstance(traits, list):
        traits = []
    if not isinstance(interests, list):
        interests = []
    if not isinstance(relationship, dict):
        relationship = {}
    traits = [str(x)[:20] for x in traits if str(x).strip()][:10]
    interests = [str(x)[:20] for x in interests if str(x).strip()][:10]

    return upsert_ai_profile(
        user_id,
        description=desc,
        traits=traits,
        interests=interests,
        relationship=relationship,
        source_window_end=time.time(),
    )


# ---------------------------------------------------------------------------
# 4) 宠物状态小时调整
# ---------------------------------------------------------------------------
PET_TICK_SYSTEM = (
    "你是 TablePet 桌宠本人。基于最近的对话与状态，调整自己当下的心情/活力。"
    "严格 JSON 输出：\n"
    "{\n"
    '  "mood_delta": -10..10,\n'
    '  "energy_delta": -10..10,\n'
    '  "ai_notes": "<≤80字主观感受>"\n'
    "}"
)


async def update_pet_state_hourly(user_id: str) -> dict[str, Any] | None:
    short_terms = list_recent_short_term(user_id, limit=4)
    pet = get_pet_state(user_id)
    ai = get_ai_profile(user_id)
    bg = (
        f"当前心情 {pet.get('mood')} 分数 {pet.get('mood_score')}, "
        f"活力 {pet.get('energy')}, 等级 Lv.{pet.get('level')}\n"
        f"AI 画像描述：{ai.get('description', '')}\n"
        "近期短期记忆：\n"
        + "\n".join(f"- {s.get('summary', '')}" for s in short_terms)
    )
    messages = [
        {"role": "system", "content": PET_TICK_SYSTEM},
        {"role": "user", "content": bg},
    ]
    try:
        resp = await call_deepseek_messages(
            messages, model=DEEPSEEK_SUMMARY_MODEL, max_tokens=200, temperature=0.7,
            user_id=user_id, purpose="summary.pet_tick",
        )
    except Exception as exc:
        logger.warning("pet-tick call failed: %s", exc)
        return None

    parsed = _parse_json_loose(extract_text(resp))
    try:
        md = max(-10, min(10, int(parsed.get("mood_delta") or 0)))
        ed = max(-10, min(10, int(parsed.get("energy_delta") or 0)))
    except (TypeError, ValueError):
        md, ed = 0, 0
    notes = str(parsed.get("ai_notes") or "")[:120]
    new_mood = max(0, min(100, int(pet.get("mood_score", 60)) + md))
    new_energy = max(0, min(100, int(pet.get("energy", 70)) + ed))
    return update_pet_state(user_id, mood_score=new_mood, energy=new_energy, ai_notes=notes)


# ---------------------------------------------------------------------------
# 5) 关闭 session：归档前总结一次 + 给 session 写标题/摘要
# ---------------------------------------------------------------------------
SESSION_CLOSE_SYSTEM = (
    "你是 TablePet 桌宠的会话归档助手。下面是一个完整会话的对话记录，"
    "请生成简洁的结尾笔记。严格按以下 JSON 输出：\n"
    "{\n"
    '  "title": "<≤20字的会话标题，如 \\"夜间情感闲聊\\">",\n'
    '  "summary": "<≤200字摘要，写本场聊了什么、用户情绪、留下了什么记忆点>"\n'
    "}"
)


async def close_session_with_summary(user_id: str) -> dict[str, Any] | None:
    """关闭当前 active session，并在归档前生成 title/summary。

    被前端 "清空对话" 按钮调用：
      1. 把当前 session 的剩余 turn 压成短期记忆
      2. LLM 写 title + summary
      3. session 标记 closed_at，下一次写 turn 时自动开新 session
    """
    from ..storage import (
        close_active_session,
        get_or_create_active,
        list_turns_by_session,
    )

    active = get_or_create_active(user_id)
    sid = int(active["id"])

    # 先把这个 session 内所有未总结 turn 压成短期记忆
    short = await summarize_to_short_term(user_id, force=True, session_id=sid)

    # 拿全量 turn 给 LLM 写归档摘要
    all_turns = list_turns_by_session(sid, include_system=True)
    if not all_turns:
        # 空 session 直接关掉，不调 LLM
        close_active_session(user_id, summary="", title="")
        return {"session_id": sid, "title": "", "summary": "", "short_term": short}

    convo_lines: list[str] = []
    for t in all_turns:
        if t.get("channel") == "system_event":
            note = (t.get("assistant_text") or "").strip()
            if note:
                convo_lines.append(f"[SYSTEM] {note}")
            continue
        if t.get("user_text"):
            convo_lines.append(f"User: {t['user_text']}")
        if t.get("assistant_text"):
            convo_lines.append(f"Assistant: {t['assistant_text']}")
    convo = "\n".join(convo_lines)
    messages = [
        {"role": "system", "content": SESSION_CLOSE_SYSTEM},
        {"role": "user", "content": f"以下是会话全文：\n\n{convo}\n\n请输出 JSON。"},
    ]
    title, summary = "", ""
    try:
        resp = await call_deepseek_messages(
            messages, model=DEEPSEEK_SUMMARY_MODEL, max_tokens=600, temperature=0.4,
            user_id=user_id, purpose="summary.session_close",
        )
        parsed = _parse_json_loose(extract_text(resp))
        title = str(parsed.get("title") or "")[:40]
        summary = str(parsed.get("summary") or "")[:1000]
    except Exception as exc:
        logger.warning("session close summary failed: %s", exc)

    close_active_session(user_id, summary=summary, title=title)
    return {"session_id": sid, "title": title, "summary": summary, "short_term": short}


# ---------------------------------------------------------------------------
# 6) 开场白：结合宠物名 / 心情 / 人设 / AI 备注
# ---------------------------------------------------------------------------
GREETING_SYSTEM = (
    "你是 TablePet 桌宠本人。请用第一人称、口语化、≤30 字写一句开场白，"
    "结合给定的当前心情、活力、人设、最近的状态备注，让主人感到你是有'人格连续性'的。"
    "不要使用 markdown，不要列表，不要英文。直接输出一句话。"
)


async def generate_greeting(user_id: str) -> str:
    from ..storage import get_pet_state, get_profile, list_recent_short_term

    pet = get_pet_state(user_id)
    user = get_profile(user_id)
    short_terms = list_recent_short_term(user_id, limit=3)

    user_name = (user.get("name") or "").strip()
    name_call = f"主人{user_name}" if user_name else "主人"

    bg_lines: list[str] = [
        f"你的名字：{pet.get('name') or '小桌'}",
        f"心情：{pet.get('mood', 'neutral')}（{pet.get('mood_score', 60)}/100）",
        f"活力：{pet.get('energy', 70)}/100，等级 Lv.{pet.get('level', 1)}",
    ]
    if pet.get("persona"):
        bg_lines.append(f"人设：{pet['persona']}")
    if pet.get("tagline"):
        bg_lines.append(f"口头禅：{pet['tagline']}")
    if pet.get("ai_notes"):
        bg_lines.append(f"最近的主观感受：{pet['ai_notes']}")
    if short_terms:
        last = short_terms[-1].get("summary") or ""
        if last:
            bg_lines.append(f"最近一次会话回忆：{last[:80]}")
    bg_lines.append(f"打招呼对象：{name_call}")

    messages = [
        {"role": "system", "content": GREETING_SYSTEM},
        {"role": "user", "content": "\n".join(bg_lines)},
    ]
    try:
        resp = await call_deepseek_messages(
            messages, model=DEEPSEEK_SUMMARY_MODEL, max_tokens=120, temperature=0.85,
            user_id=user_id, purpose="summary.greeting",
        )
        text = extract_text(resp).strip()
        if text:
            return text
    except Exception as exc:
        logger.warning("greeting generation failed: %s", exc)

    # 回退：本地拼一句不依赖 LLM 的开场白
    pet_name = pet.get("name") or "小桌"
    mood_label = {
        "happy": "心情很好", "excited": "超兴奋的", "sleepy": "有点困",
        "sick": "不太舒服", "neutral": "平平静静",
    }.get(pet.get("mood", "neutral"), "")
    suffix = f"，{mood_label}呢～" if mood_label else "～"
    return f"嗨{name_call}，我是{pet_name}{suffix}今天想聊点啥？"
