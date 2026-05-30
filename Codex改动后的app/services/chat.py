"""DeepSeek/model API client for TablePet.

Prompt construction and context selection belong to interaction.py and
prompt_builder.py. This module only performs model API calls.
"""

from __future__ import annotations

from typing import Any

import httpx
from fastapi import HTTPException

from ..config import DEEPSEEK_API_KEY, DEEPSEEK_MODEL, DEEPSEEK_URL


async def call_deepseek_messages(messages: list[dict[str, str]]) -> Any:
    if not DEEPSEEK_API_KEY:
        raise HTTPException(
            status_code=500, detail="Set DEEPSEEK_API_KEY for gateway chat proxy."
        )
    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.post(
            DEEPSEEK_URL,
            headers={
                "Authorization": f"Bearer {DEEPSEEK_API_KEY}",
                "Content-Type": "application/json",
            },
            json={
                "model": DEEPSEEK_MODEL,
                "messages": messages,
                "temperature": 0.68,
                "max_tokens": 500,
                "stream": False,
                "thinking": {"type": "disabled"},
            },
        )
    if response.status_code != 200:
        raise HTTPException(status_code=502, detail=response.text)
    return response.json()
