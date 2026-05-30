"""Intent detection for the unified TablePet interaction runtime."""

from __future__ import annotations


def _contains_any(text: str, tokens: tuple[str, ...]) -> bool:
    lowered = text.lower()
    return any(token.lower() in lowered for token in tokens)


def detect_intent(text: str | None, event: str | None = None, source: str | None = None) -> str:
    """Small deterministic intent detector used before any LLM call."""
    if event == "force_wake" or source == "button_d9":
        return "force_wake"

    normalized = (text or "").strip()
    if not normalized:
        return "empty"

    if _contains_any(normalized, ("weather", "rain", "temperature", "天气", "天氣", "下雨", "温度", "氣溫")):
        return "weather"
    if _contains_any(normalized, ("music", "play song", "sing", "播放", "放歌", "音乐", "音樂", "唱歌")):
        return "music"
    if _contains_any(normalized, ("remember", "memorize", "记住", "記住", "以后你要记得", "以後你要記得")):
        return "memory_write"
    if _contains_any(normalized, ("do you remember", "what do you know about me", "你还记得", "你還記得", "记得我", "記得我")):
        return "memory_query"
    if _contains_any(normalized, ("battery", "charge", "status", "how are you", "your body", "电量", "電量", "状态", "狀態", "你现在怎么样", "你現在怎麼樣")):
        return "device_status"
    if _contains_any(normalized, ("slow", "delay", "latency", "lag", "卡", "慢", "延迟", "延遲")):
        return "latency_debug"
    if _contains_any(normalized, ("see", "look", "camera", "image", "你看到什么", "你看到什麼", "看一下", "摄像头", "鏡頭")):
        return "vision"
    if _contains_any(normalized, ("move", "turn", "screen", "volume", "brightness", "转动", "轉動", "屏幕", "音量", "亮度")):
        return "device_control"
    if _contains_any(normalized, ("sad", "stressed", "tired", "lonely", "anxious", "难过", "難過", "压力", "壓力", "累", "焦虑", "焦慮", "孤独", "孤單")):
        return "emotional_support"
    if _contains_any(normalized, ("code", "python", "api", "fastapi", "debug", "error", "architecture", "代码", "程式", "架构", "架構", "报错", "報錯")):
        return "technical_help"
    if _contains_any(normalized, ("hello", "how are you", "tell me", "joke", "你好", "聊天", "笑话", "笑話")):
        return "casual_chat"
    return "chat"
