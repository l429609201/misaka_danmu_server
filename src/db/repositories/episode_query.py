"""
EpisodeQueryRepository - 分集复杂查询层

职责：处理多表 JOIN、聚合、复杂搜索等读操作。
基础 CRUD 操作请使用 EpisodeRepository。
"""

import logging
from typing import Optional, List, Dict, Any
from sqlalchemy import select, func, and_, or_, distinct
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload, joinedload, aliased

from ..orm_models import (
    Episode, AnimeSource, Anime, AnimeMetadata, AnimeAlias,
    TmdbEpisodeMapping, Scraper
)
from .source_query import SourceQueryRepository

logger = logging.getLogger(__name__)


class EpisodeQueryRepository:
    """分集复杂查询 Repository"""

    def __init__(self, session: AsyncSession):
        """
        初始化查询 Repository

        Args:
            session: SQLAlchemy AsyncSession 实例
        """
        self._session = session

    async def count_by_anime_ids(self, anime_ids: List[int]) -> Dict[int, int]:
        """批量统计作品下所有数据源的分集数，保留重复集数计数。"""
        if not anime_ids:
            return {}
        stmt = (
            select(AnimeSource.animeId, func.count(Episode.id))
            .join(Episode, Episode.sourceId == AnimeSource.id)
            .where(AnimeSource.animeId.in_(anime_ids))
            .group_by(AnimeSource.animeId)
        )
        result = await self._session.execute(stmt)
        return dict(result.all())

    async def list_by_anime_id_for_command(self, anime_id: int) -> List[Dict[str, Any]]:
        """按集数排序返回作品的分集快照，供播放器指令选择。"""
        stmt = (
            select(Episode.id, Episode.title, Episode.episodeIndex, Episode.commentCount)
            .join(AnimeSource, Episode.sourceId == AnimeSource.id)
            .where(AnimeSource.animeId == anime_id)
            .order_by(Episode.episodeIndex)
        )
        result = await self._session.execute(stmt)
        return [dict(row) for row in result.mappings().all()]

    async def get_calendar_stale_episodes(self, threshold: int) -> List[Dict[str, Any]]:
        """查询追更源中弹幕数不超过阈值的分集，保留原接口最多一百条的口径。"""
        # 直接投影响应字段，避免返回 ORM 对象造成事务外关系加载。
        stmt = (
            select(
                Episode.id.label("episodeId"),
                Episode.title.label("episodeTitle"),
                Episode.episodeIndex.label("episodeNumber"), Episode.commentCount,
                func.coalesce(Anime.title, "").label("animeTitle"),
                AnimeSource.animeId,
            )
            .join(AnimeSource, AnimeSource.id == Episode.sourceId)
            .outerjoin(Anime, Anime.id == AnimeSource.animeId)
            .where(
                AnimeSource.incrementalRefreshEnabled.is_(True),
                Episode.commentCount <= threshold,
            )
            .limit(100)
        )
        result = await self._session.execute(stmt)
        return [dict(row) for row in result.mappings()]


    async def get_danmaku_path_references(self) -> List[tuple[int, str]]:
        """返回所有非空弹幕路径引用，供编排层判断历史路径别名。"""
        # 不能按路径字符串过滤，否则会漏掉同一文件的不同存储表示。
        result = await self._session.execute(
            select(Episode.id, Episode.danmakuFilePath).where(
                Episode.danmakuFilePath.is_not(None), Episode.danmakuFilePath != "",
            )
        )
        return [(row[0], row[1]) for row in result.all()]

    async def find_by_media_server_episode(
        self,
        server_type: str,
        episode_id: str,
    ) -> Optional[Episode]:
        """按媒体服务器类型和分集 ID 获取分集记录。"""
        stmt = (
            select(Episode)
            .join(AnimeSource, Episode.sourceId == AnimeSource.id)
            .join(AnimeMetadata, AnimeMetadata.animeId == AnimeSource.animeId)
            .where(
                Episode.mediaServerEpisodeId == episode_id,
                AnimeMetadata.mediaServerType == server_type,
            )
            .limit(1)
        )
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()



    async def get_last_episode_for_source(self, source_id: int) -> Optional[Dict[str, Any]]:
        """
        获取指定源的最后一个分集

        替代 crud.get_last_episode_for_source

        Args:
            source_id: 数据源ID

        Returns:
            {"episodeIndex": int} 或 None
        """
        stmt = (
            select(Episode.episodeIndex)
            .where(Episode.sourceId == source_id)
            .order_by(Episode.episodeIndex.desc())
            .limit(1)
        )
        result = await self._session.execute(stmt)
        row = result.scalar_one_or_none()
        return {"episodeIndex": row} if row is not None else None

    async def get_max_episode_index_by_source(self, source_id: int) -> int:
        """查询指定源已入库的最大分集序号。"""
        result = await self._session.execute(
            select(func.max(Episode.episodeIndex)).where(Episode.sourceId == source_id)
        )
        return int(result.scalar_one_or_none() or 0)

    async def get_max_episode_index_by_source_media(
        self,
        provider_name: str,
        media_id: str,
    ) -> int:
        """按提供方和媒体 ID 查询已收录分集的最大集号。"""
        stmt = (
            select(func.max(Episode.episodeIndex))
            .join(AnimeSource, Episode.sourceId == AnimeSource.id)
            .where(
                AnimeSource.providerName == provider_name,
                AnimeSource.mediaId == media_id,
            )
        )
        result = await self._session.execute(stmt)
        return int(result.scalar_one_or_none() or 0)

    async def get_episode_for_refresh(self, episode_id: int) -> Optional[Dict[str, Any]]:
        """获取用于刷新的分集信息（含数据源信息）。"""
        stmt = (
            select(Episode, AnimeSource)
            .join(AnimeSource, Episode.sourceId == AnimeSource.id)
            .where(Episode.id == episode_id)
        )
        result = await self._session.execute(stmt)
        row = result.one_or_none()
        if not row:
            return None

        episode, source = row
        return {
            "episodeId": episode.id,
            "episodeTitle": episode.title,
            "episodeNumber": episode.episodeIndex,
            "sourceId": episode.sourceId,
            "providerName": source.providerName,
            "mediaId": source.mediaId,
            "sourceUrl": episode.sourceUrl,
            "danmakuFilePath": episode.danmakuFilePath,
        }

    async def get_episodes_by_source(
        self,
        source_id: int,
        page: int = 1,
        page_size: int = 5000
    ) -> Dict[str, Any]:
        """
        获取数据源的所有分集（分页）

        替代 crud.get_episodes_for_source

        Args:
            source_id: 数据源ID
            page: 页码（从1开始）
            page_size: 每页数量

        Returns:
            {"list": [...], "total": int, "page": int, "page_size": int}
        """
        # 查询总数
        count_stmt = select(func.count()).select_from(Episode).where(Episode.sourceId == source_id)
        total_result = await self._session.execute(count_stmt)
        total = total_result.scalar_one()

        # 查询分页数据
        offset = (page - 1) * page_size
        stmt = (
            select(Episode)
            .where(Episode.sourceId == source_id)
            .order_by(Episode.episodeIndex)
            .limit(page_size)
            .offset(offset)
        )
        result = await self._session.execute(stmt)
        episodes = result.scalars().all()

        return {
            "list": [
                {
                    "episodeId": ep.id,
                    "episodeTitle": ep.title,
                    "episodeNumber": ep.episodeIndex,
                    "sourceUrl": ep.sourceUrl,
                    "danmakuFilePath": ep.danmakuFilePath,
                    "commentCount": ep.commentCount or 0,
                    "fetchedAt": ep.fetchedAt.isoformat() if ep.fetchedAt else None,
                }
                for ep in episodes
            ],
            "total": total,
            "page": page,
            "page_size": page_size,
        }

    async def check_duplicate_import(
        self,
        provider: str,
        media_id: str,
        anime_title: str,
        season: Optional[int] = None,
        is_single_episode: bool = False,
        episode_index: Optional[int] = None,
    ) -> Optional[str]:
        """
        重复导入检查（精确模式）。

        替代 crud.check_duplicate_import。
        why: 仅当「相同 provider + media_id + 集数且已有弹幕」时才判定为重复，
             避免因存在同名作品或部分集数而误拦新增集数的导入。

        Args:
            provider: 数据源提供方
            media_id: 该数据源上的媒体 ID
            anime_title: 作品标题（仅用于组装提示文案）
            season: 季度号
            is_single_episode: 是否单集导入
            episode_index: 单集导入时的集数

        Returns:
            None 表示允许导入；字符串表示重复原因
        """
        source_query = SourceQueryRepository(self._session)

        # 数据源不存在，直接允许导入
        exists = await source_query.check_source_exists_by_media_id(
            provider, media_id, season=season
        )
        if not exists:
            return None

        anime_id = await source_query.get_anime_id_by_source_media_id(
            provider, media_id, season=season
        )
        if not anime_id:
            # 理论上不会发生，保守放行
            return None

        # 单集导入：仅当该集已有弹幕文件且弹幕数 > 0 才算重复
        if is_single_episode and episode_index is not None:
            stmt = (
                select(Episode)
                .join(AnimeSource, Episode.sourceId == AnimeSource.id)
                .where(
                    AnimeSource.providerName == provider,
                    AnimeSource.mediaId == media_id,
                    Episode.episodeIndex == episode_index,
                )
                .limit(1)
            )
            result = await self._session.execute(stmt)
            episode = result.scalar_one_or_none()
            if episode and episode.danmakuFilePath and (episode.commentCount or 0) > 0:
                return (
                    f"作品 '{anime_title}' 的第 {episode_index} 集（{provider}源）"
                    f"已在媒体库中且已有 {episode.commentCount} 条弹幕，无需重复导入"
                )
            return None

        # 全量导入：已有弹幕也放行，因为可能存在新增集数
        stmt = (
            select(func.count(Episode.id))
            .join(AnimeSource, Episode.sourceId == AnimeSource.id)
            .where(
                AnimeSource.providerName == provider,
                AnimeSource.mediaId == media_id,
                Episode.danmakuFilePath.isnot(None),
                Episode.commentCount > 0,
            )
        )
        result = await self._session.execute(stmt)
        episode_count = result.scalar_one()
        if episode_count > 0:
            logger.info(
                f"数据源 ({provider}/{media_id}) 已有 {episode_count} 集弹幕，但允许导入新集数"
            )
        return None

    async def count_episodes_with_danmaku_by_provider(
        self,
        provider: str,
        media_id: str
    ) -> int:
        """
        统计指定数据源下已有弹幕的集数

        替代 crud.count_danmaku_episodes_by_source

        Args:
            provider: 提供商名称
            media_id: 媒体ID

        Returns:
            已有弹幕的集数
        """
        stmt = (
            select(func.count(Episode.id))
            .join(AnimeSource, Episode.sourceId == AnimeSource.id)
            .where(
                AnimeSource.providerName == provider,
                AnimeSource.mediaId == media_id,
                Episode.danmakuFilePath.isnot(None),
                Episode.danmakuFilePath != '',
            )
        )
        result = await self._session.execute(stmt)
        return result.scalar_one()

    async def get_episode_fetched_at(self, episode_id: int) -> Optional[Any]:
        """
        获取分集的 fetchedAt 时间戳（用于自动刷新过期检测）

        替代 crud.get_episode_fetched_at

        Args:
            episode_id: 分集ID

        Returns:
            datetime 或 None
        """
        stmt = select(Episode.fetchedAt).where(Episode.id == episode_id)
        result = await self._session.execute(stmt)
        row = result.one_or_none()
        return row[0] if row else None

    async def search_episodes_in_library(
        self,
        keyword: Optional[str] = None,
        anime_id: Optional[int] = None,
        limit: int = 100
    ) -> List[Dict[str, Any]]:
        """
        在媒体库中搜索分集（跨表：Episode → AnimeSource → Anime）

        替代旧 EpisodeRepository.search_in_library

        Args:
            keyword: 搜索关键词（匹配分集标题）
            anime_id: 作品ID过滤
            limit: 返回数量限制

        Returns:
            分集信息列表
        """
        stmt = (
            select(Episode, Anime.title.label("animeTitle"))
            .join(AnimeSource, Episode.sourceId == AnimeSource.id)
            .join(Anime, AnimeSource.animeId == Anime.id)
        )

        # 关键词搜索
        if keyword:
            stmt = stmt.where(Episode.title.ilike(f'%{keyword}%'))

        # 作品ID过滤
        if anime_id:
            stmt = stmt.where(Anime.id == anime_id)

        stmt = stmt.order_by(Episode.episodeIndex).limit(limit)

        result = await self._session.execute(stmt)
        rows = result.all()

        return [
            {
                "episodeId": row.Episode.id,
                "episodeTitle": row.Episode.title,
                "episodeNumber": row.Episode.episodeIndex,
                "animeTitle": row.animeTitle,
                "commentCount": row.Episode.commentCount or 0,
            }
            for row in rows
        ]


    async def search_in_library(
        self,
        keyword: Optional[str] = None,
        season: Optional[int] = None,
        episode: Optional[int] = None,
        limit: int = 100,
    ) -> List[Dict[str, Any]]:
        """按作品标题、季度和集数搜索媒体库中的分集匹配记录。"""
        stmt = (
            select(
                Episode.id.label("episodeId"),
                Episode.title.label("episodeTitle"),
                Episode.episodeIndex.label("episodeNumber"),
                Anime.id.label("animeId"),
                Anime.title.label("animeTitle"),
                Anime.type.label("type"),
                Anime.imageUrl.label("imageUrl"),
                Anime.season.label("season"),
                AnimeAlias.nameEn.label("nameEn"),
                AnimeAlias.nameJp.label("nameJp"),
                AnimeAlias.nameRomaji.label("nameRomaji"),
                AnimeAlias.aliasCn1.label("aliasCn1"),
                AnimeAlias.aliasCn2.label("aliasCn2"),
                AnimeAlias.aliasCn3.label("aliasCn3"),
                AnimeSource.providerName.label("providerName"),
                AnimeSource.isFavorited.label("isFavorited"),
            )
            .join(AnimeSource, Episode.sourceId == AnimeSource.id)
            .join(Anime, AnimeSource.animeId == Anime.id)
            .outerjoin(AnimeAlias, AnimeAlias.animeId == Anime.id)
        )
        if keyword:
            normalized_keyword = keyword.replace(" ", "").replace("：", ":")
            title_match = or_(
                func.replace(func.replace(Anime.title, " ", ""), "：", ":").ilike(
                    f"%{normalized_keyword}%"
                ),
                AnimeAlias.nameEn.ilike(f"%{keyword}%"),
                AnimeAlias.nameJp.ilike(f"%{keyword}%"),
                AnimeAlias.nameRomaji.ilike(f"%{keyword}%"),
                AnimeAlias.aliasCn1.ilike(f"%{keyword}%"),
                AnimeAlias.aliasCn2.ilike(f"%{keyword}%"),
                AnimeAlias.aliasCn3.ilike(f"%{keyword}%"),
            )
            stmt = stmt.where(title_match)
        if season is not None:
            stmt = stmt.where(Anime.season == season)
        if episode is not None:
            stmt = stmt.where(Episode.episodeIndex == episode)

        stmt = stmt.order_by(AnimeSource.isFavorited.desc(), Episode.id).limit(limit)
        result = await self._session.execute(stmt)
        return [dict(row) for row in result.mappings().all()]

    async def get_episode_provider_info(self, episode_id: int) -> Optional[Dict[str, Any]]:
        """
        获取分集的提供方信息（用于任务系统）

        替代 crud.get_episode_provider_info

        Args:
            episode_id: 分集ID

        Returns:
            包含 providerName / animeId / mediaId / providerEpisodeId / danmakuFilePath / episodeIndex 的字典
        """
        stmt = (
            select(
                AnimeSource.providerName,
                AnimeSource.animeId,
                AnimeSource.mediaId,
                Episode.providerEpisodeId,
                Episode.danmakuFilePath,
                Episode.episodeIndex
            )
            .join(AnimeSource, Episode.sourceId == AnimeSource.id)
            .where(Episode.id == episode_id)
        )
        result = await self._session.execute(stmt)
        row = result.mappings().first()
        # 返回实际查询结果，避免调用方将存在的分集误判为不存在。
        return dict(row) if row else None

    async def find_episode_via_tmdb_mapping(
        self,
        tmdb_id: str,
        group_id: str,
        custom_season: Optional[int],
        custom_episode: Optional[int]
    ) -> List[Dict[str, Any]]:
        """
        通过TMDB映射关系查找本地数据库中的分集

        替代 crud.find_episode_via_tmdb_mapping
        此实现使用自连接(self-join)来查找与文件名S/E对应的库内S/E

        Args:
            tmdb_id: TMDB TV ID
            group_id: TMDB Episode Group ID
            custom_season: 自定义季度号
            custom_episode: 自定义集数号

        Returns:
            匹配的分集列表
        """
        # 为 tmdb_episode_mapping 表的自连接创建别名
        MappingFromFile = aliased(TmdbEpisodeMapping)
        MappingToLibrary = aliased(TmdbEpisodeMapping)

        stmt = (
            select(
                Anime.id.label("animeId"),
                Anime.title.label("animeTitle"),
                Anime.type,
                Anime.imageUrl.label("imageUrl"),
                Anime.createdAt.label("startDate"),
                Episode.id.label("episodeId"),
                Episode.title.label("episodeTitle"),
                Scraper.displayOrder,
                AnimeSource.isFavorited.label("isFavorited"),
                AnimeMetadata.bangumiId.label("bangumiId")
            )
            .select_from(MappingFromFile)
            .join(
                MappingToLibrary,
                and_(
                    MappingFromFile.absoluteEpisodeNumber == MappingToLibrary.absoluteEpisodeNumber,
                    MappingFromFile.tmdbTvId == MappingToLibrary.tmdbTvId,
                    MappingFromFile.tmdbEpisodeGroupId == MappingToLibrary.tmdbEpisodeGroupId
                )
            )
            .join(AnimeMetadata, AnimeMetadata.tmdbId == MappingToLibrary.tmdbTvId)
            .join(Anime, and_(
                Anime.id == AnimeMetadata.animeId,
                Anime.season == MappingToLibrary.customSeasonNumber
            ))
            .join(AnimeSource, Anime.id == AnimeSource.animeId)
            .join(Episode, and_(
                Episode.sourceId == AnimeSource.id,
                Episode.episodeIndex == MappingToLibrary.customEpisodeNumber
            ))
            .join(Scraper, AnimeSource.providerName == Scraper.providerName)
            .where(
                MappingFromFile.tmdbTvId == tmdb_id,
                MappingFromFile.tmdbEpisodeGroupId == group_id
            )
        )

        if custom_season is not None and custom_episode is not None:
            # 增强：同时匹配自定义编号和TMDB官方编号
            stmt = stmt.where(
                or_(
                    and_(
                        MappingFromFile.customSeasonNumber == custom_season,
                        MappingFromFile.customEpisodeNumber == custom_episode
                    ),
                    and_(
                        MappingFromFile.tmdbSeasonNumber == custom_season,
                        MappingFromFile.tmdbEpisodeNumber == custom_episode
                    )
                )
            )

        stmt = stmt.order_by(
            AnimeSource.isFavorited.desc(),
            Scraper.displayOrder.asc(),
            Anime.createdAt.desc()
        )

        result = await self._session.execute(stmt)
        return [dict(row) for row in result.mappings()]

    async def get_episode_indices_by_source_media_id(
        self,
        provider_name: str,
        media_id: str
    ) -> List[int]:
        """
        通过源提供商和媒体ID精确获取该源下所有已有的分集序号。

        与 get_episode_indices_by_anime_title 不同，此函数按 provider+mediaId 精确匹配，
        避免同名不同类型的作品被错误关联（如TV版和剧场版同名问题）。
        """
        stmt = (
            select(distinct(Episode.episodeIndex))
            .join(AnimeSource, Episode.sourceId == AnimeSource.id)
            .where(
                AnimeSource.providerName == provider_name,
                AnimeSource.mediaId == media_id
            )
            .order_by(Episode.episodeIndex)
        )
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def get_episode_indices_by_source_media_ids_batch(
        self,
        provider_media_pairs: List[tuple[str, str]],
    ) -> Dict[tuple[str, str], List[int]]:
        """按提供方和媒体 ID 成对批量查询集号，与单源查询保持相同口径。"""
        pairs = list(dict.fromkeys(provider_media_pairs))
        if not pairs:
            return {}

        stmt = (
            select(
                AnimeSource.providerName,
                AnimeSource.mediaId,
                Episode.episodeIndex,
            )
            .select_from(Episode)
            .join(AnimeSource, Episode.sourceId == AnimeSource.id)
            .where(or_(*(
                and_(
                    AnimeSource.providerName == provider,
                    AnimeSource.mediaId == media_id,
                )
                for provider, media_id in pairs
            )))
            .distinct()
            .order_by(Episode.episodeIndex)
        )
        result = await self._session.execute(stmt)
        indices_by_pair: Dict[tuple[str, str], List[int]] = {
            pair: [] for pair in pairs
        }
        for provider, media_id, episode_index in result.all():
            indices_by_pair[(provider, media_id)].append(episode_index)
        return indices_by_pair


    async def get_existing_danmaku_indices(
        self, provider: str, media_id: str, episode_indices: List[int],
    ) -> List[int]:
        """批量查询指定源中已有弹幕的集号，避免将空分集当作导入完成。"""
        if not episode_indices:
            return []
        stmt = (
            select(distinct(Episode.episodeIndex))
            .join(AnimeSource, Episode.sourceId == AnimeSource.id)
            .where(
                AnimeSource.providerName == provider,
                AnimeSource.mediaId == media_id,
                Episode.episodeIndex.in_(episode_indices),
                Episode.danmakuFilePath.isnot(None),
                Episode.commentCount > 0,
            )
        )
        result = await self._session.execute(stmt)
        return list(result.scalars().all())
