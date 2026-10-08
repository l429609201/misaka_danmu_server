"""
AnimeQueryRepository - 动漫复杂查询层

职责：处理多表 JOIN、聚合、复杂搜索等读操作。
基础 CRUD 操作请使用 AnimeRepository。

设计原则（方案B：分离 Query 类）：
- Repository（anime.py）：单表 CRUD，写操作为主
- QueryRepository（本文件）：复杂读查询，性能优化
- Service：业务编排，组合多个 Repository

修复说明：
本文件修复了旧 src/repositories/anime_repository.py 的 4 个 Bug：
1. joinedload(Anime.metadata) → metadataRecord（正确字段名）
2. AnimeMetadata 读别名字段 → 改从 AnimeAlias 读
3. aliases 不是列表 → uselist=False，单个对象
4. get_library_list 补齐缺失字段（imageUrl/localImagePath/year/groupId/groupName）
"""

import logging
from collections import defaultdict
from typing import Optional, List, Dict, Any
from sqlalchemy import select, func, or_, exists
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload, joinedload

from ..orm_models import Anime, AnimeSource, AnimeAlias, AnimeMetadata, Episode, AnimeGroup

logger = logging.getLogger(__name__)


async def find_animes_for_matching(session: AsyncSession, title: str) -> List[Dict[str, Any]]:
    """
    为匹配流程查找可能的番剧，并返回其核心ID以供TMDB映射使用

    替代 crud.find_animes_for_matching

    Args:
        session: 数据库会话
        title: 标题关键词

    Returns:
        匹配的动漫列表，包含 animeId, tmdbId, tmdbEpisodeGroupId, title
    """
    title_len_expr = func.length(Anime.title)
    stmt = (
        select(
            Anime.id.label("animeId"),
            AnimeMetadata.tmdbId,
            AnimeMetadata.tmdbEpisodeGroupId,
            Anime.title,
            # 修正：将用于排序的列添加到 SELECT 列表中，以兼容 PostgreSQL 的 DISTINCT 规则
            title_len_expr.label("title_length")
        )
        .join(AnimeMetadata, Anime.id == AnimeMetadata.animeId, isouter=True)
        .join(AnimeAlias, Anime.id == AnimeAlias.animeId, isouter=True)
    )

    normalized_like_title = f"%{title.replace('：', ':').replace(' ', '')}%"
    like_conditions = [
        func.replace(func.replace(col, '：', ':'), ' ', '').like(normalized_like_title)
        for col in [Anime.title, AnimeAlias.nameEn, AnimeAlias.nameJp, AnimeAlias.nameRomaji,
                    AnimeAlias.aliasCn1, AnimeAlias.aliasCn2, AnimeAlias.aliasCn3]
    ]
    stmt = stmt.where(or_(*like_conditions)).distinct().order_by(title_len_expr).limit(5)

    result = await session.execute(stmt)
    return [dict(row) for row in result.mappings()]


