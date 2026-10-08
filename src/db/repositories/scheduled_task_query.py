"""
ScheduledTaskQueryRepository - 定时任务查询层

职责：处理定时任务的查询、创建、更新、删除等操作。
"""

import logging
import json
from typing import List, Optional, Dict, Any
from datetime import datetime

from sqlalchemy import select, update, delete
from sqlalchemy.ext.asyncio import AsyncSession

from ..orm_models import ScheduledTask, TaskHistory
from src.core.timezone import get_now

logger = logging.getLogger(__name__)


class ScheduledTaskQueryRepository:
    """定时任务查询仓储"""

    def __init__(self, session: AsyncSession):
        self._session = session

    @staticmethod
    def _parse_task_config(raw) -> dict:
        """将数据库中的 taskConfig TEXT 字段解析为 dict"""
        if raw is None:
            return {}
        if isinstance(raw, dict):
            return raw
        if isinstance(raw, str):
            try:
                return json.loads(raw)
            except (json.JSONDecodeError, ValueError):
                return {}
        return {}

    async def is_system_task(self, task_id: str) -> bool:
        """检查是否为系统内置任务"""
        system_task_ids = ["system_token_reset"]
        return task_id in system_task_ids

    async def get_scheduled_tasks(self) -> List[Dict[str, Any]]:
        """获取所有定时任务"""
        stmt = select(
            ScheduledTask.taskId.label("taskId"),
            ScheduledTask.name.label("name"),
            ScheduledTask.jobType.label("jobType"),
            ScheduledTask.cronExpression.label("cronExpression"),
            ScheduledTask.isEnabled.label("isEnabled"),
            ScheduledTask.taskConfig.label("taskConfig"),
            ScheduledTask.lastRunAt.label("lastRunAt"),
            ScheduledTask.nextRunAt.label("nextRunAt")
        ).order_by(ScheduledTask.name)
        result = await self._session.execute(stmt)
        rows = []
        for row in result.mappings():
            d = dict(row)
            d['taskConfig'] = self._parse_task_config(d.get('taskConfig'))
            rows.append(d)
        return rows

    async def get_scheduled_task(self, task_id: str) -> Optional[Dict[str, Any]]:
        """获取单个定时任务"""
        stmt = select(
            ScheduledTask.taskId.label("taskId"),
            ScheduledTask.name.label("name"),
            ScheduledTask.jobType.label("jobType"),
            ScheduledTask.cronExpression.label("cronExpression"),
            ScheduledTask.isEnabled.label("isEnabled"),
            ScheduledTask.taskConfig.label("taskConfig"),
            ScheduledTask.lastRunAt.label("lastRunAt"),
            ScheduledTask.nextRunAt.label("nextRunAt")
        ).where(ScheduledTask.taskId == task_id)
        result = await self._session.execute(stmt)
        row = result.mappings().first()
        if not row:
            return None
        d = dict(row)
        d['taskConfig'] = self._parse_task_config(d.get('taskConfig'))
        return d

    async def check_scheduled_task_exists_by_type(self, job_type: str) -> bool:
        """检查指定类型的定时任务是否存在"""
        stmt = select(ScheduledTask.taskId).where(ScheduledTask.jobType == job_type).limit(1)
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none() is not None

    async def get_scheduled_task_id_by_type(self, job_type: str) -> Optional[str]:
        """获取指定类型的定时任务ID"""
        stmt = select(ScheduledTask.taskId).where(ScheduledTask.jobType == job_type).limit(1)
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def create_scheduled_task(
        self,
        task_id: str,
        name: str,
        job_type: str,
        cron: str,
        is_enabled: bool,
        task_config: dict = None
    ):
        """创建定时任务"""
        new_task = ScheduledTask(
            taskId=task_id,
            name=name,
            jobType=job_type,
            cronExpression=cron,
            isEnabled=is_enabled,
            taskConfig=json.dumps(task_config or {}, ensure_ascii=False)
        )
        self._session.add(new_task)
        await self._session.flush()

    async def update_scheduled_task(
        self,
        task_id: str,
        name: str,
        cron: str,
        is_enabled: bool,
        task_config: dict = None
    ) -> bool:
        """更新定时任务,但不允许修改系统内置任务的关键属性"""
        task = await self._session.get(ScheduledTask, task_id)
        if not task:
            return False

        # 系统任务只允许修改启用状态
        if await self.is_system_task(task_id):
            task.isEnabled = is_enabled
        else:
            task.name = name
            task.cronExpression = cron
            task.isEnabled = is_enabled
            serialized_config = json.dumps(task_config or {}, ensure_ascii=False)
            logger.info(f"[调试] CRUD update_scheduled_task '{task_id}' - task_config 入参: {task_config}, 序列化后: {serialized_config}")
            task.taskConfig = serialized_config

        await self._session.flush()
        return True

    async def remove_obsolete_token_reset(self) -> bool:
        """启动时清理已迁移至内部轮询的旧系统任务。"""
        task = await self._session.get(ScheduledTask, "system_token_reset")
        if task is None:
            return False
        await self._session.delete(task)
        await self._session.flush()
        return True

    async def delete_scheduled_task(self, task_id: str) -> bool:
        """删除定时任务,但不允许删除系统内置任务"""
        # 检查是否为系统任务
        if await self.is_system_task(task_id):
            raise ValueError("不允许删除系统内置任务。")

        task = await self._session.get(ScheduledTask, task_id)
        if not task:
            return False

        await self._session.delete(task)
        await self._session.flush()
        return True

    async def update_scheduled_task_run_times(
        self,
        task_id: str,
        last_run: Optional[datetime],
        next_run: Optional[datetime]
    ):
        """更新定时任务的运行时间"""
        values_to_update = {
            "lastRunAt": last_run.replace(tzinfo=None) if last_run else None,
            "nextRunAt": next_run.replace(tzinfo=None) if next_run else None
        }
        await self._session.execute(
            update(ScheduledTask).where(ScheduledTask.taskId == task_id).values(**values_to_update)
        )
        await self._session.flush()

    async def get_last_run_result_for_scheduled_task(
        self,
        scheduled_task_id: str
    ) -> Optional[Dict[str, Any]]:
        """获取指定定时任务的最近一次运行结果"""
        stmt = (
            select(TaskHistory)
            .where(TaskHistory.scheduledTaskId == scheduled_task_id)
            .order_by(TaskHistory.createdAt.desc())
            .limit(1)
        )
        result = await self._session.execute(stmt)
        task_run = result.scalar_one_or_none()
        if not task_run:
            return None

        # 返回一个与 models.TaskInfo 兼容的字典
        return {
            "taskId": task_run.taskId,
            "title": task_run.title,
            "status": task_run.status,
            "progress": task_run.progress,
            # ORM 字段为 description，响应模型要求字符串而非 None。
            "description": task_run.description or "",
            "createdAt": task_run.createdAt.isoformat() if task_run.createdAt else None,
            "isSystemTask": False,
            "queueType": task_run.queueType or "management"
        }
