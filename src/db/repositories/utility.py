"""
UtilityRepository - 通用工具数据访问层
"""

import logging
from typing import Optional, List, Dict, Any
from datetime import datetime, timedelta
from sqlalchemy import select, delete, or_, and_
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy.sql.elements import ColumnElement

from ..orm_models import OauthState, TaskHistory, TaskStateCache
from .base import BaseRepository
from src.core.timezone import get_now

logger = logging.getLogger(__name__)


class UtilityRepository(BaseRepository[TaskHistory]):
    """通用工具 Repository（提供各种辅助功能）"""
    
    async def get_by_id(self, task_id: str) -> Optional[TaskHistory]:
        """根据 ID 获取任务"""
        stmt = select(TaskHistory).where(TaskHistory.taskId == task_id)
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()
    
    async def get_all(self, **filters) -> List[TaskHistory]:
        """获取所有任务"""
        stmt = select(TaskHistory)
        result = await self._session.execute(stmt)
        return list(result.scalars().all())
    
    async def create(self, **data) -> TaskHistory:
        """创建任务"""
        task = TaskHistory(**data)
        self._session.add(task)
        await self._session.flush()
        return task
    
    async def update(self, task_id: str, **data) -> Optional[TaskHistory]:
        """更新任务"""
        task = await self.get_by_id(task_id)
        if not task:
            return None
        
        for key, value in data.items():
            if hasattr(task, key):
                setattr(task, key, value)
        
        await self._session.flush()
        return task
    
    async def delete(self, task_id: str) -> bool:
        """删除任务"""
        task = await self.get_by_id(task_id)
        if not task:
            return False
        
        await self._session.delete(task)
        await self._session.flush()
        return True
    
    async def prune_logs(
        self,
        model: type[DeclarativeBase],
        date_column: ColumnElement,
        cutoff_date: datetime
    ) -> int:
        """通用日志清理函数"""
        stmt = delete(model).where(date_column < cutoff_date)
        result = await self._session.execute(stmt)
        await self._session.flush()
        return result.rowcount
    
    async def clear_expired_oauth_states(self) -> int:
        """清理过期的 OAuth 状态"""
        stmt = delete(OauthState).where(OauthState.expiresAt <= get_now())
        result = await self._session.execute(stmt)
        await self._session.flush()
        return result.rowcount
    
    async def find_recent_task_by_unique_key(
        self,
        unique_key: str,
        within_hours: int
    ) -> Optional[TaskHistory]:
        """查找最近的任务（按唯一键）"""
        if not unique_key:
            return None
        
        cutoff_time = get_now() - timedelta(hours=within_hours)
        
        stmt = (
            select(TaskHistory)
            .where(
                TaskHistory.uniqueKey == unique_key,
                or_(
                    TaskHistory.status.in_(['排队中', '运行中', '已暂停']),
                    and_(
                        TaskHistory.status == '已完成',
                        TaskHistory.finishedAt >= cutoff_time
                    )
                )
            )
            .order_by(TaskHistory.createdAt.desc())
            .limit(1)
        )
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()
    
    async def get_all_running_task_states(self) -> List[Dict[str, Any]]:
        """获取所有正在运行的任务状态缓存"""
        stmt = (
            select(TaskStateCache)
            .join(TaskHistory, TaskStateCache.taskId == TaskHistory.taskId)
            .where(TaskHistory.status == '运行中')
        )
        result = await self._session.execute(stmt)
        task_states = result.scalars().all()
        
        return [
            {
                "task_id": ts.taskId,
                "task_type": ts.taskType,
                "task_parameters": ts.taskParameters,
                "queue_type": ts.queueType
            }
            for ts in task_states
        ]
