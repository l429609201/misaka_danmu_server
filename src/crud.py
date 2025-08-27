import json
import logging
import re
import secrets
from datetime import datetime, timedelta, timezone
from typing import List, Optional, Dict, Any, Type

from sqlalchemy import select, func, delete, update, and_, or_, text, distinct, case
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import selectinload, joinedload, aliased, DeclarativeBase
from sqlalchemy.dialects.mysql import insert as mysql_insert
from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.sql.elements import ColumnElement

from . import models
from .orm_models import (
    Anime, AnimeSource, Episode, Comment, User, Scraper, AnimeMetadata, Config, CacheData, ApiToken, TokenAccessLog, UaRule, BangumiAuth, OauthState, AnimeAlias, TmdbEpisodeMapping, ScheduledTask, TaskHistory, MetadataSource, ExternalApiLog
, RateLimitState)
from .config import settings

logger = logging.getLogger(__name__)

# --- Anime & Library ---

async def get_library_anime(session: AsyncSession) -> List[Dict[str, Any]]:
    """获取媒体库中的所有番剧及其关联信息（如分集数）"""
    stmt = (
        select(
            Anime.id.label("animeId"),
            Anime.localImagePath.label("localImagePath"),
            Anime.imageUrl.label("imageUrl"),
            Anime.title,
            Anime.type,
            Anime.season,
            Anime.year,
            Anime.createdAt.label("createdAt"),
            case(
                (Anime.type == 'movie', 1),
                else_=func.coalesce(func.max(Episode.episodeIndex), 0)
            ).label("episodeCount"),
            func.count(distinct(AnimeSource.id)).label("sourceCount")
        )
        .join(AnimeSource, Anime.id == AnimeSource.animeId, isouter=True)
        .join(Episode, AnimeSource.id == Episode.sourceId, isouter=True)
        .group_by(Anime.id)
        .order_by(Anime.createdAt.desc())
    )
    result = await session.execute(stmt)
    return [dict(row) for row in result.mappings()]

async def get_last_episode_for_source(session: AsyncSession, sourceId: int) -> Optional[Dict[str, Any]]:
    """获取指定源的最后一个分集。"""
    stmt = (
        select(Episode.episodeIndex.label("episodeIndex"))
        .where(Episode.sourceId == sourceId)
        .order_by(Episode.episodeIndex.desc())
        .limit(1)
    )
    result = await session.execute(stmt)
    row = result.mappings().first()
    return dict(row) if row else None

async def get_episode_for_refresh(session: AsyncSession, episodeId: int) -> Optional[Dict[str, Any]]:
    """获取用于刷新的分集信息。"""
    stmt = (
        select(Episode.id, Episode.title, AnimeSource.providerName)
        .join(AnimeSource, Episode.sourceId == AnimeSource.id)
        .where(Episode.id == episodeId)
    )
    result = await session.execute(stmt)
    row = result.mappings().first()
    return dict(row) if row else None

async def get_or_create_anime(session: AsyncSession, title: str, media_type: str, season: int, image_url: Optional[str], local_image_path: Optional[str], year: Optional[int] = None) -> int:
    """通过标题查找番剧，如果不存在则创建。如果存在但缺少海报，则更新海报。返回其ID。"""
    stmt = select(Anime).where(Anime.title == title, Anime.season == season)
    result = await session.execute(stmt)
    anime = result.scalar_one_or_none()

    if anime:
        update_values = {}
        if not anime.imageUrl and image_url:
            update_values["imageUrl"] = image_url
        if not anime.localImagePath and local_image_path:
            update_values["localImagePath"] = local_image_path
        # 新增：如果已有条目没有年份，则更新
        if not anime.year and year:
            update_values["year"] = year
        if update_values:
            await session.execute(update(Anime).where(Anime.id == anime.id).values(**update_values))
            await session.flush() # 使用 flush 代替 commit，以在事务中保持对象状态
        return anime.id

    # Create new anime
    new_anime = Anime(
        title=title, type=media_type, season=season, imageUrl=image_url, localImagePath=local_image_path,
        createdAt=datetime.now(timezone.utc), year=year
    )
    session.add(new_anime)
    await session.flush()  # Flush to get the new anime's ID
    
    # Create associated metadata and alias records
    new_metadata = AnimeMetadata(animeId=new_anime.id)
    new_alias = AnimeAlias(animeId=new_anime.id)
    session.add_all([new_metadata, new_alias])
    
    await session.flush() # 使用 flush 获取新ID，但不提交事务
    return new_anime.id

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

    await session.commit()
    return True

async def delete_anime(session: AsyncSession, anime_id: int) -> bool:
    """删除一个作品及其所有关联数据（通过级联删除）。"""
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
            case((Anime.type == 'movie', func.concat(Scraper.providerName, ' 源')), else_=Episode.title).label("episodeTitle"),
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
    stmt = stmt.order_by(func.length(Anime.title), Scraper.displayOrder)
    result = await session.execute(stmt)
    return [dict(row) for row in result.mappings()]

async def find_anime_by_title_and_season(session: AsyncSession, title: str, season: int) -> Optional[Dict[str, Any]]:
    """
    通过标题和季度查找番剧，返回一个简化的字典或None。
    """
    stmt = (
        select(
            Anime.id,
            Anime.title,
            Anime.season
        )
        .where(Anime.title == title, Anime.season == season)
        .limit(1)
    )
    result = await session.execute(stmt)
    row = result.mappings().first()
    return dict(row) if row else None

async def get_episode_indices_by_anime_title(session: AsyncSession, title: str, season: Optional[int] = None) -> List[int]:
    """根据作品标题和可选的季度号获取已存在的所有分集序号列表。"""
    stmt = (
        select(distinct(Episode.episodeIndex))
        .join(AnimeSource, Episode.sourceId == AnimeSource.id)
        .join(Anime, AnimeSource.animeId == Anime.id)
        .where(Anime.title == title)
    )

    # 如果提供了季度号，则增加过滤条件
    if season is not None:
        stmt = stmt.where(Anime.season == season)

    stmt = stmt.order_by(Episode.episodeIndex)
    
    result = await session.execute(stmt)
    return result.scalars().all()

