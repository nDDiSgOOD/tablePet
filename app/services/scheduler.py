"""APScheduler 后台任务 / Background scheduled tasks.

注册的任务
==========
- 每小时（``SCHEDULER_PET_TICK_MINUTES``）跑 ``update_pet_state_hourly``
- 每天凌晨（``SCHEDULER_DAILY_SUMMARY_HOUR:MINUTE``）跑昨日总结 + AI 画像更新

设计决策
========
- 使用 ``AsyncIOScheduler``，与 FastAPI 共用 event loop，不需要单独线程；
- 任务定时间用 cron / interval；
- 单用户固定 ``DEFAULT_USER_ID``（你的需求），多用户场景未来可以遍历用户表。
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from ..config import (
    SCHEDULER_DAILY_SUMMARY_HOUR,
    SCHEDULER_DAILY_SUMMARY_MINUTE,
    SCHEDULER_ENABLED,
    SCHEDULER_PET_TICK_MINUTES,
)
from ..memory import DEFAULT_USER_ID
from .memory_summarizer import (
    summarize_daily,
    update_ai_profile,
    update_pet_state_hourly,
)

logger = logging.getLogger(__name__)

_scheduler: AsyncIOScheduler | None = None


async def _job_pet_tick() -> None:
    try:
        await update_pet_state_hourly(DEFAULT_USER_ID)
    except Exception as exc:  # pragma: no cover - 后台任务不能让进程挂
        logger.warning("pet_tick failed: %s", exc)


async def _job_daily_summary() -> None:
    yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
    try:
        await summarize_daily(DEFAULT_USER_ID, yesterday)
    except Exception as exc:
        logger.warning("daily_summary failed: %s", exc)
    try:
        await update_ai_profile(DEFAULT_USER_ID)
    except Exception as exc:
        logger.warning("ai_profile update failed: %s", exc)


def start_scheduler() -> AsyncIOScheduler | None:
    """在 FastAPI 启动钩子里调用一次。"""
    global _scheduler
    if not SCHEDULER_ENABLED:
        logger.info("scheduler disabled by env")
        return None
    if _scheduler is not None:
        return _scheduler

    sch = AsyncIOScheduler()
    sch.add_job(
        _job_pet_tick,
        IntervalTrigger(minutes=SCHEDULER_PET_TICK_MINUTES),
        id="pet_tick",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )
    sch.add_job(
        _job_daily_summary,
        CronTrigger(
            hour=SCHEDULER_DAILY_SUMMARY_HOUR,
            minute=SCHEDULER_DAILY_SUMMARY_MINUTE,
        ),
        id="daily_summary",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )
    sch.start()
    _scheduler = sch
    logger.info(
        "scheduler started: pet_tick every %dmin, daily_summary at %02d:%02d",
        SCHEDULER_PET_TICK_MINUTES,
        SCHEDULER_DAILY_SUMMARY_HOUR,
        SCHEDULER_DAILY_SUMMARY_MINUTE,
    )
    return sch


def stop_scheduler() -> None:
    global _scheduler
    if _scheduler is not None:
        _scheduler.shutdown(wait=False)
        _scheduler = None
