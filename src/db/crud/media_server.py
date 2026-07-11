"""
媒体服务器相关的CRUD操作
包括媒体服务器配置和媒体项管理
"""

import json
import logging
from typing import List, Optional, Dict, Any
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, update, delete, func, or_, and_, literal, union_all, case

from src.core.timezone import get_now
from .. import orm_models

logger = logging.getLogger(__name__)


# --- Media Server Configuration ---

async def get_all_media_servers(session: AsyncSession) -> List[Dict[str, Any]]:
    """获取所有媒体服务器配置"""
    stmt = select(orm_models.MediaServer).order_by(orm_models.MediaServer.createdAt)
    result = await session.execute(stmt)
    servers = result.scalars().all()
    return [
        {
            "id": s.id,
            "name": s.name,
            "providerName": s.providerName,
            "url": s.url,
            "apiToken": s.apiToken,
            "isEnabled": s.isEnabled,
            "selectedLibraries": json.loads(s.selectedLibraries) if s.selectedLibraries else [],
            "filterRules": json.loads(s.filterRules) if s.filterRules else {},
            "createdAt": s.createdAt,
            "updatedAt": s.updatedAt
        }
        for s in servers
    ]


async def get_media_server_by_id(session: AsyncSession, server_id: int) -> Optional[Dict[str, Any]]:
    """根据ID获取媒体服务器配置"""
    server = await session.get(orm_models.MediaServer, server_id)
    if not server:
        return None
    return {
        "id": server.id,
        "name": server.name,
        "providerName": server.providerName,
        "url": server.url,
        "apiToken": server.apiToken,
        "isEnabled": server.isEnabled,
        "selectedLibraries": json.loads(server.selectedLibraries) if server.selectedLibraries else [],
        "filterRules": json.loads(server.filterRules) if server.filterRules else {},
        "createdAt": server.createdAt,
        "updatedAt": server.updatedAt
    }


async def create_media_server(
    session: AsyncSession,
    name: str,
    provider_name: str,
    url: str,
    api_token: str,
    is_enabled: bool = True,
    selected_libraries: Optional[List[str]] = None,
    filter_rules: Optional[Dict[str, Any]] = None
) -> int:
    """创建新的媒体服务器配置"""
    new_server = orm_models.MediaServer(
        name=name,
        providerName=provider_name,
        url=url,
        apiToken=api_token,
        isEnabled=is_enabled,
        selectedLibraries=json.dumps(selected_libraries or []),
        filterRules=json.dumps(filter_rules or {}),
        createdAt=get_now(),
        updatedAt=get_now()
    )
    session.add(new_server)
    await session.flush()
    return new_server.id


async def update_media_server(
    session: AsyncSession,
    server_id: int,
    name: Optional[str] = None,
    provider_name: Optional[str] = None,
    url: Optional[str] = None,
    api_token: Optional[str] = None,
    is_enabled: Optional[bool] = None,
    selected_libraries: Optional[List[str]] = None,
    filter_rules: Optional[Dict[str, Any]] = None
) -> bool:
    """更新媒体服务器配置"""
    server = await session.get(orm_models.MediaServer, server_id)
    if not server:
        return False

    if name is not None:
        server.name = name
    if provider_name is not None:
        server.providerName = provider_name
    if url is not None:
        server.url = url
    if api_token is not None:
        server.apiToken = api_token
    if is_enabled is not None:
        server.isEnabled = is_enabled
    if selected_libraries is not None:
        server.selectedLibraries = json.dumps(selected_libraries)
    if filter_rules is not None:
        server.filterRules = json.dumps(filter_rules)

    server.updatedAt = get_now()
    await session.flush()
    return True


async def delete_media_server(session: AsyncSession, server_id: int) -> bool:
    """删除媒体服务器配置(级联删除关联的媒体项)"""
    server = await session.get(orm_models.MediaServer, server_id)
    if not server:
        return False
    await session.delete(server)
    await session.flush()
    return True


# --- Media Items ---

