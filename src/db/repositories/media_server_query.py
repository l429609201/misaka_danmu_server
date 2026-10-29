"""
MediaServerQueryRepository：媒体服务器查询仓储
替代 crud.py 中的媒体服务器相关查询方法
"""

import json
import logging
import time
from typing import Optional, Dict, Any, List

from sqlalchemy import select, update, func, case, literal, or_, union_all
from sqlalchemy.ext.asyncio import AsyncSession

from src.db import orm_models
from src.db.orm_models import MediaServer, MediaItem

logger = logging.getLogger(__name__)


class MediaServerQueryRepository:
    """
    媒体服务器查询仓储

    替代 crud.py 中的:
    - get_all_media_servers
    - get_media_server_by_id
    - get_media_items / get_media_works
    - get_show_seasons / get_season_episodes
    - get_episode_ids_by_show / get_episode_ids_by_season
    - mark_media_items_imported
    - get_unimported_item_ids / get_unimported_count
    """

    # 媒体项字典化时输出的字段（与 crud 实现保持完全一致）
    _ITEM_FIELDS = (
        "id", "serverId", "mediaId", "libraryId", "title", "mediaType",
        "season", "episode", "year", "tmdbId", "tvdbId", "imdbId",
        "posterUrl", "isImported", "createdAt", "updatedAt",
    )

    @classmethod
    def _item_to_dict(cls, item: MediaItem) -> Dict[str, Any]:
        """将 MediaItem ORM 对象转为字典"""
        return {field: getattr(item, field) for field in cls._ITEM_FIELDS}

    def __init__(self, session: AsyncSession):
        self._session = session

    @staticmethod
    def _server_to_dict(server: MediaServer) -> Dict[str, Any]:
        """将 ORM 配置恢复为 UI 和扫描所需的完整值快照。"""
        return {
            "id": server.id,
            "name": server.name,
            "providerName": server.providerName,
            "url": server.url,
            "apiToken": server.apiToken,
            "isEnabled": server.isEnabled,
            "selectedLibraries": json.loads(server.selectedLibraries or "[]"),
            "filterRules": json.loads(server.filterRules or "{}"),
            "createdAt": server.createdAt,
            "updatedAt": server.updatedAt,
        }

    async def get_all_media_servers(self) -> List[Dict[str, Any]]:
        """
        获取所有媒体服务器配置

        替代 crud.get_all_media_servers

        Returns:
            媒体服务器配置列表（字典格式）
        """
        stmt = select(MediaServer).order_by(MediaServer.createdAt)
        result = await self._session.execute(stmt)
        servers = result.scalars().all()

        return [self._server_to_dict(server) for server in servers]

    async def get_media_server_by_id(self, server_id: int) -> Optional[Dict[str, Any]]:
        """
        根据ID获取媒体服务器配置

        替代 crud.get_media_server_by_id

        Args:
            server_id: 服务器ID

        Returns:
            服务器配置（字典格式），不存在时返回 None
        """
        server = await self._session.get(MediaServer, server_id)
        if not server:
            return None

        return self._server_to_dict(server)

    # ==================================================================
    # 媒体项查询
    # ==================================================================

    async def get_media_items(
        self,
        server_id: Optional[int] = None,
        is_imported: Optional[bool] = None,
        media_type: Optional[str] = None,
        page: int = 1,
        page_size: int = 100,
    ) -> Dict[str, Any]:
        """
        获取媒体项列表，支持过滤和分页

        替代 crud.get_media_items
        """
        stmt = select(MediaItem)

        if server_id is not None:
            stmt = stmt.where(MediaItem.serverId == server_id)
        if is_imported is not None:
            stmt = stmt.where(MediaItem.isImported == is_imported)
        if media_type is not None:
            stmt = stmt.where(MediaItem.mediaType == media_type)

        # 先在过滤条件上取总数，再做分页查询
        count_stmt = select(func.count()).select_from(stmt.alias("count_subquery"))
        total = (await self._session.execute(count_stmt)).scalar_one()

        stmt = (
            stmt.order_by(MediaItem.createdAt.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
        result = await self._session.execute(stmt)
        items = result.scalars().all()

        return {
            "total": total,
            "list": [self._item_to_dict(item) for item in items],
        }

    async def get_show_seasons(self, server_id: int, title: str) -> List[Dict[str, Any]]:
        """
        获取某部剧集的所有季度信息

        替代 crud.get_show_seasons
        """
        stmt = (
            select(
                MediaItem.season,
                func.count(MediaItem.id).label("episodeCount"),
                func.min(MediaItem.year).label("year"),
                func.min(MediaItem.posterUrl).label("posterUrl"),
            )
            .where(
                MediaItem.serverId == server_id,
                MediaItem.title == title,
                MediaItem.mediaType == "tv_series",
            )
            .group_by(MediaItem.season)
            .order_by(MediaItem.season)
        )

        result = await self._session.execute(stmt)
        return [
            {
                "season": s.season,
                "episodeCount": s.episodeCount,
                "year": s.year,
                "posterUrl": s.posterUrl,
            }
            for s in result.all()
        ]

    async def get_season_episodes(
        self,
        server_id: int,
        title: str,
        season: int,
        page: int = 1,
        page_size: int = 100,
    ) -> Dict[str, Any]:
        """
        获取某一季的所有集（分页）

        替代 crud.get_season_episodes
        """
        stmt = select(MediaItem).where(
            MediaItem.serverId == server_id,
            MediaItem.title == title,
            MediaItem.season == season,
            MediaItem.mediaType == "tv_series",
        )

        count_stmt = select(func.count()).select_from(stmt.alias("count_subquery"))
        total = (await self._session.execute(count_stmt)).scalar_one()

        stmt = (
            stmt.order_by(MediaItem.episode)
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
        result = await self._session.execute(stmt)
        episodes = result.scalars().all()

        return {
            "total": total,
            "list": [self._item_to_dict(ep) for ep in episodes],
        }

    async def get_import_title_groups(self, item_ids: List[int]) -> List[tuple[str, int]]:
        """按批次统计要导入的媒体标题，避免数据库参数数量限制。"""
        groups = []
        for start in range(0, len(item_ids), 30000):
            batch = item_ids[start:start + 30000]
            stmt = select(
                MediaItem.title, func.count(MediaItem.id).label("cnt"),
            ).where(MediaItem.id.in_(batch)).group_by(MediaItem.title)
            result = await self._session.execute(stmt)
            groups.extend((title, count) for title, count in result.all())
        return groups

    async def get_episode_ids_by_show(self, server_id: int, title: str) -> List[int]:
        """
        根据剧集名称获取所有集的ID

        替代 crud.get_episode_ids_by_show
        """
        stmt = select(MediaItem.id).where(
            MediaItem.serverId == server_id,
            MediaItem.title == title,
            MediaItem.mediaType == "tv_series",
        )
        result = await self._session.execute(stmt)
        return [row[0] for row in result.all()]

    async def get_episode_ids_by_season(
        self, server_id: int, title: str, season: int
    ) -> List[int]:
        """
        根据剧集名称和季度获取所有集的ID

        替代 crud.get_episode_ids_by_season
        """
        stmt = select(MediaItem.id).where(
            MediaItem.serverId == server_id,
            MediaItem.title == title,
            MediaItem.season == season,
            MediaItem.mediaType == "tv_series",
        )
        result = await self._session.execute(stmt)
        return [row[0] for row in result.all()]

    # ==================================================================
    # 导入状态维护
    # ==================================================================

    async def mark_media_items_imported(self, item_ids: List[int]) -> int:
        """
        标记媒体项为已导入

        替代 crud.mark_media_items_imported

        why：分批执行，规避 asyncpg 单语句 32767 个绑定参数的上限。
        仅 flush，事务边界交由上层控制。
        """
        if not item_ids:
            return 0

        PG_BATCH = 30000
        total_updated = 0
        for i in range(0, len(item_ids), PG_BATCH):
            batch = item_ids[i:i + PG_BATCH]
            stmt = (
                update(MediaItem)
                .where(MediaItem.id.in_(batch))
                .values(isImported=True)
            )
            result = await self._session.execute(stmt)
            total_updated += result.rowcount
        await self._session.flush()
        return total_updated

    @staticmethod
    def _has_danmaku_exists():
        """构造关联子查询：判断某个 MediaItem 对应的作品在弹幕库中是否已有真实弹幕。

        why：原逻辑用 MediaItem.isImported==False 判定"未导入"，但 isImported 只表示
        "曾提交过导入任务"，与是否真正抓到弹幕无关（见 issue #441）。导入失败的项
        isImported 也被置为 True，导致"一键导入/定时任务"永远跳过这些失败项，无法重试。
        改为以弹幕库真实状态判定：只要弹幕库中没有对应且含弹幕的分集，就视为未导入。

        匹配规则（A2 方案，已知局限：以标题+季度为准，需与库内 Anime 命名一致）：
          - 标题去空格后精确匹配 Anime.title
          - 电视剧额外要求季度一致；电影/其他不比季度
          - 仅统计 commentCount > 0 的分集，避免"空壳条目"被误判为已导入
        """
        A = orm_models.Anime
        S = orm_models.AnimeSource
        E = orm_models.Episode
        return (
            select(literal(1))
            .select_from(A)
            .join(S, S.animeId == A.id)
            .join(E, E.sourceId == S.id)
            .where(
                func.replace(A.title, " ", "") == func.replace(MediaItem.title, " ", ""),
                or_(MediaItem.mediaType != "tv_series", A.season == MediaItem.season),
                E.commentCount > 0,
            )
            .correlate(MediaItem)
            .exists()
        )

    async def get_unimported_item_ids(
        self, server_id: int, media_type: Optional[str] = None
    ) -> List[int]:
        """
        获取指定服务器下所有"弹幕库中尚无对应弹幕"的媒体项ID（可重新导入）

        替代 crud.get_unimported_item_ids
        """
        stmt = select(MediaItem.id).where(
            MediaItem.serverId == server_id,
            ~self._has_danmaku_exists(),  # 弹幕库中无对应弹幕才算"未导入"
        )
        if media_type is not None:
            stmt = stmt.where(MediaItem.mediaType == media_type)
        result = await self._session.execute(stmt)
        return [row[0] for row in result.all()]

    async def get_unimported_count(
        self, server_id: int, media_type: Optional[str] = None
    ) -> int:
        """
        获取指定服务器下"弹幕库中尚无对应弹幕"的媒体项数量

        替代 crud.get_unimported_count，判定标准与 get_unimported_item_ids 一致
        """
        stmt = select(func.count(MediaItem.id)).where(
            MediaItem.serverId == server_id,
            ~self._has_danmaku_exists(),
        )
        if media_type is not None:
            stmt = stmt.where(MediaItem.mediaType == media_type)
        result = await self._session.execute(stmt)
        return result.scalar_one()

    # ==================================================================
    # 作品维度聚合查询
    # ==================================================================

    def _build_movie_conditions(
        self,
        server_id: Optional[int],
        is_imported: Optional[bool],
        search: Optional[str],
        year_from: Optional[int],
        year_to: Optional[int],
    ) -> List[Any]:
        """构建电影查询的 WHERE 条件"""
        conditions: List[Any] = [MediaItem.mediaType == "movie"]
        if server_id is not None:
            conditions.append(MediaItem.serverId == server_id)
        if is_imported is not None:
            conditions.append(MediaItem.isImported == is_imported)
        if search:
            conditions.append(MediaItem.title.ilike(f"%{search}%"))
        if year_from is not None:
            conditions.append(MediaItem.year >= year_from)
        if year_to is not None:
            conditions.append(MediaItem.year <= year_to)
        return conditions

    def _build_tv_conditions(
        self,
        server_id: Optional[int],
        search: Optional[str],
        year_from: Optional[int],
        year_to: Optional[int],
    ) -> List[Any]:
        """构建电视剧查询的 WHERE 条件

        why：电视剧按作品聚合，单集的 isImported 不能作为整部剧的过滤条件，
        故此处不接收 is_imported 参数（与 crud 原实现一致）。
        """
        conditions: List[Any] = [MediaItem.mediaType == "tv_series"]
        if server_id is not None:
            conditions.append(MediaItem.serverId == server_id)
        if search:
            conditions.append(MediaItem.title.ilike(f"%{search}%"))
        if year_from is not None:
            conditions.append(MediaItem.year >= year_from)
        if year_to is not None:
            conditions.append(MediaItem.year <= year_to)
        return conditions

    async def get_media_works(
        self,
        server_id: Optional[int] = None,
        is_imported: Optional[bool] = None,
        media_type: Optional[str] = None,
        search: Optional[str] = None,
        year_from: Optional[int] = None,
        year_to: Optional[int] = None,
        page: int = 1,
        page_size: int = 100,
    ) -> Dict[str, Any]:
        """
        获取作品列表（电影 + 电视剧组），按作品计数

        替代 crud.get_media_works

        实现要点：
        1. 用 UNION ALL 合并电影与电视剧查询，排序分页在数据库层完成
        2. 电视剧的季度信息批量补齐，避免 N+1 查询
        3. 不把全量数据加载进内存
        """
        start_time = time.time()
        MI = MediaItem

        movie_conditions = self._build_movie_conditions(
            server_id, is_imported, search, year_from, year_to
        )
        tv_conditions = self._build_tv_conditions(server_id, search, year_from, year_to)

        # ---------- 步骤 1：计算总数（电影数 + 电视剧组数）----------
        if media_type is None:
            # 一次数据库往返同时得到两类计数
            count_stmt = select(
                func.sum(case((MI.mediaType == "movie", 1), else_=0)).label("movie_count"),
                func.count(
                    func.distinct(
                        case(
                            (MI.mediaType == "tv_series", func.concat(MI.title, "-", MI.serverId)),
                            else_=None,
                        )
                    )
                ).label("tv_count"),
            )
            if server_id is not None:
                count_stmt = count_stmt.where(MI.serverId == server_id)
            if search:
                count_stmt = count_stmt.where(MI.title.ilike(f"%{search}%"))
            if year_from is not None:
                count_stmt = count_stmt.where(MI.year >= year_from)
            if year_to is not None:
                count_stmt = count_stmt.where(MI.year <= year_to)

            count_result = (await self._session.execute(count_stmt)).one()
            total = (count_result.movie_count or 0) + (count_result.tv_count or 0)
        elif media_type == "movie":
            count_stmt = select(func.count(MI.id)).where(*movie_conditions)
            total = (await self._session.execute(count_stmt)).scalar_one()
        else:  # tv_series
            count_stmt = select(
                func.count(func.distinct(func.concat(MI.title, "-", MI.serverId)))
            ).where(*tv_conditions)
            total = (await self._session.execute(count_stmt)).scalar_one()

        if total == 0:
            elapsed = time.time() - start_time
            logger.debug(f"[get_media_works] 无数据, 耗时={elapsed * 1000:.1f}ms")
            return {"total": 0, "list": []}

        # ---------- 步骤 2：UNION ALL 在数据库层分页 ----------
        offset = (page - 1) * page_size
        queries = []

        if media_type is None or media_type == "movie":
            queries.append(
                select(
                    literal("movie").label("type"),
                    MI.id.label("id"),
                    MI.serverId.label("serverId"),
                    MI.mediaId.label("mediaId"),
                    MI.libraryId.label("libraryId"),
                    MI.title.label("title"),
                    MI.year.label("year"),
                    MI.tmdbId.label("tmdbId"),
                    MI.tvdbId.label("tvdbId"),
                    MI.imdbId.label("imdbId"),
                    MI.posterUrl.label("posterUrl"),
                    MI.isImported.label("isImported"),
                    MI.createdAt.label("createdAt"),
                    MI.updatedAt.label("updatedAt"),
                    literal(0).label("seasonCount"),
                    literal(0).label("episodeCount"),
                    case((MI.isImported == True, 1), else_=0).label("importedCount"),
                ).where(*movie_conditions)
            )

        if media_type is None or media_type == "tv_series":
            queries.append(
                select(
                    literal("tv_show").label("type"),
                    literal(0).label("id"),
                    MI.serverId.label("serverId"),
                    literal("").label("mediaId"),
                    literal("").label("libraryId"),
                    MI.title.label("title"),
                    func.min(MI.year).label("year"),
                    func.min(MI.tmdbId).label("tmdbId"),
                    func.min(MI.tvdbId).label("tvdbId"),
                    func.min(MI.imdbId).label("imdbId"),
                    func.min(MI.posterUrl).label("posterUrl"),
                    literal(None).label("isImported"),
                    func.max(MI.createdAt).label("createdAt"),
                    func.max(MI.updatedAt).label("updatedAt"),
                    func.count(func.distinct(MI.season)).label("seasonCount"),
                    func.count(MI.id).label("episodeCount"),
                    func.sum(case((MI.isImported == True, 1), else_=0)).label("importedCount"),
                )
                .where(*tv_conditions)
                .group_by(MI.title, MI.serverId)
            )

        union_query = queries[0] if len(queries) == 1 else union_all(*queries)
        subquery = union_query.subquery("works")
        final_query = (
            select(subquery)
            .order_by(subquery.c.createdAt.desc())
            .offset(offset)
            .limit(page_size)
        )

        rows = (await self._session.execute(final_query)).all()

        paginated_works: List[Dict[str, Any]] = []
        for row in rows:
            work = {
                "type": row.type,
                "serverId": row.serverId,
                "title": row.title,
                "mediaType": row.type,
                "year": row.year,
                "tmdbId": row.tmdbId,
                "tvdbId": row.tvdbId,
                "imdbId": row.imdbId,
                "posterUrl": row.posterUrl,
                "createdAt": row.createdAt,
            }
            if row.type == "movie":
                work["id"] = row.id
                work["mediaId"] = row.mediaId
                work["libraryId"] = row.libraryId
                work["isImported"] = row.isImported
                work["updatedAt"] = row.updatedAt
            else:
                work["seasonCount"] = row.seasonCount
                work["episodeCount"] = row.episodeCount
                work["importedCount"] = int(row.importedCount or 0)
            paginated_works.append(work)

        # ---------- 步骤 3：批量补齐电视剧季度信息，避免 N+1 ----------
        await self._attach_seasons(paginated_works)

        elapsed = time.time() - start_time
        logger.info(
            f"[get_media_works] 查询完成: total={total}, page={page}, "
            f"page_size={page_size}, 耗时={elapsed * 1000:.1f}ms"
        )

        return {"total": total, "list": paginated_works}

    async def _attach_seasons(self, works: List[Dict[str, Any]]) -> None:
        """为本页内的电视剧作品批量附加季度信息（原地修改）"""
        tv_shows = [w for w in works if w["type"] == "tv_show"]
        if not tv_shows:
            return

        MI = MediaItem
        server_ids = {w["serverId"] for w in tv_shows}
        titles = [w["title"] for w in tv_shows]

        seasons_stmt = select(
            MI.title,
            MI.serverId,
            MI.season,
            func.count(MI.id).label("episodeCount"),
            func.sum(case((MI.isImported == True, 1), else_=0)).label("importedCount"),
            func.min(MI.year).label("year"),
            func.min(MI.posterUrl).label("posterUrl"),
        ).where(MI.mediaType == "tv_series", MI.title.in_(titles))

        # 单服务器时用等值条件，比 IN 更利于走索引
        if len(server_ids) == 1:
            seasons_stmt = seasons_stmt.where(MI.serverId == next(iter(server_ids)))
        else:
            seasons_stmt = seasons_stmt.where(MI.serverId.in_(server_ids))

        seasons_stmt = seasons_stmt.group_by(MI.title, MI.serverId, MI.season).order_by(MI.season)

        all_seasons = (await self._session.execute(seasons_stmt)).all()

        seasons_map: Dict[Any, List[Dict[str, Any]]] = {}
        for s in all_seasons:
            seasons_map.setdefault((s.title, s.serverId), []).append(
                {
                    "season": s.season,
                    "episodeCount": s.episodeCount,
                    "importedCount": int(s.importedCount or 0),
                    "year": s.year,
                    "posterUrl": s.posterUrl,
                }
            )

        for work in tv_shows:
            work["seasons"] = seasons_map.get((work["title"], work["serverId"]), [])
