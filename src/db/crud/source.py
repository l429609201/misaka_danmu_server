"""
Source相关的CRUD操作
"""

import logging
from typing import Optional, Dict, Any, List
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, distinct, case, or_, and_, update, delete
from sqlalchemy.orm import selectinload
from datetime import datetime, timedelta

from ..orm_models import Anime, AnimeSource, Episode, Scraper, AnimeMetadata
from .. import models
from src.core.timezone import get_now

logger = logging.getLogger(__name__)


async def check_source_exists_by_media_id(session: AsyncSession, provider_name: str, media_id: str, season: Optional[int] = None) -> bool:
    """检查具有给定提供商和媒体ID的源是否已存在。传入 season 时会 JOIN anime 表按季度精确匹配。"""
    stmt = select(AnimeSource.id).where(
        AnimeSource.providerName == provider_name,
        AnimeSource.mediaId == media_id
    )
    if season is not None:
        stmt = stmt.join(Anime, AnimeSource.animeId == Anime.id).where(Anime.season == season)
    stmt = stmt.limit(1)
    result = await session.execute(stmt)
    return result.scalar_one_or_none() is not None


async def get_anime_id_by_source_media_id(session: AsyncSession, provider_name: str, media_id: str, season: Optional[int] = None) -> Optional[int]:
    """通过数据源的provider和media_id获取对应的anime_id。传入 season 时会 JOIN anime 表按季度精确匹配。"""
    stmt = select(AnimeSource.animeId).where(
        AnimeSource.providerName == provider_name,
        AnimeSource.mediaId == media_id
    )
    if season is not None:
        stmt = stmt.join(Anime, AnimeSource.animeId == Anime.id).where(Anime.season == season)
    stmt = stmt.limit(1)
    result = await session.execute(stmt)
    return result.scalar_one_or_none()


async def link_source_to_anime(session: AsyncSession, anime_id: int, provider_name: str, media_id: str) -> int:
    """将一个外部数据源关联到一个番剧条目，如果关联已存在则直接返回其ID。"""
    # 修正：在链接源之前，确保该提供商在 scrapers 表中存在。
    # 这修复了当创建 'custom' 等非文件型源时，因 scrapers 表中缺少对应条目而导致后续查询失败的问题。
    # 这是比 LEFT JOIN 更根本的解决方案。
    scraper_entry = await session.get(Scraper, provider_name)
    if not scraper_entry:
        logger.info(f"提供商 '{provider_name}' 在 scrapers 表中不存在，将为其创建新条目。")
        max_order_stmt = select(func.max(Scraper.displayOrder))
        max_order = (await session.execute(max_order_stmt)).scalar_one_or_none() or 0
        new_scraper_entry = Scraper(
            providerName=provider_name,
            displayOrder=max_order + 1,
            isEnabled=True, # 自定义源默认启用
            useProxy=False
        )
        session.add(new_scraper_entry)
        await session.flush() # Flush to make it available within the transaction
    stmt = select(AnimeSource.id).where(
        AnimeSource.animeId == anime_id,
        AnimeSource.providerName == provider_name,
        AnimeSource.mediaId == media_id
    )
    result = await session.execute(stmt)
    existing_id = result.scalar_one_or_none()
    if existing_id:
        return existing_id

    # 如果源不存在，则创建一个新的，并为其分配一个持久的、唯一的顺序号
    # 查找此作品当前最大的 sourceOrder
    max_order_stmt = select(func.max(AnimeSource.sourceOrder)).where(AnimeSource.animeId == anime_id)
    max_order_result = await session.execute(max_order_stmt)
    current_max_order = max_order_result.scalar_one_or_none() or 0

    new_source = AnimeSource(
        animeId=anime_id,
        providerName=provider_name,
        mediaId=media_id,
        sourceOrder=current_max_order + 1,
        createdAt=get_now()
    )
    session.add(new_source)
    await session.flush() # 使用 flush 获取新ID，但不提交事务
    return new_source.id