async def get_media_items(
    session: AsyncSession,
    server_id: Optional[int] = None,
    is_imported: Optional[bool] = None,
    media_type: Optional[str] = None,
    page: int = 1,
    page_size: int = 100
) -> Dict[str, Any]:
    """获取媒体项列表,支持过滤和分页"""
    stmt = select(orm_models.MediaItem)

    if server_id is not None:
        stmt = stmt.where(orm_models.MediaItem.serverId == server_id)
    if is_imported is not None:
        stmt = stmt.where(orm_models.MediaItem.isImported == is_imported)
    if media_type is not None:
        stmt = stmt.where(orm_models.MediaItem.mediaType == media_type)

    # 计算总数
    count_stmt = select(func.count()).select_from(stmt.alias("count_subquery"))
    total = (await session.execute(count_stmt)).scalar_one()

    # 分页查询
    stmt = stmt.order_by(orm_models.MediaItem.createdAt.desc()).offset((page - 1) * page_size).limit(page_size)
    result = await session.execute(stmt)
    items = result.scalars().all()

    return {
        "total": total,
        "list": [
            {
                "id": item.id,
                "serverId": item.serverId,
                "mediaId": item.mediaId,
                "libraryId": item.libraryId,
                "title": item.title,
                "mediaType": item.mediaType,
                "season": item.season,
                "episode": item.episode,
                "year": item.year,
                "tmdbId": item.tmdbId,
                "tvdbId": item.tvdbId,
                "imdbId": item.imdbId,
                "posterUrl": item.posterUrl,
                "isImported": item.isImported,
                "createdAt": item.createdAt,
                "updatedAt": item.updatedAt
            }
            for item in items
        ]
    }