class AnimeQueryRepository:
    """动漫复杂查询 Repository"""

    def __init__(self, session: AsyncSession):
        """
        初始化查询 Repository

        Args:
            session: SQLAlchemy AsyncSession 实例
        """
        self._session = session

    async def find_animes_for_matching(self, title: str) -> List[Dict[str, Any]]:
        """通过仓储会话查找匹配候选，供 DatabaseService 的 anime 代理调用。"""
        return await find_animes_for_matching(self._session, title)


    async def get_calendar_tracking_items(self) -> List[Dict[str, Any]]:
        """获取有播出星期的追更作品及其首个有分集追更源的最新分集。"""
        stmt = (
            select(Anime)
            .join(AnimeMetadata, AnimeMetadata.animeId == Anime.id)
            .where(
                AnimeMetadata.airWeekday.isnot(None),
                Anime.sources.any(AnimeSource.incrementalRefreshEnabled.is_(True)),
            )
            .options(selectinload(Anime.metadataRecord), selectinload(Anime.sources))
        )
        animes = (await self._session.execute(stmt)).scalars().all()
        # 一次批量查询各源最新分集，避免随作品和源数量增长的逐条查询。
        latest_indices = (
            select(
                Episode.sourceId.label("source_id"),
                func.max(Episode.episodeIndex).label("episode_index"),
            )
            .join(AnimeSource, AnimeSource.id == Episode.sourceId)
            .join(AnimeMetadata, AnimeMetadata.animeId == AnimeSource.animeId)
            .where(
                AnimeSource.incrementalRefreshEnabled.is_(True),
                AnimeMetadata.airWeekday.isnot(None),
            )
            .group_by(Episode.sourceId)
            .subquery()
        )
        episodes = (await self._session.execute(
            select(Episode).join(
                latest_indices,
                (Episode.sourceId == latest_indices.c.source_id)
                & (Episode.episodeIndex == latest_indices.c.episode_index),
            )
        )).scalars().all()
        latest_by_source = {episode.sourceId: episode for episode in episodes}
        items = []
        for anime in animes:
            meta = anime.metadataRecord
            if not meta or not meta.airWeekday:
                continue
            # 保留原逻辑：取首个有分集的追更源，而不是跨源取最大集数。
            latest = next((
                latest_by_source[source.id]
                for source in anime.sources
                if source.incrementalRefreshEnabled and source.id in latest_by_source
            ), None)
            items.append({
                "animeId": anime.id, "title": anime.title, "season": anime.season,
                "airWeekday": meta.airWeekday, "airTime": meta.airTime,
                "latestEpisode": latest.episodeIndex if latest else None,
                "latestHasDanmaku": (latest.commentCount or 0) > 0 if latest else False,
                "imageUrl": anime.imageUrl,
            })
        # 返回普通数据，避免事务退出后路由读取 ORM 关系触发隐式 I/O。
        return items


    async def get_local_bangumi_ids(self) -> set[str]:
        """读取本地 Bangumi 标识集合，供日历标注已入库作品。"""
        result = await self._session.execute(
            select(AnimeMetadata.bangumiId).where(AnimeMetadata.bangumiId.isnot(None))
        )
        return {str(value) for value in result.scalars().all()}


    async def find_import_identity(
        self, *, season: Optional[int], provider: Optional[str] = None,
        media_id: Optional[str] = None, metadata_key: Optional[str] = None,
        metadata_id: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        """查询导入强标识的首个候选；标题一致性与年份补齐由编排层决定。"""
        if metadata_key is not None:
            if metadata_key not in {"tmdbId", "tvdbId", "imdbId"}:
                raise ValueError("不支持的导入元数据标识")
            stmt = select(Anime.id.label("anime_id"), Anime.title, Anime.year).join(
                AnimeMetadata, Anime.id == AnimeMetadata.animeId
            ).where(getattr(AnimeMetadata, metadata_key) == str(metadata_id))
        else:
            stmt = select(
                Anime.id.label("anime_id"), Anime.title, Anime.year,
                AnimeSource.id.label("source_id"),
            ).join(AnimeSource, AnimeSource.animeId == Anime.id).where(
                AnimeSource.providerName == provider, AnimeSource.mediaId == media_id,
            )
        if season is not None:
            stmt = stmt.where(Anime.season == season)
        row = (await self._session.execute(stmt.order_by(Anime.id).limit(1))).mappings().first()
        return dict(row) if row is not None else None

    async def find_import_title_ids(
        self, title: str, season: Optional[int], media_type: Optional[str],
        year: Optional[int],
    ) -> List[int]:
        """按标题、季度、类型及确切年份状态查重，未知年份仅匹配 NULL。"""
        stmt = select(Anime.id).where(Anime.title == title, Anime.season == season)
        if media_type:
            stmt = stmt.where(Anime.type == media_type)
        # 不复用宽松年份查询，防止未知年份作品被并入任意确定年份条目。
        stmt = stmt.where(Anime.year.is_(None) if year is None else Anime.year == year)
        result = await self._session.execute(stmt.order_by(Anime.id))
        return list(result.scalars().all())


    async def search_anime(self, keyword: str) -> List[Dict[str, Any]]:
        """按关键词搜索库内作品（替代 crud.search_anime）。

        why：使用 LIKE 而非 MATCH...AGAINST，以兼容 PostgreSQL。
        虽比全文索引慢，但提供跨数据库方言的一致行为。
        按标题长度升序排列，较短的匹配通常更相关。

        Args:
            keyword: 搜索关键词，会剔除全文检索保留字符

        Returns:
            含 id/title/type 的字典列表；关键词清洗后为空则返回空列表
        """
        search_keyword = keyword.strip()
        if not search_keyword:
            return []

        stmt = (
            select(Anime.id, Anime.title, Anime.type)
            .where(Anime.title.like(f"%{search_keyword}%"))
            .order_by(func.length(Anime.title))
        )
        result = await self._session.execute(stmt)
        return [dict(row) for row in result.mappings()]


    async def search_animes_for_dandan(self, keyword: str) -> List[Dict[str, Any]]:
        """搜索弹弹Play所需的作品摘要及元数据。"""
        search_keyword = keyword.strip()
        if not search_keyword:
            return []

        episode_count = (
            select(func.count(Episode.id))
            .join(AnimeSource, AnimeSource.id == Episode.sourceId)
            .where(AnimeSource.animeId == Anime.id)
            .correlate(Anime)
            .scalar_subquery()
        )
        # 统一中文冒号与空格，兼容库内标题和别名的常见写法差异。
        normalized_keyword = search_keyword.replace("：", ":").replace(" ", "")
        normalized_like_keyword = f"%{normalized_keyword}%"
        searchable_columns = (
            Anime.title,
            AnimeAlias.nameEn,
            AnimeAlias.nameJp,
            AnimeAlias.nameRomaji,
            AnimeAlias.aliasCn1,
            AnimeAlias.aliasCn2,
            AnimeAlias.aliasCn3,
        )
        like_conditions = [
            func.replace(func.replace(column, "：", ":"), " ", "").like(normalized_like_keyword)
            for column in searchable_columns
        ]

        stmt = (
            select(
                Anime.id.label("animeId"), Anime.title.label("animeTitle"),
                Anime.type, Anime.year, Anime.imageUrl,
                Anime.createdAt.label("startDate"),
                AnimeMetadata.bangumiId, episode_count.label("episodeCount"),
            )
            .outerjoin(AnimeMetadata, AnimeMetadata.animeId == Anime.id)
            .outerjoin(AnimeAlias, AnimeAlias.animeId == Anime.id)
            .where(or_(*like_conditions))
            .order_by(func.length(Anime.title), Anime.id)
        )
        result = await self._session.execute(stmt)
        return [dict(row) for row in result.mappings()]



    async def get_full_details(self, anime_id: int) -> Optional[Dict[str, Any]]:
        """
        获取作品的完整详情（包含元数据和别名）

        替代旧 crud.get_anime_full_details 和 旧 AnimeRepository.get_full_details
        修复了字段名 Bug（metadata→metadataRecord，别名字段从 AnimeAlias 读）

        Args:
            anime_id: 作品ID

        Returns:
            包含完整信息的字典，或 None
        """
        # 使用 joinedload 预加载关联对象（修复 Bug 1：metadata→metadataRecord）
        stmt = (
            select(Anime)
            .where(Anime.id == anime_id)
            .options(
                joinedload(Anime.metadataRecord),  # ✅ 正确字段名
                joinedload(Anime.aliases),         # ✅ uselist=False，单个对象
                joinedload(Anime.group)            # 补充：用于返回 groupName
            )
        )
        result = await self._session.execute(stmt)
        anime = result.unique().scalar_one_or_none()

        if not anime:
            return None

        # 构建返回字典
        meta = anime.metadataRecord  # ✅ 正确访问元数据
        alias = anime.aliases        # ✅ 单个对象，不是列表（修复 Bug 3）

        return {
            "animeId": anime.id,
            "title": anime.title,
            "type": anime.type,
            "season": anime.season,
            "episodeCount": anime.episodeCount,
            "year": anime.year,
            "imageUrl": anime.imageUrl,
            "localImagePath": anime.localImagePath,
            "createdAt": anime.createdAt.isoformat() if anime.createdAt else None,
            "groupId": anime.groupId,
            "groupName": anime.group.name if anime.group else None,
            # 元数据（✅ 从 AnimeMetadata 读正确字段，修复 Bug 2）
            "tmdbId": meta.tmdbId if meta else None,
            "tvdbId": meta.tvdbId if meta else None,
            "imdbId": meta.imdbId if meta else None,
            "doubanId": meta.doubanId if meta else None,
            "bangumiId": meta.bangumiId if meta else None,
            "tmdbEpisodeGroupId": meta.tmdbEpisodeGroupId if meta else None,
            "mediaServerType": meta.mediaServerType if meta else None,
            "mediaServerSeriesId": meta.mediaServerSeriesId if meta else None,
            "mediaServerSeasonId": meta.mediaServerSeasonId if meta else None,
            # 别名（✅ 从 AnimeAlias 读，修复 Bug 2）
            "nameEn": alias.nameEn if alias else None,
            "nameJp": alias.nameJp if alias else None,
            "nameRomaji": alias.nameRomaji if alias else None,
            "aliasCn1": alias.aliasCn1 if alias else None,
            "aliasCn2": alias.aliasCn2 if alias else None,
            "aliasCn3": alias.aliasCn3 if alias else None,
            "aliasLocked": alias.aliasLocked if alias else False,
        }

    async def get_library_anime_by_id(self, anime_id: int) -> Optional[Dict[str, Any]]:
        """按作品 ID 复用媒体库聚合查询，避免创建后全库扫描。"""
        result = await self.get_library_list(anime_id=anime_id)
        return next(iter(result["list"]), None)

    async def get_library_list(
        self,
        keyword: Optional[str] = None,
        page: Optional[int] = None,
        page_size: Optional[int] = None,
        *,
        anime_type: Optional[str] = None,
        sort_by: str = "anime_created",
        sort_order: str = "desc",
        anime_id: Optional[int] = None,
    ) -> Dict[str, Any]:
        """聚合媒体库列表，保留 UI 类型过滤、排序及分页契约。"""
        stmt = (
            select(
                Anime.id, Anime.title, Anime.type, Anime.season,
                Anime.episodeCount, Anime.createdAt, Anime.imageUrl,
                Anime.localImagePath, Anime.year, Anime.groupId,
                AnimeGroup.name.label("groupName"),
                func.count(func.distinct(AnimeSource.id)).label("sourceCount"),
            )
            .outerjoin(AnimeSource, AnimeSource.animeId == Anime.id)
            .outerjoin(AnimeGroup, Anime.groupId == AnimeGroup.id)
            .group_by(
                Anime.id, Anime.title, Anime.type, Anime.season,
                Anime.episodeCount, Anime.createdAt, Anime.imageUrl,
                Anime.localImagePath, Anime.year, Anime.groupId, AnimeGroup.name,
            )
        )
        if anime_id is not None:
            stmt = stmt.where(Anime.id == anime_id)
        if anime_type == "movie":
            stmt = stmt.where(Anime.type == "movie")
        elif anime_type == "tv":
            stmt = stmt.where(Anime.type != "movie")
        elif anime_type:
            stmt = stmt.where(Anime.type == anime_type)
        if keyword:
            normalized_keyword = keyword.replace(" ", "").replace("：", ":")
            alias_match = exists(
                select(1).where(AnimeAlias.animeId == Anime.id).where(or_(
                    AnimeAlias.nameEn.ilike(f"%{keyword}%"),
                    AnimeAlias.nameJp.ilike(f"%{keyword}%"),
                    AnimeAlias.nameRomaji.ilike(f"%{keyword}%"),
                    AnimeAlias.aliasCn1.ilike(f"%{keyword}%"),
                    AnimeAlias.aliasCn2.ilike(f"%{keyword}%"),
                    AnimeAlias.aliasCn3.ilike(f"%{keyword}%"),
                ))
            )
            stmt = stmt.where(or_(
                Anime.title.ilike(f"%{keyword}%"),
                Anime.title.ilike(f"%{normalized_keyword}%"), alias_match,
            ))
        # 分集时间使用相关聚合，避免主查询 JOIN 放大源计数；同值以 ID 稳定分页。
        sort_column = Anime.createdAt
        if sort_by == "episode_fetched":
            sort_column = (
                select(func.max(Episode.fetchedAt))
                .join(AnimeSource, Episode.sourceId == AnimeSource.id)
                .where(AnimeSource.animeId == Anime.id)
                .correlate(Anime).scalar_subquery()
            )
            sort_column = func.coalesce(sort_column, Anime.createdAt)
        ascending = sort_order == "asc"
        stmt = stmt.order_by(
            sort_column.asc() if ascending else sort_column.desc(),
            Anime.id.asc() if ascending else Anime.id.desc(),
        )
        if page is not None and page_size is not None:
            count_stmt = select(func.count()).select_from(stmt.order_by(None).subquery())
            total_result = await self._session.execute(count_stmt)
            total_count = total_result.scalar() or 0
            stmt = stmt.offset((page - 1) * page_size).limit(page_size)
        else:
            total_count = None

        result = await self._session.execute(stmt)
        rows = result.all()

        # 为每个作品查询源信息（优化：一次查询所有 animeId 的源）
        anime_ids = [row.id for row in rows]

        # 返回字段均来自源表自身，无需加载关系；AnimeSource 未定义 scraper 关系。
        sources_stmt = (
            select(AnimeSource)
            .where(AnimeSource.animeId.in_(anime_ids))
        )
        sources_result = await self._session.execute(sources_stmt)
        all_sources = sources_result.scalars().all()

        # 按 animeId 分组
        sources_by_anime = {}
        for source in all_sources:
            if source.animeId not in sources_by_anime:
                sources_by_anime[source.animeId] = []
            sources_by_anime[source.animeId].append(source)

        # 组装返回列表
        anime_list = []
        for row in rows:
            sources = sources_by_anime.get(row.id, [])
            source_infos = [
                {
                    "sourceId": s.id,
                    "providerName": s.providerName,
                    "isFavorited": s.isFavorited or False,
                    "incrementalRefreshEnabled": s.incrementalRefreshEnabled or False,
                    "isFinished": s.isFinished or False,
                }
                for s in sources
            ]

            anime_list.append({
                "animeId": row.id,
                "title": row.title,
                "type": row.type,
                "season": row.season,
                "episodeCount": row.episodeCount,
                "sourceCount": row.sourceCount,
                "createdAt": row.createdAt.isoformat() if row.createdAt else None,
                "imageUrl": row.imageUrl,              # ✅ 补充
                "localImagePath": row.localImagePath,  # ✅ 补充
                "year": row.year,                      # ✅ 补充
                "groupId": row.groupId,                # ✅ 补充
                "groupName": row.groupName,            # ✅ 补充
                "sources": source_infos,
            })

        return {
            "list": anime_list,
            "total": total_count if total_count is not None else len(anime_list),
        }

    async def find_by_title_season_year(
        self,
        title: str,
        season: Optional[int],
        year: Optional[int] = None,
    ) -> Optional[Dict[str, Any]]:
        """
        按标题 + 季度 (+ 可选年份) 精确查找作品，并带出元数据 ID

        替代 crud.find_anime_by_title_season_year 的单次查询部分（不含识别词重试）。
        与 AnimeRepository.get_by_title_and_season 的区别：
        - 支持可选 year 维度过滤
        - LEFT JOIN AnimeMetadata 带出各平台 ID
        - 返回字典而非 ORM 对象

        Args:
            title: 作品标题（精确匹配）
            season: 季度（精确匹配）
            year: 年份，为 None 时不参与过滤

        Returns:
            含作品基础字段与元数据 ID 的字典，未找到时返回 None
        """
        stmt = (
            select(
                Anime.id,
                Anime.title,
                Anime.season,
                Anime.type,
                Anime.year,
                Anime.imageUrl,
                Anime.localImagePath,
            )
            .where(Anime.title == title, Anime.season == season)
            .limit(1)
        )
        if year:
            stmt = stmt.where(Anime.year == year)

        # 左连接元数据表带出各平台 ID（作品可能没有元数据记录，故用 outerjoin）
        stmt = stmt.outerjoin(AnimeMetadata, Anime.id == AnimeMetadata.animeId).add_columns(
            AnimeMetadata.tmdbId,
            AnimeMetadata.imdbId,
            AnimeMetadata.tvdbId,
            AnimeMetadata.doubanId,
            AnimeMetadata.bangumiId,
        )

        result = await self._session.execute(stmt)
        row = result.mappings().first()
        return dict(row) if row else None

    async def find_by_title_season_year_with_recognition(
        self,
        title: str,
        season: Optional[int],
        year: Optional[int] = None,
        title_recognition_manager=None,
        source: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        """
        按标题 + 季度 (+ 年份) 查找作品，失败时应用识别词转换后重试

        替代 crud.find_anime_by_title_season_year（完整行为）。

        why 两段式：入库标题可能与外部传入的标题存在别名差异（如「XX 第二季」与
        「XX S2」）。先做完全匹配可避免对已规范化的标题做无谓转换；只有匹配失败
        才动用识别词规则，兼顾准确性与性能。

        Args:
            title: 作品标题
            season: 季度
            year: 年份，为 None 时不参与过滤
            title_recognition_manager: 识别词管理器（调用方注入，为 None 时退化为纯查询）
            source: 数据源标识，供识别词规则按源匹配

        Returns:
            命中的作品字典，两轮均未命中时返回 None
        """
        season_str = f"S{season:02d}" if season is not None else "S??"

        # 步骤1：完全匹配（不应用识别词转换）
        logger.info(f"🔍 数据库查找: title='{title}', season={season}, year={year}")
        row = await self.find_by_title_season_year(title, season, year)
        if row:
            logger.info(f"✓ 完全匹配成功: 找到作品 '{title}' {season_str}")
            return row

        logger.info(f"○ 完全匹配失败: 未找到匹配的番剧")

        # 步骤2：完全匹配失败，尝试识别词转换后重试
        if not title_recognition_manager:
            return None

        converted_title, converted_season, was_converted, _metadata_info, _ = (
            await title_recognition_manager.apply_storage_postprocessing(title, season, source)
        )

        if not was_converted:
            logger.info(f"○ 标题识别转换未生效: '{title}' {season_str} (无匹配规则)")
            return None

        converted_season_str = f"S{converted_season:02d}" if converted_season is not None else "S??"
        logger.info(
            f"🔍 尝试识别词转换匹配: '{title}' {season_str} -> '{converted_title}' {converted_season_str}"
        )

        row = await self.find_by_title_season_year(converted_title, converted_season, year)
        if row:
            logger.info(f"✓ 识别词转换匹配成功: 找到作品 '{converted_title}' {converted_season_str}")
            return row

        logger.info(f"○ 识别词转换匹配也失败: 未找到匹配的番剧")
        return None

    # ==================== 元数据ID查询 ====================
    async def find_anime_ids_by_media_server(
        self,
        server_type: str,
        series_id: Optional[str] = None,
        season_id: Optional[str] = None,
    ) -> List[int]:
        """按媒体服务器的剧集或季度 ID 获取作品 ID。"""
        stmt = select(AnimeMetadata.animeId).where(
            AnimeMetadata.mediaServerType == server_type,
        )
        if series_id is not None:
            stmt = stmt.where(AnimeMetadata.mediaServerSeriesId == series_id)
        if season_id is not None:
            stmt = stmt.where(AnimeMetadata.mediaServerSeasonId == season_id)
        result = await self._session.execute(stmt)
        return list(dict.fromkeys(result.scalars().all()))



    async def find_by_metadata_id_and_season(
        self,
        id_type: str,
        id_value: str,
        season: Optional[int]
    ) -> Optional[Dict[str, Any]]:
        """
        通过元数据ID和季度查找作品

        替代 crud.find_anime_by_metadata_id_and_season

        Args:
            id_type: ID类型 (tmdbId, tvdbId, imdbId, doubanId, bangumiId)
            id_value: ID值
            season: 季度号

        Returns:
            作品字典或 None
        """
        # 构建基础查询
        stmt = (
            select(
                Anime.id,
                Anime.title,
                Anime.type,
                Anime.season,
                Anime.episodeCount,
                Anime.year,
                Anime.imageUrl,
                Anime.localImagePath,
            )
            .join(AnimeMetadata, Anime.id == AnimeMetadata.animeId)
        )

        # 根据 id_type 添加对应的过滤条件
        if id_type == "tmdbId":
            stmt = stmt.where(AnimeMetadata.tmdbId == id_value)
        elif id_type == "tvdbId":
            stmt = stmt.where(AnimeMetadata.tvdbId == id_value)
        elif id_type == "imdbId":
            stmt = stmt.where(AnimeMetadata.imdbId == id_value)
        elif id_type == "doubanId":
            stmt = stmt.where(AnimeMetadata.doubanId == id_value)
        elif id_type == "bangumiId":
            stmt = stmt.where(AnimeMetadata.bangumiId == id_value)
        else:
            logger.warning(f"不支持的元数据ID类型: {id_type}")
            return None

        # 添加季度过滤
        if season is not None:
            stmt = stmt.where(Anime.season == season)

        result = await self._session.execute(stmt)
        row = result.first()

        if not row:
            return None

        return dict(row._mapping)

    # ==================== 重复条目扫描 ====================

    async def scan_duplicate_animes(self, strict: bool = True) -> List[Dict[str, Any]]:
        """
        扫描弹幕库中基于 TMDB ID 的重复条目。

        替代 crud.scan_duplicate_animes（纯聚合查询，无写操作）。

        Args:
            strict: True 按 tmdbId + season 分组（严格模式）；
                    False 仅按 tmdbId 分组（宽松模式，用于剧集组场景）

        Returns:
            [{ tmdbId, season(仅严格模式), items: [{ animeId, title, season,
               year, sourceCount, imageUrl, localImagePath }] }]
        """
        # 源数统计子查询：按番剧聚合关联源数量
        source_count_subq = (
            select(
                AnimeSource.animeId,
                func.count(AnimeSource.id).label("sourceCount")
            )
            .group_by(AnimeSource.animeId)
            .subquery()
        )

        # why: 只有带 tmdbId 的条目才有重复判定依据，故在 SQL 层就过滤掉空值，
        #      避免把大量无元数据条目拉到内存再丢弃。
        stmt = (
            select(
                Anime.id.label("animeId"),
                Anime.title,
                Anime.season,
                Anime.year,
                Anime.imageUrl,
                Anime.localImagePath,
                AnimeMetadata.tmdbId,
                func.coalesce(source_count_subq.c.sourceCount, 0).label("sourceCount"),
            )
            .join(AnimeMetadata, Anime.id == AnimeMetadata.animeId)
            .outerjoin(source_count_subq, Anime.id == source_count_subq.c.animeId)
            .where(
                AnimeMetadata.tmdbId.isnot(None),
                AnimeMetadata.tmdbId != "",
            )
            .order_by(AnimeMetadata.tmdbId, Anime.season)
        )

        result = await self._session.execute(stmt)
        rows = [dict(r) for r in result.mappings()]

        # 按分组键聚合
        groups: Dict[Any, list] = defaultdict(list)
        for row in rows:
            key = (row["tmdbId"], row["season"]) if strict else row["tmdbId"]
            groups[key].append(row)

        # 只保留有 2 条及以上条目的组
        duplicate_groups: List[Dict[str, Any]] = []
        for items in groups.values():
            if len(items) < 2:
                continue
            group: Dict[str, Any] = {"tmdbId": items[0]["tmdbId"], "items": items}
            if strict:
                group["season"] = items[0]["season"]
            duplicate_groups.append(group)

        return duplicate_groups

    async def get_anime_id_by_bangumi_id(self, bangumi_id: str) -> Optional[int]:
        """
        根据 Bangumi ID 查找对应的 anime_id

        替代 crud.get_anime_id_by_bangumi_id

        Args:
            bangumi_id: Bangumi ID（字符串格式）

        Returns:
            anime_id 或 None
        """
        stmt = (
            select(Anime.id)
            .join(AnimeMetadata, Anime.id == AnimeMetadata.animeId)
            .where(AnimeMetadata.bangumiId == bangumi_id)
        )
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def get_anime_details_for_dandan(self, anime_id: int) -> Optional[Dict[str, Any]]:
        """
        获取用于 DanDan API 的番剧详情（包含分集列表）

        替代 crud.get_anime_details_for_dandan

        Args:
            anime_id: 作品 ID

        Returns:
            {
                "anime": {id, title, type, season, year, imageUrl, ...},
                "episodes": [{episodeId, episodeTitle, episodeNumber, sourceId, ...}, ...]
            }
        """
        try:
            # 查询作品信息
            anime_stmt = (
                select(Anime, AnimeMetadata, AnimeAlias, AnimeGroup)
                .join(AnimeMetadata, Anime.id == AnimeMetadata.animeId, isouter=True)
                .join(AnimeAlias, Anime.id == AnimeAlias.animeId, isouter=True)
                .join(AnimeGroup, Anime.groupId == AnimeGroup.id, isouter=True)
                .where(Anime.id == anime_id)
            )
            anime_result = await self._session.execute(anime_stmt)
            anime_row = anime_result.first()

            if not anime_row:
                return None

            anime, metadata, alias, group = anime_row

            # 构建作品信息
            anime_data = {
                "animeId": anime.id,
                "animeTitle": anime.title,
                "type": anime.type,
                "season": anime.season,
                "year": anime.year,
                "imageUrl": anime.imageUrl,
                "localImagePath": anime.localImagePath,
                "groupId": anime.groupId,
                "groupName": group.name if group else None,
                # 元数据字段
                "tmdbId": metadata.tmdbId if metadata else None,
                "imdbId": metadata.imdbId if metadata else None,
                "tvdbId": metadata.tvdbId if metadata else None,
                "doubanId": metadata.doubanId if metadata else None,
                "bangumiId": metadata.bangumiId if metadata else None,
                # 别名字段
                "nameEn": alias.nameEn if alias else None,
                "nameJp": alias.nameJp if alias else None,
                "nameRomaji": alias.nameRomaji if alias else None,
                "aliasCn1": alias.aliasCn1 if alias else None,
                "aliasCn2": alias.aliasCn2 if alias else None,
                "aliasCn3": alias.aliasCn3 if alias else None,
            }

            # 查询分集列表
            episodes_stmt = (
                select(Episode, AnimeSource)
                .join(AnimeSource, Episode.sourceId == AnimeSource.id)
                .where(AnimeSource.animeId == anime_id)
                .order_by(AnimeSource.sourceOrder, Episode.episodeIndex)
            )
            episodes_result = await self._session.execute(episodes_stmt)

            episodes_data = []
            for episode, source in episodes_result:
                episodes_data.append({
                    "episodeId": episode.id,
                    "episodeTitle": episode.title,
                    "episodeNumber": episode.episodeIndex,
                    "sourceId": episode.sourceId,
                    "providerName": source.providerName,
                    "mediaId": source.mediaId,
                    "danmakuFilePath": episode.danmakuFilePath,
                    "commentCount": episode.commentCount,
                })

            return {
                "anime": anime_data,
                "episodes": episodes_data,
            }
        except Exception as e:
            logger.error(f"获取 DanDan API 番剧详情失败 (anime_id={anime_id}): {e}", exc_info=True)
            return None