async def update_source_media_id(session: AsyncSession, source_id: int, new_media_id: str):
    """更新指定源的 mediaId。"""
    stmt = update(AnimeSource).where(AnimeSource.id == source_id).values(mediaId=new_media_id)
    await session.execute(stmt)
    # 注意：这里不 commit，由调用方（任务）来决定何时提交事务


async def get_anime_source_info(session: AsyncSession, source_id: int) -> Optional[Dict[str, Any]]:
    stmt = (
        select(
            AnimeSource.id.label("sourceId"), AnimeSource.animeId.label("animeId"), AnimeSource.providerName.label("providerName"),
            AnimeSource.mediaId.label("mediaId"), AnimeSource.sourceOrder.label("sourceOrder"), Anime.year,
            Anime.title, Anime.type, Anime.season, Anime.imageUrl.label("imageUrl"),
            AnimeMetadata.tmdbId.label("tmdbId"), AnimeMetadata.bangumiId.label("bangumiId"),
            AnimeMetadata.airWeekday.label("airWeekday"),
        )
        .join(Anime, AnimeSource.animeId == Anime.id)
        .join(AnimeMetadata, Anime.id == AnimeMetadata.animeId, isouter=True)
        .where(AnimeSource.id == source_id)
    )
    result = await session.execute(stmt)
    row = result.mappings().first()
    return dict(row) if row else None


async def get_anime_sources(session: AsyncSession, anime_id: int) -> List[Dict[str, Any]]:
    """获取指定作品的所有数据源，并高效地计算每个源的分集数。"""
    # 步骤1: 创建一个子查询，用于高效地计算每个 source_id 对应的分集数量。
    # 这种方式比在主查询中直接 JOIN 和 COUNT 更快，尤其是在 episode 表很大的情况下。
    episode_count_subquery = (
        select(
            Episode.sourceId,
            func.count(Episode.id).label("episode_count")
        )
        .group_by(Episode.sourceId)
        .subquery()
    )

    # 步骤2: 构建主查询，LEFT JOIN 上面的子查询来获取分集数。
    stmt = (
        select(
            AnimeSource.id.label("sourceId"),
            AnimeSource.providerName.label("providerName"),
            AnimeSource.mediaId.label("mediaId"),
            AnimeSource.sourceOrder.label("sourceOrder"),
            AnimeSource.isFavorited.label("isFavorited"),
            AnimeSource.incrementalRefreshEnabled.label("incrementalRefreshEnabled"),
            AnimeSource.isFinished.label("isFinished"),
            AnimeSource.createdAt.label("createdAt"),
            # 使用 coalesce 确保即使没有分集的源也返回 0 而不是 NULL
            func.coalesce(episode_count_subquery.c.episode_count, 0).label("episodeCount")
        )
        .outerjoin(episode_count_subquery, AnimeSource.id == episode_count_subquery.c.sourceId)
        .where(AnimeSource.animeId == anime_id)
        .order_by(AnimeSource.createdAt)
    )
    result = await session.execute(stmt)
    return [dict(row) for row in result.mappings()]


async def get_episodes_for_source(session: AsyncSession, source_id: int, page: int = 1, page_size: int = 5000) -> Dict[str, Any]:
    """获取指定源的分集列表，支持分页。"""
    # 首先，获取总的分集数量，用于前端分页控件
    count_stmt = select(func.count(Episode.id)).where(Episode.sourceId == source_id)
    total_count = (await session.execute(count_stmt)).scalar_one()

    # 然后，根据分页参数查询特定页的数据
    # 修正：确保返回一个包含完整信息的字典列表，以修复UI中的TypeError
    offset = (page - 1) * page_size
    stmt = (
        select(
            Episode.id.label("episodeId"),
            Episode.title,
            Episode.episodeIndex.label("episodeIndex"),
            Episode.sourceUrl.label("sourceUrl"),
            Episode.fetchedAt.label("fetchedAt"),
            Episode.commentCount.label("commentCount"),
            Episode.danmakuFilePath.label("danmakuFilePath")
        )
        .where(Episode.sourceId == source_id)
        .order_by(Episode.episodeIndex).offset(offset).limit(page_size)
    )
    result = await session.execute(stmt)
    episodes = [dict(row) for row in result.mappings()]

    return {"total": total_count, "episodes": episodes}


