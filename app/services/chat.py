"""DeepSeek/model API client for TablePet.

Prompt construction and context selection belong to interaction.py and
prompt_builder.py. This module only performs model API calls.

设计要点
========
- **API Key 唯一来源 = 账户情况页面里配置的账号**（落 SQLite ``llm_account`` 表）。
  环境变量里的 DEEPSEEK_API_KEY 已经永久禁用，不会被回退使用。
- ``user_id`` 没传 → 自动用 DEFAULT_USER_ID 兜底，保证总结类后台任务也能拿到账号
- 同一个账号配置里可以分别填 ``chat_model`` / ``summary_model``，
  调用方通过 ``purpose`` 判断走哪个：
  - ``purpose="chat"`` → ``chat_model``
  - ``purpose`` 以 ``"summary"`` 开头（短期/日记/AI画像/会话总结/开场白等） → ``summary_model``
- 每次调用都写一行 ``llm_usage``，给前端的 token / latency / 趋势图提供数据
"""

from __future__ import annotations

import time
from typing import Any

import httpx
from fastapi import HTTPException

from ..config import DEEPSEEK_CHAT_MODEL, DEEPSEEK_SUMMARY_MODEL, DEEPSEEK_URL
from ..memory import DEFAULT_USER_ID
from ..storage import get_active_account, record_usage


def _is_summary_purpose(purpose: str) -> bool:
    """是否属于"总结/后台"类调用 —— 用 summary_model；否则用 chat_model."""
    p = (purpose or "").lower()
    return p.startswith("summary") or p in {"daily", "ai_profile", "pet_tick", "greeting", "session_close"}


def _resolve_endpoint(
    user_id: str,
    model: str | None,
    *,
    purpose: str = "chat",
) -> tuple[str, str, str, int | None]:
    """返回 (api_key, base_url, model, account_id).

    - 始终先查 ``user_id`` 的 active account
    - 没传 user_id 时回落到 ``DEFAULT_USER_ID``（"tablepet"），保证后台任务也命中
    - ``model`` 显式传入 > account.summary_model/chat_model > 全局默认
    """
    uid = user_id or DEFAULT_USER_ID
    acct = get_active_account(uid) or get_active_account(DEFAULT_USER_ID)
    use_summary = _is_summary_purpose(purpose)
    if acct and acct.get("api_key"):
        # 账户配置里挑 chat_model / summary_model
        per_acct_model = (
            (acct.get("summary_model") if use_summary else acct.get("chat_model"))
            or acct.get("chat_model")
            or acct.get("summary_model")
        )
        chosen_model = (
            model
            or per_acct_model
            or (DEEPSEEK_SUMMARY_MODEL if use_summary else DEEPSEEK_CHAT_MODEL)
        )
        # base_url 兼容两种写法：纯 host vs 已经写到 /chat/completions
        raw_base = (acct.get("base_url") or DEEPSEEK_URL).rstrip("/")
        base = raw_base.replace("/chat/completions", "")
        if not base:
            base = DEEPSEEK_URL.rstrip("/").replace("/chat/completions", "")
        return acct["api_key"], base, chosen_model, int(acct["id"])

    # 没配账号 —— 不再回退到环境变量，明确报错让 UI 引导用户配置
    fallback_model = (
        model or (DEEPSEEK_SUMMARY_MODEL if use_summary else DEEPSEEK_CHAT_MODEL)
    )
    return "", DEEPSEEK_URL, fallback_model, None


def _full_chat_url(base: str) -> str:
    """适配两种风格：纯 base_url（拼 /chat/completions）vs 已经写完整 URL."""
    if base.endswith("/chat/completions"):
        return base
    return base.rstrip("/") + "/chat/completions"


async def call_deepseek_messages(
    messages: list[dict[str, Any]],
    *,
    model: str | None = None,
    temperature: float = 0.68,
    max_tokens: int = 500,
    user_id: str | None = None,
    purpose: str = "chat",
    turn_id: int | None = None,
    session_id: int | None = None,
    tools: list[dict[str, Any]] | None = None,
    tool_choice: str | dict[str, Any] | None = None,
) -> Any:
    """主对话 / 总结类调用统一入口。返回原始 DeepSeek JSON。

    - ``user_id`` 不传 → 自动用 DEFAULT_USER_ID 兜底
    - ``purpose``："chat" / "summary.short_term" / "summary.daily" / "summary.ai_profile"
      / "summary.pet_tick" / "summary.session_close" / "summary.greeting" 等。
      根据它会在 ``_resolve_endpoint`` 里自动挑 ``chat_model`` 还是 ``summary_model``。
    """
    api_key, base_url, real_model, account_id = _resolve_endpoint(
        user_id or "", model, purpose=purpose,
    )
    if not api_key:
        raise HTTPException(
            status_code=500,
            detail="未配置 LLM API Key，请前往「账户情况」页面添加。",
        )
    url = _full_chat_url(base_url)
    started = time.perf_counter()
    err_text = ""
    resp_json: Any = None
    try:
        payload: dict[str, Any] = {
            "model": real_model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": False,
            "thinking": {"type": "disabled"},
        }
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = tool_choice or "auto"
        async with httpx.AsyncClient(timeout=60) as client:
            response = await client.post(
                url,
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
                json=payload,
            )
        if response.status_code != 200:
            err_text = response.text[:300]
            raise HTTPException(status_code=502, detail=err_text)
        resp_json = response.json()
        return resp_json
    except HTTPException:
        raise
    except Exception as exc:
        err_text = str(exc)[:300]
        raise HTTPException(status_code=502, detail=err_text) from exc
    finally:
        elapsed_ms = int((time.perf_counter() - started) * 1000)
        # 即使 user_id 没传，也用 DEFAULT_USER_ID 把 usage 落库 —— 否则后台总结调用就不计入用量
        uid_for_usage = user_id or DEFAULT_USER_ID
        usage = (resp_json or {}).get("usage") if isinstance(resp_json, dict) else None
        try:
            record_usage(
                uid_for_usage,
                purpose=purpose,
                model=real_model,
                prompt_tokens=int((usage or {}).get("prompt_tokens") or 0),
                completion_tokens=int((usage or {}).get("completion_tokens") or 0),
                elapsed_ms=elapsed_ms,
                turn_id=turn_id,
                session_id=session_id,
                account_id=account_id,
                ok=not err_text,
                err=err_text,
            )
        except Exception:
            pass


def extract_text(response: Any) -> str:
    """从 DeepSeek 响应中提取 assistant 文本（兼容 None / 异常）."""
    if not isinstance(response, dict):
        return ""
    choices = response.get("choices") or []
    if not choices:
        return ""
    msg = choices[0].get("message") or {}
    return str(msg.get("content") or "").strip()


def extract_metrics(response: Any) -> dict[str, int]:
    """从 DeepSeek 响应中提取 token 统计（不存在则返回 0）."""
    if not isinstance(response, dict):
        return {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
    u = response.get("usage") or {}
    return {
        "prompt_tokens": int(u.get("prompt_tokens") or 0),
        "completion_tokens": int(u.get("completion_tokens") or 0),
        "total_tokens": int(u.get("total_tokens") or 0),
    }
