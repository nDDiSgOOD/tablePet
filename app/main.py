"""FastAPI 应用入口 / FastAPI application entry point.

启动方式 / Run with:
    uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
"""

from __future__ import annotations

import asyncio

from fastapi import FastAPI

from .routers import asr, chat, dashboard, health, music, telemetry, tts, vision
from .usb.bridge import start_usb_bridge_thread


app = FastAPI(title="TablePet Gateway", version="1.0.0")

# 路由注册 / Mount routers
app.include_router(dashboard.router)
app.include_router(health.router)
app.include_router(asr.router)
app.include_router(tts.router)
app.include_router(chat.router)
app.include_router(vision.router)
app.include_router(music.router)
app.include_router(telemetry.router)


@app.on_event("startup")
async def _start_usb_bridge() -> None:
    """启动后台 USB 串口工作线程。/ Start background USB worker thread."""
    loop = asyncio.get_running_loop()
    start_usb_bridge_thread(loop)
