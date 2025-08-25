import logging
from typing import Callable

from sqlalchemy.ext.asyncio import AsyncSession

from .. import tasks
from .base import BaseJob


class DatabaseMaintenanceJob(BaseJob):
    """
    一个用于执行数据库维护的定时任务，包括清理旧日志和优化表。
    """
    job_type = "databaseMaintenance"
    job_name = "数据库维护"

    async def run(self, session: AsyncSession, progress_callback: Callable):
        """
        此任务的运行逻辑被委托给一个通用的任务函数，以保持代码的模块化。
        """
        self.logger.info(f"通过定时任务调度器触发 [{self.job_name}]。")
        # 直接调用在 tasks.py 中定义的核心维护逻辑
        await tasks.database_maintenance_task(session, progress_callback)
