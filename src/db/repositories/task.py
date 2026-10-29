"""
TaskRepository - 任务数据访问层
"""

import logging
import json
from typing import Optional, List, Dict, Any
from datetime import datetime, timedelta
from sqlalchemy import select, func, and_, or_, update, delete
from sqlalchemy.ext.asyncio import AsyncSession

from ..orm_models import TaskHistory, ScheduledTask, WebhookTask, TaskStateCache
from .base import BaseRepository
from src.core.timezone import get_now

logger = logging.getLogger(__name__)


class TaskRepository(BaseRepository[TaskHistory]):
    """任务 Repository"""
    
    async def get_by_id(self, task_id: str) -> Optional[TaskHistory]:
        """根据 ID 获取任务"""
        stmt = select(TaskHistory).where(TaskHistory.taskId == task_id)
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()
    
    async def get_all(
        self,
        status: Optional[str] = None,
        limit: Optional[int] = None,
        offset: Optional[int] = None
    ) -> List[TaskHistory]:
        """获取所有任务"""
        stmt = select(TaskHistory)
        
        if status:
            stmt = stmt.where(TaskHistory.status == status)
        
        stmt = stmt.order_by(TaskHistory.createdAt.desc())
        
        if offset:
            stmt = stmt.offset(offset)
        if limit:
            stmt = stmt.limit(limit)
        
        result = await self._session.execute(stmt)
        return list(result.scalars().all())
    
    async def create(
        self,
        task_id: str,
        task_title: str,
        status: str = '排队中',
        unique_key: Optional[str] = None,
        **extra_data
    ) -> TaskHistory:
        """创建任务"""
        # 使用 ORM 实际标题字段，避免创建任务时传入不存在的属性。
        task = TaskHistory(
            taskId=task_id,
            title=task_title,
            status=status,
            uniqueKey=unique_key,
            createdAt=get_now(),
            **extra_data
        )
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
    
    async def update_status(
        self,
        task_id: str,
        status: str,
        status_message: Optional[str] = None,
        progress: Optional[int] = None
    ) -> Optional[TaskHistory]:
        """更新任务状态"""
        update_data = {'status': status}
        
        if status_message is not None:
            # ORM 的描述字段为 description，不能写入不存在的 statusMessage。
            update_data['description'] = status_message
        if progress is not None:
            update_data['progress'] = progress
        
        # 根据状态设置完成时间
        if status in ['已完成', '失败', '已取消']:
            update_data['finishedAt'] = get_now()
        
        return await self.update(task_id, **update_data)
    
    async def get_tasks_by_status(
        self,
        statuses: List[str],
        limit: Optional[int] = None
    ) -> List[TaskHistory]:
        """根据状态列表获取任务"""
        stmt = select(TaskHistory).where(TaskHistory.status.in_(statuses))
        stmt = stmt.order_by(TaskHistory.createdAt.desc())
        
        if limit:
            stmt = stmt.limit(limit)
        
        result = await self._session.execute(stmt)
        return list(result.scalars().all())
    
    async def search_tasks(
        self,
        keyword: Optional[str] = None,
        status: Optional[str] = None,
        limit: int = 100,
        offset: int = 0
    ) -> List[TaskHistory]:
        """搜索任务"""
        stmt = select(TaskHistory)
        
        conditions = []
        if keyword:
            conditions.append(
                or_(
                    TaskHistory.title.like(f"%{keyword}%"),
                    TaskHistory.description.like(f"%{keyword}%")
                )
            )
        if status and status != 'all':
            if status == 'in_progress':
                conditions.append(TaskHistory.status.in_(['排队中', '运行中', '已暂停']))
            elif status == 'completed':
                conditions.append(TaskHistory.status.in_(['已完成', '失败', '已取消']))
            else:
                conditions.append(TaskHistory.status == status)
        
        if conditions:
            stmt = stmt.where(and_(*conditions))
        
        stmt = stmt.order_by(TaskHistory.createdAt.desc()).offset(offset).limit(limit)
        result = await self._session.execute(stmt)
        return list(result.scalars().all())
    
    async def count_tasks(
        self,
        status: Optional[str] = None
    ) -> int:
        """统计任务数量"""
        stmt = select(func.count()).select_from(TaskHistory)
        
        if status:
            stmt = stmt.where(TaskHistory.status == status)
        
        result = await self._session.execute(stmt)
        return result.scalar_one()