async def get_media_works(
    session: AsyncSession,
    server_id: Optional[int] = None,
    is_imported: Optional[bool] = None,
    media_type: Optional[str] = None,
    search: Optional[str] = None,
    year_from: Optional[int] = None,
    year_to: Optional[int] = None,
    page: int = 1,
    page_size: int = 100
) -> Dict[str, Any]:
    """
    【优化版 v3 - 数据库分页】获取作品列表(电影+电视剧组),按作品计数

    优化要点:
    1. 使用 UNION ALL 合并电影和电视剧查询，在数据库层面完成排序和分页
    2. 电视剧直接返回季度信息（seasons 字段），避免 N+1 查询
    3. 避免加载全部数据到内存
    """
    import time
    start_time = time.time()

    MI = orm_models.MediaItem

    # ============================================================
    # 构建通用的 WHERE 条件
    # ============================================================
    def build_movie_conditions():
        conditions = [MI.mediaType == 'movie']
        if server_id is not None:
            conditions.append(MI.serverId == server_id)
        if is_imported is not None:
            conditions.append(MI.isImported == is_imported)
        if search:
            conditions.append(MI.title.ilike(f'%{search}%'))
        if year_from is not None:
            conditions.append(MI.year >= year_from)
        if year_to is not None:
            conditions.append(MI.year <= year_to)
        return conditions

    def build_tv_conditions():
        conditions = [MI.mediaType == 'tv_series']
        if server_id is not None:
            conditions.append(MI.serverId == server_id)
        if search:
            conditions.append(MI.title.ilike(f'%{search}%'))
        if year_from is not None:
            conditions.append(MI.year >= year_from)
        if year_to is not None:
            conditions.append(MI.year <= year_to)
        return conditions

    # ============================================================
    # 步骤 1: 计算总数（电影数 + 电视剧组数）- 合并为单次查询
    # ============================================================
    if media_type is None:
        # 同时查询电影数和电视剧组数（一次数据库往返）
        count_stmt = select(
            func.sum(case((MI.mediaType == 'movie', 1), else_=0)).label('movie_count'),
            func.count(func.distinct(case(
                (MI.mediaType == 'tv_series', func.concat(MI.title, '-', MI.serverId)),
                else_=None
            ))).label('tv_count')
        )
        # 添加通用条件
        if server_id is not None:
            count_stmt = count_stmt.where(MI.serverId == server_id)
        if search:
            count_stmt = count_stmt.where(MI.title.ilike(f'%{search}%'))
        if year_from is not None:
            count_stmt = count_stmt.where(MI.year >= year_from)
        if year_to is not None:
            count_stmt = count_stmt.where(MI.year <= year_to)

        count_result = (await session.execute(count_stmt)).one()
        movie_count = count_result.movie_count or 0
        tv_count = count_result.tv_count or 0
        total = movie_count + tv_count
    elif media_type == 'movie':
        movie_count_stmt = select(func.count(MI.id)).where(*build_movie_conditions())
        movie_count = (await session.execute(movie_count_stmt)).scalar_one()
        tv_count = 0
        total = movie_count
    else:  # tv_series
        tv_count_stmt = select(func.count(func.distinct(
            func.concat(MI.title, '-', MI.serverId)
        ))).where(*build_tv_conditions())
        tv_count = (await session.execute(tv_count_stmt)).scalar_one()
        movie_count = 0
        total = tv_count

    # 如果没有数据，直接返回
    if total == 0:
        elapsed = time.time() - start_time
        logger.debug(f"[get_media_works] 无数据, 耗时={elapsed*1000:.1f}ms")
        return {"total": 0, "list": []}

    # ============================================================
    # 步骤 2: 使用 UNION ALL 在数据库层面分页
    # ============================================================
    offset = (page - 1) * page_size
    queries = []

    # 电影查询（统一字段结构）
    if media_type is None or media_type == 'movie':
        movie_query = select(
            literal('movie').label('type'),
            MI.id.label('id'),
            MI.serverId.label('serverId'),
            MI.mediaId.label('mediaId'),
            MI.libraryId.label('libraryId'),
            MI.title.label('title'),
            MI.year.label('year'),
            MI.tmdbId.label('tmdbId'),
            MI.tvdbId.label('tvdbId'),
            MI.imdbId.label('imdbId'),
            MI.posterUrl.label('posterUrl'),
            MI.isImported.label('isImported'),
            MI.createdAt.label('createdAt'),
            MI.updatedAt.label('updatedAt'),
            literal(0).label('seasonCount'),
            literal(0).label('episodeCount'),
            case((MI.isImported == True, 1), else_=0).label('importedCount')
        ).where(*build_movie_conditions())
        queries.append(movie_query)

    # 电视剧组查询（统一字段结构）
    if media_type is None or media_type == 'tv_series':
        tv_query = select(
            literal('tv_show').label('type'),
            literal(0).label('id'),
            MI.serverId.label('serverId'),
            literal('').label('mediaId'),
            literal('').label('libraryId'),
            MI.title.label('title'),
            func.min(MI.year).label('year'),
            func.min(MI.tmdbId).label('tmdbId'),
            func.min(MI.tvdbId).label('tvdbId'),
            func.min(MI.imdbId).label('imdbId'),
            func.min(MI.posterUrl).label('posterUrl'),
            literal(None).label('isImported'),
            func.max(MI.createdAt).label('createdAt'),
            func.max(MI.updatedAt).label('updatedAt'),
            func.count(func.distinct(MI.season)).label('seasonCount'),
            func.count(MI.id).label('episodeCount'),
            func.sum(case((MI.isImported == True, 1), else_=0)).label('importedCount')
        ).where(*build_tv_conditions()).group_by(MI.title, MI.serverId)
        queries.append(tv_query)

    # 合并查询并分页
    if len(queries) == 1:
        union_query = queries[0]
    else:
        union_query = union_all(*queries)

    # 包装为子查询，然后排序分页
    subquery = union_query.subquery('works')
    final_query = select(subquery).order_by(
        subquery.c.createdAt.desc()
    ).offset(offset).limit(page_size)

    result = await session.execute(final_query)
    rows = result.all()

    # 转换为字典列表
    paginated_works = []
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
        if row.type == 'movie':
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

    # ============================================================
    # 步骤 3: 【关键优化】为电视剧批量获取季度信息，避免 N+1 查询
    # ============================================================
    tv_shows_in_page = [w for w in paginated_works if w['type'] == 'tv_show']

    if tv_shows_in_page:
        # 如果所有电视剧都属于同一个 serverId，使用更高效的 IN 查询
        server_ids = set(w['serverId'] for w in tv_shows_in_page)
        titles = [w['title'] for w in tv_shows_in_page]

        # 一次性查询所有电视剧的季度信息（含导入状态统计）
        seasons_stmt = select(
            MI.title,
            MI.serverId,
            MI.season,
            func.count(MI.id).label('episodeCount'),
            func.sum(case((MI.isImported == True, 1), else_=0)).label('importedCount'),
            func.min(MI.year).label('year'),
            func.min(MI.posterUrl).label('posterUrl')
        ).where(
            MI.mediaType == 'tv_series',
            MI.title.in_(titles)
        )

        # 如果只有一个 serverId，直接用等于条件（更高效）
        if len(server_ids) == 1:
            seasons_stmt = seasons_stmt.where(MI.serverId == list(server_ids)[0])
        else:
            seasons_stmt = seasons_stmt.where(MI.serverId.in_(server_ids))

        seasons_stmt = seasons_stmt.group_by(
            MI.title,
            MI.serverId,
            MI.season
        ).order_by(
            MI.season
        )

        seasons_result = await session.execute(seasons_stmt)
        all_seasons = seasons_result.all()

        # 按 (title, serverId) 分组季度信息
        seasons_map = {}
        for s in all_seasons:
            key = (s.title, s.serverId)
            if key not in seasons_map:
                seasons_map[key] = []
            seasons_map[key].append({
                "season": s.season,
                "episodeCount": s.episodeCount,
                "importedCount": int(s.importedCount or 0),
                "year": s.year,
                "posterUrl": s.posterUrl
            })

        # 将季度信息附加到电视剧作品上
        for work in paginated_works:
            if work['type'] == 'tv_show':
                key = (work['title'], work['serverId'])
                work['seasons'] = seasons_map.get(key, [])

    # 性能日志
    elapsed = time.time() - start_time
    logger.info(f"[get_media_works] 查询完成: total={total}, page={page}, page_size={page_size}, 耗时={elapsed*1000:.1f}ms")

    return {
        "total": total,
        "list": paginated_works
    }


