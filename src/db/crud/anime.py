"""
Anime相关的CRUD操作
"""

import logging
from typing import Optional, Dict, Any, List
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, distinct, case, or_, and_, update, delete
from sqlalchemy.orm import selectinload
from datetime import datetime, timedelta

from ..orm_models import (
    Anime, AnimeSource, Episode, AnimeAlias, AnimeMetadata,
    Scraper, CacheData, ApiToken, TokenAccessLog, UaRule,
    TmdbEpisodeMapping, RateLimitState, ExternalApiLog,
    WebhookTask, TaskHistory, ScheduledTask, MetadataSource, AnimeGroup
)
from .. import models
from src.core.timezone import get_now
from .source import link_source_to_anime
from ..database import sync_postgres_sequence

logger = logging.getLogger(__name__)


async def get_library_anime(
    session: AsyncSession,
    keyword: Optional[str] = None,
    anime_type: Optional[str] = None,
    page: int = 1,
    page_size: int = -1,
    sort_by: str = "anime_created",
    sort_order: str = "desc",
) -> Dict[str, Any]:
    """
    【优化版 v3 】获取媒体库中的所有番剧及其关联信息（如分集数），支持搜索、类型过滤和分页。

    优化要点（v3 两阶段查询，极速版）:
    - 阶段1: 只查 Anime 表获取当前页的 ID 列表（极快，~10ms）
    - 阶段2: 用 ID 列表获取详细信息（只处理当前页，~20ms）
    - 总响应时间: ~30-50ms（比 v2 快 3-5 倍）

    Args:
        session: 数据库会话
        keyword: 搜索关键词
        anime_type: 类型过滤 ('movie' 或 'tv'，其中 'tv' 包含 tv_series 和 ova)
        page: 页码
        page_size: 每页数量，-1 表示不分页
        sort_by: 排序字段，'anime_created'（媒体库入库时间）或 'episode_fetched'（分集入库时间）
        sort_order: 排序方向，'asc' 或 'desc'

    Returns:
        包含 total 和 list 的字典
    """
    import time
    start_time = time.time()

    # ============================================================
    # 步骤 1: 构建基础的 WHERE 条件
    # ============================================================
    base_conditions = []

    # 类型过滤（简单条件，直接在 Anime 表上过滤）
    if anime_type:
        if anime_type == 'movie':
            base_conditions.append(Anime.type == 'movie')
        elif anime_type == 'tv':
            base_conditions.append(Anime.type.in_(['tv_series', 'ova']))

    # 搜索条件
    keyword_condition = None
    if keyword:
        clean_keyword = keyword.strip()
        if clean_keyword:
            normalized_like_keyword = f"%{clean_keyword.replace('：', ':').replace(' ', '')}%"

            # 别名匹配子查询（使用 EXISTS）
            alias_exists = (
                select(1)
                .where(AnimeAlias.animeId == Anime.id)
                .where(
                    or_(*[
                        func.replace(func.replace(col, '：', ':'), ' ', '').like(normalized_like_keyword)
                        for col in [
                            AnimeAlias.nameEn,
                            AnimeAlias.nameJp,
                            AnimeAlias.nameRomaji,
                            AnimeAlias.aliasCn1,
                            AnimeAlias.aliasCn2,
                            AnimeAlias.aliasCn3
                        ]
                    ])
                )
            ).exists()

            keyword_condition = or_(
                func.replace(func.replace(Anime.title, '：', ':'), ' ', '').like(normalized_like_keyword),
                alias_exists
            )

    # ============================================================
    # 步骤 2: 快速计数查询（只查 Anime 表）
    # ============================================================
    count_stmt = select(func.count(Anime.id))
    if base_conditions:
        count_stmt = count_stmt.where(and_(*base_conditions))
    if keyword_condition is not None:
        count_stmt = count_stmt.where(keyword_condition)

    total_count = (await session.execute(count_stmt)).scalar_one()

    # 如果没有数据，直接返回
    if total_count == 0:
        return {"total": 0, "list": []}

    # ============================================================
    # 步骤 3: 阶段1 - 获取当前页的 anime_id 列表（极快）
    # ============================================================
    # 根据排序方式决定是否需要 JOIN Episode
    if sort_by == "episode_fetched":
        # 按分集入库时间排序：LEFT JOIN 取每个 anime 的最新 fetchedAt
        # 无分集时 COALESCE 回退到 Anime.createdAt，保证所有番剧都参与排序
        episode_latest_subq = (
            select(
                AnimeSource.animeId.label("animeId"),
                func.max(Episode.fetchedAt).label("latestFetchedAt"),
            )
            .join(Episode, AnimeSource.id == Episode.sourceId)
            .group_by(AnimeSource.animeId)
        ).subquery()

        sort_key = func.coalesce(
            episode_latest_subq.c.latestFetchedAt,
            Anime.createdAt,
        )
        id_stmt = (
            select(Anime.id)
            .outerjoin(episode_latest_subq, Anime.id == episode_latest_subq.c.animeId)
        )
    else:
        # 默认按媒体库入库时间（Anime.createdAt）
        sort_key = Anime.createdAt
        id_stmt = select(Anime.id)

    if base_conditions:
        id_stmt = id_stmt.where(and_(*base_conditions))
    if keyword_condition is not None:
        id_stmt = id_stmt.where(keyword_condition)

    order_expr = sort_key.asc() if sort_order == "asc" else sort_key.desc()
    id_stmt = id_stmt.order_by(order_expr)
    if page_size > 0:
        offset = (page - 1) * page_size
        id_stmt = id_stmt.offset(offset).limit(page_size)

    id_result = await session.execute(id_stmt)
    anime_ids = [row[0] for row in id_result.fetchall()]

    # 如果当前页没有数据，直接返回
    if not anime_ids:
        return {"total": total_count, "list": []}

    # ============================================================
    # 步骤 4: 阶段2 - 用 ID 列表获取详细信息（只处理当前页）
    # ============================================================

    # 子查询1: 数据源数量（只查当前页的 anime）
    source_count_subquery = (
        select(
            AnimeSource.animeId,
            func.count(AnimeSource.id).label("sourceCount")
        )
        .where(AnimeSource.animeId.in_(anime_ids))
        .group_by(AnimeSource.animeId)
    ).subquery()

    # 子查询2: 最大集数（只查当前页的 anime）
    episode_count_subquery = (
        select(
            AnimeSource.animeId,
            func.max(Episode.episodeIndex).label("maxEpisodeIndex")
        )
        .join(Episode, AnimeSource.id == Episode.sourceId)
        .where(AnimeSource.animeId.in_(anime_ids))
        .group_by(AnimeSource.animeId)
    ).subquery()

    # 子查询3（仅 episode_fetched 排序时）: 每个 anime 的最新分集 fetchedAt
    if sort_by == "episode_fetched":
        episode_sort_subq = (
            select(
                AnimeSource.animeId.label("animeId"),
                func.max(Episode.fetchedAt).label("latestFetchedAt"),
            )
            .join(Episode, AnimeSource.id == Episode.sourceId)
            .where(AnimeSource.animeId.in_(anime_ids))
            .group_by(AnimeSource.animeId)
        ).subquery()
        data_order_key = func.coalesce(episode_sort_subq.c.latestFetchedAt, Anime.createdAt)
        data_order_expr = data_order_key.asc() if sort_order == "asc" else data_order_key.desc()
    else:
        episode_sort_subq = None
        data_order_expr = order_expr

    # 主查询：只查当前页的 anime
    data_stmt = (
        select(
            Anime.id.label("animeId"),
            Anime.localImagePath.label("localImagePath"),
            Anime.imageUrl.label("imageUrl"),
            Anime.title,
            Anime.type,
            Anime.season,
            Anime.year,
            Anime.createdAt.label("createdAt"),
            Anime.groupId.label("groupId"),
            AnimeGroup.name.label("groupName"),
            # 电影固定为1集，否则使用实际最大集数
            case(
                (Anime.type == 'movie', 1),
                else_=func.coalesce(episode_count_subquery.c.maxEpisodeIndex, 0)
            ).label("episodeCount"),
            # 数据源数量，没有源时返回0
            func.coalesce(source_count_subquery.c.sourceCount, 0).label("sourceCount")
        )
        .where(Anime.id.in_(anime_ids))
        .outerjoin(source_count_subquery, Anime.id == source_count_subquery.c.animeId)
        .outerjoin(episode_count_subquery, Anime.id == episode_count_subquery.c.animeId)
        .outerjoin(AnimeGroup, Anime.groupId == AnimeGroup.id)
    )
    if episode_sort_subq is not None:
        data_stmt = data_stmt.outerjoin(episode_sort_subq, Anime.id == episode_sort_subq.c.animeId)
    data_stmt = data_stmt.order_by(data_order_expr)

    result = await session.execute(data_stmt)
    items = [dict(row) for row in result.mappings()]

    # ============================================================
    # 步骤 5: 获取每个 anime 的源列表（用于快速操作标记和追更）
    # ============================================================
    if anime_ids:
        sources_stmt = (
            select(
                AnimeSource.animeId.label("animeId"),
                AnimeSource.id.label("sourceId"),
                AnimeSource.providerName.label("providerName"),
                AnimeSource.isFavorited.label("isFavorited"),
                AnimeSource.incrementalRefreshEnabled.label("incrementalRefreshEnabled"),
                AnimeSource.isFinished.label("isFinished"),
            )
            .where(AnimeSource.animeId.in_(anime_ids))
            .order_by(AnimeSource.animeId, AnimeSource.createdAt)
        )
        sources_result = await session.execute(sources_stmt)
        sources_rows = sources_result.mappings().all()

        # 按 animeId 分组
        sources_by_anime = {}
        for row in sources_rows:
            anime_id = row["animeId"]
            if anime_id not in sources_by_anime:
                sources_by_anime[anime_id] = []
            sources_by_anime[anime_id].append({
                "sourceId": row["sourceId"],
                "providerName": row["providerName"],
                "isFavorited": row["isFavorited"],
                "incrementalRefreshEnabled": row["incrementalRefreshEnabled"],
                "isFinished": row["isFinished"],
            })

        # 将源列表添加到每个 item
        for item in items:
            item["sources"] = sources_by_anime.get(item["animeId"], [])

    # 性能日志
    elapsed = time.time() - start_time
    logger.debug(f"[get_library_anime] 查询完成: total={total_count}, page={page}, page_size={page_size}, 耗时={elapsed*1000:.1f}ms")

    return {"total": total_count, "list": items}


