"""天气查询 / Weather lookup via wttr.in."""

from __future__ import annotations

import re
from urllib.parse import quote

import httpx
from fastapi import HTTPException

from ..config import WEATHER_LOCATION


def is_weather_request(text: str) -> bool:
    lowered = text.lower()
    return any(
        token in lowered for token in ("天气", "天氣", "气温", "氣溫", "下雨", "weather", "forecast")
    )


def weather_location_from_text(text: str) -> str:
    patterns = [
        r"(?:查|看|问|說|说)?\s*([\u4e00-\u9fffA-Za-z .-]{2,24})\s*(?:天气|天氣|气温|氣溫)",
        r"(?:weather|forecast)\s+(?:in|for)?\s*([A-Za-z .-]{2,32})",
    ]
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if match:
            candidate = match.group(1).strip(" ，,。?？")
            if candidate and not any(
                word in candidate for word in ("今天", "明天", "现在", "現在", "帮我", "幫我")
            ):
                return candidate
    return WEATHER_LOCATION


async def weather_reply(user_text: str) -> str:
    location = weather_location_from_text(user_text)
    url = f"https://wttr.in/{quote(location)}"
    async with httpx.AsyncClient(timeout=8) as client:
        response = await client.get(url, params={"format": "j1", "lang": "zh"})
    if response.status_code != 200:
        raise HTTPException(
            status_code=502, detail=f"Weather lookup failed: {response.text[:200]}"
        )
    data = response.json()
    current = (data.get("current_condition") or [{}])[0]
    today = (data.get("weather") or [{}])[0]
    hourly = today.get("hourly") or []
    desc_items = current.get("lang_zh") or current.get("weatherDesc") or [{}]
    desc = desc_items[0].get("value", "天气状况未知") if desc_items else "天气状况未知"
    temp = current.get("temp_C", "--")
    feels = current.get("FeelsLikeC", "--")
    humidity = current.get("humidity", "--")
    wind = current.get("windspeedKmph", "--")
    rain = today.get("hourly", [{}])[0].get("chanceofrain", "--") if hourly else "--"
    max_temp = today.get("maxtempC", "--")
    min_temp = today.get("mintempC", "--")
    return (
        f"{location}现在{desc}，{temp}度，体感{feels}度，湿度{humidity}%，风速{wind}公里每小时。"
        f"今天大约{min_temp}到{max_temp}度，降雨概率约{rain}%。"
    )