async def clear_source_data(session: AsyncSession, source_id: int):
    """Deletes all episodes and their danmaku files for a given source."""
    source = await session.get(AnimeSource, source_id)
    if not source:
        return

    # 修正：逐个删除文件，而不是删除一个不存在的目录，以提高健壮性
    episodes_to_delete_res = await session.execute(
        select(Episode.danmakuFilePath).where(Episode.sourceId == source_id)
    )
    for file_path_str in episodes_to_delete_res.scalars().all():
        if fs_path := _get_fs_path_from_web_path(file_path_str):
            if fs_path.is_file():
                fs_path.unlink(missing_ok=True)

    await session.execute(delete(Episode).where(Episode.sourceId == source_id))
    await session.commit()


async def delete_anime_source(session: AsyncSession, source_id: int) -> bool:
    """删除一个数据源及其所有分集的弹幕文件，并清理空目录。"""
    from src.tasks.delete import delete_danmaku_files_batch

    source = await session.get(AnimeSource, source_id)
    if source:
        # 收集所有弹幕文件路径
        episodes_to_delete_res = await session.execute(
            select(Episode.danmakuFilePath).where(Episode.sourceId == source_id)
        )
        file_paths = [fp for fp in episodes_to_delete_res.scalars().all() if fp]

        # 批量删除文件并清理空目录
        if file_paths:
            delete_danmaku_files_batch(file_paths)

        await session.delete(source)
        await session.commit()
        return True
    return False


async def toggle_source_favorite_status(session: AsyncSession, source_id: int) -> Optional[bool]:
    """
    Toggles the favorite status of a source.
    Returns the new favorite status (True/False) on success, or None if not found.
    """
    source = await session.get(AnimeSource, source_id)
    if not source:
        return None

    # Toggle the target source
    source.isFavorited = not source.isFavorited

    # If it was favorited, unfavorite all others for the same anime
    if source.isFavorited:
        stmt = (
            update(AnimeSource)
            .where(AnimeSource.animeId == source.animeId, AnimeSource.id != source_id)
            .values(isFavorited=False)
        )
        await session.execute(stmt)

    await session.commit()
    return source.isFavorited


async def toggle_source_incremental_refresh(session: AsyncSession, source_id: int) -> Optional[bool]:
    """
    切换源的追更状态。
    同一番剧下只能有一个源开启追更（与标记规则相同）。
    返回新的追更状态（True/False），如果源不存在则返回 None。
    """
    source = await session.get(AnimeSource, source_id)
    if not source:
        return None

    # 切换当前源的追更状态
    source.incrementalRefreshEnabled = not source.incrementalRefreshEnabled

    # 如果开启了追更，则关闭同一番剧下其他源的追更，并解除当前源完结标记
    if source.incrementalRefreshEnabled:
        source.isFinished = False
        stmt = (
            update(AnimeSource)
            .where(AnimeSource.animeId == source.animeId, AnimeSource.id != source_id)
            .values(incrementalRefreshEnabled=False)
        )
        await session.execute(stmt)

    await session.commit()
    return source.incrementalRefreshEnabled


async def increment_incremental_refresh_failures(session: AsyncSession, source_id: int) -> int:
    source = await session.get(AnimeSource, source_id)
    if not source:
        return 0
    source.incrementalRefreshFailures += 1
    await session.commit()
    return source.incrementalRefreshFailures