async def get_library_anime_by_id(session: AsyncSession, anime_id: int) -> Optional[Dict[str, Any]]:
    """
    Gets a single anime from the library by its ID, with counts.
    """
    stmt = (
        select(
            Anime.id.label("animeId"),
            Anime.localImagePath.label("localImagePath"),
            Anime.imageUrl.label("imageUrl"),
            Anime.title,
            Anime.type,
            Anime.season,
            Anime.year,
            Anime.createdAt.label("createdAt"),  # 直接使用 createdAt，不使用 func.now()
            case(
                (Anime.type == 'movie', 1),
                else_=func.coalesce(func.max(Episode.episodeIndex), 0)
            ).label("episodeCount"),
            func.count(distinct(AnimeSource.id)).label("sourceCount")
        )
        .join(AnimeSource, Anime.id == AnimeSource.animeId, isouter=True)
        .join(Episode, AnimeSource.id == Episode.sourceId, isouter=True)
        .where(Anime.id == anime_id)
        .group_by(Anime.id)
    )
    result = await session.execute(stmt)
    row = result.mappings().one_or_none()
    return dict(row) if row else None


async def get_or_create_anime(session: AsyncSession, title: str, media_type: str, season: int, image_url: Optional[str], local_image_path: Optional[str], year: Optional[int] = None, title_recognition_manager=None, source: Optional[str] = None) -> int:
    """通过标题、季度和年份查找番剧，如果不存在则创建。如果存在但缺少海报，则更新海报。返回其ID。
    优先进行完全匹配，只有在没有找到时才应用识别词转换。"""
    logger.info(f"开始处理番剧: 原始标题='{title}', 季数={season}, 年份={year}")

    original_title = title
    original_season = season

    def _apply_identity_filters(stmt, match_title: str, match_season: int):
        """弱标题身份必须完整匹配类型与年份状态。"""
        stmt = stmt.where(
            Anime.title == match_title,
            Anime.season == match_season,
            Anime.type == media_type,
        )
        # why：未知年份只复用未知年份，不能猜测并入任一确定年份作品。
        return stmt.where(Anime.year.is_(None) if year is None else Anime.year == year)

    # 步骤1：先尝试完整身份匹配（不应用识别词转换）
    logger.info(f"🔍 数据库查找（完整身份）: title='{original_title}', season={original_season}, type={media_type}, year={year}")
    stmt = _apply_identity_filters(select(Anime), original_title, original_season).order_by(Anime.id)
    anime = (await session.execute(stmt)).scalars().first()

    if anime:
        logger.info(f"✓ 完全匹配成功: ID={anime.id}, 标题='{anime.title}', 季数={anime.season}, 年份={anime.year}")
        # 检查并更新海报
        if not anime.imageUrl and (image_url or local_image_path):
            if image_url:
                anime.imageUrl = image_url
                logger.info(f"更新海报URL: {image_url}")
            if local_image_path:
                anime.localImagePath = local_image_path
                logger.info(f"更新本地海报路径: {local_image_path}")
            await session.commit()
        return anime.id

    # 步骤2：如果完全匹配失败，尝试应用识别词转换
    logger.info(f"○ 完全匹配失败: 未找到匹配的番剧")

    converted_title = original_title
    converted_season = original_season
    was_converted = False
    metadata_info = None

    if title_recognition_manager:
        # 先尝试用原始标题进行入库后处理（查找是否有匹配的偏移规则）
        converted_title, converted_season, was_converted, metadata_info, _ = await title_recognition_manager.apply_storage_postprocessing(title, season, source)

        if was_converted:
            original_season_str = f"S{original_season:02d}" if original_season is not None else "S??"
            converted_season_str = f"S{converted_season:02d}" if converted_season is not None else "S??"
            logger.info(f"🔍 尝试识别词转换匹配: '{original_title}' {original_season_str} -> '{converted_title}' {converted_season_str}")

            # 使用转换后的标题、季度、类型和明确年份状态查找。
            stmt = _apply_identity_filters(select(Anime), converted_title, converted_season).order_by(Anime.id)
            anime = (await session.execute(stmt)).scalars().first()

            if anime:
                logger.info(f"✓ 识别词转换匹配成功: ID={anime.id}, 标题='{anime.title}', 季数={anime.season}, 年份={anime.year}")
                # 检查并更新海报
                if not anime.imageUrl and (image_url or local_image_path):
                    if image_url:
                        anime.imageUrl = image_url
                        logger.info(f"更新海报URL: {image_url}")
                    if local_image_path:
                        anime.localImagePath = local_image_path
                        logger.info(f"更新本地海报路径: {local_image_path}")
                    await session.commit()
                return anime.id
            else:
                logger.info(f"○ 识别词转换匹配也失败: 未找到匹配的番剧")
        else:
            original_season_str = f"S{original_season:02d}" if original_season is not None else "S??"
            logger.info(f"○ 标题识别转换未生效: '{original_title}' {original_season_str} (无匹配规则)")

    # 步骤2.5a：别名匹配 — 在已有条目的别名中查找传入的标题
    search_titles = [original_title]
    if was_converted and converted_title != original_title:
        search_titles.append(converted_title)

    final_title = converted_title if was_converted else original_title
    final_season = converted_season if was_converted else original_season

    for search_title in search_titles:
        alias_conditions = or_(
            AnimeAlias.nameEn == search_title,
            AnimeAlias.nameJp == search_title,
            AnimeAlias.nameRomaji == search_title,
            AnimeAlias.aliasCn1 == search_title,
            AnimeAlias.aliasCn2 == search_title,
            AnimeAlias.aliasCn3 == search_title,
        )
        alias_stmt = select(Anime).join(
            AnimeAlias, Anime.id == AnimeAlias.animeId
        ).where(
            alias_conditions,
            Anime.season == final_season,
            Anime.type == media_type,
            Anime.year.is_(None) if year is None else Anime.year == year,
        ).order_by(Anime.id)

        anime = (await session.execute(alias_stmt)).scalars().first()
        if anime:
            logger.info(f"✓ 别名匹配成功: ID={anime.id}, 标题='{anime.title}', 通过别名'{search_title}' 命中")
            # 检查并更新海报
            if not anime.imageUrl and (image_url or local_image_path):
                if image_url:
                    anime.imageUrl = image_url
                    logger.info(f"更新海报URL: {image_url}")
                if local_image_path:
                    anime.localImagePath = local_image_path
                    logger.info(f"更新本地海报路径: {local_image_path}")
                await session.commit()
            return anime.id

    logger.info(f"○ 别名匹配失败: 未在已有条目的别名中找到 '{original_title}'")

    # 步骤2.5b：模糊标题匹配 — 用 thefuzz 做标题相似度比较
    try:
        from thefuzz import fuzz
        fuzzy_stmt = select(Anime).where(
            Anime.season == final_season,
            Anime.type == media_type,
            Anime.year.is_(None) if year is None else Anime.year == year,
        )
        result = await session.execute(fuzzy_stmt)
        candidates = result.scalars().all()

        if candidates:
            best_match = None
            best_score = 0
            for candidate in candidates:
                # 对每个候选，同时比较主标题和别名
                score = fuzz.token_set_ratio(final_title, candidate.title)
                if score > best_score:
                    best_score = score
                    best_match = candidate

            if best_match and best_score >= 85:
                logger.info(f"✓ 模糊匹配成功: ID={best_match.id}, 标题='{best_match.title}', 相似度={best_score}%, 搜索标题='{final_title}'")
                # 检查并更新海报
                if not best_match.imageUrl and (image_url or local_image_path):
                    if image_url:
                        best_match.imageUrl = image_url
                        logger.info(f"更新海报URL: {image_url}")
                    if local_image_path:
                        best_match.localImagePath = local_image_path
                        logger.info(f"更新本地海报路径: {local_image_path}")
                    await session.commit()
                return best_match.id
            elif best_match:
                logger.info(f"○ 模糊匹配未达阈值: 最佳候选'{best_match.title}', 相似度={best_score}% (需要>=85%)")
            else:
                logger.info(f"○ 模糊匹配: 无同季候选条目")
        else:
            logger.info(f"○ 模糊匹配: 无同季候选条目")
    except ImportError:
        logger.warning("thefuzz 库未安装，跳过模糊匹配步骤")
    except Exception as e:
        logger.warning(f"模糊匹配过程出错，跳过: {e}")

    # 步骤3：如果都没找到，创建新番剧
    logger.info(f"创建新番剧: 标题='{final_title}', 季数={final_season}, 类型={media_type}")
    if was_converted:
        logger.info(f"✓ 使用识别词转换后的标题和季数创建新条目")

    created_time = get_now()
    logger.info(f"设置创建时间: {created_time}")
    new_anime = Anime(
        title=final_title,  # 使用最终标题创建
        season=final_season,  # 使用最终季数创建
        type=media_type,
        year=year,
        imageUrl=image_url,
        localImagePath=local_image_path,
        createdAt=created_time  # 设置创建时间
    )
    session.add(new_anime)
    await session.flush()  # 获取ID但不提交事务
    logger.info(f"新番剧创建完成: ID={new_anime.id}, 标题='{new_anime.title}', 季数={new_anime.season}, createdAt={new_anime.createdAt}")

    # 同步PostgreSQL序列(避免主键冲突)
    await sync_postgres_sequence(session)

    return new_anime.id


