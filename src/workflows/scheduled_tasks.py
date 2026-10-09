"""定时任务的轮询政策、唯一实例和 Bangumi 同步业务编排。"""

import logging
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


def cron_is_valid(cron: str, min_hours: int) -> bool:
    """检查现有常用小时步长是否满足最小轮询间隔。"""
    try:
        parts = cron.split()
        if len(parts) != 5:
            return True
        hour = parts[1]
        if hour == "*":
            return False
        return not hour.startswith("*/") or int(hour[2:]) >= min_hours
    except (ValueError, IndexError):
        return False


class ScheduledTaskWorkflow:
    """在通用调度服务之前应用领域策略。"""

    SINGLETON_NAMES = {
        "incrementalRefresh": "定时追更",
        "refreshLatestEpisode": "刷新最新集弹幕",
        "tmdbAutoMap": "TMDB自动映射与更新",
        "webhookProcessor": "Webhook 延时任务处理器",
        "autoFinish": "追更自动完结",
    }

    def __init__(self, scheduler, database_service) -> None:
        self.scheduler = scheduler
        self.db = database_service

    @staticmethod
    def validate_policy(job_type: str, cron: str) -> None:
        """追更和最新集刷新使用同一三小时政策，创建更新均适用。"""
        if job_type in {"incrementalRefresh", "refreshLatestEpisode"} and not cron_is_valid(cron, 3):
            raise ValueError("刷新任务的轮询间隔不得低于3小时。请使用如 '0 */3 * * *' 或更长的间隔。")

    async def add_task(self, name: str, job_type: str, cron: str, is_enabled: bool, task_config: Optional[dict] = None) -> Dict[str, Any]:
        """验证轮询和单例政策后创建调度实例。"""
        self.validate_policy(job_type, cron)
        async with self.db.transaction():
            if job_type in self.SINGLETON_NAMES and await self.db.scheduled_task.check_scheduled_task_exists_by_type(job_type):
                raise ValueError(f"{self.SINGLETON_NAMES[job_type]}任务已存在，无法重复创建。")
            return await self.scheduler.add_task(name, job_type, cron, is_enabled, task_config)

    async def update_task(self, task_id: str, name: str, cron: str, is_enabled: bool, task_config: Optional[dict] = None) -> Optional[Dict[str, Any]]:
        """根据已有任务类型检查更新政策。"""
        async with self.db.transaction():
            task = await self.db.scheduled_task.get_scheduled_task(task_id)
            if task is None:
                return None
            self.validate_policy(task["jobType"], cron)
            return await self.scheduler.update_task(task_id, name, cron, is_enabled, task_config)

    async def sync_bangumi_data_schedule(self, enabled: bool, cron: str) -> None:
        """根据源配置维护离线索引同步任务，停用保留历史。"""
        cron = (cron or "").strip() or "0 4 * * *"
        async with self.db.transaction():
            task_id = await self.db.scheduled_task.get_scheduled_task_id_by_type("bangumiDataSync")
        if task_id:
            await self.update_task(task_id, "bangumi-data 离线索引同步", cron, enabled)
        elif enabled:
            await self.add_task("bangumi-data 离线索引同步", "bangumiDataSync", cron, True)
