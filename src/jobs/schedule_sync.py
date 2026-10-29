"""日程同步定时任务：复用统一 Workflow。"""

from typing import Any, Callable

from src.jobs.base import BaseJob
from src.utils.diagnostics.task_exceptions import TaskSuccess
from src.services.service_container import get_database_service, get_metadata_service
from src.workflows.calendar.schedule_flow import sync_calendar_schedule


class ScheduleSyncJob(BaseJob):
    """自动从 Bangumi / Trakt 同步追更番剧日程。"""

    job_type = "scheduleSync"
    job_name = "日程同步"
    job_name_en = "Schedule Sync"
    job_name_tw = "日程同步"
    description = "自动从 Bangumi 和 Trakt 同步追更番剧的播出日程信息（星期几更新），用于日历视图和智能追更调度。"
    description_en = "Auto-sync airing schedule from Bangumi and Trakt for calendar view and smart refresh scheduling."
    description_tw = "自動從 Bangumi 和 Trakt 同步追更番劇的播出日程資訊（星期幾更新），用於日曆檢視和智慧追更排程。"

    async def run(self, session: Any, progress_callback: Callable) -> None:
        """使用独立短事务读取用户并交给日程同步 Workflow。"""
        db = get_database_service()
        async with db.transaction():
            user = await db.user.get_first_user()
        if user is None:
            raise TaskSuccess("没有用户，无需同步日程。")
        await progress_callback(10, "正在同步播出日程...")
        result = await sync_calendar_schedule(user, get_metadata_service())
        await progress_callback(100, result["message"])
        raise TaskSuccess(result["message"])