async def get_show_seasons(
    session: AsyncSession,
    server_id: int,
    title: str
) -> List[Dict[str, Any]]:
    """获取某部剧集的所有季度信息"""
    stmt = select(
        orm_models.MediaItem.season,
        func.count(orm_models.MediaItem.id).label('episodeCount'),
        func.min(orm_models.MediaItem.year).label('year'),
        func.min(orm_models.MediaItem.posterUrl).label('posterUrl')
    ).where(
        orm_models.MediaItem.serverId == server_id,
        orm_models.MediaItem.title == title,
        orm_models.MediaItem.mediaType == 'tv_series'
    ).group_by(orm_models.MediaItem.season).order_by(orm_models.MediaItem.season)

    result = await session.execute(stmt)
    seasons = result.all()

    return [
        {
            "season": s.season,
            "episodeCount": s.episodeCount,
            "year": s.year,
            "posterUrl": s.posterUrl
        }
        for s in seasons
    ]


async def get_season_episodes(
    session: AsyncSession,
    server_id: int,
    title: str,
    season: int,
    page: int = 1,
    page_size: int = 100
) -> Dict[str, Any]:
    """获取某一季的所有集"""
    stmt = select(orm_models.MediaItem).where(
        orm_models.MediaItem.serverId == server_id,
        orm_models.MediaItem.title == title,
        orm_models.MediaItem.season == season,
        orm_models.MediaItem.mediaType == 'tv_series'
    )

    # 计算总数
    count_stmt = select(func.count()).select_from(stmt.alias("count_subquery"))
    total = (await session.execute(count_stmt)).scalar_one()

    # 分页查询
    stmt = stmt.order_by(orm_models.MediaItem.episode).offset((page - 1) * page_size).limit(page_size)
    result = await session.execute(stmt)
    episodes = result.scalars().all()

    return {
        "total": total,
        "list": [
            {
                "id": ep.id,
                "serverId": ep.serverId,
                "mediaId": ep.mediaId,
                "libraryId": ep.libraryId,
                "title": ep.title,
                "mediaType": ep.mediaType,
                "season": ep.season,
                "episode": ep.episode,
                "year": ep.year,
                "tmdbId": ep.tmdbId,
                "tvdbId": ep.tvdbId,
                "imdbId": ep.imdbId,
                "posterUrl": ep.posterUrl,
                "isImported": ep.isImported,
                "createdAt": ep.createdAt,
                "updatedAt": ep.updatedAt
            }
            for ep in episodes
        ]
    }


