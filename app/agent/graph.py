"""LangGraph 骨架 / TablePet agent graph skeleton.

设计原则
--------
1) **节点仅声明骨架**：每个节点函数体只放 ``# TODO`` 和最小的 passthrough。
   业务逻辑由你后续填入，参考 ``app/services/interaction.py`` 中的对应段落。
2) **State 是单一可变字典**：langgraph 通过返回 partial dict 来 merge 状态。
3) **节点之间不直接调 services**：避免再次耦合，所有副作用集中在节点内部。
4) **可重入 / 无状态**：节点不持有全局变量，便于以后接 checkpoint。

节点拓扑（中等粒度，6-7 个节点）
--------------------------------
::

    START
      │
      ▼
    load_context     # 拉 profile / robot_state / 历史 / sensor
      │
      ▼
    detect_intent    # 走 services.intent.detect_intent 或重写
      │
      ▼
    policy           # 走 services.dialogue_policy.choose_dialogue_policy
      │
      ▼
    tool_call        # 走 services.tool_registry.run_tool_if_needed
      │
      ├─ skip_llm? ──► save  (短路：工具/直答场景)
      │
      ▼
    llm              # 走 services.chat.call_deepseek_messages
      │
      ▼
    parse            # 走 services.response_parser.parse_model_output
      │
      ▼
    save             # 写 memory / state / telemetry
      │
      ▼
    END
"""

from __future__ import annotations

from typing import Any, Optional, TypedDict

from langgraph.graph import END, START, StateGraph

from .contract import AgentInput, AgentOutput


class AgentState(TypedDict, total=False):
    """LangGraph 内部状态 / Mutable state passed between nodes.

    设计成 TypedDict + total=False，方便节点用 partial dict 增量更新。

    注意：langgraph 用 stdlib ``typing.get_type_hints`` 解析 TypedDict，
    无法识别 PEP 604 的 ``X | None`` 写法。这里统一用 ``Optional``。
    """

    # ------- 输入快照 / input snapshot -------
    channel: str
    device_id: str
    user_id: str
    text: str
    event: Optional[str]
    want_tts: bool
    history: list
    profile: dict
    extra: dict

    # ------- 上下文 / context -------
    robot_state: dict
    relationship_state: dict
    sensor_context: str
    relevant_memory: str
    conversation_context: str

    # ------- 决策 / decision -------
    intent: str
    dialogue_act: Optional[str]
    emotional_tone: Optional[str]
    policy: dict
    should_call_llm: bool

    # ------- 工具 / tools -------
    tool_context: str
    tool_result: Optional[dict]
    device_action: Optional[dict]

    # ------- LLM 输入输出 / llm io -------
    messages: list
    raw_model_output: Any
    assistant_text: str

    # ------- 副作用预定 / pending side effects -------
    state_update: dict
    memory_update: dict
    audio_url: Optional[str]

    # ------- 观测 / observability -------
    timing_ms: dict
    debug: dict
    error: Optional[str]


# ---------------------------------------------------------------------------
# 节点实现 / Nodes (TODO: 由你填充)
# ---------------------------------------------------------------------------


async def node_load_context(state: AgentState) -> dict[str, Any]:
    """拉取记忆体系 / Memory system pull.

    这里从五个维度拉上下文：
      1) 用户填写的 profile（含自定义字段）
      2) AI 自己总结的画像
      3) 宠物状态
      4) 临时记忆（24h 内全部对话）
      5) 短期记忆（最近 N 条摘要）
      6) 长期记忆（基于当前 user_text 的 topK 召回）

    业务逻辑请见 ``app.services.memory_recall.load_memory_context``。
    """
    from ..services.memory_recall import load_memory_context

    user_id = state.get("user_id") or "tablepet"
    text = state.get("text") or ""
    ctx = await load_memory_context(user_id, query_text=text)
    return {
        "profile": ctx.user_profile,
        "history": ctx.to_messages(include_ephemeral=True),
        "robot_state": ctx.pet_state,
        "relationship_state": ctx.ai_profile,
        "sensor_context": "",
        "relevant_memory": "",
        "conversation_context": ctx.to_system_block(),
    }


async def node_detect_intent(state: AgentState) -> dict[str, Any]:
    """识别意图 / Intent classification.

    TODO:
      - 调 ``services.intent.detect_intent(text, event, source=channel)``
      - 或者换成 LLM-based intent，自由发挥
    """
    return {"intent": "chat"}