async def reset_incremental_refresh_failures(session: AsyncSession, source_id: int):
    await session.execute(update(AnimeSource).where(AnimeSource.id == source_id).values(incrementalRefreshFailures=0))
    await session.commit()


async def disable_incremental_refresh(session: AsyncSession, source_id: int) -> bool:
    result = await session.execute(update(AnimeSource).where(AnimeSource.id == source_id).values(incrementalRefreshEnabled=False))
    await session.commit()
    return result.rowcount > 0

# --- OAuth State Management ---


async def get_sources_with_incremental_refresh_enabled(session: AsyncSession) -> List[int]:
    stmt = select(AnimeSource.id).where(AnimeSource.incrementalRefreshEnabled == True)
    result = await session.execute(stmt)
    return result.scalars().all()



async def get_calendar_sources(session: AsyncSession) -> List[Dict[str, Any]]:
    """获取所有追更中且未完结的源，连同日程信息，用于日历视图。"""
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
    result = await session.execute(stmt)
    rows = result.mappings().all()
    return [dict(r) for r in rows]


async def update_air_schedule(
    session: AsyncSession,
    anime_id: int,
    air_weekday: Optional[int],
    air_time: Optional[str],
    commit: bool = True,
):
    """更新番剧的播出日程信息；批量调用方可延迟提交。"""
    stmt = select(AnimeMetadata).where(AnimeMetadata.animeId == anime_id)
    meta = (await session.execute(stmt)).scalar_one_or_none()
    if meta:
        meta.airWeekday = air_weekday
        meta.airTime = air_time
        if commit:
            await session.commit()


async def update_metadata_ids(session: AsyncSession, anime_id: int, commit: bool = True, **kwargs):
    """更新番剧的元数据 ID（bangumiId, traktId, tmdbId 等）和日程信息。

    用法: await update_metadata_ids(session, anime_id, bangumiId="12345", airWeekday=6)
    批量调用方传 ``commit=False``，由业务层统一提交。
    """
    stmt = select(AnimeMetadata).where(AnimeMetadata.animeId == anime_id)
    meta = (await session.execute(stmt)).scalar_one_or_none()
    if not meta:
        # 如果没有 metadata 记录，创建一个
        meta = AnimeMetadata(animeId=anime_id)
        session.add(meta)
    allowed_fields = {"bangumiId", "traktId", "tmdbId", "imdbId", "tvdbId", "doubanId", "airWeekday", "airTime"}
    for key, value in kwargs.items():
        if key in allowed_fields and value is not None:
            setattr(meta, key, value)
    if commit:
        await session.commit()

# --- Scheduled Tasks ---


async def _assign_source_order_if_missing(session: AsyncSession, anime_id: int, source_id: int) -> int:
    """一个辅助函数，用于为没有 sourceOrder 的旧记录分配一个新的、持久的序号。"""
    async with session.begin_nested(): # 使用嵌套事务确保操作的原子性
        max_order_stmt = select(func.max(AnimeSource.sourceOrder)).where(AnimeSource.animeId == anime_id)
        max_order_res = await session.execute(max_order_stmt)
        current_max_order = max_order_res.scalar_one_or_none() or 0
        new_order = current_max_order + 1

        await session.execute(update(AnimeSource).where(AnimeSource.id == source_id).values(sourceOrder=new_order))
        return new_order