async def create_anime(session: AsyncSession, anime_data: models.AnimeCreate) -> Anime:
    """
    Manually creates a new anime entry in the database, and automatically
    creates and links a default 'custom' source for it.
    """
    # 修正：在重复检查时也包含年份
    existing_anime = await find_anime_by_title_season_year(
        session, anime_data.title, anime_data.season, anime_data.year
    )
    if existing_anime:
        raise ValueError(f"作品 '{anime_data.title}' (第 {anime_data.season} 季) 已存在。")

    created_time = get_now().replace(tzinfo=None)
    logger.info(f"create_anime: 设置创建时间: {created_time}")
    new_anime = Anime(
        title=anime_data.title,
        type=anime_data.type,
        season=anime_data.season,
        year=anime_data.year,
        imageUrl=anime_data.imageUrl,
        createdAt=created_time
    )
    session.add(new_anime)
    await session.flush()

    # 同步PostgreSQL序列(避免主键冲突)
    await sync_postgres_sequence(session)

    # Create associated metadata and alias records
    new_metadata = AnimeMetadata(animeId=new_anime.id)
    new_alias = AnimeAlias(animeId=new_anime.id)
    session.add_all([new_metadata, new_alias])

    # 修正：在创建新作品时，自动为其创建一个'custom'数据源。
    # 这简化了用户操作，并从根源上确保了数据完整性，
    # 因为 link_source_to_anime 会负责在 scrapers 表中创建对应的条目。
    logger.info(f"为新作品 '{anime_data.title}' 自动创建 'custom' 数据源。")
    custom_media_id = f"custom_{new_anime.id}"
    await link_source_to_anime(session, new_anime.id, "custom", custom_media_id)

    await session.flush()
    await session.refresh(new_anime)
    return new_anime