async def create_media_item(
    session: AsyncSession,
    server_id: int,
    media_id: str,
    library_id: Optional[str],
    series_id: Optional[str],
    season_id: Optional[str],
    episode_id: Optional[str],
    title: str,
    media_type: str,
    season: Optional[int] = None,
    episode: Optional[int] = None,
    year: Optional[int] = None,
    tmdb_id: Optional[str] = None,
    tvdb_id: Optional[str] = None,
    imdb_id: Optional[str] = None,
    poster_url: Optional[str] = None
) -> int:
    """创建媒体项(如果已存在则更新)"""
    # 检查是否已存在
    stmt = select(orm_models.MediaItem).where(
        orm_models.MediaItem.serverId == server_id,
        orm_models.MediaItem.mediaId == media_id
    )
    result = await session.execute(stmt)
    existing_item = result.scalar_one_or_none()

    if existing_item:
        # 更新现有项
        existing_item.libraryId = library_id
        existing_item.seriesId = series_id
        existing_item.seasonId = season_id
        existing_item.episodeId = episode_id
        existing_item.title = title
        existing_item.mediaType = media_type
        existing_item.season = season
        existing_item.episode = episode
        existing_item.year = year
        existing_item.tmdbId = tmdb_id
        existing_item.tvdbId = tvdb_id
        existing_item.imdbId = imdb_id
        existing_item.posterUrl = poster_url
        existing_item.updatedAt = get_now()
        await session.flush()
        return existing_item.id
    else:
        # 创建新项
        new_item = orm_models.MediaItem(
            serverId=server_id,
            mediaId=media_id,
            libraryId=library_id,
            seriesId=series_id,
            seasonId=season_id,
            episodeId=episode_id,
            title=title,
            mediaType=media_type,
            season=season,
            episode=episode,
            year=year,
            tmdbId=tmdb_id,
            tvdbId=tvdb_id,
            imdbId=imdb_id,
            posterUrl=poster_url,
            isImported=False,
            createdAt=get_now(),
            updatedAt=get_now()
        )
        session.add(new_item)
        await session.flush()
        return new_item.id


async def update_media_item(
    session: AsyncSession,
    item_id: int,
    title: Optional[str] = None,
    media_type: Optional[str] = None,
    season: Optional[int] = None,
    episode: Optional[int] = None,
    year: Optional[int] = None,
    tmdb_id: Optional[str] = None,
    tvdb_id: Optional[str] = None,
    imdb_id: Optional[str] = None,
    poster_url: Optional[str] = None
) -> bool:
    """更新媒体项"""
    item = await session.get(orm_models.MediaItem, item_id)
    if not item:
        return False

    if title is not None:
        item.title = title
    if media_type is not None:
        item.mediaType = media_type
    if season is not None:
        item.season = season
    if episode is not None:
        item.episode = episode
    if year is not None:
        item.year = year
    if tmdb_id is not None:
        item.tmdbId = tmdb_id
    if tvdb_id is not None:
        item.tvdbId = tvdb_id
    if imdb_id is not None:
        item.imdbId = imdb_id
    if poster_url is not None:
        item.posterUrl = poster_url

    item.updatedAt = get_now()
    await session.flush()
    return True


async def delete_media_item(session: AsyncSession, item_id: int) -> bool:
    """删除单个媒体项"""
    item = await session.get(orm_models.MediaItem, item_id)
    if not item:
        return False
    await session.delete(item)
    await session.flush()
    return True


async def delete_media_items_batch(session: AsyncSession, item_ids: List[int]) -> int:
    """批量删除媒体项（分批操作，避免 asyncpg 的 32767 参数限制）"""
    if not item_ids:
        return 0
    PG_BATCH = 30000
    total_deleted = 0
    for i in range(0, len(item_ids), PG_BATCH):
        batch = item_ids[i:i + PG_BATCH]
        stmt = delete(orm_models.MediaItem).where(orm_models.MediaItem.id.in_(batch))
        result = await session.execute(stmt)
        total_deleted += result.rowcount
    await session.flush()
    return total_deleted


async def get_episode_ids_by_show(session: AsyncSession, server_id: int, title: str) -> List[int]:
    """根据剧集名称获取所有集的ID"""
    stmt = select(orm_models.MediaItem.id).where(
        orm_models.MediaItem.serverId == server_id,
        orm_models.MediaItem.title == title,
        orm_models.MediaItem.mediaType == 'tv_series'
    )
    result = await session.execute(stmt)
    return [row[0] for row in result.all()]


