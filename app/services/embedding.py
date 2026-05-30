"""Ollama 嵌入 adapter / Local embedding via Ollama.

设计参考 Reasonix/src/index/semantic/embedding.ts —— 同样用 Ollama 的
``/api/embeddings`` 接口拉本地向量。优点是零云端依赖，缺点是用户需要
本地运行 ``ollama serve``。如果 Ollama 不可用，调用方应当回退到关键词
检索（见 ``recall_by_vector`` 的 keyword_hint 参数）。
"""

from __future__ import annotations

import logging
import math
from typing import Any

import httpx

from ..config import (
    OLLAMA_BASE_URL,
    OLLAMA_EMBED_MODEL,
    OLLAMA_EMBED_TIMEOUT_SECONDS,
)

logger = logging.getLogger(__name__)


class EmbeddingError(RuntimeError):
    pass


def _l2_normalize(vec: list[float]) -> list[float]:
    norm = math.sqrt(sum(v * v for v in vec))
    if norm == 0:
        return vec
    return [v / norm for v in vec]


async def embed_text(
    text: str,
    *,
    base_url: str | None = None,
    model: str | None = None,
    timeout_seconds: float | None = None,
) -> list[float]:
    """请求 Ollama 给一段文本的向量表示，自动 L2 归一化."""
    text = (text or "").strip()
    if not text:
        return []
    base = base_url or OLLAMA_BASE_URL
    mdl = model or OLLAMA_EMBED_MODEL
    timeout = timeout_seconds or OLLAMA_EMBED_TIMEOUT_SECONDS
    url = f"{base.rstrip('/')}/api/embeddings"
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.post(url, json={"model": mdl, "prompt": text})
    except httpx.ConnectError as exc:
        raise EmbeddingError(
            f"Cannot reach Ollama at {base}. Run `ollama serve` and "
            f"`ollama pull {mdl}` first."
        ) from exc
    except httpx.HTTPError as exc:
        raise EmbeddingError(f"Ollama request failed: {exc}") from exc
    if resp.status_code != 200:
        raise EmbeddingError(f"Ollama returned {resp.status_code}: {resp.text[:200]}")
    data: dict[str, Any] = resp.json()
    vec = data.get("embedding") or []
    if not isinstance(vec, list) or not vec:
        raise EmbeddingError("Ollama embedding response missing 'embedding' field")
    return _l2_normalize([float(v) for v in vec])


async def embed_text_safe(text: str) -> list[float]:
    """容错版本：失败返回空向量，调用方走关键词回退."""
    try:
        return await embed_text(text)
    except EmbeddingError as exc:
        logger.warning("embedding fallback (no vector): %s", exc)
        return []
