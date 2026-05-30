"""LLM 账号管理 + 用量统计 路由.

设计：
- 账户多套，``is_active=1`` 那条对所有 LLM 调用生效
- 切换 / 改 api_key 立刻热生效（chat.py 每次调用都重新 _resolve_endpoint）
- usage 表给前端的 token / 趋势图供数据
- 余额优先用 ``provider`` 自身的 ``/user/balance`` 接口实时查询，失败再 fallback 到本地手动录入值
"""

from __future__ import annotations

import time
from typing import Any

import httpx
from fastapi import APIRouter, HTTPException

from ..memory import DEFAULT_USER_ID
from ..storage import (
    delete_account,
    get_account,
    get_active_account,
    list_accounts,
    set_active_account,
    upsert_account,
    usage_by_day,
    usage_daily,
    usage_today,
)

router = APIRouter()


# ---------------------------------------------------------------------------
# 远程余额查询 / Remote balance probing
# ---------------------------------------------------------------------------
_BALANCE_CACHE: dict[int, dict[str, Any]] = {}  # account_id -> {ts, payload}
_BALANCE_TTL = 60.0  # 秒


def _balance_url(provider: str, base_url: str) -> str | None:
    """目前支持 deepseek 一家；其它 provider 返回 None 让前端走本地 fallback."""
    base = (base_url or "").rstrip("/")
    if provider == "deepseek":
        # 兼容 base_url 写成 https://api.deepseek.com 或 https://api.deepseek.com/v1
        # 官方文档：GET https://api.deepseek.com/user/balance
        if base.endswith("/v1"):
            base = base[:-3]
        if not base:
            base = "https://api.deepseek.com"
        return f"{base}/user/balance"
    return None


async def _fetch_remote_balance(account: dict[str, Any]) -> dict[str, Any] | None:
    """调 provider 的 /user/balance 取真实余额。失败返回 None."""
    api_key = account.get("api_key") or ""
    if not api_key:
        return None
    url = _balance_url(account.get("provider") or "", account.get("base_url") or "")
    if not url:
        return None
    aid = int(account["id"])
    cached = _BALANCE_CACHE.get(aid)
    if cached and time.time() - cached["ts"] < _BALANCE_TTL:
        return cached["payload"]
    try:
        async with httpx.AsyncClient(timeout=8) as client:
            r = await client.get(url, headers={"Authorization": f"Bearer {api_key}", "Accept": "application/json"})
        if r.status_code != 200:
            return None
        data = r.json()
    except Exception:
        return None

    # DeepSeek schema: {"is_available": true, "balance_infos": [{"currency":"CNY","total_balance":"10.00",...}]}
    payload: dict[str, Any] = {"raw": data, "source": "remote"}
    if isinstance(data, dict):
        infos = data.get("balance_infos") or []
        if infos and isinstance(infos, list):
            preferred = next(
                (b for b in infos if (b.get("currency") or "").upper() == (account.get("balance_currency") or "CNY").upper()),
                infos[0],
            )
            try:
                payload["balance"] = float(preferred.get("total_balance") or 0)
                payload["balance_currency"] = preferred.get("currency") or "CNY"
                payload["granted"] = float(preferred.get("granted_balance") or 0) if preferred.get("granted_balance") is not None else None
                payload["topped_up"] = float(preferred.get("topped_up_balance") or 0) if preferred.get("topped_up_balance") is not None else None
            except (TypeError, ValueError):
                return None
            payload["is_available"] = bool(data.get("is_available", True))
    if "balance" not in payload:
        return None
    _BALANCE_CACHE[aid] = {"ts": time.time(), "payload": payload}
    return payload


def _invalidate_balance_cache(account_id: int | None = None) -> None:
    if account_id is None:
        _BALANCE_CACHE.clear()
    else:
        _BALANCE_CACHE.pop(int(account_id), None)