async def update_anime_aliases(session: AsyncSession, anime_id: int, payload: Any):
    """
    Updates the aliases for a given anime.
    The payload can be any object with the alias attributes.
    """
    stmt = select(AnimeAlias).where(AnimeAlias.animeId == anime_id)
    result = await session.execute(stmt)
    alias_record = result.scalar_one_or_none()

    if not alias_record:
        alias_record = AnimeAlias(animeId=anime_id)
        session.add(alias_record)

    alias_record.nameEn = getattr(payload, 'nameEn', alias_record.nameEn)
    alias_record.nameJp = getattr(payload, 'nameJp', alias_record.nameJp)
    alias_record.nameRomaji = getattr(payload, 'nameRomaji', alias_record.nameRomaji)
    alias_record.aliasCn1 = getattr(payload, 'aliasCn1', alias_record.aliasCn1)
    alias_record.aliasCn2 = getattr(payload, 'aliasCn2', alias_record.aliasCn2)
    alias_record.aliasCn3 = getattr(payload, 'aliasCn3', alias_record.aliasCn3)

    await session.flush()


async def update_anime_details(session: AsyncSession, anime_id: int, update_data: models.AnimeDetailUpdate) -> bool:
    """在事务中更新番剧的核心信息、元数据和别名。"""
    anime = await session.get(Anime, anime_id, options=[selectinload(Anime.metadataRecord), selectinload(Anime.aliases)])
    if not anime:
        return False

    # Update Anime table
    anime.title = update_data.title
    anime.type = update_data.type
    anime.season = update_data.season
    anime.episodeCount = update_data.episodeCount
    anime.year = update_data.year
    anime.imageUrl = update_data.imageUrl

    # Update or create AnimeMetadata
    if not anime.metadataRecord:
        anime.metadataRecord = AnimeMetadata(animeId=anime_id)
    anime.metadataRecord.tmdbId = update_data.tmdbId
    anime.metadataRecord.tmdbEpisodeGroupId = update_data.tmdbEpisodeGroupId
    anime.metadataRecord.bangumiId = update_data.bangumiId
    anime.metadataRecord.tvdbId = update_data.tvdbId
    anime.metadataRecord.doubanId = update_data.doubanId
    anime.metadataRecord.imdbId = update_data.imdbId

    # Update or create AnimeAlias
    if not anime.aliases:
        anime.aliases = AnimeAlias(animeId=anime_id)
    anime.aliases.nameEn = update_data.nameEn
    anime.aliases.nameJp = update_data.nameJp
    anime.aliases.nameRomaji = update_data.nameRomaji
    anime.aliases.aliasCn1 = update_data.aliasCn1
    anime.aliases.aliasCn2 = update_data.aliasCn2
    anime.aliases.aliasCn3 = update_data.aliasCn3
    if update_data.aliasLocked is not None:
        anime.aliases.aliasLocked = update_data.aliasLocked

    await session.commit()
    return True


async def delete_anime(session: AsyncSession, anime_id: int) -> bool:
    """删除一个作品及其所有关联数据（通过级联删除）。"""
    import shutil
    anime = await session.get(Anime, anime_id)
    if anime:
        await session.delete(anime)
        await session.commit()
        return True
    return False


