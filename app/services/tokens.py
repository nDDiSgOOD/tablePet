"""Token 计数 / Token counting helper.

用 ``tiktoken`` 的 ``cl100k_base``（GPT-4 / DeepSeek 等通用 BPE）做近似估计。
DeepSeek 官方未公开 tokenizer，cl100k_base 在中英混合长文本上误差 ≤ 8%，
对预算判断够用了——比按字符 / 按词估准得多。
"""

from __future__ import annotations

from functools import lru_cache

try:
    import tiktoken  # type: ignore
except Exception:  # pragma: no cover
    tiktoken = None  # type: ignore[assignment]

from ..config import MEMORY_TOKEN_ENCODING


@lru_cache(maxsize=4)
def _encoding(name: str):
    if tiktoken is None:
        return None
    try:
        return tiktoken.get_encoding(name)
    except Exception:
        return tiktoken.get_encoding("cl100k_base")


def count_tokens(text: str) -> int:
    if not text:
        return 0
    enc = _encoding(MEMORY_TOKEN_ENCODING)
    if enc is None:
        return max(1, len(text) // 2)
    try:
        return len(enc.encode(text))
    except Exception:
        return max(1, len(text) // 2)


def count_messages_tokens(messages: list[dict]) -> int:
    """OpenAI/Anthropic 格式 messages 的总 token 数（每条 +4 元开销）."""
    total = 0
    for m in messages:
        content = m.get("content") or ""
        if isinstance(content, list):
            for part in content:
                if isinstance(part, dict):
                    content = part.get("text") or ""
                    total += count_tokens(content)
        else:
            total += count_tokens(str(content))
        total += 4
    return total + 2
