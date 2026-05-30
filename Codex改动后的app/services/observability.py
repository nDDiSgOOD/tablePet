"""Small timing helpers for interaction observability."""

from __future__ import annotations

import time
from contextlib import contextmanager
from typing import Iterator


@contextmanager
def timed(timing_ms: dict[str, float], key: str) -> Iterator[None]:
    started = time.perf_counter()
    try:
        yield
    finally:
        timing_ms[key] = round((time.perf_counter() - started) * 1000, 2)