async def search_anime(session: AsyncSession, keyword: str) -> List[Dict[str, Any]]:
    """在数据库中搜索番剧 (使用FULLTEXT索引)"""
    sanitized_keyword = re.sub(r'[+\-><()~*@"]', ' ', keyword).strip()
    if not sanitized_keyword:
        return []

    # 修正：使用 LIKE 代替 MATCH...AGAINST 以兼容 PostgreSQL
    # 注意：这会比全文索引慢，但提供了跨数据库的兼容性。
    # 对于高性能需求，可以考虑为每个数据库方言实现特定的全文搜索查询。
    stmt = select(Anime.id, Anime.title, Anime.type).where(
        Anime.title.like(f"%{sanitized_keyword}%")
    ).order_by(func.length(Anime.title)) # 按标题长度排序，较短的匹配更相关

    result = await session.execute(stmt)
    return [dict(row) for row in result.mappings()]


async def search_episodes_in_library(session: AsyncSession, anime_title: str, episode_number: Optional[int], season_number: Optional[int] = None) -> List[Dict[str, Any]]:
    """在本地库中通过番剧标题和可选的集数搜索匹配的分集。"""
    clean_title = anime_title.strip()
    if not clean_title:
        return []

    # Base query
    stmt = (
        select(
            Anime.id.label("animeId"),
            Anime.title.label("animeTitle"),
            Anime.type,
            Anime.imageUrl.label("imageUrl"),
            Anime.createdAt.label("startDate"),
            Episode.id.label("episodeId"),
            Episode.episodeIndex.label("episodeIndex"),
            case((Anime.type == 'movie', func.concat(Scraper.providerName, ' 源')), else_=Episode.title).label("episodeTitle"),
            Scraper.providerName.label("providerName"),
            AnimeAlias.nameEn,
            AnimeAlias.nameJp,
            AnimeAlias.nameRomaji,
            AnimeAlias.aliasCn1,
            AnimeAlias.aliasCn2,
            AnimeAlias.aliasCn3,
            Scraper.displayOrder,
            AnimeSource.isFavorited.label("isFavorited"),
            AnimeMetadata.bangumiId.label("bangumiId")
        )
        .join(AnimeSource, Anime.id == AnimeSource.animeId)
        .join(Episode, AnimeSource.id == Episode.sourceId)
        .join(Scraper, AnimeSource.providerName == Scraper.providerName)
        .join(AnimeMetadata, Anime.id == AnimeMetadata.animeId, isouter=True)
        .join(AnimeAlias, Anime.id == AnimeAlias.animeId, isouter=True)
    )

    # Add conditions
    if episode_number is not None:
        stmt = stmt.where(Episode.episodeIndex == episode_number)
    if season_number is not None:
        stmt = stmt.where(Anime.season == season_number)

    # Title condition
    normalized_like_title = f"%{clean_title.replace('：', ':').replace(' ', '')}%"
    like_conditions = [
        func.replace(func.replace(col, '：', ':'), ' ', '').like(normalized_like_title)
        for col in [Anime.title, AnimeAlias.nameEn, AnimeAlias.nameJp, AnimeAlias.nameRomaji,
                    AnimeAlias.aliasCn1, AnimeAlias.aliasCn2, AnimeAlias.aliasCn3]
    ]
    stmt = stmt.where(or_(*like_conditions))

    # Order and execute
    # 修正：按集数排序，确保episodes按正确顺序返回
    stmt = stmt.order_by(func.length(Anime.title), Scraper.displayOrder, Episode.episodeIndex)
    result = await session.execute(stmt)
    return [dict(row) for row in result.mappings()]