async def _balance_for(active: dict[str, Any] | None) -> dict[str, Any]:
    """统一的"先远程后本地"余额计算，返回 {balance, balance_currency, balance_source}.

    - balance_source: ``remote`` (现拉的) / ``local`` (用户手填) / ``none`` (没数据)
    """
    if not active:
        return {"balance": None, "balance_currency": None, "balance_source": "none"}
    remote = await _fetch_remote_balance(active)
    if remote and remote.get("balance") is not None:
        return {
            "balance": remote["balance"],
            "balance_currency": remote.get("balance_currency") or active.get("balance_currency") or "CNY",
            "balance_source": "remote",
            "remote_extra": {
                k: remote.get(k) for k in ("granted", "topped_up", "is_available") if remote.get(k) is not None
            } or None,
        }
    if active.get("balance") is not None:
        return {
            "balance": float(active["balance"]),
            "balance_currency": active.get("balance_currency") or "CNY",
            "balance_source": "local",
        }
    return {"balance": None, "balance_currency": active.get("balance_currency"), "balance_source": "none"}


# ---------------------------------------------------------------------------
# 账号 CRUD
# ---------------------------------------------------------------------------
@router.get("/api/llm/accounts")
async def api_list_accounts() -> dict[str, Any]:
    """列出所有 LLM 账号，**每条都顺手并发拉一次 provider 实时余额**。

    设计要点：
    - 老实现只返回 DB 里的 balance 字段（默认 0），导致前端列表全是 0，
      用户必须手点 "🔄 余额" 才能看到真实数。这违反"启动即可用"原则。
    - 现在统一在路由层对每条账号 await 一次 ``_balance_for(a)``——
      内部已有 ``_BALANCE_CACHE`` 60s 缓存（见 _BALANCE_TTL），所以
      多账号并发也不会反复打 provider；同账号 30s 轮询命中缓存几乎零成本。
    - 失败的 provider 退化为 ``balance_source='local'``（用 DB 里的兜底值）
      或 ``'none'``，前端按现有 ``bal-tag`` 渲染逻辑展示。
    - 用 ``asyncio.gather`` 并发，N 条账号总耗时 ≈ 单条 RTT，不会线性放大。
    """
    import asyncio

    items = list_accounts(DEFAULT_USER_ID)
    if not items:
        return {"items": []}

    async def _enrich(a: dict[str, Any]) -> dict[str, Any]:
        bal = await _balance_for(a)
        # _balance_for 返回 {balance, balance_currency, balance_source[, remote_extra]}
        # 注意：直接覆盖原 a 的 balance / balance_currency 字段，让前端
        # 卡片渲染逻辑（_renderAcctCard 读 a.balance）天然拿到实时值，不用
        # 再单独维护 _liveBalance map。balance_source 也带上方便前端打 tag。
        a["balance"] = bal["balance"]
        a["balance_currency"] = bal["balance_currency"] or a.get("balance_currency") or "CNY"
        a["balance_source"] = bal["balance_source"]
        return a

    enriched = await asyncio.gather(*(_enrich(a) for a in items))
    return {"items": list(enriched)}


@router.get("/api/llm/active")
async def api_get_active() -> dict[str, Any]:
    a = get_active_account(DEFAULT_USER_ID)
    if not a:
        return {"configured": False}
    a["api_key_masked"] = (a["api_key"][:6] + "***" + a["api_key"][-4:]) if a["api_key"] else ""
    a.pop("api_key", None)
    a["configured"] = True
    bal = await _balance_for(a)
    a.update(bal)
    return a


@router.post("/api/llm/accounts")
async def api_create_account(payload: dict[str, Any]) -> dict[str, Any]:
    new_id = upsert_account(DEFAULT_USER_ID, payload)
    _invalidate_balance_cache()
    return {"ok": True, "id": new_id}


