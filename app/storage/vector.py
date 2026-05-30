"""向量嵌入序列化辅助 / float32 vector helpers for SQLite BLOB storage."""

from __future__ import annotations

import struct


def encode_vector(vec: list[float]) -> bytes:
    """list[float] -> bytes（little-endian float32 紧凑数组）."""
    return struct.pack(f"<{len(vec)}f", *vec)


def decode_vector(blob: bytes | None) -> list[float] | None:
    if not blob:
        return None
    n = len(blob) // 4
    if n == 0:
        return None
    return list(struct.unpack(f"<{n}f", blob))


def cosine(a: list[float], b: list[float]) -> float:
    """两个等长向量的余弦相似度。两边都已 L2-normalize 时退化为点积."""
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = sum(x * x for x in a) ** 0.5
    nb = sum(y * y for y in b) ** 0.5
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)