async def node_policy(state: AgentState) -> dict[str, Any]:
    """选择对话策略 / Dialogue policy.

    TODO:
      - 调 ``services.dialogue_policy.choose_dialogue_policy(...)``
      - 决定 should_call_llm / dialogue_act / emotional_tone
    """
    return {
        "policy": {},
        "dialogue_act": None,
        "emotional_tone": None,
        "should_call_llm": True,
    }


async def node_tool_call(state: AgentState) -> dict[str, Any]:
    """工具调用 / Tool execution.

    TODO:
      - 调 ``services.tool_registry.run_tool_if_needed(intent, text, device_id, runtime)``
      - 如果 skip_llm=True，把 should_call_llm 置 False，并准备直接回复
    """
    return {
        "tool_context": "",
        "tool_result": None,
        "device_action": None,
    }


async def node_llm(state: AgentState) -> dict[str, Any]:
    """构造 prompt 并调用主对话模型 / Build prompt then call chat LLM."""
    from ..services.chat import call_deepseek_messages, extract_text, extract_metrics
    from ..services.tokens import count_messages_tokens
    from ..services.memory_summarizer import summarize_to_short_term
    from ..config import MEMORY_CONTEXT_BUDGET_TOKENS
    import time as _time

    text = state.get("text") or ""
    if not text:
        return {"messages": [], "raw_model_output": None}

    system_block = state.get("conversation_context") or ""
    # 人格 prompt 由 ``services/personas.py`` 维护，由 ``ui_settings`` 选择当前模式。
    # 在这里**每次调用都** resolve 一次，所以选 "random" 时每次回复都会换人格。
    persona_id = "default"
    persona_label = "原版桌宠"
    is_random_mode = False
    try:
        from ..storage import get_setting
        from ..services.personas import resolve_active_persona, DEFAULT_MODE, RANDOM_MODE
        user_id_for_persona = state.get("user_id") or "tablepet"
        mode = get_setting(user_id_for_persona, "pet_persona_mode") or DEFAULT_MODE
        is_random_mode = (mode == RANDOM_MODE)
        active = resolve_active_persona(mode)
        persona = active["prompt"]
        persona_id = active.get("id", "default")
        persona_label = active.get("label", persona_id)
    except Exception:
        # 任何 storage / 模块导入异常都安全回退到原 baseline，不影响对话
        persona = (
            "你是 TablePet——一只会聊天、有情绪、有记忆的桌面宠物。"
            "你说话自然、口语化、带一点撒娇感。"
        )
    # 随机模式下，强制 LLM 忽略历史回复语气，严格按照本轮抽到的人格说话。
    # 否则模型会从 history 里 assistant 之前的语气延续过来，造成"看起来没换人格"。
    if is_random_mode:
        persona = (
            f"⚠️ 本轮人格：「{persona_label}」（随机模式 / id={persona_id}）。\n"
            "你必须严格按照下面这一段人格设定说话，**完全忽略历史对话里你自己之前的语气、口头禅和自称**。"
            "随机模式的特性就是每一轮回复风格都不同，这是预期行为；如果上一轮你叫自己「喵酱」，本轮人格不让你这样叫，你就要换。\n"
            "⚠️ 即使在随机模式下，也绝对不要输出括号动作描写、emoji、Markdown —— 全部输出都会被 TTS 念出来。\n\n"
            + persona
        )
    import logging as _logging
    _logging.getLogger(__name__).info(
        "node_llm persona=%s mode_random=%s user=%s",
        persona_id, is_random_mode, state.get("user_id"),
    )
    system = persona + "\n\n" + system_block if system_block else persona

    history = state.get("history") or []
    messages = [{"role": "system", "content": system}, *history, {"role": "user", "content": text}]

    total_tokens = count_messages_tokens(messages)
    if total_tokens > MEMORY_CONTEXT_BUDGET_TOKENS:
        user_id = state.get("user_id") or "tablepet"
        try:
            await summarize_to_short_term(user_id, force=True)
        except Exception:
            pass
        from ..services.memory_recall import load_memory_context

        ctx = await load_memory_context(user_id, query_text=text)
        system = persona + "\n\n" + ctx.to_system_block()
        history = ctx.to_messages(include_ephemeral=True)
        messages = [
            {"role": "system", "content": system},
            *history,
            {"role": "user", "content": text},
        ]

    user_id = state.get("user_id") or "tablepet"
    started = _time.perf_counter()
    try:
        resp = await call_deepseek_messages(
            messages,
            max_tokens=600,
            user_id=user_id,
            purpose="chat",
            session_id=None,  # 这里还不知道 turn 写到哪个 session，下面 save 节点统一处理
        )
    except Exception as exc:
        elapsed = int((_time.perf_counter() - started) * 1000)
        return {
            "messages": messages,
            "raw_model_output": None,
            "error": str(exc)[:200],
            "latency_ms": elapsed,
        }

    elapsed = int((_time.perf_counter() - started) * 1000)
    metrics = extract_metrics(resp)
    return {
        "messages": messages,
        "raw_model_output": resp,
        "assistant_text": extract_text(resp),
        "latency_ms": elapsed,
        "prompt_tokens": metrics["prompt_tokens"],
        "completion_tokens": metrics["completion_tokens"],
        "total_tokens": metrics["total_tokens"],
    }