async def find_anime_by_title_season_year(session: AsyncSession, title: str, season: int, year: Optional[int] = None, title_recognition_manager=None, source: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """
    通过标题、季度和可选的年份查找番剧，返回一个简化的字典或None。
    优先进行完全匹配，只有在没有找到时才应用识别词转换。
    """
    original_title = title
    original_season = season

    # 步骤1：先尝试完全匹配（不应用识别词转换）
    logger.info(f"🔍 数据库查找: title='{original_title}', season={original_season}, year={year}")
    stmt = (
        select(
            Anime.id,
            Anime.title,
            Anime.season,
            Anime.type,
            Anime.year,
            Anime.imageUrl,
            Anime.localImagePath
        )
        .where(Anime.title == original_title, Anime.season == original_season)
        .limit(1)
    )
    if year:
        stmt = stmt.where(Anime.year == year)

    # 左连接 AnimeMetadata 以获取元数据ID
    stmt = stmt.outerjoin(AnimeMetadata, Anime.id == AnimeMetadata.animeId).add_columns(
        AnimeMetadata.tmdbId,
        AnimeMetadata.imdbId,
        AnimeMetadata.tvdbId,
        AnimeMetadata.doubanId,
        AnimeMetadata.bangumiId
    )

    result = await session.execute(stmt)
    row = result.mappings().first()

    if row:
        original_season_str = f"S{original_season:02d}" if original_season is not None else "S??"
        logger.info(f"✓ 完全匹配成功: 找到作品 '{original_title}' {original_season_str}")
        return dict(row)

    # 步骤2：如果完全匹配失败，尝试应用识别词转换
    logger.info(f"○ 完全匹配失败: 未找到匹配的番剧")

    if title_recognition_manager:
        converted_title, converted_season, was_converted, metadata_info, _ = await title_recognition_manager.apply_storage_postprocessing(title, season, source)

        if was_converted:
            original_season_str = f"S{original_season:02d}" if original_season is not None else "S??"
            converted_season_str = f"S{converted_season:02d}" if converted_season is not None else "S??"
            logger.info(f"🔍 尝试识别词转换匹配: '{original_title}' {original_season_str} -> '{converted_title}' {converted_season_str}")

            # 使用转换后的标题和季数进行查找
            stmt = (
                select(
                    Anime.id,
                    Anime.title,
                    Anime.season,
                    Anime.type,
                    Anime.year,
                    Anime.imageUrl,
                    Anime.localImagePath
                )
                .where(Anime.title == converted_title, Anime.season == converted_season)
                .limit(1)
            )
            if year:
                stmt = stmt.where(Anime.year == year)

            # 左连接 AnimeMetadata 以获取元数据ID
            stmt = stmt.outerjoin(AnimeMetadata, Anime.id == AnimeMetadata.animeId).add_columns(
                AnimeMetadata.tmdbId,
                AnimeMetadata.imdbId,
                AnimeMetadata.tvdbId,
                AnimeMetadata.doubanId,
                AnimeMetadata.bangumiId
            )

            result = await session.execute(stmt)
            row = result.mappings().first()

            if row:
                converted_season_str = f"S{converted_season:02d}" if converted_season is not None else "S??"
                logger.info(f"✓ 识别词转换匹配成功: 找到作品 '{converted_title}' {converted_season_str}")
                return dict(row)
            else:
                logger.info(f"○ 识别词转换匹配也失败: 未找到匹配的番剧")
        else:
            season_str = f"S{original_season:02d}" if original_season is not None else "S??"
            logger.info(f"○ 标题识别转换未生效: '{original_title}' {season_str} (无匹配规则)")

    return None


async def find_anime_by_metadata_id_and_season(
    session: AsyncSession,
    id_type: str,
    media_id: str,
    season: int
) -> Optional[Dict[str, Any]]:
    """
    通过元数据ID和季度号精确查找一个作品。
    """
    id_column = getattr(AnimeMetadata, id_type, None)
    if id_column is None:
        raise ValueError(f"无效的元数据ID类型: {id_type}")

    stmt = (
        select(
            Anime.id,
            Anime.title,
            Anime.season,
            Anime.type,
            Anime.year,
            Anime.imageUrl,
            Anime.localImagePath,
            AnimeMetadata.tmdbId,
            AnimeMetadata.imdbId,
            AnimeMetadata.tvdbId,
            AnimeMetadata.doubanId,
            AnimeMetadata.bangumiId
        )
        .join(AnimeMetadata, Anime.id == AnimeMetadata.animeId)
        .where(id_column == media_id, Anime.season == season)
        .limit(1)
    )
    result = await session.execute(stmt)
    row = result.mappings().first()
    return dict(row) if row else None


async def find_favorited_source_for_anime(session: AsyncSession, anime_id: int) -> Optional[Dict[str, Any]]:
    """通过 anime_id 查找已存在于库中且被标记为“精确”的数据源。"""
    stmt = (
        select(
            AnimeSource.providerName.label("providerName"),
            AnimeSource.mediaId.label("mediaId"),
            Anime.id.label("animeId"),
            Anime.title.label("animeTitle"), # 保留标题以用于任务创建
            Anime.type.label("mediaType"),
            Anime.imageUrl.label("imageUrl"),
            Anime.year.label("year") # 新增年份以保持数据完整性
        )
        .join(Anime, AnimeSource.animeId == Anime.id)
        .where(AnimeSource.animeId == anime_id, AnimeSource.isFavorited == True)
        .limit(1)
    )
    result = await session.execute(stmt)
    row = result.mappings().first()
    return dict(row) if row else None


async def search_animes_for_dandan(session: AsyncSession, keyword: str) -> List[Dict[str, Any]]:
    """在本地库中通过番剧标题搜索匹配的番剧，用于 /search/anime 接口。"""
    clean_title = keyword.strip()
    if not clean_title:
        return []

    stmt = (
        select(
            Anime.id.label("animeId"),
            Anime.title.label("animeTitle"),
            Anime.type,
            Anime.imageUrl.label("imageUrl"),
            Anime.createdAt.label("startDate"),
            Anime.year,
            func.count(distinct(Episode.id)).label("episodeCount"),
            AnimeMetadata.bangumiId.label("bangumiId")
        )
        .join(AnimeSource, Anime.id == AnimeSource.animeId, isouter=True)
        .join(Episode, AnimeSource.id == Episode.sourceId, isouter=True)
        .join(AnimeMetadata, Anime.id == AnimeMetadata.animeId, isouter=True)
        .join(AnimeAlias, Anime.id == AnimeAlias.animeId, isouter=True)
        .group_by(Anime.id, AnimeMetadata.bangumiId)
        .order_by(Anime.id)
    )

    normalized_like_title = f"%{clean_title.replace('：', ':').replace(' ', '')}%"
    like_conditions = [
        func.replace(func.replace(col, '：', ':'), ' ', '').like(normalized_like_title)
        for col in [Anime.title, AnimeAlias.nameEn, AnimeAlias.nameJp, AnimeAlias.nameRomaji,
                    AnimeAlias.aliasCn1, AnimeAlias.aliasCn2, AnimeAlias.aliasCn3]
    ]
    stmt = stmt.where(or_(*like_conditions))

    result = await session.execute(stmt)
    return [dict(row) for row in result.mappings()]


async def get_anime_ids_with_custom_source(session: AsyncSession, anime_ids: List[int]) -> List[int]:
    """查询给定的 anime IDs 中哪些有 custom 源关联，返回有 custom 源的 anime ID 列表。"""
    if not anime_ids:
        return []
    stmt = (
        select(distinct(AnimeSource.animeId))
        .where(
            AnimeSource.providerName == 'custom',
            AnimeSource.animeId.in_(anime_ids)
        )
    )
    result = await session.execute(stmt)
    return [row[0] for row in result.all()]


async def find_animes_for_matching(session: AsyncSession, title: str) -> List[Dict[str, Any]]:
    """为匹配流程查找可能的番剧，并返回其核心ID以供TMDB映射使用。"""
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


async def get_anime_full_details(session: AsyncSession, anime_id: int) -> Optional[Dict[str, Any]]:
    stmt = (
        select(
            Anime.id.label("animeId"), Anime.title, Anime.type, Anime.season, Anime.year, Anime.localImagePath.label("localImagePath"),
            Anime.episodeCount.label("episodeCount"), Anime.imageUrl.label("imageUrl"),
            AnimeMetadata.tmdbId.label("tmdbId"), AnimeMetadata.tmdbEpisodeGroupId.label("tmdbEpisodeGroupId"),
            AnimeMetadata.bangumiId.label("bangumiId"), AnimeMetadata.tvdbId.label("tvdbId"), AnimeMetadata.doubanId.label("doubanId"), AnimeMetadata.imdbId.label("imdbId"),
            AnimeAlias.nameEn.label("nameEn"), AnimeAlias.nameJp.label("nameJp"), AnimeAlias.nameRomaji.label("nameRomaji"), AnimeAlias.aliasCn1.label("aliasCn1"),
            AnimeAlias.aliasCn2.label("aliasCn2"), AnimeAlias.aliasCn3.label("aliasCn3"), AnimeAlias.aliasLocked.label("aliasLocked")
        )
        .join(AnimeMetadata, Anime.id == AnimeMetadata.animeId, isouter=True)
        .join(AnimeAlias, Anime.id == AnimeAlias.animeId, isouter=True)
        .where(Anime.id == anime_id)
    )
    result = await session.execute(stmt)
    row = result.mappings().first()
    return dict(row) if row else None


async def get_anime_id_by_bangumi_id(session: AsyncSession, bangumi_id: str) -> Optional[int]:
    """通过 bangumi_id 查找 anime_id。"""
    stmt = select(AnimeMetadata.animeId).where(AnimeMetadata.bangumiId == bangumi_id)
    result = await session.execute(stmt)
    return result.scalars().first()


async def get_anime_id_by_tmdb_id(session: AsyncSession, tmdb_id: str) -> Optional[int]:
    """通过 tmdb_id 查找 anime_id。"""
    stmt = select(AnimeMetadata.animeId).where(AnimeMetadata.tmdbId == tmdb_id)
    result = await session.execute(stmt)
    return result.scalars().first()


async def get_anime_id_by_tvdb_id(session: AsyncSession, tvdb_id: str) -> Optional[int]:
    """通过 tvdb_id 查找 anime_id。"""
    stmt = select(AnimeMetadata.animeId).where(AnimeMetadata.tvdbId == tvdb_id)
    result = await session.execute(stmt)
    return result.scalars().first()


async def get_anime_id_by_imdb_id(session: AsyncSession, imdb_id: str) -> Optional[int]:
    """通过 imdb_id 查找 anime_id。"""
    stmt = select(AnimeMetadata.animeId).where(AnimeMetadata.imdbId == imdb_id)
    result = await session.execute(stmt)
    return result.scalars().first()


async def get_anime_id_by_douban_id(session: AsyncSession, douban_id: str) -> Optional[int]:
    """通过 douban_id 查找 anime_id。"""
    stmt = select(AnimeMetadata.animeId).where(AnimeMetadata.doubanId == douban_id)
    result = await session.execute(stmt)
    return result.scalars().first()


async def update_anime_tmdb_group_id(session: AsyncSession, anime_id: int, group_id: str):
    await session.execute(update(AnimeMetadata).where(AnimeMetadata.animeId == anime_id).values(tmdbEpisodeGroupId=group_id))
    await session.commit()


async def update_anime_aliases_if_empty(session: AsyncSession, anime_id: int, aliases: Dict[str, Any], force_update: bool = False):
    """
    更新作品别名,如果字段为空则填充
    如果别名记录不存在,则创建新记录

    Args:
        session: 数据库会话
        anime_id: 作品ID
        aliases: 别名数据字典
        force_update: 是否强制更新(用于AI修正),默认False
    """
    from ..orm_models import AnimeAlias

    # 修正：使用 select().where() 而不是 session.get()，因为 anime_id 不是主键
    stmt = select(AnimeAlias).where(AnimeAlias.animeId == anime_id)
    result = await session.execute(stmt)
    alias_record = result.scalar_one_or_none()

    # 如果记录不存在,创建新记录
    if not alias_record:
        alias_record = AnimeAlias(animeId=anime_id, aliasLocked=False)
        session.add(alias_record)
        logging.info(f"为作品 ID {anime_id} 创建新的别名记录。")

    # 检查锁定状态
    if alias_record.aliasLocked and not force_update:
        logging.info(f"作品 ID {anime_id} 的别名已锁定,跳过更新。")
        return

    # 如果是强制更新(AI修正),则更新所有字段
    # 否则只在字段为空时更新
    updated_fields = []

    if force_update:
        if aliases.get('name_en'):
            alias_record.nameEn = aliases['name_en']
            updated_fields.append(f"nameEn='{aliases['name_en']}'")
        if aliases.get('name_jp'):
            alias_record.nameJp = aliases['name_jp']
            updated_fields.append(f"nameJp='{aliases['name_jp']}'")
        if aliases.get('name_romaji'):
            alias_record.nameRomaji = aliases['name_romaji']
            updated_fields.append(f"nameRomaji='{aliases['name_romaji']}'")

        cn_aliases = aliases.get('aliases_cn', [])
        if len(cn_aliases) > 0:
            alias_record.aliasCn1 = cn_aliases[0]
            updated_fields.append(f"aliasCn1='{cn_aliases[0]}'")
        if len(cn_aliases) > 1:
            alias_record.aliasCn2 = cn_aliases[1]
            updated_fields.append(f"aliasCn2='{cn_aliases[1]}'")
        if len(cn_aliases) > 2:
            alias_record.aliasCn3 = cn_aliases[2]
            updated_fields.append(f"aliasCn3='{cn_aliases[2]}'")

        if updated_fields:
            logging.info(f"为作品 ID {anime_id} 强制更新了别名字段(AI修正): {', '.join(updated_fields)}")
    else:
        # 只在字段为空时更新
        if not alias_record.nameEn and aliases.get('name_en'):
            alias_record.nameEn = aliases['name_en']
            updated_fields.append(f"nameEn='{aliases['name_en']}'")
        if not alias_record.nameJp and aliases.get('name_jp'):
            alias_record.nameJp = aliases['name_jp']
            updated_fields.append(f"nameJp='{aliases['name_jp']}'")
        if not alias_record.nameRomaji and aliases.get('name_romaji'):
            alias_record.nameRomaji = aliases['name_romaji']
            updated_fields.append(f"nameRomaji='{aliases['name_romaji']}'")

        cn_aliases = aliases.get('aliases_cn', [])
        if not alias_record.aliasCn1 and len(cn_aliases) > 0:
            alias_record.aliasCn1 = cn_aliases[0]
            updated_fields.append(f"aliasCn1='{cn_aliases[0]}'")
        if not alias_record.aliasCn2 and len(cn_aliases) > 1:
            alias_record.aliasCn2 = cn_aliases[1]
            updated_fields.append(f"aliasCn2='{cn_aliases[1]}'")
        if not alias_record.aliasCn3 and len(cn_aliases) > 2:
            alias_record.aliasCn3 = cn_aliases[2]
            updated_fields.append(f"aliasCn3='{cn_aliases[2]}'")

        if updated_fields:
            logging.info(f"为作品 ID {anime_id} 更新了别名字段: {', '.join(updated_fields)}")

    await session.flush()
    return updated_fields  # 返回更新的字段列表


async def get_animes_with_tmdb_id(session: AsyncSession) -> List[Dict[str, Any]]:
    stmt = (
        select(Anime.id.label("animeId"), Anime.title, AnimeMetadata.tmdbId, AnimeMetadata.tmdbEpisodeGroupId)
        .join(AnimeMetadata, Anime.id == AnimeMetadata.animeId)
        .where(Anime.type == 'tv_series', AnimeMetadata.tmdbId != None, AnimeMetadata.tmdbId != '')
    )
    result = await session.execute(stmt)
    return [dict(row) for row in result.mappings()]


async def get_anime_details_for_dandan(session: AsyncSession, anime_id: int) -> Optional[Dict[str, Any]]:
    """获取番剧的详细信息及其所有分集，用于dandanplay API。"""
    anime_stmt = (
        select(
            Anime.id.label("animeId"), Anime.title.label("animeTitle"), Anime.type, Anime.imageUrl.label("imageUrl"),
            Anime.createdAt.label("startDate"), Anime.year,
            func.count(distinct(Episode.id)).label("episodeCount"), AnimeMetadata.bangumiId.label("bangumiId")
        )
        .join(AnimeSource, Anime.id == AnimeSource.animeId, isouter=True)
        .join(Episode, AnimeSource.id == Episode.sourceId, isouter=True)
        .join(AnimeMetadata, Anime.id == AnimeMetadata.animeId, isouter=True)
        .where(Anime.id == anime_id)
        .group_by(Anime.id, AnimeMetadata.bangumiId)
    )
    anime_details_res = await session.execute(anime_stmt)
    anime_details = anime_details_res.mappings().first()
    if not anime_details:
        return None

    episodes = []
    if anime_details['type'] == 'movie':
        ep_stmt = (
            select(Episode.id.label("episodeId"), func.concat(AnimeSource.providerName, ' 源').label("episodeTitle"), Scraper.displayOrder.label("episodeNumber"))
            .join(AnimeSource, Episode.sourceId == AnimeSource.id)
            .join(Scraper, AnimeSource.providerName == Scraper.providerName)
            .where(AnimeSource.animeId == anime_id)
            .order_by(Scraper.displayOrder)
        )
        episodes_res = await session.execute(ep_stmt)
        episodes = [dict(row) for row in episodes_res.mappings()]
    else:
        # 查出分集及其所属的 providerName
        ep_stmt = (
            select(
                Episode.id.label("episodeId"),
                Episode.title.label("episodeTitle"),
                Episode.episodeIndex.label("episodeNumber"),
                AnimeSource.providerName.label("providerName"),
            )
            .join(AnimeSource, Episode.sourceId == AnimeSource.id)
            .where(AnimeSource.animeId == anime_id)
            .order_by(Episode.episodeIndex)
        )
        episodes_res = await session.execute(ep_stmt)
        raw_episodes = [dict(row) for row in episodes_res.mappings()]

        # 判断是否多源：统计不同的 provider 数量
        providers = {ep['providerName'] for ep in raw_episodes if ep.get('providerName')}
        is_multi_source = len(providers) > 1

        for ep in raw_episodes:
            title = ep['episodeTitle']
            if is_multi_source and ep.get('providerName') and not title.startswith("【"):
                title = f"【{ep['providerName']}】{title}"
            episodes.append({
                "episodeId": ep['episodeId'],
                "episodeTitle": title,
                "episodeNumber": ep['episodeNumber'],
            })

    return {"anime": dict(anime_details), "episodes": episodes}




async def scan_duplicate_animes(session: AsyncSession, strict: bool = True) -> List[Dict[str, Any]]:
    """
    扫描弹幕库中的重复条目。
    strict=True: 按 tmdbId + season 分组（严格模式）
    strict=False: 仅按 tmdbId 分组（宽松模式，用于剧集组场景）

    返回: [{ tmdbId, season(仅严格模式), items: [{ animeId, title, season, year, sourceCount, imageUrl, localImagePath }] }]
    """
    # 基础查询：Anime + AnimeMetadata + 源数统计
    source_count_subq = (
        select(
            AnimeSource.animeId,
            func.count(AnimeSource.id).label("sourceCount")
        )
        .group_by(AnimeSource.animeId)
        .subquery()
    )

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

    result = await session.execute(stmt)
    rows = [dict(r) for r in result.mappings()]

    # 按分组键聚合
    from collections import defaultdict
    groups: Dict[Any, list] = defaultdict(list)
    for row in rows:
        if strict:
            key = (row["tmdbId"], row["season"])
        else:
            key = row["tmdbId"]
        groups[key].append(row)

    # 只保留有 2+ 条目的组
    duplicate_groups = []
    for key, items in groups.items():
        if len(items) < 2:
            continue
        group = {"tmdbId": items[0]["tmdbId"], "items": items}
        if strict:
            group["season"] = items[0]["season"]
        duplicate_groups.append(group)

    return duplicate_groups



async def bulk_set_sources_finished_by_anime_ids(session: AsyncSession, anime_ids: List[int], is_finished: bool) -> int:
    """批量设置指定番剧下所有源的完结状态，返回实际更新的记录数。"""
    if not anime_ids:
        return 0
    PG_BATCH = 30000
    total_updated = 0
    for i in range(0, len(anime_ids), PG_BATCH):
        batch = anime_ids[i:i + PG_BATCH]
        stmt = (
            update(AnimeSource)
            .where(AnimeSource.animeId.in_(batch))
            .values(isFinished=is_finished)
        )
        result = await session.execute(stmt)
        total_updated += result.rowcount
    await session.commit()
    return total_updated