async def get_incremental_refresh_sources_grouped(
    session: AsyncSession,
    page: int = 1,
    page_size: int = 20,
    keyword: str = "",
    favorite_filter: str = "all",   # all / favorited / unfavorited
    refresh_filter: str = "all",    # all / enabled / disabled
    type_filter: str = "all",       # all / movie / tv_series
    finished_filter: str = "all",   # all / finished / unfinished
    sort_by: str = "created",       # created / title
    sort_order: str = "desc",       # asc / desc
) -> Dict[str, Any]:
    """
    获取所有源（包括启用和未启用追更的），按番剧分组返回，支持分页和过滤。

    参数:
        page: 页码（从1开始）
        page_size: 每页番剧数量
        keyword: 搜索关键词（匹配番剧名称或源名称）
        favorite_filter: 标记过滤 (all/favorited/unfavorited)
        refresh_filter: 追更过滤 (all/enabled/disabled)
        type_filter: 类型过滤 (all/movie/tv_series)
        finished_filter: 完结过滤 (all/finished/unfinished)
        sort_by: 排序字段 (created=按入库时间降序, title=按标题升序)

    返回格式: {
        "total": 100,  # 总番剧数
        "totalSources": 118,  # 总源数
        "refreshEnabled": 5,  # 追更中数量
        "favorited": 3,  # 已标记数量
        "list": [
            {
                "animeId": 1,
                "animeTitle": "葬送的芙莉莲",
                "sources": [...]
            },
            ...
        ]
    }
    """
    # 子查询：统计每个源的分集数量
    episode_count_subquery = (
        select(
            Episode.sourceId,
            func.count(Episode.id).label("episode_count")
        )
        .group_by(Episode.sourceId)
        .subquery()
    )

    # 基础查询：获取所有源及其关联的番剧信息
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
            func.coalesce(episode_count_subquery.c.episode_count, 0).label("episodeCount")
        )
        .join(Anime, AnimeSource.animeId == Anime.id)
        .outerjoin(episode_count_subquery, AnimeSource.id == episode_count_subquery.c.sourceId)
    )

    # 应用过滤条件
    conditions = []
    if keyword:
        keyword_pattern = f"%{keyword}%"
        conditions.append(
            or_(
                Anime.title.ilike(keyword_pattern),
                AnimeSource.providerName.ilike(keyword_pattern)
            )
        )
    if favorite_filter == "favorited":
        conditions.append(AnimeSource.isFavorited == True)
    elif favorite_filter == "unfavorited":
        conditions.append(AnimeSource.isFavorited == False)
    if refresh_filter == "enabled":
        conditions.append(AnimeSource.incrementalRefreshEnabled == True)
    elif refresh_filter == "disabled":
        conditions.append(AnimeSource.incrementalRefreshEnabled == False)
    if type_filter == "movie":
        conditions.append(Anime.type == "movie")
    elif type_filter == "tv_series":
        conditions.append(Anime.type == "tv_series")
    if finished_filter == "finished":
        conditions.append(AnimeSource.isFinished == True)
    elif finished_filter == "unfinished":
        conditions.append(AnimeSource.isFinished == False)

    if conditions:
        base_stmt = base_stmt.where(and_(*conditions))

    # 排序
    if sort_by == "created":
        col = Anime.createdAt.desc() if sort_order == "desc" else Anime.createdAt.asc()
    else:
        col = Anime.title.asc() if sort_order == "asc" else Anime.title.desc()
    base_stmt = base_stmt.order_by(col, AnimeSource.providerName)

    # 执行查询
    result = await session.execute(base_stmt)
    rows = result.mappings().all()

    # 按番剧分组
    grouped = {}
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
                "sources": []
            }
        grouped[anime_id]["sources"].append({
            "sourceId": row["sourceId"],
            "providerName": row["providerName"],
            "isFavorited": row["isFavorited"],
            "incrementalRefreshEnabled": row["incrementalRefreshEnabled"],
            "incrementalRefreshFailures": row["incrementalRefreshFailures"],
            "lastRefreshLatestEpisodeAt": row["lastRefreshLatestEpisodeAt"],
            "isFinished": row["isFinished"],
            "episodeCount": row["episodeCount"]
        })
        total_sources += 1
        if row["incrementalRefreshEnabled"]:
            refresh_enabled_count += 1
        if row["isFavorited"]:
            favorited_count += 1

    all_groups = list(grouped.values())
    total_anime = len(all_groups)

    # 分页（按番剧分页）
    start_idx = (page - 1) * page_size
    end_idx = start_idx + page_size
    paginated_groups = all_groups[start_idx:end_idx]

    return {
        "total": total_anime,
        "totalSources": total_sources,
        "refreshEnabled": refresh_enabled_count,
        "favorited": favorited_count,
        "list": paginated_groups
    }


