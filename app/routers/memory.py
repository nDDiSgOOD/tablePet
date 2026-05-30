"""User profile / Memory / Pet 路由 / Unified user-level routes.

设计：
  - 用户级数据按 ``user_id`` 隔离，但目前是单用户系统，路由层的 ``user_id``
    用 ``app.memory.DEFAULT_USER_ID`` 兜底。
  - 三端（web / wifi / usb）通过 agent 都看到同一份数据。
  - 所有写操作走 ``app.storage``，单一真理源（SQLite）。
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException

from ..memory import DEFAULT_USER_ID
from ..storage import (
    add_fact,
    append_system_event,
    apply_event,
    clear_recent,
    close_active_session,
    delete_all_turns,
    delete_custom_field,
    delete_fact_by_index,
    delete_session,
    delete_turn,
    get_ai_profile,
    get_daily_summary,
    get_or_create_active,
    get_pet_state,
    get_profile,
    get_setting,
    list_daily_summary_days,
    list_facts,
    list_long_term,
    list_recent,
    list_recent_short_term,
    list_sessions,
    list_turns_by_session,
    list_turns_paged,
    set_custom_field,
    update_pet_state,
    update_profile,
    upsert_ai_profile,
)
from ..services.memory_summarizer import (
    close_session_with_summary,
    generate_greeting,
    summarize_daily,
    summarize_to_short_term,
    update_ai_profile,
    update_pet_state_hourly,
)

router = APIRouter()


FIXED_FIELD_LABELS = {
    "name": "昵称",
    "language": "偏好语言",
    "bio": "性格/兴趣",
    "city": "所在城市",
}


def _diff_profile_narrative(old: dict[str, Any], new: dict[str, Any]) -> str:
    """对比新旧 profile，生成一句中文叙述。空 → 不发事件.

    措辞要点：明确"事实发生了变化"而非"之前认错"。AI 看到这条 system 事件
    时应该理解为"用户主动告知的新状态"，不需要为之前的回答道歉。
    """
    bits: list[str] = []
    for key, label in FIXED_FIELD_LABELS.items():
        before = str(old.get(key) or "").strip()
        after = str(new.get(key) or "").strip()
        if before == after:
            continue
        if not before:
            bits.append(f'{label}首次填写为"{after}"')
        elif not after:
            bits.append(f"{label}从「{before}」清空了")
        else:
            bits.append(f"{label}从「{before}」改为「{after}」")
    if not bits:
        return ""
    return (
        "[系统事件 · 资料同步] 用户主动更新了个人资料："
        + "；".join(bits)
        + "。这是事实层面的变化（搬家 / 改昵称等），并非你之前回答有误。"
        "请基于新资料继续对话，不需要为此前的回答道歉或解释。"
    )


# ---------------------------------------------------------------------------
# 用户简介 / User profile
# ---------------------------------------------------------------------------
@router.get("/api/user/profile")
async def api_get_profile() -> dict[str, Any]:
    p = get_profile(DEFAULT_USER_ID)
    # 头像走 ui_settings.user_avatar；前端 PUT 时也走 ui_settings 接口，
    # 这里只是把 avatar 顺手拼回 profile 里方便一次渲染。
    avatar = get_setting(DEFAULT_USER_ID, "user_avatar") or ""
    p["avatar"] = avatar
    return p


@router.put("/api/user/profile")
async def api_put_profile(payload: dict[str, Any]) -> dict[str, Any]:
    """改资料时自动在临时记忆里插一条系统事件帧，让 AI "亲眼看到"变更，
    避免 AI 把"现在的资料"当成"自己之前认错过"。
    """
    old = get_profile(DEFAULT_USER_ID)
    new = update_profile(DEFAULT_USER_ID, payload)
    narrative = _diff_profile_narrative(old, new)
    if narrative:
        append_system_event(DEFAULT_USER_ID, narrative)
    return {"ok": True, "profile": new}


@router.put("/api/user/profile/custom/{key}")
async def api_put_custom_field(key: str, payload: dict[str, Any]) -> dict[str, Any]:
    value = payload.get("value")
    if value is None:
        raise HTTPException(status_code=400, detail="value is required")
    old_custom = (get_profile(DEFAULT_USER_ID).get("custom") or {}).get(key, "")
    try:
        set_custom_field(DEFAULT_USER_ID, key, str(value))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    new_value = str(value)
    if str(old_custom).strip() != new_value.strip():
        if not old_custom:
            text = f'（系统记录）用户在自定义字段里新增了"{key}"="{new_value}"。'
        else:
            text = f'（系统记录）用户把自定义字段"{key}"从「{old_custom}」改为「{new_value}」。'
        append_system_event(DEFAULT_USER_ID, text)
    return {"ok": True, "profile": get_profile(DEFAULT_USER_ID)}


@router.delete("/api/user/profile/custom/{key}")
async def api_delete_custom_field(key: str) -> dict[str, Any]:
    old_custom = (get_profile(DEFAULT_USER_ID).get("custom") or {}).get(key, "")
    if not delete_custom_field(DEFAULT_USER_ID, key):
        raise HTTPException(status_code=404, detail="custom field not found")
    if old_custom:
        append_system_event(
            DEFAULT_USER_ID,
            f'（系统记录）用户删除了自定义字段"{key}"（原值：{old_custom}）。',
        )
    return {"ok": True, "profile": get_profile(DEFAULT_USER_ID)}


PET_FIELD_LABELS = {
    "name": "名字",
    "persona": "人设",
    "tagline": "口头禅/自我介绍",
}


def _diff_pet_narrative(old: dict[str, Any], new: dict[str, Any]) -> str:
    bits: list[str] = []
    for key, label in PET_FIELD_LABELS.items():
        before = str(old.get(key) or "").strip()
        after = str(new.get(key) or "").strip()
        if before == after:
            continue
        if not before:
            bits.append(f'{label}首次填写为"{after}"')
        elif not after:
            bits.append(f"{label}从「{before}」清空了")
        else:
            bits.append(f"{label}从「{before}」改为「{after}」")
    if not bits:
        return ""
    return (
        "[系统事件 · 桌宠资料更新] 主人刚刚调整了你的设定："
        + "；".join(bits)
        + "。请按新设定继续对话，不必为之前的回答道歉，可以自然提及 "
        '"我现在叫……" 或 "主人刚把我的设定改成……" 之类的过渡。'
    )


# ---------------------------------------------------------------------------
# 宠物状态 / Pet state
# ---------------------------------------------------------------------------
@router.get("/api/pet/status")
async def api_get_pet_status() -> dict[str, Any]:
    s = get_pet_state(DEFAULT_USER_ID)
    s["avatar"] = get_setting(DEFAULT_USER_ID, "pet_avatar") or ""
    return s


@router.post("/api/pet/interact")
async def api_pet_interact(payload: dict[str, Any]) -> dict[str, Any]:
    action = str(payload.get("action") or "").strip()
    try:
        return {"ok": True, "status": apply_event(DEFAULT_USER_ID, action)}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.put("/api/pet/status")
async def api_put_pet_status(payload: dict[str, Any]) -> dict[str, Any]:
    """编辑宠物基础资料（name / persona / tagline）。改动会自动写一条
    system_event 到当前 session，让 AI 知道"主人调整了我的设定"。

    特别地：当 ``name`` 真的发生变化时，会**强制关闭当前 active session**
    并把改名叙述写到新 session 的开头。原因是 LLM 在同一段对话里看到自己
    之前用旧名（"喵酱"）回应过，会强烈倾向继续使用旧名 —— 单靠 system
    指令压不住 assistant 历史的"延续效应"。把改名当成"换一段对话的天然
    分界点"是最干净的解法，旧 session 仍会通过短期/长期记忆保留下来。
    """
    old = get_pet_state(DEFAULT_USER_ID)
    new = update_pet_state(DEFAULT_USER_ID, **payload)
    narrative = _diff_pet_narrative(old, new)

    name_changed = (
        str(old.get("name") or "").strip() != str(new.get("name") or "").strip()
    )

    if name_changed:
        # 1) 先归档当前 session（含 LLM 摘要 + 短期记忆压缩），避免记忆断片
        try:
            await close_session_with_summary(DEFAULT_USER_ID)
        except Exception:
            # 摘要失败不阻塞改名 —— 兜底直接物理关闭
            try:
                close_active_session(DEFAULT_USER_ID, summary="", title="")
            except Exception:
                pass
        # 2) 把改名叙述写到"新 session"的开头
        #    append_system_event → append_turn → get_or_create_active
        #    旧 active 已经 closed_at 了，这里会自动开一条新的 active session。
        if narrative:
            append_system_event(DEFAULT_USER_ID, narrative)
    else:
        # persona / tagline 改动留在当前 session 里（不切断对话连续性）
        if narrative:
            append_system_event(DEFAULT_USER_ID, narrative)

    return {"ok": True, "status": new, "session_rolled": name_changed}


# ---------------------------------------------------------------------------
# 记忆 / Memory
# ---------------------------------------------------------------------------
@router.get("/api/memory")
async def api_get_memory() -> dict[str, Any]:
    """主入口：当前用户的全部记忆."""
    return {
        "profile": list_facts(DEFAULT_USER_ID),
        "recent": list_recent(DEFAULT_USER_ID, limit=30),
        "user_profile": get_profile(DEFAULT_USER_ID),
    }


@router.post("/api/memory")
async def api_add_memory(payload: dict[str, Any]) -> dict[str, bool]:
    fact = str(payload.get("fact") or "").strip()
    if not fact:
        raise HTTPException(status_code=400, detail="fact is required")
    add_fact(DEFAULT_USER_ID, fact)
    return {"ok": True}


@router.delete("/api/memory/profile/{idx}")
async def api_delete_memory(idx: int) -> dict[str, bool]:
    if not delete_fact_by_index(DEFAULT_USER_ID, idx):
        raise HTTPException(status_code=404, detail="profile fact not found")
    return {"ok": True}


@router.post("/api/memory/clear-short-term")
async def api_clear_recent() -> dict[str, bool]:
    clear_recent(DEFAULT_USER_ID)
    return {"ok": True}


# ---------------------------------------------------------------------------
# 对话原始记录 / Raw conversation turns（前端"完整对话"视图用）
# ---------------------------------------------------------------------------
@router.get("/api/memory/turns")
async def api_list_turns(
    limit: int = 50,
    before_id: int | None = None,
    include_system: bool = True,
) -> dict[str, Any]:
    """分页拉对话流水，按 id 倒序。

    - ``limit``：每页条数（默认 50，最多 200）
    - ``before_id``：游标分页，传上一页最小 id
    - ``include_system``：是否包含 channel='system_event' 的事件帧
    """
    limit = max(1, min(200, int(limit)))
    items = list_turns_paged(
        DEFAULT_USER_ID,
        limit=limit,
        before_id=before_id,
        include_system=include_system,
    )
    next_cursor = items[-1]["id"] if len(items) == limit else None
    return {"items": items, "next_before_id": next_cursor}


@router.delete("/api/memory/turns/{turn_id}")
async def api_delete_turn(turn_id: int) -> dict[str, bool]:
    if not delete_turn(DEFAULT_USER_ID, turn_id):
        raise HTTPException(status_code=404, detail="turn not found")
    return {"ok": True}


@router.delete("/api/memory/turns")
async def api_delete_all_turns() -> dict[str, int]:
    """清空全部原始对话（不动短期/长期记忆和日记）."""
    n = delete_all_turns(DEFAULT_USER_ID)
    return {"ok": True, "deleted": n}


# ---------------------------------------------------------------------------
# 会话 / Sessions
# ---------------------------------------------------------------------------
@router.get("/api/sessions")
async def api_list_sessions(limit: int = 30, before_id: int | None = None) -> dict[str, Any]:
    items = list_sessions(DEFAULT_USER_ID, limit=max(1, min(100, int(limit))), before_id=before_id)
    next_cursor = items[-1]["id"] if len(items) == limit else None
    return {"items": items, "next_before_id": next_cursor}


@router.get("/api/sessions/active")
async def api_get_active_session() -> dict[str, Any]:
    return get_or_create_active(DEFAULT_USER_ID)


@router.get("/api/sessions/{session_id}/turns")
async def api_list_session_turns(session_id: int, include_system: bool = True) -> dict[str, Any]:
    return {
        "items": list_turns_by_session(session_id, include_system=include_system),
    }


@router.delete("/api/sessions/{session_id}/turns/{turn_id}")
async def api_delete_session_turn(session_id: int, turn_id: int) -> dict[str, bool]:
    # session_id 校验留个口子；当前用全局 delete_turn（按 user 隔离已经够用）
    if not delete_turn(DEFAULT_USER_ID, turn_id):
        raise HTTPException(status_code=404, detail="turn not found")
    return {"ok": True}


@router.delete("/api/sessions/{session_id}")
async def api_delete_session(session_id: int) -> dict[str, bool]:
    if not delete_session(DEFAULT_USER_ID, session_id):
        raise HTTPException(status_code=404, detail="session not found")
    return {"ok": True}


@router.post("/api/sessions/close")
async def api_close_session() -> dict[str, Any]:
    """关闭当前 active session，先总结再归档；下条 turn 会自动开新 session.

    Web 端"清空对话"按钮调这里。USB / WiFi 端不会触发本接口（按你的要求保持
    长会话不重置）。
    """
    result = await close_session_with_summary(DEFAULT_USER_ID)
    return {"ok": True, "closed": result}


@router.post("/api/sessions/{session_id}/resume")
async def api_resume_session(session_id: int) -> dict[str, Any]:
    """从一条已归档的会话续接：
    1. 先把当前 active session 关闭并总结（避免记忆断片）
    2. 把目标 session 重新置为 active
    3. 写一条 system_event 让 AI 知道"主人想接着上次的话题继续聊"

    使用场景：用户在「记忆 / 会话流水」面板点某条历史会话的"接着聊"。
    ``reopen_session`` 自带 ownership 校验，目标不存在/不属于当前用户会返回 None.
    """
    from ..storage import reopen_session

    # 1) 关闭当前 active 并总结（避免记忆断片）
    closed = await close_session_with_summary(DEFAULT_USER_ID)
    # 2) 重开目标 session
    revived = reopen_session(DEFAULT_USER_ID, session_id)
    if not revived:
        raise HTTPException(status_code=404, detail="session not found")
    # 3) 写一条 system_event 让 AI 衔接上文
    title = (revived.get("title") or "").strip()
    summary = (revived.get("summary") or "").strip()
    salute_bits = []
    if title:
        salute_bits.append(f"标题：{title}")
    if summary:
        salute_bits.append(f"上次聊到：{summary[:200]}")
    salute = "；".join(salute_bits) or "（这段会话之前没有记录摘要）"
    narrative = (
        f"[系统事件 · 会话续接] 主人选择继续之前的会话（#{session_id}）。{salute}。"
        "请基于这段历史自然地接着聊，不需要重新打招呼，也不要假装第一次见面。"
    )
    append_system_event(DEFAULT_USER_ID, narrative)
    return {"ok": True, "resumed": revived, "closed_previous": closed}


# ---------------------------------------------------------------------------
# 开场白 / Greeting
# ---------------------------------------------------------------------------
@router.get("/api/chat/greeting")
async def api_get_greeting() -> dict[str, str]:
    """读取一句开场白，结合宠物名 / 心情 / 人设 / AI 备注动态生成."""
    text = await generate_greeting(DEFAULT_USER_ID)
    return {"text": text}


# ---------------------------------------------------------------------------
# 分层记忆查看 / Tiered memory views
# ---------------------------------------------------------------------------
@router.get("/api/memory/short-term")
async def api_list_short_term(limit: int = 20) -> dict[str, Any]:
    return {"items": list_recent_short_term(DEFAULT_USER_ID, limit=limit)}


@router.get("/api/memory/long-term")
async def api_list_long_term(limit: int = 50) -> dict[str, Any]:
    return {"items": list_long_term(DEFAULT_USER_ID, limit=limit)}


# 日历视图 / Calendar
@router.get("/api/memory/calendar")
async def api_calendar(start: str | None = None, end: str | None = None) -> dict[str, Any]:
    return {
        "items": list_daily_summary_days(DEFAULT_USER_ID, start_day=start, end_day=end),
    }


@router.get("/api/memory/day/{day}")
async def api_get_day(day: str) -> dict[str, Any]:
    item = get_daily_summary(DEFAULT_USER_ID, day)
    if not item:
        raise HTTPException(status_code=404, detail="no summary for this day")
    return item


@router.post("/api/memory/day/{day}/summarize")
async def api_summarize_day(day: str) -> dict[str, Any]:
    """手动触发某天的总结。

    - 当天真的没聊过 → 返回 ``{empty: true, message}`` 友好提示
    - 当天有数据，但 LLM 调用失败 → 返回 502 + 错误信息（前端会提示重试），
      不再误报 "今天没来找我聊天"
    """
    from ..services.memory_summarizer import _day_bounds  # type: ignore
    from ..storage import list_turns_for_day, list_recent_short_term

    day_start, day_end = _day_bounds(day)
    turns = list_turns_for_day(DEFAULT_USER_ID, day_start, day_end)
    short_terms = [
        s for s in list_recent_short_term(DEFAULT_USER_ID, limit=20)
        if day_start <= s.get("window_end", 0) < day_end
    ]
    has_data = bool(turns or short_terms)

    result = await summarize_daily(DEFAULT_USER_ID, day)
    if result is None:
        if not has_data:
            # 真没聊过 —— 友好兜底
            u = get_profile(DEFAULT_USER_ID)
            name = (u.get("name") or "").strip()
            salute = f"{name}" if name else "主人"
            return {
                "empty": True,
                "day": day,
                "message": f"{day} 这一天 {salute} 没有来找我聊天哦～",
            }
        # 有数据但模型挂了，明确告诉前端"是后端在 LLM 阶段失败"
        raise HTTPException(
            status_code=502,
            detail=(
                f"已找到当日 {len(turns)} 轮对话，但日记总结的模型调用失败了。"
                "请检查「账户情况」里的 API Key / 网络，再点一次重新总结。"
            ),
        )
    return result


@router.post("/api/memory/summarize-now")
async def api_summarize_now() -> dict[str, Any]:
    """强制触发短期总结（调试用）."""
    result = await summarize_to_short_term(DEFAULT_USER_ID, force=True)
    return {"ok": True, "short_term": result}


# ---------------------------------------------------------------------------
# AI 用户画像
# ---------------------------------------------------------------------------
@router.get("/api/ai-profile")
async def api_get_ai_profile() -> dict[str, Any]:
    return get_ai_profile(DEFAULT_USER_ID)


@router.post("/api/ai-profile/refresh")
async def api_refresh_ai_profile() -> dict[str, Any]:
    profile = await update_ai_profile(DEFAULT_USER_ID)
    if profile is None:
        raise HTTPException(status_code=409, detail="not enough memory to summarize")
    return profile


@router.post("/api/pet/tick")
async def api_pet_tick() -> dict[str, Any]:
    """手动触发宠物状态小时调整（调试用）."""
    result = await update_pet_state_hourly(DEFAULT_USER_ID)
    return {"ok": True, "status": result}


# ---------------------------------------------------------------------------
# 兼容旧 device_id 路径（前端老调用、设备端固件兼容）
# ---------------------------------------------------------------------------
@router.get("/api/memory/{device_id}")
async def api_get_memory_legacy(device_id: str) -> dict[str, Any]:
    return await api_get_memory()


@router.post("/api/memory/{device_id}")
async def api_add_memory_legacy(device_id: str, payload: dict[str, Any]) -> dict[str, bool]:
    return await api_add_memory(payload)


@router.delete("/api/memory/{device_id}/profile/{idx}")
async def api_delete_memory_legacy(device_id: str, idx: int) -> dict[str, bool]:
    return await api_delete_memory(idx)


@router.post("/api/memory/{device_id}/clear-short-term")
async def api_clear_recent_legacy(device_id: str) -> dict[str, bool]:
    return await api_clear_recent()
