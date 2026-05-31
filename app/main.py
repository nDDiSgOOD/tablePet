"""FastAPI 应用入口 / FastAPI application entry point.

启动方式 / Run with:
    uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
"""

from __future__ import annotations

import asyncio

from fastapi import FastAPI

from .routers import agent_extensions, asr, chat, dashboard, health, interactions, llm_account, memory, music, personas, telemetry, tts, ui_settings, vision
from .services.scheduler import start_scheduler, stop_scheduler
from .usb.bridge import start_usb_bridge_thread


app = FastAPI(title="TablePet Gateway", version="1.0.0")

# 路由注册 / Mount routers
app.include_router(dashboard.router)
app.include_router(health.router)
app.include_router(interactions.router)
app.include_router(asr.router)
app.include_router(tts.router)
app.include_router(chat.router)
app.include_router(vision.router)
app.include_router(music.router)
app.include_router(telemetry.router)
app.include_router(memory.router)
app.include_router(ui_settings.router)
app.include_router(llm_account.router)
app.include_router(personas.router)
app.include_router(agent_extensions.router)


@app.on_event("startup")
async def _start_usb_bridge() -> None:
    """启动后台 USB 串口工作线程。/ Start background USB worker thread."""
    loop = asyncio.get_running_loop()
    start_usb_bridge_thread(loop)


@app.on_event("startup")
async def _start_scheduler() -> None:
    """启动 APScheduler，注册小时/每日记忆总结任务."""
    start_scheduler()


@app.on_event("shutdown")
async def _stop_scheduler() -> None:
    stop_scheduler()