async def node_parse(state: AgentState) -> dict[str, Any]:
    """解析模型输出 / Parse model output.

    现在 prompt 不要求结构化 JSON，直接拿 assistant_text 就行。
    在这里顺便做 TTS 友好清洗：去掉括号动作描写、emoji、Markdown 标记，
    防止 USB / WiFi 端把 "（歪头思考）" 这种舞台说明真的念出来。
    """
    import re as _re

    text = state.get("assistant_text") or ""
    cleaned = _sanitize_for_tts(text) if text else text
    return {"assistant_text": cleaned, "state_update": {}, "memory_update": {}}


def _sanitize_for_tts(text: str) -> str:
    """把 LLM 输出里的"屏幕看才有意义"的内容剥掉，让 TTS 念出来不违和。

    - 去掉中英文括号包裹的动作 / 神态描写：（歪头思考）/(smile)
    - 去掉 *动作* / **加粗** 这种 markdown
    - 去掉 emoji（基本平面之外的码点 + 杂项符号区段）
    - 去掉行首 Markdown 列表符号 / 标题符号
    """
    import re as _re
    if not text:
        return text
    s = text
    # 1) 去掉中英文圆括号里"看起来是动作描写"的部分（≤30 字、不含完整问号/句号）
    #    保留正常的解释性括号（如「东京（日本首都）」）—— 用「不含整句结尾标点」的启发式。
    def _is_action_paren(inner: str) -> bool:
        if len(inner) > 60:
            return False
        # 含动作 / 心理描写关键词 → 强制判定为舞台说明，即使里面有 ！？也要剥掉
        action_kw = ("歪头", "思考", "状", "抖动", "扑", "拽", "委屈", "撒娇", "眨眼",
                     "挠", "蹭", "尾巴", "耳朵", "偷偷", "偷瞄", "悄悄", "心想", "心里",
                     "笑", "叹气", "翻", "举起", "蹦", "跳", "趴", "瘫", "假装", "故作",
                     "突然", "记着", "嘟囔", "嘀咕")
        if any(k in inner for k in action_kw):
            return True
        if "*" in inner or "~" in inner:
            return True
        # 解释性括号（如「东京（日本首都）」、「Lv.5（精通）」）保留 —— 默认不剥
        return False

    s = _re.sub(r"（([^（）]{0,60})）", lambda m: "" if _is_action_paren(m.group(1)) else m.group(0), s)
    s = _re.sub(r"\(([^()]{0,60})\)", lambda m: "" if _is_action_paren(m.group(1)) else m.group(0), s)
    # 2) 去掉 *xxx* / **xxx** 这种 markdown 强调和星号包裹的动作
    s = _re.sub(r"\*\*([^*]+)\*\*", r"\1", s)
    s = _re.sub(r"\*([^*]+)\*", "", s)
    # 3) 去掉 emoji（粗略覆盖常见区段）
    s = _re.sub(
        r"[\U0001F300-\U0001FAFF\U00002600-\U000027BF\U0001F000-\U0001F2FF]",
        "", s,
    )
    # 4) 行首 Markdown 列表 / 标题
    s = _re.sub(r"^[ \t]*[-•*]\s+", "", s, flags=_re.MULTILINE)
    s = _re.sub(r"^[ \t]*#+\s+", "", s, flags=_re.MULTILINE)
    # 5) 多余空白整理
    s = _re.sub(r"[ \t]+\n", "\n", s)
    s = _re.sub(r"\n{3,}", "\n\n", s)
    s = _re.sub(r"  +", " ", s)
    return s.strip()


