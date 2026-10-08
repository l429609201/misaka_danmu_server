"""每十五分钟同步追更日程的内置轮询任务。"""

import logging

from fastapi import FastAPI

from src.services.service_container import get_database_service, get_metadata_service
from src.workflows.calendar.schedule_flow import sync_calendar_schedule

from .base import BasePollingTask

logger = logging.getLogger("ScheduleSync")


class ScheduleSyncPollingTask(BasePollingTask):
    """自动同步追更作品的播出日程。"""

    name = "schedule_sync"
    enabled_key = ""
    interval_key = ""
    default_interval = 15
    min_interval = 5
    startup_delay = 90

    @staticmethod
    async def handler(app: FastAPI) -> None:
        """由内置任务调度器触发同步。"""
        await _schedule_sync_handler(app)


async def _schedule_sync_handler(app: FastAPI) -> None:
    """获取首位用户并委托统一日程 Workflow。"""
    db = get_database_service()
    async with db.transaction():
        user = await db.user.get_first_user()
    if user is None:
        return
    result = await sync_calendar_schedule(user, get_metadata_service())
    if result["updatedCount"] or result["boundCount"]:
        logger.info("日程同步完成: %s", result["message"])