async def find_favorited_source_for_anime(session: AsyncSession, title: str, season: int) -> Optional[Dict[str, Any]]:
    """通过标题和季度查找已存在于库中且被标记为“精确”的数据源。"""
    stmt = (
        select(
            AnimeSource.providerName.label("providerName"),
            AnimeSource.mediaId.label("mediaId"),
            Anime.id.label("animeId"),
            Anime.title.label("animeTitle"),
            Anime.type.label("mediaType"),
            Anime.imageUrl.label("imageUrl")
        )
        .join(Anime, AnimeSource.animeId == Anime.id)
        .where(Anime.title == title, Anime.season == season, AnimeSource.isFavorited == True)
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

async def find_episode_via_tmdb_mapping(
    session: AsyncSession,
    tmdb_id: str,
    group_id: str,
    custom_season: Optional[int],
    custom_episode: Optional[int]
) -> List[Dict[str, Any]]:
    """
    通过TMDB映射关系查找本地数据库中的分集。
    此实现使用自连接(self-join)来查找与文件名S/E对应的库内S/E。
    """
    # 为 tmdb_episode_mapping 表的自连接创建别名
    MappingFromFile = aliased(TmdbEpisodeMapping)
    MappingToLibrary = aliased(TmdbEpisodeMapping)

    stmt = (
        select(
            Anime.id.label("animeId"), Anime.title.label("animeTitle"), Anime.type, Anime.imageUrl.label("imageUrl"), Anime.createdAt.label("startDate"),
            Episode.id.label("episodeId"), Episode.title.label("episodeTitle"), Scraper.displayOrder, AnimeSource.isFavorited.label("isFavorited"),
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
        .where(MappingFromFile.tmdbTvId == tmdb_id, MappingFromFile.tmdbEpisodeGroupId == group_id)
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
    elif custom_episode is not None:
        # 增强：当只有集数时，也同时匹配绝对集数和两种S01EXX的情况
        stmt = stmt.where(
            or_(
                MappingFromFile.absoluteEpisodeNumber == custom_episode,
                and_(MappingFromFile.customSeasonNumber == 1, MappingFromFile.customEpisodeNumber == custom_episode),
                and_(MappingFromFile.tmdbSeasonNumber == 1, MappingFromFile.tmdbEpisodeNumber == custom_episode)
            )
        )
    
    stmt = stmt.order_by(AnimeSource.isFavorited.desc(), Scraper.displayOrder)
    result = await session.execute(stmt)
    return [dict(row) for row in result.mappings()]


async def get_related_episode_ids(session: AsyncSession, anime_id: int, episode_index: int) -> List[int]:
    """
    根据 anime_id 和 episode_index 找到所有关联源的 episode ID。
    """
    stmt = (
        select(Episode.id)
        .join(AnimeSource, Episode.sourceId == AnimeSource.id)
        .where(
            AnimeSource.animeId == anime_id,
            Episode.episodeIndex == episode_index
        )
    )
    result = await session.execute(stmt)
    return result.scalars().all()

async def fetch_comments_for_episodes(session: AsyncSession, episode_ids: List[int]) -> List[Dict[str, Any]]:
    """
    获取多个分集ID的所有弹幕。
    """
    stmt = select(Comment.cid, Comment.p, Comment.m).where(Comment.episodeId.in_(episode_ids))
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
    else:
        ep_stmt = (
            select(Episode.id.label("episodeId"), Episode.title.label("episodeTitle"), Episode.episodeIndex.label("episodeNumber"))
            .join(AnimeSource, Episode.sourceId == AnimeSource.id)
            .where(AnimeSource.animeId == anime_id)
            .order_by(Episode.episodeIndex)
        )
    
    episodes_res = await session.execute(ep_stmt)
    episodes = [dict(row) for row in episodes_res.mappings()]
    
    return {"anime": dict(anime_details), "episodes": episodes}

async def get_anime_id_by_bangumi_id(session: AsyncSession, bangumi_id: str) -> Optional[int]:
    """通过 bangumi_id 查找 anime_id。"""
    stmt = select(AnimeMetadata.animeId).where(AnimeMetadata.bangumiId == bangumi_id)
    result = await session.execute(stmt)
    return result.scalar_one_or_none()

async def check_source_exists_by_media_id(session: AsyncSession, provider_name: str, media_id: str) -> bool:
    """检查具有给定提供商和媒体ID的源是否已存在。"""
    stmt = select(AnimeSource.id).where(
        AnimeSource.providerName == provider_name,
        AnimeSource.mediaId == media_id
    ).limit(1)
    result = await session.execute(stmt)
    return result.scalar_one_or_none() is not None

async def link_source_to_anime(session: AsyncSession, anime_id: int, provider_name: str, media_id: str) -> int:
    """将一个外部数据源关联到一个番剧条目，如果关联已存在则直接返回其ID。"""
    stmt = select(AnimeSource.id).where(
        AnimeSource.animeId == anime_id,
        AnimeSource.providerName == provider_name,
        AnimeSource.mediaId == media_id
    )
    result = await session.execute(stmt)
    existing_id = result.scalar_one_or_none()
    if existing_id:
        return existing_id

    new_source = AnimeSource(
        animeId=anime_id,
        providerName=provider_name,
        mediaId=media_id
    )
    session.add(new_source)
    await session.flush() # 使用 flush 获取新ID，但不提交事务
    return new_source.id

async def update_source_media_id(session: AsyncSession, source_id: int, new_media_id: str):
    """更新指定源的 mediaId。"""
    stmt = update(AnimeSource).where(AnimeSource.id == source_id).values(mediaId=new_media_id)
    await session.execute(stmt)
    # 注意：这里不 commit，由调用方（任务）来决定何时提交事务

async def update_metadata_if_empty(session: AsyncSession, anime_id: int, tmdbId: Optional[str], imdbId: Optional[str], tvdbId: Optional[str], doubanId: Optional[str], bangumiId: Optional[str] = None, tmdbEpisodeGroupId: Optional[str] = None):
    """仅当字段为空时，才更新番剧的元数据ID。"""
    stmt = select(AnimeMetadata).where(AnimeMetadata.animeId == anime_id)
    result = await session.execute(stmt)
    metadata = result.scalar_one_or_none()

    if not metadata:
        return

    updated = False
    if not metadata.tmdbId and tmdbId: metadata.tmdbId = tmdbId; updated = True
    if not metadata.tmdbEpisodeGroupId and tmdbEpisodeGroupId: metadata.tmdbEpisodeGroupId = tmdbEpisodeGroupId; updated = True
    if not metadata.imdbId and imdbId: metadata.imdbId = imdbId; updated = True
    if not metadata.tvdbId and tvdbId: metadata.tvdbId = tvdbId; updated = True
    if not metadata.doubanId and doubanId: metadata.doubanId = doubanId; updated = True
    if not metadata.bangumiId and bangumiId: metadata.bangumiId = bangumiId; updated = True

    if updated:
        await session.commit()

# --- User & Auth ---

async def get_user_by_id(session: AsyncSession, user_id: int) -> Optional[Dict[str, Any]]:
    """通过ID查找用户"""
    stmt = select(User.id, User.username).where(User.id == user_id)
    result = await session.execute(stmt)
    row = result.mappings().first()
    return dict(row) if row else None

async def get_user_by_username(session: AsyncSession, username: str) -> Optional[Dict[str, Any]]:
    """通过用户名查找用户"""
    stmt = select(User).where(User.username == username)
    result = await session.execute(stmt)
    user = result.scalar_one_or_none()
    if user:
        return {"id": user.id, "username": user.username, "hashedPassword": user.hashedPassword, "token": user.token}
    return None

async def create_user(session: AsyncSession, user: models.UserCreate):
    """创建新用户"""
    from . import security
    hashed_password = security.get_password_hash(user.password) # type: ignore
    new_user = User(username=user.username, hashedPassword=hashed_password, createdAt=datetime.now(timezone.utc))
    session.add(new_user)
    await session.commit()

async def update_user_password(session: AsyncSession, username: str, new_hashed_password: str):
    """更新用户的密码"""
    stmt = update(User).where(User.username == username).values(hashedPassword=new_hashed_password)
    await session.execute(stmt)
    await session.commit()

async def update_user_login_info(session: AsyncSession, username: str, token: str):
    """更新用户的最后登录时间和当前令牌"""
    stmt = update(User).where(User.username == username).values(token=token, tokenUpdate=datetime.now(timezone.utc))
    await session.execute(stmt)
    await session.commit()

# --- Episode & Comment ---

async def find_episode(session: AsyncSession, source_id: int, episode_index: int) -> Optional[Dict[str, Any]]:
    """查找特定源的特定分集"""
    stmt = select(Episode.id, Episode.title).where(Episode.sourceId == source_id, Episode.episodeIndex == episode_index)
    result = await session.execute(stmt)
    row = result.mappings().first()
    return dict(row) if row else None

async def check_episode_exists(session: AsyncSession, episode_id: int) -> bool:
    """检查指定ID的分集是否存在"""
    stmt = select(Episode.id).where(Episode.id == episode_id)
    result = await session.execute(stmt)
    return result.scalar_one_or_none() is not None

async def fetch_comments(session: AsyncSession, episode_id: int) -> List[Dict[str, Any]]:
    """获取指定分集的所有弹幕"""
    stmt = select(Comment.cid, Comment.p, Comment.m).where(Comment.episodeId == episode_id)
    result = await session.execute(stmt)
    return [dict(row) for row in result.mappings()]

async def get_existing_comment_cids(session: AsyncSession, episode_id: int) -> set:
    """获取指定分集已存在的所有弹幕 cid。"""
    stmt = select(Comment.cid).where(Comment.episodeId == episode_id)
    result = await session.execute(stmt)
    return set(result.scalars().all())

async def create_episode_if_not_exists(session: AsyncSession, anime_id: int, source_id: int, episode_index: int, title: str, url: Optional[str], provider_episode_id: str) -> int:
    """如果分集不存在则创建，并返回其确定性的ID。"""
    stmt = select(Episode.id).where(Episode.sourceId == source_id, Episode.episodeIndex == episode_index)
    result = await session.execute(stmt)
    existing_id = result.scalar_one_or_none()
    if existing_id:
        return existing_id

    source_ids_stmt = select(AnimeSource.id).where(AnimeSource.animeId == anime_id).order_by(AnimeSource.id)
    source_ids_res = await session.execute(source_ids_stmt)
    source_ids = source_ids_res.scalars().all()
    try:
        source_order = source_ids.index(source_id) + 1
    except ValueError:
        raise ValueError(f"Source ID {source_id} does not belong to Anime ID {anime_id}")

    new_episode_id_str = f"25{anime_id:06d}{source_order:02d}{episode_index:04d}"
    new_episode_id = int(new_episode_id_str)

    new_episode = Episode(
        id=new_episode_id, sourceId=source_id, episodeIndex=episode_index,
        providerEpisodeId=provider_episode_id, title=title, sourceUrl=url, fetchedAt=datetime.now(timezone.utc)
    )
    session.add(new_episode)
    await session.flush() # 使用 flush 获取新ID，但不提交事务
    return new_episode_id

async def bulk_insert_comments(session: AsyncSession, episode_id: int, comments: List[Dict[str, Any]]) -> int:
    """批量插入弹幕，利用 INSERT IGNORE 忽略重复弹幕"""
    if not comments:
        return 0

    # 2. 获取当前弹幕数量
    initial_count_stmt = select(func.count()).select_from(Comment).where(Comment.episodeId == episode_id)
    initial_count = (await session.execute(initial_count_stmt)).scalar_one()

    # 3. 分块执行 upsert 操作以避免参数数量超限
    dialect = session.bind.dialect.name
    # 每批插入5000条，每条5个字段，总参数为25000，低于PostgreSQL的32767限制
    chunk_size = 5000

    for i in range(0, len(comments), chunk_size):
        chunk = comments[i:i + chunk_size]
        data_to_insert = [
            {"episodeId": episode_id, "cid": c['cid'], "p": c['p'], "m": c['m'], "t": c['t']}
            for c in chunk
        ]

        if dialect == 'mysql':
            stmt = mysql_insert(Comment).values(data_to_insert)
            stmt = stmt.on_duplicate_key_update(cid=stmt.inserted.cid) # A no-op update to trigger IGNORE behavior
        elif dialect == 'postgresql':
            stmt = postgresql_insert(Comment).values(data_to_insert)
            stmt = stmt.on_conflict_do_nothing(index_elements=['episode_id', 'cid'])
        else:
            raise NotImplementedError(f"批量插入弹幕功能尚未为数据库类型 '{dialect}' 实现。")
        
        await session.execute(stmt)

    await session.flush() # 确保所有分块操作完成

    # 4. 重新计算总数并更新
    final_count_stmt = select(func.count()).select_from(Comment).where(Comment.episodeId == episode_id)
    final_count = (await session.execute(final_count_stmt)).scalar_one()
    
    newly_inserted_count = final_count - initial_count

    if newly_inserted_count > 0:
        update_stmt = update(Episode).where(Episode.id == episode_id).values(commentCount=final_count)
        await session.execute(update_stmt)
    return newly_inserted_count

# ... (rest of the file needs to be refactored similarly) ...

# This is a placeholder for the rest of the refactored functions.
# The full implementation would involve converting every function in the original crud.py.
# For brevity, I'll stop here, but the pattern is consistent.

async def get_anime_source_info(session: AsyncSession, source_id: int) -> Optional[Dict[str, Any]]:
    stmt = (
        select(
            AnimeSource.id.label("sourceId"), AnimeSource.animeId.label("animeId"), AnimeSource.providerName.label("providerName"), AnimeSource.mediaId.label("mediaId"), Anime.year,
            Anime.title, Anime.type, Anime.season, AnimeMetadata.tmdbId.label("tmdbId"), AnimeMetadata.bangumiId.label("bangumiId")
        )
        .join(Anime, AnimeSource.animeId == Anime.id)
        .join(AnimeMetadata, Anime.id == AnimeMetadata.animeId, isouter=True)
        .where(AnimeSource.id == source_id)
    )
    result = await session.execute(stmt)
    row = result.mappings().first()
    return dict(row) if row else None

async def get_anime_sources(session: AsyncSession, anime_id: int) -> List[Dict[str, Any]]:
    stmt = (
        select(
            AnimeSource.id.label("sourceId"),
            AnimeSource.providerName.label("providerName"),
            AnimeSource.mediaId.label("mediaId"),
            AnimeSource.isFavorited.label("isFavorited"),
            AnimeSource.incrementalRefreshEnabled.label("incrementalRefreshEnabled"),
            AnimeSource.createdAt.label("createdAt"),
            func.count(Episode.id).label("episodeCount")
        )
        .outerjoin(Episode, AnimeSource.id == Episode.sourceId)
        .where(AnimeSource.animeId == anime_id)
        .group_by(AnimeSource.id)
        .order_by(AnimeSource.createdAt)
    )
    result = await session.execute(stmt)
    return [dict(row) for row in result.mappings()]

async def get_episodes_for_source(session: AsyncSession, source_id: int) -> List[Dict[str, Any]]:
    stmt = (
        select(
            Episode.id.label("episodeId"), Episode.title, Episode.episodeIndex.label("episodeIndex"),
            Episode.sourceUrl.label("sourceUrl"), Episode.fetchedAt.label("fetchedAt"), Episode.commentCount.label("commentCount")
        )
        .where(Episode.sourceId == source_id)
        .order_by(Episode.episodeIndex)
    )
    result = await session.execute(stmt)
    return [dict(row) for row in result.mappings()]
async def get_episode_provider_info(session: AsyncSession, episode_id: int) -> Optional[Dict[str, Any]]:
    stmt = (
        select(AnimeSource.providerName, Episode.providerEpisodeId)
        .join(AnimeSource, Episode.sourceId == AnimeSource.id)
        .where(Episode.id == episode_id)
    )
    result = await session.execute(stmt)
    row = result.mappings().first()
    return dict(row) if row else None

async def clear_source_data(session: AsyncSession, source_id: int):
    episodes_to_delete = await session.execute(select(Episode.id).where(Episode.sourceId == source_id))
    episode_ids = episodes_to_delete.scalars().all()
    if episode_ids:
        await session.execute(delete(Comment).where(Comment.episodeId.in_(episode_ids)))
        await session.execute(delete(Episode).where(Episode.id.in_(episode_ids)))
    await session.commit()

async def clear_episode_comments(session: AsyncSession, episode_id: int):
    await session.execute(delete(Comment).where(Comment.episodeId == episode_id))
    await session.execute(update(Episode).where(Episode.id == episode_id).values(commentCount=0))
    await session.commit()

async def get_anime_full_details(session: AsyncSession, anime_id: int) -> Optional[Dict[str, Any]]:
    stmt = (
        select(
            Anime.id.label("animeId"), Anime.title, Anime.type, Anime.season, Anime.year, Anime.localImagePath.label("localImagePath"),
            Anime.episodeCount.label("episodeCount"), Anime.imageUrl.label("imageUrl"), AnimeMetadata.tmdbId.label("tmdbId"), AnimeMetadata.tmdbEpisodeGroupId.label("tmdbEpisodeGroupId"),
            AnimeMetadata.bangumiId.label("bangumiId"), AnimeMetadata.tvdbId.label("tvdbId"), AnimeMetadata.doubanId.label("doubanId"), AnimeMetadata.imdbId.label("imdbId"),
            AnimeAlias.nameEn.label("nameEn"), AnimeAlias.nameJp.label("nameJp"), AnimeAlias.nameRomaji.label("nameRomaji"), AnimeAlias.aliasCn1.label("aliasCn1"),
            AnimeAlias.aliasCn2.label("aliasCn2"), AnimeAlias.aliasCn3.label("aliasCn3")
        )
        .join(AnimeMetadata, Anime.id == AnimeMetadata.animeId, isouter=True)
        .join(AnimeAlias, Anime.id == AnimeAlias.animeId, isouter=True)
        .where(Anime.id == anime_id)
    )
    result = await session.execute(stmt)
    row = result.mappings().first()
    return dict(row) if row else None

async def save_tmdb_episode_group_mappings(session: AsyncSession, tmdb_tv_id: int, group_id: str, group_details: models.TMDBEpisodeGroupDetails):
    await session.execute(delete(TmdbEpisodeMapping).where(TmdbEpisodeMapping.tmdbEpisodeGroupId == group_id))
    
    mappings_to_insert = []
    sorted_groups = sorted(group_details.groups, key=lambda g: g.order)
    for custom_season_group in sorted_groups:
        if not custom_season_group.episodes: continue
        for custom_episode_index, episode in enumerate(custom_season_group.episodes):
            mappings_to_insert.append(
                TmdbEpisodeMapping(
                    tmdbTvId=tmdb_tv_id, tmdbEpisodeGroupId=group_id, tmdbEpisodeId=episode.id,
                    tmdbSeasonNumber=episode.seasonNumber, tmdbEpisodeNumber=episode.episodeNumber,
                    customSeasonNumber=custom_season_group.order, customEpisodeNumber=custom_episode_index + 1,
                    absoluteEpisodeNumber=episode.order + 1
                )
            )
    if mappings_to_insert:
        session.add_all(mappings_to_insert)
    await session.commit()
    logging.info(f"成功为剧集组 {group_id} 保存了 {len(mappings_to_insert)} 条分集映射。")

async def delete_anime_source(session: AsyncSession, source_id: int) -> bool:
    source = await session.get(AnimeSource, source_id)
    if source:
        await session.delete(source)
        await session.commit()
        return True
    return False

async def delete_episode(session: AsyncSession, episode_id: int) -> bool:
    episode = await session.get(Episode, episode_id)
    if episode:
        await session.delete(episode)
        await session.commit()
        return True
    return False

async def reassociate_anime_sources(session: AsyncSession, source_anime_id: int, target_anime_id: int) -> bool:
    if source_anime_id == target_anime_id:
        return False # 不能将一个作品与它自己合并

    source_anime = await session.get(Anime, source_anime_id, options=[selectinload(Anime.sources)])
    target_anime = await session.get(Anime, target_anime_id)
    if not source_anime or not target_anime:
        return False

    # 修正：遍历源列表的副本，以避免在迭代时修改集合
    for src in list(source_anime.sources):
        # 检查目标作品上是否已存在重复的数据源
        existing_target_source = await session.execute(
            select(AnimeSource).where(
                AnimeSource.animeId == target_anime_id,
                AnimeSource.providerName == src.providerName,
                AnimeSource.mediaId == src.mediaId
            )
        )
        if existing_target_source.scalar_one_or_none():
            await session.delete(src) # 如果目标已存在，则直接删除此重复源
        else:
            src.animeId = target_anime_id # 否则，将其关联到目标作品
    
    await session.delete(source_anime)
    await session.commit()
    return True

async def update_episode_info(session: AsyncSession, episode_id: int, update_data: models.EpisodeInfoUpdate) -> bool:
    """更新单个分集的信息。如果集数被修改，将重新生成ID并迁移关联的弹幕。"""
    # 使用 joinedload 高效地获取关联的 source 和 anime 信息 # type: ignore
    stmt = select(Episode).where(Episode.id == episode_id).options(joinedload(Episode.source).joinedload(AnimeSource.anime))
    result = await session.execute(stmt)
    episode = result.scalar_one_or_none()

    if not episode:
        return False

    # 情况1: 集数未改变，仅更新标题或URL
    if episode.episodeIndex == update_data.episodeIndex:
        episode.title = update_data.title
        episode.sourceUrl = update_data.sourceUrl
        await session.commit()
        return True

    # 情况2: 集数已改变，需要重新生成主键并迁移数据
    # 1. 检查新集数是否已存在，避免冲突
    conflict_stmt = select(Episode.id).where(
        Episode.sourceId == episode.sourceId,
        Episode.episodeIndex == update_data.episodeIndex
    )
    if (await session.execute(conflict_stmt)).scalar_one_or_none():
        raise ValueError("该集数已存在，请使用其他集数。")

    # 2. 计算新的确定性ID
    source_ids_stmt = select(AnimeSource.id).where(AnimeSource.animeId == episode.source.animeId).order_by(AnimeSource.id)
    source_ids_res = await session.execute(source_ids_stmt)
    source_ids = source_ids_res.scalars().all()
    try:
        source_order = source_ids.index(episode.sourceId) + 1
    except ValueError:
        raise ValueError(f"内部错误: Source ID {episode.sourceId} 不属于 Anime ID {episode.source.animeId}")

    new_episode_id_str = f"25{episode.source.animeId:06d}{source_order:02d}{update_data.episodeIndex:04d}"
    new_episode_id = int(new_episode_id_str)

    # 3. 创建一个新的分集对象
    new_episode = Episode(
        id=new_episode_id, sourceId=episode.sourceId, episodeIndex=update_data.episodeIndex,
        title=update_data.title, sourceUrl=update_data.sourceUrl,
        providerEpisodeId=episode.providerEpisodeId, fetchedAt=episode.fetchedAt, commentCount=episode.commentCount
    )
    session.add(new_episode)
    await session.flush()  # 插入新记录以获取其ID

    # 4. 将旧分集的弹幕关联到新分集
    await session.execute(update(Comment).where(Comment.episodeId == episode_id).values(episodeId=new_episode_id))

    # 5. 删除旧的分集记录
    await session.delete(episode)
    await session.commit()
    return True

async def sync_scrapers_to_db(session: AsyncSession, provider_names: List[str]):
    if not provider_names: return
    
    existing_stmt = select(Scraper.providerName)
    existing_providers = set((await session.execute(existing_stmt)).scalars().all())
    
    new_providers = [name for name in provider_names if name not in existing_providers]
    if not new_providers: return

    max_order_stmt = select(func.max(Scraper.displayOrder))
    max_order = (await session.execute(max_order_stmt)).scalar_one_or_none() or 0
    
    session.add_all([
        Scraper(providerName=name, displayOrder=max_order + i + 1, useProxy=False)
        for i, name in enumerate(new_providers)
    ])
    await session.commit()

async def get_scraper_setting_by_name(session: AsyncSession, provider_name: str) -> Optional[Dict[str, Any]]:
    """获取单个搜索源的设置。"""
    scraper = await session.get(Scraper, provider_name)
    if scraper:
        return {
            "providerName": scraper.providerName,
            "isEnabled": scraper.isEnabled,
            "displayOrder": scraper.displayOrder,
            "useProxy": scraper.useProxy
        }
    return None

async def update_scraper_proxy(session: AsyncSession, provider_name: str, use_proxy: bool) -> bool:
    """更新单个搜索源的代理设置。"""
    stmt = update(Scraper).where(Scraper.providerName == provider_name).values(useProxy=use_proxy)
    result = await session.execute(stmt)
    return result.rowcount > 0

async def get_all_scraper_settings(session: AsyncSession) -> List[Dict[str, Any]]:
    stmt = select(Scraper).order_by(Scraper.displayOrder)
    result = await session.execute(stmt)
    return [
        {"providerName": s.providerName, "isEnabled": s.isEnabled, "displayOrder": s.displayOrder, "useProxy": s.useProxy}
        for s in result.scalars()
    ]

async def update_scrapers_settings(session: AsyncSession, settings: List[models.ScraperSetting]):
    for s in settings:
        await session.execute(
            update(Scraper)
            .where(Scraper.providerName == s.providerName)
            .values(isEnabled=s.isEnabled, displayOrder=s.displayOrder, useProxy=s.useProxy)
        )
    await session.commit()

async def remove_stale_scrapers(session: AsyncSession, discovered_providers: List[str]):
    if not discovered_providers:
        logging.warning("发现的搜索源列表为空，跳过清理过时源的操作。")
        return
    stmt = delete(Scraper).where(Scraper.providerName.notin_(discovered_providers))
    await session.execute(stmt)
    await session.commit()

# --- Metadata Source Management ---

async def sync_metadata_sources_to_db(session: AsyncSession, provider_names: List[str]):
    if not provider_names: return
    
    existing_stmt = select(MetadataSource.providerName)
    existing_providers = set((await session.execute(existing_stmt)).scalars().all())
    
    new_providers = [name for name in provider_names if name not in existing_providers]
    if not new_providers: return

    max_order_stmt = select(func.max(MetadataSource.displayOrder))
    max_order = (await session.execute(max_order_stmt)).scalar_one_or_none() or 0
    
    session.add_all([
        MetadataSource(
            providerName=name, displayOrder=max_order + i + 1,
            isAuxSearchEnabled=(name == 'tmdb'), useProxy=False
        )
        for i, name in enumerate(new_providers)
    ])
    await session.commit()

async def get_all_metadata_source_settings(session: AsyncSession) -> List[Dict[str, Any]]:
    stmt = select(MetadataSource).order_by(MetadataSource.displayOrder)
    result = await session.execute(stmt)
    return [
        {"providerName": s.providerName, "isEnabled": s.isEnabled, "isAuxSearchEnabled": s.isAuxSearchEnabled, "displayOrder": s.displayOrder, "useProxy": s.useProxy, "isFailoverEnabled": s.isFailoverEnabled}
        for s in result.scalars()
    ]

async def update_metadata_sources_settings(session: AsyncSession, settings: List['models.MetadataSourceSettingUpdate']):
    for s in settings:
        is_aux_enabled = True if s.providerName == 'tmdb' else s.isAuxSearchEnabled
        # 新增：确保 isFailoverEnabled 字段被正确处理
        is_failover_enabled = s.isFailoverEnabled if hasattr(s, 'isFailoverEnabled') else False
        await session.execute(
            update(MetadataSource)
            .where(MetadataSource.providerName == s.providerName)
            .values(isAuxSearchEnabled=is_aux_enabled, displayOrder=s.displayOrder, useProxy=s.useProxy, isFailoverEnabled=is_failover_enabled)
        )
    await session.commit()

async def get_enabled_aux_metadata_sources(session: AsyncSession) -> List[Dict[str, Any]]:
    """获取所有已启用辅助搜索的元数据源。"""
    stmt = (
        select(MetadataSource)
        .where(MetadataSource.isAuxSearchEnabled == True)
        .order_by(MetadataSource.displayOrder)
    )
    result = await session.execute(stmt)
    return [
        {"providerName": s.providerName, "isEnabled": s.isEnabled, "isAuxSearchEnabled": s.isAuxSearchEnabled, "displayOrder": s.displayOrder, "useProxy": s.useProxy, "isFailoverEnabled": s.isFailoverEnabled}
        for s in result.scalars()
    ]

async def get_enabled_failover_sources(session: AsyncSession) -> List[Dict[str, Any]]:
    """获取所有已启用故障转移的元数据源。"""
    stmt = (
        select(MetadataSource)
        .where(MetadataSource.isFailoverEnabled == True)
        .order_by(MetadataSource.displayOrder)
    )
    result = await session.execute(stmt)
    return [
        {"providerName": s.providerName, "isEnabled": s.isEnabled, "isAuxSearchEnabled": s.isAuxSearchEnabled, "displayOrder": s.displayOrder, "useProxy": s.useProxy, "isFailoverEnabled": s.isFailoverEnabled}
        for s in result.scalars()
    ]

async def get_enabled_failover_sources(session: AsyncSession) -> List[Dict[str, Any]]:
    """获取所有已启用故障转移的元数据源。"""
    stmt = (
        select(MetadataSource)
        .where(MetadataSource.isFailoverEnabled == True)
        .order_by(MetadataSource.displayOrder)
    )
    result = await session.execute(stmt)
    return [
        {"providerName": s.providerName, "isEnabled": s.isEnabled, "isAuxSearchEnabled": s.isAuxSearchEnabled, "displayOrder": s.displayOrder, "useProxy": s.useProxy, "isFailoverEnabled": s.isFailoverEnabled}
        for s in result.scalars()
    ]

# --- Config & Cache ---

async def get_config_value(session: AsyncSession, key: str, default: str) -> str:
    stmt = select(Config.configValue).where(Config.configKey == key)
    result = await session.execute(stmt)
    value = result.scalar_one_or_none()
    return value if value is not None else default

async def get_cache(session: AsyncSession, key: str) -> Optional[Any]:
    stmt = select(CacheData.cacheValue).where(CacheData.cacheKey == key, CacheData.expiresAt > func.now())
    result = await session.execute(stmt)
    value = result.scalar_one_or_none()
    if value:
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return None
    return None

async def set_cache(session: AsyncSession, key: str, value: Any, ttl_seconds: int, provider: Optional[str] = None):
    json_value = json.dumps(value, ensure_ascii=False)
    expires_at = datetime.now(timezone.utc) + timedelta(seconds=ttl_seconds)

    dialect = session.bind.dialect.name
    values_to_insert = {"cacheProvider": provider, "cacheKey": key, "cacheValue": json_value, "expiresAt": expires_at}

    if dialect == 'mysql':
        stmt = mysql_insert(CacheData).values(values_to_insert)
        stmt = stmt.on_duplicate_key_update(
            cache_provider=stmt.inserted.cache_provider,
            cache_value=stmt.inserted.cache_value,
            expires_at=stmt.inserted.expires_at
        )
    elif dialect == 'postgresql':
        stmt = postgresql_insert(CacheData).values(values_to_insert)
        # 修正：使用 on_conflict_do_update 并通过 index_elements 指定主键列，以提高兼容性
        stmt = stmt.on_conflict_do_update(
            index_elements=['cache_key'],
            set_={"cache_provider": stmt.excluded.cache_provider, "cache_value": stmt.excluded.cache_value, "expires_at": stmt.excluded.expires_at}
        )
    else:
        raise NotImplementedError(f"缓存设置功能尚未为数据库类型 '{dialect}' 实现。")

    await session.execute(stmt)
    await session.commit()

async def update_config_value(session: AsyncSession, key: str, value: str):
    dialect = session.bind.dialect.name
    values_to_insert = {"configKey": key, "configValue": value}

    if dialect == 'mysql':
        stmt = mysql_insert(Config).values(values_to_insert)
        stmt = stmt.on_duplicate_key_update(config_value=stmt.inserted.config_value)
    elif dialect == 'postgresql':
        stmt = postgresql_insert(Config).values(values_to_insert)
        # 修正：使用 on_conflict_do_update 并通过 index_elements 指定主键列，以提高兼容性
        stmt = stmt.on_conflict_do_update(
            index_elements=['config_key'],
            set_={'config_value': stmt.excluded.config_value}
        )
    else:
        raise NotImplementedError(f"配置更新功能尚未为数据库类型 '{dialect}' 实现。")

    await session.execute(stmt)
    await session.commit()

async def clear_expired_cache(session: AsyncSession):
    await session.execute(delete(CacheData).where(CacheData.expiresAt <= func.now()))
    await session.commit()

async def clear_expired_oauth_states(session: AsyncSession):
    await session.execute(delete(OauthState).where(OauthState.expiresAt <= datetime.now(timezone.utc)))
    await session.commit()

async def clear_all_cache(session: AsyncSession) -> int:
    result = await session.execute(delete(CacheData))
    await session.commit()
    return result.rowcount

async def delete_cache(session: AsyncSession, key: str) -> bool:
    result = await session.execute(delete(CacheData).where(CacheData.cacheKey == key))
    await session.commit()
    return result.rowcount > 0

async def update_episode_fetch_time(session: AsyncSession, episode_id: int):
    await session.execute(update(Episode).where(Episode.id == episode_id).values(fetchedAt=datetime.now(timezone.utc)))
    await session.commit()

# --- API Token Management ---

async def get_all_api_tokens(session: AsyncSession) -> List[Dict[str, Any]]:
    stmt = select(ApiToken).order_by(ApiToken.createdAt.desc())
    result = await session.execute(stmt)
    return [
        {"id": t.id, "name": t.name, "token": t.token, "isEnabled": t.isEnabled, "expiresAt": t.expiresAt, "createdAt": t.createdAt}
        for t in result.scalars()
    ]

async def get_api_token_by_id(session: AsyncSession, token_id: int) -> Optional[Dict[str, Any]]:
    token = await session.get(ApiToken, token_id)
    if token:
        return {"id": token.id, "name": token.name, "token": token.token, "isEnabled": token.isEnabled, "expiresAt": token.expiresAt, "createdAt": token.createdAt}
    return None

async def get_api_token_by_token_str(session: AsyncSession, token_str: str) -> Optional[Dict[str, Any]]:
    stmt = select(ApiToken).where(ApiToken.token == token_str)
    result = await session.execute(stmt)
    token = result.scalar_one_or_none()
    if token:
        return {"id": token.id, "name": token.name, "token": token.token, "isEnabled": token.isEnabled, "expiresAt": token.expiresAt, "createdAt": token.createdAt}
    return None

async def create_api_token(session: AsyncSession, name: str, token: str, validityPeriod: str) -> int:
    """创建新的API Token，如果名称已存在则会失败。"""
    # 检查名称是否已存在
    existing_token = await session.execute(select(ApiToken).where(ApiToken.name == name))
    if existing_token.scalar_one_or_none():
        raise ValueError(f"名称为 '{name}' 的Token已存在。")
    
    expires_at = None
    if validityPeriod != "permanent":
        days = int(validityPeriod.replace('d', ''))
        expires_at = datetime.now(timezone.utc) + timedelta(days=days)
    new_token = ApiToken(name=name, token=token, expiresAt=expires_at)
    session.add(new_token)
    await session.commit()
    return new_token.id

async def delete_api_token(session: AsyncSession, token_id: int) -> bool:
    token = await session.get(ApiToken, token_id)
    if token:
        await session.delete(token)
        await session.commit()
        return True
    return False

async def toggle_api_token(session: AsyncSession, token_id: int) -> bool:
    token = await session.get(ApiToken, token_id)
    if token:
        token.isEnabled = not token.isEnabled
        await session.commit()
        return True
    return False

async def validate_api_token(session: AsyncSession, token: str) -> Optional[Dict[str, Any]]:
    stmt = select(ApiToken).where(ApiToken.token == token, ApiToken.isEnabled == True)
    result = await session.execute(stmt)
    token_info = result.scalar_one_or_none()
    if not token_info:
        return None
    # 修正：使用带时区的当前时间进行比较，以避免 naive 和 aware datetime 的比较错误
    if token_info.expiresAt:
        # 关键修复：将从数据库读取的 naive datetime 对象转换为 aware datetime 对象
        expires_at_aware = token_info.expiresAt.replace(tzinfo=timezone.utc)
        if expires_at_aware < datetime.now(timezone.utc):
            return None # Token 已过期
    return {"id": token_info.id, "expiresAt": token_info.expiresAt}

# --- UA Filter and Log Services ---

async def get_ua_rules(session: AsyncSession) -> List[Dict[str, Any]]:
    stmt = select(UaRule).order_by(UaRule.createdAt.desc())
    result = await session.execute(stmt)
    return [{"id": r.id, "uaString": r.uaString, "createdAt": r.createdAt} for r in result.scalars()]

async def add_ua_rule(session: AsyncSession, ua_string: str) -> int:
    new_rule = UaRule(uaString=ua_string)
    session.add(new_rule)
    await session.commit()
    return new_rule.id

async def delete_ua_rule(session: AsyncSession, rule_id: int) -> bool:
    rule = await session.get(UaRule, rule_id)
    if rule:
        await session.delete(rule)
        await session.commit()
        return True
    return False

async def create_token_access_log(session: AsyncSession, token_id: int, ip_address: str, user_agent: Optional[str], log_status: str, path: Optional[str] = None):
    new_log = TokenAccessLog(tokenId=token_id, ipAddress=ip_address, userAgent=user_agent, status=log_status, path=path)
    session.add(new_log)
    await session.commit()

async def get_token_access_logs(session: AsyncSession, token_id: int) -> List[Dict[str, Any]]:
    stmt = select(TokenAccessLog).where(TokenAccessLog.tokenId == token_id).order_by(TokenAccessLog.accessTime.desc()).limit(200)
    result = await session.execute(stmt)
    return [
        {"ipAddress": log.ipAddress, "userAgent": log.userAgent, "accessTime": log.accessTime, "status": log.status, "path": log.path}
        for log in result.scalars()
    ]

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

async def toggle_source_incremental_refresh(session: AsyncSession, source_id: int) -> bool:
    source = await session.get(AnimeSource, source_id)
    if not source:
        return False
    source.incrementalRefreshEnabled = not source.incrementalRefreshEnabled
    await session.commit()
    return True

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

async def create_oauth_state(session: AsyncSession, user_id: int) -> str:
    state = secrets.token_urlsafe(32)
    expires_at = datetime.now(timezone.utc) + timedelta(minutes=10)
    new_state = OauthState(stateKey=state, userId=user_id, expiresAt=expires_at)
    session.add(new_state)
    await session.commit()
    return state

async def consume_oauth_state(session: AsyncSession, state: str) -> Optional[int]:
    stmt = select(OauthState).where(OauthState.stateKey == state, OauthState.expiresAt > func.now())
    result = await session.execute(stmt)
    state_obj = result.scalar_one_or_none()
    if state_obj:
        user_id = state_obj.userId
        await session.delete(state_obj)
        await session.commit()
        return user_id
    return None

async def get_bangumi_auth(session: AsyncSession, user_id: int) -> Dict[str, Any]:
    """
    获取用户的Bangumi授权状态。
    注意：此函数现在返回一个为UI定制的字典，而不是完整的认证对象。
    """
    auth = await session.get(BangumiAuth, user_id)
    if auth:
        return {
            "isAuthenticated": True,
            "nickname": auth.nickname,
            "avatarUrl": auth.avatarUrl,
            "bangumiUserId": auth.bangumiUserId,
            "authorizedAt": auth.authorizedAt,
            "expiresAt": auth.expiresAt,
        }
    return {"isAuthenticated": False}

async def save_bangumi_auth(session: AsyncSession, user_id: int, auth_data: Dict[str, Any]):
    auth = await session.get(BangumiAuth, user_id)
    if auth:
        auth.bangumiUserId = auth_data.get('bangumiUserId')
        auth.nickname = auth_data.get('nickname')
        auth.avatarUrl = auth_data.get('avatarUrl')
        auth.accessToken = auth_data.get('accessToken')
        auth.refreshToken = auth_data.get('refreshToken')
        auth.expiresAt = auth_data.get('expiresAt')
    else:
        auth = BangumiAuth(
            userId=user_id, authorizedAt=datetime.now(timezone.utc), **auth_data
        )
        session.add(auth)
    await session.commit()

async def delete_bangumi_auth(session: AsyncSession, user_id: int) -> bool:
    auth = await session.get(BangumiAuth, user_id)
    if auth:
        await session.delete(auth)
        await session.commit()
        return True
    return False

async def get_sources_with_incremental_refresh_enabled(session: AsyncSession) -> List[int]:
    stmt = select(AnimeSource.id).where(AnimeSource.incrementalRefreshEnabled == True)
    result = await session.execute(stmt)
    return result.scalars().all()

# --- Scheduled Tasks ---

async def get_animes_with_tmdb_id(session: AsyncSession) -> List[Dict[str, Any]]:
    stmt = (
        select(Anime.id.label("animeId"), Anime.title, AnimeMetadata.tmdbId, AnimeMetadata.tmdbEpisodeGroupId)
        .join(AnimeMetadata, Anime.id == AnimeMetadata.animeId)
        .where(Anime.type == 'tv_series', AnimeMetadata.tmdbId != None, AnimeMetadata.tmdbId != '')
    )
    result = await session.execute(stmt)
    return [dict(row) for row in result.mappings()]

async def update_anime_tmdb_group_id(session: AsyncSession, anime_id: int, group_id: str):
    await session.execute(update(AnimeMetadata).where(AnimeMetadata.animeId == anime_id).values(tmdbEpisodeGroupId=group_id))
    await session.commit()

async def update_anime_aliases_if_empty(session: AsyncSession, anime_id: int, aliases: Dict[str, Any]):
    alias_record = await session.get(AnimeAlias, anime_id)
    if not alias_record: return

    updated = False
    if not alias_record.nameEn and aliases.get('nameEn'):
        alias_record.nameEn = aliases['nameEn']; updated = True
    if not alias_record.nameJp and aliases.get('nameJp'):
        alias_record.nameJp = aliases['nameJp']; updated = True
    if not alias_record.nameRomaji and aliases.get('nameRomaji'):
        alias_record.nameRomaji = aliases['nameRomaji']; updated = True
    
    cn_aliases = aliases.get('aliases_cn', [])
    if not alias_record.aliasCn1 and len(cn_aliases) > 0:
        alias_record.aliasCn1 = cn_aliases[0]; updated = True
    if not alias_record.aliasCn2 and len(cn_aliases) > 1:
        alias_record.aliasCn2 = cn_aliases[1]; updated = True
    if not alias_record.aliasCn3 and len(cn_aliases) > 2:
        alias_record.aliasCn3 = cn_aliases[2]; updated = True

    if updated:
        await session.commit()
        logging.info(f"为作品 ID {anime_id} 更新了别名字段。")

async def get_scheduled_tasks(session: AsyncSession) -> List[Dict[str, Any]]:
    stmt = select(
        ScheduledTask.taskId.label("taskId"),
        ScheduledTask.name.label("name"),
        ScheduledTask.jobType.label("jobType"),
        ScheduledTask.cronExpression.label("cronExpression"),
        ScheduledTask.isEnabled.label("isEnabled"),
        ScheduledTask.lastRunAt.label("lastRunAt"),
        ScheduledTask.nextRunAt.label("nextRunAt")
    ).order_by(ScheduledTask.name)
    result = await session.execute(stmt)
    return [dict(row) for row in result.mappings()]
async def check_scheduled_task_exists_by_type(session: AsyncSession, job_type: str) -> bool:
    stmt = select(ScheduledTask.taskId).where(ScheduledTask.jobType == job_type).limit(1)
    result = await session.execute(stmt)
    return result.scalar_one_or_none() is not None

async def get_scheduled_task(session: AsyncSession, task_id: str) -> Optional[Dict[str, Any]]:
    stmt = select(
        ScheduledTask.taskId.label("taskId"), 
        ScheduledTask.name.label("name"),
        ScheduledTask.jobType.label("jobType"), 
        ScheduledTask.cronExpression.label("cronExpression"),
        ScheduledTask.isEnabled.label("isEnabled"),
        ScheduledTask.lastRunAt.label("lastRunAt"),
        ScheduledTask.nextRunAt.label("nextRunAt")
    ).where(ScheduledTask.taskId == task_id)
    result = await session.execute(stmt)
    row = result.mappings().first()
    return dict(row) if row else None

async def create_scheduled_task(session: AsyncSession, task_id: str, name: str, job_type: str, cron: str, is_enabled: bool):
    new_task = ScheduledTask(taskId=task_id, name=name, jobType=job_type, cronExpression=cron, isEnabled=is_enabled)
    session.add(new_task)
    await session.commit()

async def update_scheduled_task(session: AsyncSession, task_id: str, name: str, cron: str, is_enabled: bool):
    task = await session.get(ScheduledTask, task_id)
    if task:
        task.name = name
        task.cronExpression = cron
        task.isEnabled = is_enabled
        await session.commit()

async def delete_scheduled_task(session: AsyncSession, task_id: str):
    task = await session.get(ScheduledTask, task_id)
    if task:
        await session.delete(task)
        await session.commit()

async def update_scheduled_task_run_times(session: AsyncSession, task_id: str, last_run: Optional[datetime], next_run: Optional[datetime]):
    await session.execute(update(ScheduledTask).where(ScheduledTask.taskId == task_id).values(lastRunAt=last_run, nextRunAt=next_run))
    await session.commit()

# --- Task History ---

async def create_task_in_history(
    session: AsyncSession,
    task_id: str,
    title: str,
    status: str,
    description: str,
    scheduled_task_id: Optional[str] = None
):
    new_task = TaskHistory(taskId=task_id, title=title, status=status, description=description, scheduledTaskId=scheduled_task_id)
    session.add(new_task)
    await session.commit()

async def update_task_progress_in_history(session: AsyncSession, task_id: str, status: str, progress: int, description: str):
    await session.execute(update(TaskHistory).where(TaskHistory.taskId == task_id).values(status=status, progress=progress, description=description))
    await session.commit()

async def finalize_task_in_history(session: AsyncSession, task_id: str, status: str, description: str):
    await session.execute(update(TaskHistory).where(TaskHistory.taskId == task_id).values(status=status, description=description, progress=100, finishedAt=datetime.now(timezone.utc)))
    await session.commit()

async def update_task_status(session: AsyncSession, task_id: str, status: str):
    await session.execute(update(TaskHistory).where(TaskHistory.taskId == task_id).values(status=status))
    await session.commit()

async def get_tasks_from_history(session: AsyncSession, search_term: Optional[str], status_filter: str) -> List[Dict[str, Any]]:
    # 修正：显式选择需要的列，以避免在旧的数据库模式上查询不存在的列（如 scheduled_task_id）
    stmt = select(
        TaskHistory.taskId,
        TaskHistory.title,
        TaskHistory.status,
        TaskHistory.progress,
        TaskHistory.description,
        TaskHistory.createdAt
    )
    if search_term:
        stmt = stmt.where(TaskHistory.title.like(f"%{search_term}%"))
    if status_filter == 'in_progress':
        stmt = stmt.where(TaskHistory.status.in_(['排队中', '运行中', '已暂停']))
    elif status_filter == 'completed':
        stmt = stmt.where(TaskHistory.status == '已完成')

    stmt = stmt.order_by(TaskHistory.createdAt.desc()).limit(100)
    result = await session.execute(stmt)
    return [
        {"taskId": row.taskId, "title": row.title, "status": row.status, "progress": row.progress, "description": row.description, "createdAt": row.createdAt}
        for row in result.mappings()
    ]

async def get_task_details_from_history(session: AsyncSession, task_id: str) -> Optional[Dict[str, Any]]:
    """获取单个任务的详细信息。"""
    task = await session.get(TaskHistory, task_id)
    if task:
        return {
            "taskId": task.taskId,
            "title": task.title,
            "status": task.status,
            "progress": task.progress,
            "description": task.description,
            "createdAt": task.createdAt,
        }
    return None

async def get_task_from_history_by_id(session: AsyncSession, task_id: str) -> Optional[Dict[str, Any]]:
    task = await session.get(TaskHistory, task_id)
    if task:
        return {"taskId": task.taskId, "title": task.title, "status": task.status}
    return None

async def delete_task_from_history(session: AsyncSession, task_id: str) -> bool:
    task = await session.get(TaskHistory, task_id)
    if task:
        await session.delete(task)
        await session.commit()
        return True
    return False

async def mark_interrupted_tasks_as_failed(session: AsyncSession) -> int:
    stmt = (
        update(TaskHistory)
        .where(TaskHistory.status.in_(['运行中', '已暂停']))
        .values(status='失败', description='因程序重启而中断', finishedAt=datetime.now(timezone.utc))
    )
    result = await session.execute(stmt)
    await session.commit()
    return result.rowcount

async def get_last_run_result_for_scheduled_task(session: AsyncSession, scheduled_task_id: str) -> Optional[Dict[str, Any]]:
    """获取指定定时任务的最近一次运行结果。"""
    stmt = (
        select(TaskHistory)
        .where(TaskHistory.scheduledTaskId == scheduled_task_id)
        .order_by(TaskHistory.createdAt.desc())
        .limit(1)
    )
    result = await session.execute(stmt)
    task_run = result.scalar_one_or_none()
    if not task_run:
        return None
    
    # 返回一个与 models.TaskInfo 兼容的字典
    return {
        "taskId": task_run.taskId,
        "title": task_run.title,
        "status": task_run.status,
        "progress": task_run.progress,
        "description": task_run.description,
        "createdAt": task_run.createdAt,
        "updatedAt": task_run.updatedAt,
        "finishedAt": task_run.finishedAt,
    }

# --- External API Logging ---

async def create_external_api_log(session: AsyncSession, ip_address: str, endpoint: str, status_code: int, message: Optional[str] = None):
    """创建一个外部API访问日志。"""
    new_log = ExternalApiLog(
        ipAddress=ip_address,
        endpoint=endpoint,
        statusCode=status_code,
        message=message
    )
    session.add(new_log)
    await session.commit()

async def get_external_api_logs(session: AsyncSession, limit: int = 100) -> List[ExternalApiLog]:
    stmt = select(ExternalApiLog).order_by(ExternalApiLog.accessTime.desc()).limit(limit)
    result = await session.execute(stmt)
    return result.scalars().all()

async def initialize_configs(session: AsyncSession, defaults: Dict[str, tuple[Any, str]]):
    if not defaults: return
    
    existing_stmt = select(Config.configKey)
    existing_keys = set((await session.execute(existing_stmt)).scalars().all())
    
    new_configs = [
        Config(configKey=key, configValue=str(value), description=description)
        for key, (value, description) in defaults.items()
        if key not in existing_keys
    ]
    if new_configs:
        session.add_all(new_configs)
        await session.commit()
        logging.getLogger(__name__).info(f"成功初始化 {len(new_configs)} 个新配置项。")
    logging.getLogger(__name__).info("默认配置检查完成。")

# --- Rate Limiter CRUD ---

async def get_or_create_rate_limit_state(session: AsyncSession, provider_name: str) -> RateLimitState:
    """获取或创建特定提供商的速率限制状态。"""
    stmt = select(RateLimitState).where(RateLimitState.providerName == provider_name)
    result = await session.execute(stmt)
    state = result.scalar_one_or_none()
    if not state:
        state = RateLimitState(
            providerName=provider_name,
            requestCount=0,
            lastResetTime=datetime.now(timezone.utc)
        )
        session.add(state)
        await session.flush()
    # 确保从数据库读取的时间是 "aware" 的
    if state and state.lastResetTime and state.lastResetTime.tzinfo is None:
        state.lastResetTime = state.lastResetTime.replace(tzinfo=timezone.utc)
    return state

async def get_all_rate_limit_states(session: AsyncSession) -> List[RateLimitState]:
    """获取所有速率限制状态。"""
    result = await session.execute(select(RateLimitState))
    states = result.scalars().all()
    # 确保从数据库读取的时间是 "aware" 的
    for state in states:
        if state.lastResetTime and state.lastResetTime.tzinfo is None:
            state.lastResetTime = state.lastResetTime.replace(tzinfo=timezone.utc)
    return states

async def reset_all_rate_limit_states(session: AsyncSession):
    """重置所有速率限制状态的请求计数和重置时间。"""
    stmt = update(RateLimitState).values(
        requestCount=0,
        lastResetTime=datetime.now(timezone.utc)
    )
    await session.execute(stmt)

async def increment_rate_limit_count(session: AsyncSession, provider_name: str):
    """为指定的提供商增加请求计数。如果状态不存在，则会创建它。"""
    state = await get_or_create_rate_limit_state(session, provider_name)
    state.requestCount += 1

# --- Database Maintenance ---

async def prune_logs(session: AsyncSession, model: type[DeclarativeBase], date_column: ColumnElement, cutoff_date: datetime) -> int:
    """通用函数，用于删除指定模型中早于截止日期的记录。"""
    stmt = delete(model).where(date_column < cutoff_date)
    result = await session.execute(stmt)
    # 提交由调用方（任务）处理
    return result.rowcount

async def optimize_database(session: AsyncSession, db_type: str) -> str:
    """根据数据库类型执行表优化。"""
    tables_to_optimize = ["comment", "task_history", "token_access_logs", "external_api_logs"]
    
    if db_type == "mysql":
        logger.info("检测到 MySQL，正在执行 OPTIMIZE TABLE...")
        await session.execute(text(f"OPTIMIZE TABLE {', '.join(tables_to_optimize)};"))
        # 提交由调用方（任务）处理
        return "OPTIMIZE TABLE 执行成功。"
    
    elif db_type == "postgresql":
        logger.info("检测到 PostgreSQL，正在执行 VACUUM...")
        # VACUUM 不能在事务块内运行。我们创建一个具有自动提交功能的新引擎来执行此特定操作。
        db_url = f"postgresql+asyncpg://{settings.database.user}:{settings.database.password}@{settings.database.host}:{settings.database.port}/{settings.database.name}"
        auto_commit_engine = create_async_engine(db_url, isolation_level="AUTOCOMMIT")
        try:
            async with auto_commit_engine.connect() as connection:
                await connection.execute(text("VACUUM;"))
            return "VACUUM 执行成功。"
        finally:
            await auto_commit_engine.dispose()
            
    else:
        message = f"不支持的数据库类型 '{db_type}'，跳过优化。"
        logger.warning(message)
        return message

async def purge_binary_logs(session: AsyncSession, days: int) -> str:
    """
    执行 PURGE BINARY LOGS 命令来清理早于指定天数的 binlog 文件。
    警告：这是一个高风险操作，需要 SUPER 或 REPLICATION_CLIENT 权限。
    """
    logger.info(f"准备执行 PURGE BINARY LOGS BEFORE NOW() - INTERVAL {days} DAY...")
    await session.execute(text(f"PURGE BINARY LOGS BEFORE NOW() - INTERVAL {days} DAY"))
    # 这个操作不需要 commit，因为它不是DML
    msg = f"成功执行 PURGE BINARY LOGS，清除了 {days} 天前的日志。"
    logger.info(msg)
    return msg