async def get_episode_ids_by_season(session: AsyncSession, server_id: int, title: str, season: int) -> List[int]:
    """根据剧集名称和季度获取所有集的ID"""
    stmt = select(orm_models.MediaItem.id).where(
        orm_models.MediaItem.serverId == server_id,
        orm_models.MediaItem.title == title,
        orm_models.MediaItem.season == season,
        orm_models.MediaItem.mediaType == 'tv_series'
    )
    result = await session.execute(stmt)
    return [row[0] for row in result.all()]


async def mark_media_items_imported(session: AsyncSession, item_ids: List[int]) -> int:
    """标记媒体项为已导入（分批操作，避免 asyncpg 的 32767 参数限制）"""
    if not item_ids:
        return 0
    PG_BATCH = 30000
    total_updated = 0
    for i in range(0, len(item_ids), PG_BATCH):
        batch = item_ids[i:i + PG_BATCH]
        stmt = update(orm_models.MediaItem).where(
            orm_models.MediaItem.id.in_(batch)
        ).values(isImported=True)
        result = await session.execute(stmt)
        total_updated += result.rowcount
    await session.flush()
    return total_updated


def _media_item_has_danmaku_exists():
    """构造一个关联子查询：判断某个 MediaItem 对应的作品在弹幕库中是否已有真实弹幕。

    why：原逻辑用 MediaItem.isImported==False 判定"未导入"，但 isImported 只表示
    "曾提交过导入任务"，与是否真正抓到弹幕无关（见 issue #441）。导入失败的项
    isImported 也被置为 True，导致"一键导入/定时任务"永远跳过这些失败项，无法重试。
    改为以弹幕库真实状态判定：只要弹幕库中没有对应且含弹幕的分集，就视为未导入，允许重新导入。

    匹配规则（A2 方案，已知局限：以标题+季度为准，需与库内 Anime 命名一致）：
      - 标题去空格后精确匹配 Anime.title
      - 电视剧额外要求季度一致；电影/其他不比季度
      - 仅统计 commentCount > 0 的分集，避免"建了空壳条目但无弹幕"被误判为已导入
    """
    A = orm_models.Anime
    S = orm_models.AnimeSource
    E = orm_models.Episode
    MI = orm_models.MediaItem
    return (
        select(literal(1))
        .select_from(A)
        .join(S, S.animeId == A.id)
        .join(E, E.sourceId == S.id)
        .where(
            func.replace(A.title, ' ', '') == func.replace(MI.title, ' ', ''),
            or_(MI.mediaType != 'tv_series', A.season == MI.season),
            E.commentCount > 0,
        )
        .correlate(MI)
        .exists()
    )


async def get_unimported_item_ids(
    session: AsyncSession,
    server_id: int,
    media_type: Optional[str] = None
) -> List[int]:
    """获取指定服务器下所有"弹幕库中尚无对应弹幕"的媒体项ID（可重新导入）。

    修复 issue #441：不再依赖 isImported 标志（该标志失败也会置 True 导致无法重试），
    改为以弹幕库真实状态判定——弹幕库中无对应弹幕的媒体项都视为未导入。
    """
    stmt = select(orm_models.MediaItem.id).where(
        orm_models.MediaItem.serverId == server_id,
        ~_media_item_has_danmaku_exists(),  # 弹幕库中无对应弹幕才算"未导入"
    )
    if media_type is not None:
        stmt = stmt.where(orm_models.MediaItem.mediaType == media_type)
    result = await session.execute(stmt)
    return [row[0] for row in result.all()]


async def get_unimported_count(
    session: AsyncSession,
    server_id: int,
    media_type: Optional[str] = None
) -> int:
    """获取指定服务器下"弹幕库中尚无对应弹幕"的媒体项数量（与 get_unimported_item_ids 判定一致）。"""
    stmt = select(func.count(orm_models.MediaItem.id)).where(
        orm_models.MediaItem.serverId == server_id,
        ~_media_item_has_danmaku_exists(),  # 与 get_unimported_item_ids 保持同一判定
    )
    if media_type is not None:
        stmt = stmt.where(orm_models.MediaItem.mediaType == media_type)
    result = await session.execute(stmt)
    return result.scalar_one()


async def clear_media_items_by_server(session: AsyncSession, server_id: int) -> int:
    """清空指定服务器的所有媒体项"""
    stmt = delete(orm_models.MediaItem).where(orm_models.MediaItem.serverId == server_id)
    result = await session.execute(stmt)
    await session.flush()
    return result.rowcount
