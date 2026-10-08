"""
SourceQueryRepository - 数据源复杂查询层

职责：处理多表 JOIN、聚合、日历查询等读操作。
基础 CRUD 操作请使用 SourceRepository。
"""

import logging
from typing import Optional, List, Dict, Any
from datetime import datetime
from sqlalchemy import select, func, and_, or_
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload, joinedload

from ..orm_models import AnimeSource, Anime, Episode, Scraper, AnimeMetadata

logger = logging.getLogger(__name__)


class SourceQueryRepository:
    """数据源复杂查询 Repository"""

    def __init__(self, session: AsyncSession):
        """
        初始化查询 Repository

        Args:
            session: SQLAlchemy AsyncSession 实例
        """
        self._session = session

    async def get_sources_with_episode_counts(
        self, skip_finished: bool = False,
    ) -> List[Dict[str, Any]]:
        """批量查询库内源及实际分集数，保留空源供缺集扫描。"""
        stmt = (
            select(
                AnimeSource.id.label("source_id"),
                AnimeSource.animeId.label("anime_id"),
                AnimeSource.providerName.label("provider_name"),
                AnimeSource.mediaId.label("media_id"),
                AnimeSource.isFinished.label("is_finished"),
                Anime.title.label("title"),
                func.count(Episode.id).label("db_episode_count"),
            )
            .join(Anime, AnimeSource.animeId == Anime.id)
            .outerjoin(Episode, AnimeSource.id == Episode.sourceId)
            .group_by(
                AnimeSource.id,
                AnimeSource.animeId,
                AnimeSource.providerName,
                AnimeSource.mediaId,
                AnimeSource.isFinished,
                Anime.title,
            )
        )
        if skip_finished:
            stmt = stmt.where(AnimeSource.isFinished == False)  # noqa: E712
        result = await self._session.execute(stmt)
        return [dict(row) for row in result.mappings()]

    async def get_low_quality_episode_sources(
        self, threshold: int, skip_finished: bool = False,
    ) -> List[Dict[str, Any]]:
        """按源聚合弹幕数低于阈值的分集，仅供扫描统计，不改变已有集。"""
        stmt = (
            select(
                Episode.sourceId.label("source_id"),
                Anime.title.label("title"),
                AnimeSource.providerName.label("provider_name"),
                func.count(Episode.id).label("episode_count"),
                func.avg(Episode.commentCount).label("avg_comments"),
            )
            .join(AnimeSource, Episode.sourceId == AnimeSource.id)
            .join(Anime, AnimeSource.animeId == Anime.id)
            .where(Episode.commentCount < threshold)
            .where(Episode.commentCount >= 0)
            .group_by(Episode.sourceId, Anime.title, AnimeSource.providerName)
        )
        if skip_finished:
            stmt = stmt.where(AnimeSource.isFinished == False)  # noqa: E712
        result = await self._session.execute(stmt)
        return [dict(row) for row in result.mappings()]

    async def get_source_titles_by_provider_media_pairs(
        self, provider_media_pairs: List[tuple[str, str]]
    ) -> List[Dict[str, Any]]:
        """批量查询候选源对应的库内标题和季度，供搜索编排识别已有源。"""
        pairs = list(dict.fromkeys(provider_media_pairs))
        rows = []
        # 分批限制 SQL 条件规模，避免逐个候选查询和参数数量超限。
        for start in range(0, len(pairs), 200):
            conditions = [
                and_(AnimeSource.providerName == provider, AnimeSource.mediaId == media_id)
                for provider, media_id in pairs[start:start + 200]
            ]
            stmt = (
                select(AnimeSource.providerName, AnimeSource.mediaId, Anime.title, Anime.season)
                .join(Anime, AnimeSource.animeId == Anime.id)
                .where(or_(*conditions))
            )
            result = await self._session.execute(stmt)
            rows.extend(dict(row) for row in result.mappings().all())
        return rows


    async def check_source_exists_by_media_id(
        self,
        provider_name: str,
        media_id: str,
        season: Optional[int] = None
    ) -> bool:
        """
        检查数据源是否存在（可选季度精确匹配）

        替代 crud.check_source_exists_by_media_id

        Args:
            provider_name: 提供商名称
            media_id: 媒体ID
            season: 季度号（可选，用于精确匹配）

        Returns:
            是否存在
        """
        stmt = select(func.count()).select_from(AnimeSource).where(
            AnimeSource.providerName == provider_name,
            AnimeSource.mediaId == media_id
        )

        if season is not None:
            stmt = stmt.join(Anime, AnimeSource.animeId == Anime.id).where(Anime.season == season)

        result = await self._session.execute(stmt)
        return result.scalar_one() > 0

    async def get_anime_id_by_source_media_id(
        self,
        provider_name: str,
        media_id: str,
        season: Optional[int] = None
    ) -> Optional[int]:
        """
        根据数据源信息反查作品ID

        替代 crud.get_anime_id_by_source_media_id

        Args:
            provider_name: 提供商名称
            media_id: 媒体ID
            season: 季度号（可选）

        Returns:
            作品ID 或 None
        """
        stmt = select(AnimeSource.animeId).where(
            AnimeSource.providerName == provider_name,
            AnimeSource.mediaId == media_id
        )

        if season is not None:
            stmt = stmt.join(Anime, AnimeSource.animeId == Anime.id).where(Anime.season == season)

        stmt = stmt.limit(1)
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def get_anime_source_info(self, source_id: int) -> Optional[Dict[str, Any]]:
        """
        获取数据源的详细信息（含作品和 Scraper 关联）

        替代 crud.get_anime_source_info

        Args:
            source_id: 数据源ID

        Returns:
            数据源详情字典或 None
        """
        stmt = (
            select(AnimeSource, Anime, Scraper)
            .join(Anime, AnimeSource.animeId == Anime.id)
            # 按实际 ORM 属性关联弹幕源配置。
            .outerjoin(Scraper, AnimeSource.providerName == Scraper.providerName)
            .where(AnimeSource.id == source_id)
        )
        result = await self._session.execute(stmt)
        row = result.one_or_none()

        if not row:
            return None

        source, anime, scraper = row
        return {
            "sourceId": source.id,
            "animeId": anime.id,
            "animeTitle": anime.title,
            "animeSeason": anime.season,
            # 刷新入口沿用作品字段名，旧别名继续兼容扫描和其他调用方。
            "title": anime.title,
            "type": anime.type,
            "season": anime.season,
            "year": anime.year,
            "imageUrl": anime.imageUrl,
            "providerName": source.providerName,
            "mediaId": source.mediaId,
            "sourceOrder": source.sourceOrder,  # 添加 sourceOrder 字段，供分集重整任务使用
            "isFavorited": source.isFavorited or False,
            "incrementalRefreshEnabled": source.incrementalRefreshEnabled or False,
            "isFinished": source.isFinished or False,
            "scraperEnabled": scraper.isEnabled if scraper else None,
        }

    async def get_anime_sources(self, anime_id: int) -> List[Dict[str, Any]]:
        """
        获取作品的所有数据源（含 Scraper 信息）

        替代 crud.get_anime_sources

        Args:
            anime_id: 作品ID

        Returns:
            数据源列表
        """
        # 在同一条 SQL 中统计各源实际收录的分集，避免逐源查询及空源计数错误。
        episode_count = (
            select(func.count(Episode.id))
            .where(Episode.sourceId == AnimeSource.id)
            .correlate(AnimeSource)
            .scalar_subquery()
        )
        stmt = (
            select(AnimeSource, Scraper, episode_count.label("episodeCount"))
            .outerjoin(Scraper, AnimeSource.providerName == Scraper.providerName)
            .where(AnimeSource.animeId == anime_id)
            .order_by(AnimeSource.id)
        )
        result = await self._session.execute(stmt)
        rows = result.all()

        return [
            {
                "sourceId": row.AnimeSource.id,
                "providerName": row.AnimeSource.providerName,
                "mediaId": row.AnimeSource.mediaId,
                "isFavorited": row.AnimeSource.isFavorited or False,
                "incrementalRefreshEnabled": row.AnimeSource.incrementalRefreshEnabled or False,
                "isFinished": row.AnimeSource.isFinished or False,
                "scraperEnabled": row.Scraper.isEnabled if row.Scraper else None,
                "episodeCount": row.episodeCount,
                "createdAt": row.AnimeSource.createdAt,
            }
            for row in rows
        ]

    async def find_favorited_source_for_anime(self, anime_id: int) -> Optional[Dict[str, Any]]:
        """
        查找作品的精确标记源（isFavorited=True）

        替代 crud.find_favorited_source_for_anime

        Args:
            anime_id: 作品ID

        Returns:
            精确标记源的字典或 None
        """
        stmt = (
            select(
                AnimeSource.id.label("sourceId"),
                AnimeSource.providerName,
                AnimeSource.mediaId,
                AnimeSource.isFavorited,
            )
            .where(
                AnimeSource.animeId == anime_id,
                AnimeSource.isFavorited == True
            )
            .limit(1)
        )
        result = await self._session.execute(stmt)
        row = result.first()

        if not row:
            return None

        return dict(row._mapping)

    async def get_incremental_refresh_sources(self) -> List[Dict[str, Any]]:
        """
        获取所有启用增量刷新且未完结的数据源

        替代 crud 中增量刷新相关查询

        Returns:
            数据源列表（含作品信息）
        """
        stmt = (
            select(AnimeSource, Anime)
            .join(Anime, AnimeSource.animeId == Anime.id)
            .where(
                AnimeSource.incrementalRefreshEnabled == True,
                AnimeSource.isFinished == False,
            )
            .order_by(Anime.createdAt.desc())
        )
        result = await self._session.execute(stmt)
        rows = result.all()

        return [
            {
                "sourceId": row.AnimeSource.id,
                "animeId": row.Anime.id,
                "animeTitle": row.Anime.title,
                "animeSeason": row.Anime.season,
                "providerName": row.AnimeSource.providerName,
                "mediaId": row.AnimeSource.mediaId,
            }
            for row in rows
        ]

    async def get_calendar_sources(self) -> List[Dict[str, Any]]:
        """获取所有追更中且未完结的源，连同日程信息，用于日历视图。

        why：使用 outerjoin 关联 AnimeMetadata 与 Episode，且**不**限定
        airWeekday 非空——没有播出日程的追更条目也要出现在日历的「未知」列，
        其日程随后可能由外部日历数据补齐。latestEpisodeIndex 取该源已入库
        分集的最大序号，故需 group_by 全部非聚合列。

        Returns:
            追更源列表，含作品信息、播出日程、外部元数据 ID 与最新集序号
        """
        stmt = (
            select(
                AnimeSource.id.label("sourceId"),
                AnimeSource.providerName.label("providerName"),
                Anime.id.label("animeId"),
                Anime.title.label("animeTitle"),
                Anime.type.label("animeType"),
                Anime.season,
                Anime.imageUrl.label("imageUrl"),
                Anime.localImagePath.label("localImagePath"),
                Anime.episodeCount.label("episodeCount"),
                AnimeMetadata.airWeekday.label("airWeekday"),
                AnimeMetadata.airTime.label("airTime"),
                AnimeMetadata.bangumiId.label("bangumiId"),
                AnimeMetadata.traktId.label("traktId"),
                AnimeMetadata.tmdbId.label("tmdbId"),
                func.max(Episode.episodeIndex).label("latestEpisodeIndex"),
            )
            .join(Anime, AnimeSource.animeId == Anime.id)
            .outerjoin(AnimeMetadata, Anime.id == AnimeMetadata.animeId)
            .outerjoin(Episode, AnimeSource.id == Episode.sourceId)
            .where(AnimeSource.incrementalRefreshEnabled == True)
            .where(AnimeSource.isFinished == False)
            .group_by(
                AnimeSource.id,
                AnimeSource.providerName,
                Anime.id,
                Anime.title,
                Anime.type,
                Anime.season,
                Anime.imageUrl,
                Anime.localImagePath,
                Anime.episodeCount,
                AnimeMetadata.airWeekday,
                AnimeMetadata.airTime,
                AnimeMetadata.bangumiId,
                AnimeMetadata.traktId,
                AnimeMetadata.tmdbId,
            )
        )
        result = await self._session.execute(stmt)
        return [dict(r) for r in result.mappings().all()]

    # ==================== 追更管理分组查询 ====================
    # why: 自 crud/source.py 迁入。分页在内存中按番剧维度切片而非 SQL LIMIT，
    #      因为一个番剧可能有多个源，SQL 层分页会把同一番剧的源截断到两页。

    async def get_incremental_refresh_sources_grouped(
        self,
        page: int = 1,
        page_size: int = 20,
        keyword: str = "",
        favorite_filter: str = "all",
        refresh_filter: str = "all",
        type_filter: str = "all",
        finished_filter: str = "all",
        sort_by: str = "created",
        sort_order: str = "desc",
    ) -> Dict[str, Any]:
        """获取全部源并按番剧分组，支持过滤、排序与分页。

        Args:
            page: 页码，从 1 开始
            page_size: 每页番剧数量
            keyword: 关键词，匹配番剧标题或源名称
            favorite_filter: all / favorited / unfavorited
            refresh_filter: all / enabled / disabled
            type_filter: all / movie / tv_series
            finished_filter: all / finished / unfinished
            sort_by: created 按入库时间，title 按标题
            sort_order: asc / desc

        Returns:
            含 total（番剧数）、totalSources、refreshEnabled、favorited
            与 list（分组后的番剧列表）的字典
        """
        episode_count_subquery = (
            select(
                Episode.sourceId,
                func.count(Episode.id).label("episode_count"),
            )
            .group_by(Episode.sourceId)
            .subquery()
        )

        base_stmt = (
            select(
                Anime.id.label("animeId"),
                Anime.title.label("animeTitle"),
                Anime.type.label("animeType"),
                Anime.season.label("animeSeason"),
                Anime.imageUrl.label("imageUrl"),
                Anime.localImagePath.label("localImagePath"),
                AnimeSource.id.label("sourceId"),
                AnimeSource.providerName.label("providerName"),
                AnimeSource.isFavorited.label("isFavorited"),
                AnimeSource.incrementalRefreshEnabled.label("incrementalRefreshEnabled"),
                AnimeSource.incrementalRefreshFailures.label("incrementalRefreshFailures"),
                AnimeSource.lastRefreshLatestEpisodeAt.label("lastRefreshLatestEpisodeAt"),
                AnimeSource.isFinished.label("isFinished"),
                func.coalesce(episode_count_subquery.c.episode_count, 0).label("episodeCount"),
            )
            .join(Anime, AnimeSource.animeId == Anime.id)
            .outerjoin(
                episode_count_subquery,
                AnimeSource.id == episode_count_subquery.c.sourceId,
            )
        )

        conditions = []
        if keyword:
            keyword_pattern = f"%{keyword}%"
            conditions.append(
                or_(
                    Anime.title.ilike(keyword_pattern),
                    AnimeSource.providerName.ilike(keyword_pattern),
                )
            )
        if favorite_filter == "favorited":
            conditions.append(AnimeSource.isFavorited == True)  # noqa: E712
        elif favorite_filter == "unfavorited":
            conditions.append(AnimeSource.isFavorited == False)  # noqa: E712
        if refresh_filter == "enabled":
            conditions.append(AnimeSource.incrementalRefreshEnabled == True)  # noqa: E712
        elif refresh_filter == "disabled":
            conditions.append(AnimeSource.incrementalRefreshEnabled == False)  # noqa: E712
        if type_filter == "movie":
            conditions.append(Anime.type == "movie")
        elif type_filter == "tv_series":
            conditions.append(Anime.type == "tv_series")
        if finished_filter == "finished":
            conditions.append(AnimeSource.isFinished == True)  # noqa: E712
        elif finished_filter == "unfinished":
            conditions.append(AnimeSource.isFinished == False)  # noqa: E712

        if conditions:
            base_stmt = base_stmt.where(and_(*conditions))

        if sort_by == "created":
            col = Anime.createdAt.desc() if sort_order == "desc" else Anime.createdAt.asc()
        else:
            col = Anime.title.asc() if sort_order == "asc" else Anime.title.desc()
        base_stmt = base_stmt.order_by(col, AnimeSource.providerName)

        result = await self._session.execute(base_stmt)
        rows = result.mappings().all()

        grouped: Dict[int, Dict[str, Any]] = {}
        total_sources = 0
        refresh_enabled_count = 0
        favorited_count = 0

        for row in rows:
            anime_id = row["animeId"]
            if anime_id not in grouped:
                grouped[anime_id] = {
                    "animeId": anime_id,
                    "animeTitle": row["animeTitle"],
                    "animeType": row["animeType"],
                    "season": row["animeSeason"],
                    "imageUrl": row["imageUrl"],
                    "localImagePath": row["localImagePath"],
                    "sources": [],
                }
            grouped[anime_id]["sources"].append({
                "sourceId": row["sourceId"],
                "providerName": row["providerName"],
                "isFavorited": row["isFavorited"],
                "incrementalRefreshEnabled": row["incrementalRefreshEnabled"],
                "incrementalRefreshFailures": row["incrementalRefreshFailures"],
                "lastRefreshLatestEpisodeAt": row["lastRefreshLatestEpisodeAt"],
                "isFinished": row["isFinished"],
                "episodeCount": row["episodeCount"],
            })
            total_sources += 1
            if row["incrementalRefreshEnabled"]:
                refresh_enabled_count += 1
            if row["isFavorited"]:
                favorited_count += 1

        all_groups = list(grouped.values())
        start_idx = (page - 1) * page_size
        paginated_groups = all_groups[start_idx:start_idx + page_size]

        return {
            "total": len(all_groups),
            "totalSources": total_sources,
            "refreshEnabled": refresh_enabled_count,
            "favorited": favorited_count,
            "list": paginated_groups,
        }

    async def get_source_episode_list(self, source_id: int) -> List[Dict[str, Any]]:
        """
        获取数据源的分集列表（用于拆分选择）。

        Args:
            source_id: 数据源ID

        Returns:
            分集列表，包含 episodeId, title, episodeIndex, commentCount
        """
        stmt = (
            select(
                Episode.id.label("episodeId"),
                Episode.title,
                Episode.episodeIndex.label("episodeIndex"),
                Episode.commentCount.label("commentCount")
            )
            .where(Episode.sourceId == source_id)
            .order_by(Episode.episodeIndex)
        )
        result = await self._session.execute(stmt)
        return [dict(row) for row in result.mappings()]

    # 追更分组查询统一使用上方实现，避免同名方法覆盖正确的 ORM 字段与响应契约。