async def batch_toggle_incremental_refresh(session: AsyncSession, source_ids: List[int], enabled: bool) -> int:
    """
    批量设置追更状态。
    如果开启追更，同一番剧下只能有一个源开启追更（与标记规则相同）。
    返回成功设置的数量。
    """
    if not source_ids:
        return 0

    if not enabled:
        # 关闭追更：直接批量关闭即可
        result = await session.execute(
            update(AnimeSource)
            .where(AnimeSource.id.in_(source_ids))
            .values(incrementalRefreshEnabled=False)
        )
        await session.commit()
        return result.rowcount

    # 开启追更：需要遵循互斥规则
    count = 0
    for source_id in source_ids:
        source = await session.get(AnimeSource, source_id)
        if not source:
            continue

        # 开启当前源的追更
        source.incrementalRefreshEnabled = True

        # 关闭同一番剧下其他源的追更
        await session.execute(
            update(AnimeSource)
            .where(AnimeSource.animeId == source.animeId, AnimeSource.id != source_id)
            .values(incrementalRefreshEnabled=False)
        )
        count += 1

    await session.commit()
    return count


async def batch_set_favorite(session: AsyncSession, source_ids: List[int]) -> int:
    """
    批量设置标记。对于每个源，将其设为标记，并取消同一番剧下其他源的标记。
    返回成功设置的数量。
    """
    if not source_ids:
        return 0

    count = 0
    for source_id in source_ids:
        source = await session.get(AnimeSource, source_id)
        if not source:
            continue

        # 设置当前源为标记
        source.isFavorited = True

        # 取消同一番剧下其他源的标记
        await session.execute(
            update(AnimeSource)
            .where(AnimeSource.animeId == source.animeId, AnimeSource.id != source_id)
            .values(isFavorited=False)
        )
        count += 1

    await session.commit()
    return count


async def batch_unset_favorite(session: AsyncSession, source_ids: List[int]) -> int:
    """
    批量取消标记。
    返回成功取消的数量。
    """
    if not source_ids:
        return 0

    result = await session.execute(
        update(AnimeSource)
        .where(AnimeSource.id.in_(source_ids))
        .values(isFavorited=False)
    )
    await session.commit()
    return result.rowcount


