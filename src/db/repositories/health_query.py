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

    async def get_dashboard_library_counts(self, today_start: datetime, week_start: datetime) -> Dict[str, object]:
        """仪表盘弹幕库统计：番剧总数/按类型分组、分集与已有弹幕分集、今日与本周新增。"""
        totals = await self._session.execute(select(
            select(func.count(Anime.id)).scalar_subquery().label("anime_total"),
            select(func.count(Episode.id)).scalar_subquery().label("episode_total"),
            # 以弹幕条数 > 0 视为“已下载弹幕”，与零弹幕缺失统计口径一致
            select(func.count(Episode.id)).where(
                Episode.commentCount > 0
            ).scalar_subquery().label("episode_with_danmaku"),
            select(func.count(Anime.id)).where(
                Anime.createdAt >= today_start
            ).scalar_subquery().label("anime_today"),
            select(func.count(Anime.id)).where(
                Anime.createdAt >= week_start
            ).scalar_subquery().label("anime_week"),
            select(func.count(Episode.id)).where(
                Episode.fetchedAt >= today_start
            ).scalar_subquery().label("episode_today"),
            select(func.count(Episode.id)).where(
                Episode.fetchedAt >= week_start
            ).scalar_subquery().label("episode_week"),
        ))
        data: Dict[str, object] = {
            key: int(value or 0) for key, value in totals.mappings().one().items()
        }
        type_rows = await self._session.execute(
            select(Anime.type, func.count(Anime.id)).group_by(Anime.type)
        )
        data["anime_by_type"] = {str(t or "other"): int(c) for t, c in type_rows.all()}
        return data

    async def get_dashboard_source_counts(self) -> Dict[str, int]:
        """仪表盘弹幕源/元信息源统计（总数与启用数）。"""
        stmt = select(
            select(func.count(Scraper.providerName)).scalar_subquery().label("scraper_total"),
            select(func.count(Scraper.providerName)).where(
                Scraper.isEnabled.is_(True)
            ).scalar_subquery().label("scraper_enabled"),
            select(func.count(MetadataSource.providerName)).scalar_subquery().label("metadata_total"),
            select(func.count(MetadataSource.providerName)).where(
                MetadataSource.isEnabled.is_(True)
            ).scalar_subquery().label("metadata_enabled"),
        )
        result = await self._session.execute(stmt)
        return {key: int(value or 0) for key, value in result.mappings().one().items()}

    async def get_dashboard_media_servers(self) -> list:
        """仪表盘媒体服务器列表快照（不含令牌），仅标量列。"""
        result = await self._session.execute(select(
            MediaServer.id, MediaServer.name, MediaServer.providerName, MediaServer.isEnabled,
        ).order_by(MediaServer.id))
        return [dict(row) for row in result.mappings().all()]

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