@router.put("/api/llm/accounts/{account_id}")
async def api_update_account(account_id: int, payload: dict[str, Any]) -> dict[str, Any]:
    existing = get_account(DEFAULT_USER_ID, account_id)
    if not existing:
        raise HTTPException(status_code=404, detail="account not found")
    # 余额 / 币种现在完全由 provider 远程查询，前端不再录入。如果调用方
    # 没传这两个字段，保持数据库旧值不动，避免误把老用户手填过的兜底值
    # 覆盖成 0（虽然显示不依赖它，但 _balance_for 在 provider 拿不到时
    # 会用它做 fallback，写成 0 会让 fallback 跌成 "0 元"）。
    merged = {**payload}
    if "balance" not in merged and existing.get("balance") is not None:
        merged["balance"] = existing["balance"]
    if "balance_currency" not in merged and existing.get("balance_currency"):
        merged["balance_currency"] = existing["balance_currency"]
    upsert_account(DEFAULT_USER_ID, merged, account_id=account_id)
    _invalidate_balance_cache(account_id)
    return {"ok": True}


@router.post("/api/llm/accounts/{account_id}/activate")
async def api_activate_account(account_id: int) -> dict[str, bool]:
    if not set_active_account(DEFAULT_USER_ID, account_id):
        raise HTTPException(status_code=404, detail="account not found")
    _invalidate_balance_cache()
    return {"ok": True}


@router.delete("/api/llm/accounts/{account_id}")
async def api_delete_account(account_id: int) -> dict[str, bool]:
    if not delete_account(DEFAULT_USER_ID, account_id):
        raise HTTPException(status_code=404, detail="account not found")
    _invalidate_balance_cache(account_id)
    return {"ok": True}


# ---------------------------------------------------------------------------
# 模型清单探测 / Probe available models via OpenAI-compatible /v1/models
# ---------------------------------------------------------------------------
_MODELS_CACHE: dict[str, dict[str, Any]] = {}  # cache key -> {ts, models}
_MODELS_TTL = 300.0  # 5 分钟，避免 input 抖动反复打 provider


def _models_url_candidates(base_url: str) -> list[str]:
    """按"先 DeepSeek 风格 / 再 OpenAI 风格"返回候选 URL 列表。

    各 provider 的官方 spec 不一致：
    - DeepSeek 文档：``GET https://api.deepseek.com/models``（**无 /v1**）
    - OpenAI 文档：``GET https://api.openai.com/v1/models``（**必须 /v1**）
    - Moonshot / 智谱 / 通义等多遵循 OpenAI 风格

    所以单一拼接路径会漏。我们做双探测，第一个返 200 且 schema 合法就用它。
    用户写 base_url 时也可能已经写到 /v1 后缀，这里都做归一化。
    """
    base = (base_url or "").rstrip("/")
    # 用户可能写成 .../chat/completions 这种"已经写到具体方法"的形式，
    # 把它退回到根 base
    if base.endswith("/chat/completions"):
        base = base[: -len("/chat/completions")]
    # 再剥掉一个 /v1（如果有），统一成 root，方便我们组合两种候选
    root = base[:-3].rstrip("/") if base.endswith("/v1") else base
    candidates: list[str] = []
    # 先 DeepSeek 风格（无 /v1）—— 这是 DeepSeek 文档的官方推荐路径
    candidates.append(f"{root}/models")
    # 再 OpenAI 风格（带 /v1）—— OpenAI / Moonshot / 智谱 等的标准
    candidates.append(f"{root}/v1/models")
    # 去重保留顺序
    seen: set[str] = set()
    out: list[str] = []
    for u in candidates:
        if u not in seen:
            seen.add(u)
            out.append(u)
    return out


