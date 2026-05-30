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
    return {"items": list_accounts(DEFAULT_USER_ID)}


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