async def node_save(state: AgentState) -> dict[str, Any]:
    """副作用提交：落 turn + 触发心情事件 + 阈值兜底总结."""
    from ..services.tokens import count_tokens
    from ..services.memory_summarizer import summarize_to_short_term
    from ..storage import append_turn, apply_event
    from ..storage.db import get_conn

    user_id = state.get("user_id") or "tablepet"
    user_text = (state.get("text") or "").strip()
    assistant_text = (state.get("assistant_text") or "").strip()
    if user_text or assistant_text:
        try:
            turn_id = append_turn(
                user_id,
                user_text,
                assistant_text,
                channel=state.get("channel") or "",
                user_tokens=int(state.get("prompt_tokens") or count_tokens(user_text)),
                assistant_tokens=int(state.get("completion_tokens") or count_tokens(assistant_text)),
            )
            # 把 latency_ms / model 也回填到 turn
            latency = int(state.get("latency_ms") or 0)
            if latency:
                with get_conn() as conn:
                    conn.execute(
                        "UPDATE conversation_turn SET latency_ms = ? WHERE id = ?",
                        (latency, turn_id),
                    )
        except Exception:
            pass
        try:
            apply_event(user_id, "chat")
        except Exception:
            pass
        try:
            await summarize_to_short_term(user_id, force=False)
        except Exception:
            pass
    return {"audio_url": None}


# ---------------------------------------------------------------------------
# 路由 / Conditional edges
# ---------------------------------------------------------------------------


def _route_after_tool(state: AgentState) -> str:
    """工具执行后是否还要走 LLM。"""
    return "llm" if state.get("should_call_llm", True) else "save"


# ---------------------------------------------------------------------------
# 编译 / Compile graph
# ---------------------------------------------------------------------------


def build_graph():
    """构建并编译 langgraph，模块级缓存以避免重复编译。"""
    graph = StateGraph(AgentState)

    graph.add_node("load_context", node_load_context)
    graph.add_node("detect_intent", node_detect_intent)
    graph.add_node("policy", node_policy)
    graph.add_node("tool_call", node_tool_call)
    graph.add_node("llm", node_llm)
    graph.add_node("parse", node_parse)
    graph.add_node("save", node_save)

    graph.add_edge(START, "load_context")
    graph.add_edge("load_context", "detect_intent")
    graph.add_edge("detect_intent", "policy")
    graph.add_edge("policy", "tool_call")
    graph.add_conditional_edges(
        "tool_call",
        _route_after_tool,
        {"llm": "llm", "save": "save"},
    )
    graph.add_edge("llm", "parse")
    graph.add_edge("parse", "save")
    graph.add_edge("save", END)

    return graph.compile()


_compiled_graph = None


def get_graph():
    """单例：第一次访问时编译。"""
    global _compiled_graph
    if _compiled_graph is None:
        _compiled_graph = build_graph()
    return _compiled_graph


def initial_state(payload: AgentInput) -> AgentState:
    """把 ``AgentInput`` 平铺成 graph 的初始 state.

    注意：history / profile 故意留空，由 ``node_load_context`` 自己从 memory 拉。
    AgentInput 已经瘦身过，不再带 history / profile 字段。
    """
    return AgentState(
        channel=payload.channel.value,
        device_id=payload.device_id,
        user_id=payload.user_id,
        text=(payload.text or "").strip(),
        event=payload.event,
        want_tts=payload.want_tts,
        locale=payload.locale,
        extra=dict(payload.extra),
        intent="chat",
        should_call_llm=True,
        timing_ms={},
        debug={},
    )


def to_output(state: AgentState) -> AgentOutput:
    """把 graph 终态映射回 ``AgentOutput``。"""
    return AgentOutput(
        ok=state.get("error") is None,
        reply=state.get("assistant_text", "") or "",
        intent=state.get("intent", "chat"),
        dialogue_act=state.get("dialogue_act"),
        emotional_tone=state.get("emotional_tone"),
        audio_url=state.get("audio_url"),
        device_action=state.get("device_action"),
        state_update=state.get("state_update") or None,
        memory_update=state.get("memory_update") or None,
        timing_ms=state.get("timing_ms", {}),
        debug=state.get("debug", {}),
        error=state.get("error"),
    )
