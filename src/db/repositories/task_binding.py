"""定时任务与数据源绑定关系的仓储层

提供任务绑定关系的增删改查操作，支持按源绑定的自动追更/刷新调度功能。
"""
from typing import List, Optional, Dict, Any
from sqlalchemy import select, delete
from sqlalchemy.ext.asyncio import AsyncSession
from datetime import datetime

from src.db import orm_models


class TaskBindingRepository:
    """定时任务与数据源绑定关系仓储"""

    def __init__(self, session: AsyncSession):
        self.session = session

    async def add_bindings(
        self,
        task_id: str,
        source_ids: List[int]
    ) -> int:
        """批量添加任务绑定关系
        
        Args:
            task_id: 定时任务ID
            source_ids: 数据源ID列表
            
        Returns:
            成功添加的绑定数量
        """
        if not source_ids:
            return 0
            
        now = datetime.now()
        bindings = [
            orm_models.ScheduledTaskSourceBinding(
                taskId=task_id,
                sourceId=source_id,
                createdAt=now
            )
            for source_id in source_ids
        ]
        
        self.session.add_all(bindings)
        await self.session.flush()
        return len(bindings)

    async def remove_bindings(
        self,
        task_id: str,
        source_ids: Optional[List[int]] = None
    ) -> int:
        """移除任务绑定关系
        
        Args:
            task_id: 定时任务ID
            source_ids: 数据源ID列表，为空则移除该任务的全部绑定
            
        Returns:
            移除的绑定数量
        """
        stmt = delete(orm_models.ScheduledTaskSourceBinding).where(
            orm_models.ScheduledTaskSourceBinding.taskId == task_id
        )
        
        if source_ids is not None:
            stmt = stmt.where(
                orm_models.ScheduledTaskSourceBinding.sourceId.in_(source_ids)
            )
        
        result = await self.session.execute(stmt)
        await self.session.flush()
        return result.rowcount

    async def get_task_bindings(self, task_id: str) -> List[Dict[str, Any]]:
        """查询任务绑定的所有数据源
        
        Args:
            task_id: 定时任务ID
            
        Returns:
            绑定的数据源信息列表，包含：sourceId, animeId, animeTitle, providerName, mediaId, createdAt
        """
        stmt = (
            select(
                orm_models.ScheduledTaskSourceBinding.sourceId,
                orm_models.ScheduledTaskSourceBinding.createdAt.label("bindingCreatedAt"),
                orm_models.AnimeSource.animeId,
                orm_models.AnimeSource.providerName,
                orm_models.AnimeSource.mediaId,
                orm_models.Anime.title.label("animeTitle"),
                orm_models.Anime.type.label("animeType"),
                orm_models.Anime.season.label("animeSeason"),
            )
            .join(
                orm_models.AnimeSource,
                orm_models.ScheduledTaskSourceBinding.sourceId == orm_models.AnimeSource.id
            )
            .join(
                orm_models.Anime,
                orm_models.AnimeSource.animeId == orm_models.Anime.id
            )
            .where(orm_models.ScheduledTaskSourceBinding.taskId == task_id)
            .order_by(orm_models.ScheduledTaskSourceBinding.createdAt)
        )
        
        result = await self.session.execute(stmt)
        rows = result.all()
        
        return [
            {
                "sourceId": row.sourceId,
                "animeId": row.animeId,
                "animeTitle": row.animeTitle,
                "animeType": row.animeType,
                "animeSeason": row.animeSeason,
                "providerName": row.providerName,
                "mediaId": row.mediaId,
                "bindingCreatedAt": row.bindingCreatedAt,
            }
            for row in rows
        ]

    async def get_source_bindings(self, source_id: int) -> List[Dict[str, Any]]:
        """查询数据源绑定的所有定时任务
        
        Args:
            source_id: 数据源ID
            
        Returns:
            绑定的定时任务信息列表，包含：taskId, taskName, jobType, isEnabled, cronExpression
        """
        stmt = (
            select(
                orm_models.ScheduledTaskSourceBinding.taskId,
                orm_models.ScheduledTaskSourceBinding.createdAt.label("bindingCreatedAt"),
                orm_models.ScheduledTask.name.label("taskName"),
                orm_models.ScheduledTask.jobType,
                orm_models.ScheduledTask.isEnabled,
                orm_models.ScheduledTask.cronExpression,
                orm_models.ScheduledTask.nextRunAt,
            )
            .join(
                orm_models.ScheduledTask,
                orm_models.ScheduledTaskSourceBinding.taskId == orm_models.ScheduledTask.taskId
            )
            .where(orm_models.ScheduledTaskSourceBinding.sourceId == source_id)
            .order_by(orm_models.ScheduledTask.jobType, orm_models.ScheduledTask.name)
        )
        
        result = await self.session.execute(stmt)
        rows = result.all()
        
        return [
            {
                "taskId": row.taskId,
                "taskName": row.taskName,
                "jobType": row.jobType,
                "isEnabled": row.isEnabled,
                "cronExpression": row.cronExpression,
                "nextRunAt": row.nextRunAt,
                "bindingCreatedAt": row.bindingCreatedAt,
            }
            for row in rows
        ]

    async def get_sources_by_task(
        self,
        task_id: str,
        enabled_only: bool = True
    ) -> List[int]:
        """获取任务绑定的所有数据源ID列表
        
        Args:
            task_id: 定时任务ID
            enabled_only: 是否只返回启用追更的源（incrementalRefreshEnabled=true）
            
        Returns:
            数据源ID列表
        """
        stmt = (
            select(orm_models.ScheduledTaskSourceBinding.sourceId)
            .join(
                orm_models.AnimeSource,
                orm_models.ScheduledTaskSourceBinding.sourceId == orm_models.AnimeSource.id
            )
            .where(orm_models.ScheduledTaskSourceBinding.taskId == task_id)
        )
        
        if enabled_only:
            stmt = stmt.where(orm_models.AnimeSource.incrementalRefreshEnabled == True)
        
        result = await self.session.execute(stmt)
        return [row[0] for row in result.all()]

    async def check_binding_exists(
        self,
        task_id: str,
        source_id: int
    ) -> bool:
        """检查特定绑定关系是否存在
        
        Args:
            task_id: 定时任务ID
            source_id: 数据源ID
            
        Returns:
            绑定是否存在
        """
        stmt = select(orm_models.ScheduledTaskSourceBinding.id).where(
            orm_models.ScheduledTaskSourceBinding.taskId == task_id,
            orm_models.ScheduledTaskSourceBinding.sourceId == source_id
        ).limit(1)
        
        result = await self.session.execute(stmt)
        return result.scalar() is not None

    async def get_bound_source_ids_by_job_type(
        self,
        job_type: str,
        enabled_tasks_only: bool = True
    ) -> List[int]:
        """获取已被特定类型任务绑定的所有数据源ID
        
        用于全局扫描时排除已有专属任务的源。
        
        Args:
            job_type: 任务类型（如 "incrementalRefresh", "refreshLatestEpisode"）
            enabled_tasks_only: 是否只考虑启用的任务
            
        Returns:
            已绑定的数据源ID列表（去重）
        """
        stmt = (
            select(orm_models.ScheduledTaskSourceBinding.sourceId)
            .join(
                orm_models.ScheduledTask,
                orm_models.ScheduledTaskSourceBinding.taskId == orm_models.ScheduledTask.taskId
            )
            .where(orm_models.ScheduledTask.jobType == job_type)
        )
        
        if enabled_tasks_only:
            stmt = stmt.where(orm_models.ScheduledTask.isEnabled == True)
        
        stmt = stmt.distinct()
        
        result = await self.session.execute(stmt)
        return [row[0] for row in result.all()]