async def split_source_episodes(
    session: AsyncSession,
    source_id: int,
    episode_ids: List[int],
    target_anime_id: int,
    reindex_episodes: bool = True
) -> int:
    """
    将指定分集从源数据源移动到目标媒体条目。

    Args:
        session: 数据库会话
        source_id: 源数据源ID
        episode_ids: 要移动的分集ID列表
        target_anime_id: 目标媒体ID
        reindex_episodes: 是否重新编号分集

    Returns:
        移动的分集数量
    """
    if not episode_ids:
        return 0

    # 1. 获取源数据源信息
    source = await session.get(AnimeSource, source_id)
    if not source:
        raise ValueError(f"源数据源 ID {source_id} 不存在")

    # 2. 检查目标媒体是否存在
    target_anime = await session.get(Anime, target_anime_id)
    if not target_anime:
        raise ValueError(f"目标媒体 ID {target_anime_id} 不存在")

    # 3. 为目标媒体创建或获取相同提供商的数据源
    # 检查目标媒体是否已有相同提供商的数据源
    existing_target_source_stmt = select(AnimeSource).where(
        AnimeSource.animeId == target_anime_id,
        AnimeSource.providerName == source.providerName,
        AnimeSource.mediaId == source.mediaId
    )
    existing_target_source_result = await session.execute(existing_target_source_stmt)
    target_source = existing_target_source_result.scalar_one_or_none()

    if not target_source:
        # 创建新的数据源关联
        max_order_stmt = select(func.max(AnimeSource.sourceOrder)).where(AnimeSource.animeId == target_anime_id)
        max_order_result = await session.execute(max_order_stmt)
        current_max_order = max_order_result.scalar_one_or_none() or 0

        target_source = AnimeSource(
            animeId=target_anime_id,
            providerName=source.providerName,
            mediaId=source.mediaId,
            sourceOrder=current_max_order + 1,
            createdAt=get_now()
        )
        session.add(target_source)
        await session.flush()
        logger.info(f"为目标媒体 {target_anime_id} 创建了新的数据源 {target_source.id}")

    # 4. 获取要移动的分集
    episodes_stmt = select(Episode).where(
        Episode.id.in_(episode_ids),
        Episode.sourceId == source_id
    ).order_by(Episode.episodeIndex)
    episodes_result = await session.execute(episodes_stmt)
    episodes = episodes_result.scalars().all()

    if not episodes:
        return 0

    # 5. 计算新的分集ID和索引
    # 获取目标数据源当前最大的分集索引
    if reindex_episodes:
        max_index_stmt = select(func.max(Episode.episodeIndex)).where(Episode.sourceId == target_source.id)
        max_index_result = await session.execute(max_index_stmt)
        current_max_index = max_index_result.scalar_one_or_none() or 0
        new_index = current_max_index + 1

    moved_count = 0
    for episode in episodes:
        # 生成新的分集ID
        new_episode_id_str = f"25{target_anime_id:06d}{target_source.sourceOrder:02d}{(new_index if reindex_episodes else episode.episodeIndex):04d}"
        new_episode_id = int(new_episode_id_str)

        # 创建新的分集记录（因为ID是主键，需要删除旧的创建新的）
        new_episode = Episode(
            id=new_episode_id,
            sourceId=target_source.id,
            title=episode.title,
            episodeIndex=new_index if reindex_episodes else episode.episodeIndex,
            providerEpisodeId=episode.providerEpisodeId,
            sourceUrl=episode.sourceUrl,
            danmakuFilePath=episode.danmakuFilePath,
            fetchedAt=episode.fetchedAt,
            commentCount=episode.commentCount
        )

        # 删除旧分集
        await session.delete(episode)
        await session.flush()

        # 添加新分集
        session.add(new_episode)

        if reindex_episodes:
            new_index += 1
        moved_count += 1

    await session.flush()
    logger.info(f"成功将 {moved_count} 个分集从源 {source_id} 移动到目标媒体 {target_anime_id}")

    return moved_count


async def get_source_episode_list(session: AsyncSession, source_id: int) -> List[Dict[str, Any]]:
    """
    获取数据源的分集列表（用于拆分选择）。

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
    result = await session.execute(stmt)
    return [dict(row) for row in result.mappings()]



async def toggle_source_finished(session: AsyncSession, source_id: int) -> Optional[bool]:
    """切换源的完结状态。返回新状态，源不存在返回 None。"""
    source = await session.get(AnimeSource, source_id)
    if not source:
        return None
    source.isFinished = not source.isFinished
    await session.commit()
    return source.isFinished


async def batch_set_finished(session: AsyncSession, source_ids: List[int]) -> int:
    """批量标记源为完结。"""
    if not source_ids:
        return 0
    result = await session.execute(
        update(AnimeSource)
        .where(AnimeSource.id.in_(source_ids))
        .values(isFinished=True)
    )
    await session.commit()
    return result.rowcount


async def batch_unset_finished(session: AsyncSession, source_ids: List[int]) -> int:
    """批量取消源的完结标记。"""
    if not source_ids:
        return 0
    result = await session.execute(
        update(AnimeSource)
        .where(AnimeSource.id.in_(source_ids))
        .values(isFinished=False)
    )
    await session.commit()
    return result.rowcount

