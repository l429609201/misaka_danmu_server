"""系统健康聚合查询；仅返回统计数据，不负责评分或事务提交。"""

from datetime import datetime
from typing import Dict

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..orm_models import (
    Anime, AnimeSource, CacheData, MediaItem,
    Episode, MediaServer, MetadataSource, NotificationChannel,
    ScheduledTask, Scraper, TaskHistory,
)


class HealthQueryRepository:
    """集中处理健康总览跨表统计，避免 API 直接构造 SQL。"""

    async def get_capacity_counts(self) -> Dict[str, int]:
        """通过独立计数子查询返回容量快照，避免跨表联接放大计数。"""
        models = (Anime, Episode, AnimeSource, TaskHistory, CacheData, MediaItem)
        stmt = select(*[
            select(func.count()).select_from(model).scalar_subquery().label(model.__tablename__)
            for model in models
        ])
        result = await self._session.execute(stmt)
        return {key: int(value or 0) for key, value in result.mappings().one().items()}

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_config_counts(self) -> Dict[str, int]:
        """使用独立标量子查询统计配置，避免多表联接导致计数放大。"""
        stmt = select(
            select(func.count(MediaServer.id)).where(
                MediaServer.isEnabled.is_(True)
            ).scalar_subquery().label("media_server"),
            select(func.count(Scraper.providerName)).where(
                Scraper.isEnabled.is_(True)
            ).scalar_subquery().label("scrapers"),
            select(func.count(NotificationChannel.id)).where(
                NotificationChannel.isEnabled.is_(True)
            ).scalar_subquery().label("notification"),
            select(func.count(ScheduledTask.taskId)).where(
                ScheduledTask.jobType == "databaseBackup",
                ScheduledTask.isEnabled.is_(True),
            ).scalar_subquery().label("backup"),
            select(func.count(MetadataSource.providerName)).where(
                MetadataSource.isEnabled.is_(True)
            ).scalar_subquery().label("metadata"),
        )
        result = await self._session.execute(stmt)
        return {key: int(value or 0) for key, value in result.mappings().one().items()}

    async def get_task_counts_since(self, since: datetime) -> Dict[str, int]:
        """按创建时间统计各任务状态，保留现有最近二十四小时口径。"""
        stmt = select(TaskHistory.status, func.count(TaskHistory.taskId)).where(
            TaskHistory.createdAt >= since
        ).group_by(TaskHistory.status)
        result = await self._session.execute(stmt)
        return {status: int(count) for status, count in result.all()}

    async def get_episode_counts(self, since: datetime) -> Dict[str, int]:
        """统计今日抓取分集与零弹幕分集；不将分集数误改为弹幕条数。"""
        stmt = select(
            select(func.count(Episode.id)).where(
                Episode.fetchedAt >= since
            ).scalar_subquery().label("today_new"),
            select(func.count(Episode.id)).where(
                Episode.commentCount == 0
            ).scalar_subquery().label("missing"),
        )
        result = await self._session.execute(stmt)
        return {key: int(value or 0) for key, value in result.mappings().one().items()}

    async def get_task_profile_rows(self, since: datetime) -> list:
        """读取任务画像所需标量列，避免 API 直接构造 SQL。"""
        result = await self._session.execute(select(
            TaskHistory.title, TaskHistory.status,
            TaskHistory.createdAt, TaskHistory.finishedAt,
        ).where(TaskHistory.createdAt >= since).order_by(TaskHistory.createdAt.desc()))
        return list(result.all())

    async def get_task_timeline_record(self, task_id: str) -> dict | None:
        """返回时间线数据快照，事务结束后不依赖 ORM 对象。"""
        result = await self._session.execute(select(
            TaskHistory.taskId, TaskHistory.title, TaskHistory.status,
            TaskHistory.description, TaskHistory.createdAt, TaskHistory.finishedAt,
        ).where(TaskHistory.taskId == task_id))
        row = result.mappings().one_or_none()
        return dict(row) if row is not None else None