@router.post("/api/llm/probe-models")
async def api_probe_models(payload: dict[str, Any]) -> dict[str, Any]:
    """用 ``base_url + api_key`` 双探测拉模型清单。

    设计：
    - 不依赖任何已存账号，纯探测。前端在编辑表单里 ``api_key`` debounce
      之后调，结果填到自定义 combobox。
    - 5min 缓存（key = base_url + api_key 末 8 位）；缓存的是接口返回的
      models 数组本身，不是按输入缓存。
    - 双探测：先试 DeepSeek 风格 ``{root}/models``，schema 合法就用；
      否则 fallback OpenAI 风格 ``{root}/v1/models``。两个都拿到结果时，
      取**模型数量更多**的那个（DeepSeek 上 /v1/models 是 alias 但内容更少
      的情况就是这条规则的目标场景）。
    - 401 / 403 直接返 ok=false，不再 fallback —— auth 错跟路径无关。
    """
    base_url = str(payload.get("base_url") or "").strip()
    api_key = str(payload.get("api_key") or "").strip()
    if not base_url or not api_key:
        raise HTTPException(status_code=400, detail="base_url 与 api_key 都是必填")

    cache_key = f"{base_url}::{api_key[-8:]}"
    cached = _MODELS_CACHE.get(cache_key)
    if cached and time.time() - cached["ts"] < _MODELS_TTL:
        return {"ok": True, "models": cached["models"], "cached": True}

    candidates = _models_url_candidates(base_url)
    last_error = ""
    auth_failed = False
    successes: list[tuple[str, list[str]]] = []  # (url, models)

    async with httpx.AsyncClient(timeout=8) as client:
        for url in candidates:
            try:
                r = await client.get(
                    url,
                    headers={
                        "Authorization": f"Bearer {api_key}",
                        "Accept": "application/json",
                    },
                )
            except Exception as exc:
                last_error = f"网络异常：{str(exc)[:120]}"
                continue

            if r.status_code in (401, 403):
                # auth 错跟路径无关，立刻退出 —— 再试别的路径也是 401，纯浪费
                auth_failed = True
                last_error = "API Key 无效或没有访问 /models 的权限"
                break
            if r.status_code != 200:
                last_error = f"{url} 返回 HTTP {r.status_code}"
                continue
            try:
                data = r.json()
            except Exception:
                last_error = f"{url} 返回非 JSON"
                continue
            items = data.get("data") if isinstance(data, dict) else None
            if not isinstance(items, list):
                last_error = f"{url} schema 不符合 OpenAI spec"
                continue
            models: list[str] = []
            for it in items:
                if isinstance(it, dict):
                    mid = it.get("id")
                    if isinstance(mid, str) and mid:
                        models.append(mid)
            models = sorted(set(models))
            if models:
                successes.append((url, models))

    if auth_failed:
        return {"ok": False, "error": last_error}

    if not successes:
        return {"ok": False, "error": last_error or "未能从任何标准路径拿到模型清单"}

    # 取模型数量最多的那条 —— 同 provider 不同路径返回不一致时
    # （DeepSeek 上 /v1/models 是 alias 但只返回部分模型即此场景），
    # 数量多的更可能是真清单
    successes.sort(key=lambda x: len(x[1]), reverse=True)
    chosen_url, chosen_models = successes[0]

    _MODELS_CACHE[cache_key] = {"ts": time.time(), "models": chosen_models}
    return {
        "ok": True,
        "models": chosen_models,
        "cached": False,
        "source_url": chosen_url,
    }


@router.post("/api/llm/accounts/{account_id}/refresh-balance")
async def api_refresh_balance(account_id: int) -> dict[str, Any]:
    """强制刷新这条账号的远程余额（绕过缓存）."""
    a = get_account(DEFAULT_USER_ID, account_id)
    if not a:
        raise HTTPException(status_code=404, detail="account not found")
    _invalidate_balance_cache(account_id)
    bal = await _balance_for(a)
    return {"ok": True, **bal}


# ---------------------------------------------------------------------------
# 用量统计
# ---------------------------------------------------------------------------
@router.get("/api/usage/today")
async def api_usage_today() -> dict[str, Any]:
    today = usage_today(DEFAULT_USER_ID)
    active = get_active_account(DEFAULT_USER_ID)
    bal = await _balance_for(active)
    return {
        "tokens": today["tokens"],
        "calls": today["calls"],
        "cost": today["cost"],
        "balance": bal["balance"],
        "balance_currency": bal["balance_currency"],
        "balance_source": bal["balance_source"],
        "configured": bool(active and active.get("api_key")),
    }


@router.get("/api/usage/daily")
async def api_usage_daily(days: int = 30) -> dict[str, Any]:
    return {"items": usage_daily(DEFAULT_USER_ID, max(1, min(180, int(days))))}


@router.get("/api/usage/day/{day}")
async def api_usage_day(day: str) -> dict[str, Any]:
    return usage_by_day(DEFAULT_USER_ID, day)
