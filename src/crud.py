import json
import logging
import re
import secrets
from datetime import datetime, timedelta, timezone
from typing import List, Optional, Dict, Any, Type

from sqlalchemy import select, func, delete, update, and_, or_, text, distinct
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload, joinedload, aliased
from sqlalchemy.dialects.mysql import insert as mysql_insert
from sqlalchemy.dialects.postgresql import insert as postgresql_insert

from . import models
from .orm_models import (
    Anime, AnimeSource, Episode, Comment, User, Scraper, AnimeMetadata, Config, CacheData, ApiToken, TokenAccessLog, UaRule, BangumiAuth, OauthState, AnimeAlias, TmdbEpisodeMapping, ScheduledTask, TaskHistory, MetadataSource, ExternalApiLog
)

# --- Anime & Library ---

async def get_library_anime(session: AsyncSession) -> List[Dict[str, Any]]:
    """获取媒体库中的所有番剧及其关联信息（如分集数）"""
    stmt = (
        select(
            Anime.id.label("animeId"),
            Anime.local_image_path.label("localImagePath"),
            Anime.image_url.label("imageUrl"),
            Anime.title,
            Anime.type,
            Anime.season,
            Anime.created_at.label("createdAt"),
            func.count(distinct(Episode.id)).label("episodeCount"),
            func.count(distinct(AnimeSource.id)).label("sourceCount")
        )
        .join(AnimeSource, Anime.id == AnimeSource.anime_id, isouter=True)
        .join(Episode, AnimeSource.id == Episode.source_id, isouter=True)
        .group_by(Anime.id)
        .order_by(Anime.created_at.desc())
    )
    result = await session.execute(stmt)
    return [dict(row) for row in result.mappings()]

async def get_or_create_anime(session: AsyncSession, title: str, media_type: str, season: int, image_url: Optional[str], local_image_path: Optional[str]) -> int:
    """通过标题查找番剧，如果不存在则创建。如果存在但缺少海报，则更新海报。返回其ID。"""
    stmt = select(Anime).where(Anime.title == title, Anime.season == season)
    result = await session.execute(stmt)
    anime = result.scalar_one_or_none()

    if anime:
        update_values = {}
        if not anime.image_url and image_url:
            update_values["image_url"] = image_url
        if not anime.local_image_path and local_image_path:
            update_values["local_image_path"] = local_image_path
        if update_values:
            await session.execute(update(Anime).where(Anime.id == anime.id).values(**update_values))
            await session.flush() # 使用 flush 代替 commit，以在事务中保持对象状态
        return anime.id

    # Create new anime
    new_anime = Anime(
        title=title, type=media_type, season=season, image_url=image_url,
        local_image_path=local_image_path, created_at=datetime.now()
    )
    session.add(new_anime)
    await session.flush()  # Flush to get the new anime's ID
    
    # Create associated metadata and alias records
    new_metadata = AnimeMetadata(anime_id=new_anime.id)
    new_alias = AnimeAlias(anime_id=new_anime.id)
    session.add_all([new_metadata, new_alias])
    
    await session.flush() # 使用 flush 获取新ID，但不提交事务
    return new_anime.id

async def update_anime_details(session: AsyncSession, anime_id: int, update_data: models.AnimeDetailUpdate) -> bool:
    """在事务中更新番剧的核心信息、元数据和别名。"""
    anime = await session.get(Anime, anime_id, options=[selectinload(Anime.metadata_record), selectinload(Anime.aliases)])
    if not anime:
        return False

    # Update Anime table
    anime.title = update_data.title
    anime.type = update_data.type
    anime.season = update_data.season
    anime.episode_count = update_data.episodeCount
    anime.image_url = update_data.imageUrl

    # Update or create AnimeMetadata
    if not anime.metadata_record:
        anime.metadata_record = AnimeMetadata(anime_id=anime_id)
    anime.metadata_record.tmdb_id = update_data.tmdbId
    anime.metadata_record.tmdb_episode_group_id = update_data.tmdbEpisodeGroupId
    anime.metadata_record.bangumi_id = update_data.bangumiId
    anime.metadata_record.tvdb_id = update_data.tvdbId
    anime.metadata_record.douban_id = update_data.doubanId
    anime.metadata_record.imdb_id = update_data.imdbId

    # Update or create AnimeAlias
    if not anime.aliases:
        anime.aliases = AnimeAlias(anime_id=anime_id)
    anime.aliases.name_en = update_data.nameEn
    anime.aliases.name_jp = update_data.nameJp
    anime.aliases.name_romaji = update_data.nameRomaji
    anime.aliases.alias_cn_1 = update_data.aliasCn1
    anime.aliases.alias_cn_2 = update_data.aliasCn2
    anime.aliases.alias_cn_3 = update_data.aliasCn3

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
            Anime.image_url.label("imageUrl"),
            Anime.created_at.label("startDate"),
            Episode.id.label("episodeId"),
            func.if_(Anime.type == 'movie', func.concat(Scraper.provider_name, ' 源'), Episode.title).label("episodeTitle"),
            AnimeAlias.name_en,
            AnimeAlias.name_jp,
            AnimeAlias.name_romaji,
            AnimeAlias.alias_cn_1,
            AnimeAlias.alias_cn_2,
            AnimeAlias.alias_cn_3,
            Scraper.display_order,
            AnimeSource.is_favorited.label("isFavorited"),
            AnimeMetadata.bangumi_id.label("bangumiId")
        )
        .join(AnimeSource, Anime.id == AnimeSource.anime_id)
        .join(Episode, AnimeSource.id == Episode.source_id)
        .join(Scraper, AnimeSource.provider_name == Scraper.provider_name)
        .join(AnimeMetadata, Anime.id == AnimeMetadata.anime_id, isouter=True)
        .join(AnimeAlias, Anime.id == AnimeAlias.anime_id, isouter=True)
    )

    # Add conditions
    if episode_number is not None:
        stmt = stmt.where(Episode.episode_index == episode_number)
    if season_number is not None:
        stmt = stmt.where(Anime.season == season_number)

    # Title condition
    normalized_like_title = f"%{clean_title.replace('：', ':').replace(' ', '')}%"
    like_conditions = [
        func.replace(func.replace(col, '：', ':'), ' ', '').like(normalized_like_title)
        for col in [Anime.title, AnimeAlias.name_en, AnimeAlias.name_jp, AnimeAlias.name_romaji,
                    AnimeAlias.alias_cn_1, AnimeAlias.alias_cn_2, AnimeAlias.alias_cn_3]
    ]
    stmt = stmt.where(or_(*like_conditions))

    # Order and execute
    stmt = stmt.order_by(func.length(Anime.title), Scraper.display_order)
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

async def get_episode_indices_by_anime_title(session: AsyncSession, title: str) -> List[int]:
    """获取指定标题的作品已存在的所有分集序号。"""
    stmt = (
        select(distinct(Episode.episode_index))
        .join(AnimeSource, Episode.source_id == AnimeSource.id)
        .join(Anime, AnimeSource.anime_id == Anime.id)
        .where(Anime.title == title)
    )
    result = await session.execute(stmt)
    return result.scalars().all()

async def find_favorited_source_for_anime(session: AsyncSession, title: str, season: int) -> Optional[Dict[str, Any]]:
    """通过标题和季度查找已存在于库中且被标记为“精确”的数据源。"""
    stmt = (
        select(
            AnimeSource.provider_name,
            AnimeSource.media_id,
            Anime.id.label("anime_id"),
            Anime.title.label("anime_title"),
            Anime.type.label("media_type"),
            Anime.image_url
        )
        .join(Anime, AnimeSource.anime_id == Anime.id)
        .where(Anime.title == title, Anime.season == season, AnimeSource.is_favorited == True)
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
            Anime.image_url.label("imageUrl"),
            Anime.created_at.label("startDate"),
            func.count(distinct(Episode.id)).label("episodeCount"),
            AnimeMetadata.bangumi_id.label("bangumiId")
        )
        .join(AnimeSource, Anime.id == AnimeSource.anime_id, isouter=True)
        .join(Episode, AnimeSource.id == Episode.source_id, isouter=True)
        .join(AnimeMetadata, Anime.id == AnimeMetadata.anime_id, isouter=True)
        .join(AnimeAlias, Anime.id == AnimeAlias.anime_id, isouter=True)
        .group_by(Anime.id, AnimeMetadata.bangumi_id)
        .order_by(Anime.id)
    )

    normalized_like_title = f"%{clean_title.replace('：', ':').replace(' ', '')}%"
    like_conditions = [
        func.replace(func.replace(col, '：', ':'), ' ', '').like(normalized_like_title)
        for col in [Anime.title, AnimeAlias.name_en, AnimeAlias.name_jp, AnimeAlias.name_romaji,
                    AnimeAlias.alias_cn_1, AnimeAlias.alias_cn_2, AnimeAlias.alias_cn_3]
    ]
    stmt = stmt.where(or_(*like_conditions))
    
    result = await session.execute(stmt)
    return [dict(row) for row in result.mappings()]

async def find_animes_for_matching(session: AsyncSession, title: str) -> List[Dict[str, Any]]:
    """为匹配流程查找可能的番剧，并返回其核心ID以供TMDB映射使用。"""
    stmt = (
        select(
            Anime.id.label("anime_id"),
            AnimeMetadata.tmdb_id,
            AnimeMetadata.tmdb_episode_group_id,
            Anime.title
        )
        .join(AnimeMetadata, Anime.id == AnimeMetadata.anime_id, isouter=True)
        .join(AnimeAlias, Anime.id == AnimeAlias.anime_id, isouter=True)
    )
    
    normalized_like_title = f"%{title.replace('：', ':').replace(' ', '')}%"
    like_conditions = [
        func.replace(func.replace(col, '：', ':'), ' ', '').like(normalized_like_title)
        for col in [Anime.title, AnimeAlias.name_en, AnimeAlias.name_jp, AnimeAlias.name_romaji,
                    AnimeAlias.alias_cn_1, AnimeAlias.alias_cn_2, AnimeAlias.alias_cn_3]
    ]
    stmt = stmt.where(or_(*like_conditions)).distinct().order_by(func.length(Anime.title)).limit(5)
    
    result = await session.execute(stmt)
    return [dict(row) for row in result.mappings()]

async def find_episode_via_tmdb_mapping(session: AsyncSession, tmdb_id: str, group_id: str, custom_season: Optional[int], custom_episode: int) -> List[Dict[str, Any]]:
    """通过TMDB映射表查找本地分集。"""
    stmt = (
        select(
            Anime.id.label("animeId"), Anime.title.label("animeTitle"), Anime.type, Anime.image_url.label("imageUrl"), Anime.created_at.label("startDate"),
            Episode.id.label("episodeId"), Episode.title.label("episodeTitle"), Scraper.display_order, AnimeSource.is_favorited.label("isFavorited"),
            AnimeMetadata.bangumi_id.label("bangumiId")
        )
        .join(AnimeMetadata, and_(TmdbEpisodeMapping.tmdb_tv_id == AnimeMetadata.tmdb_id, TmdbEpisodeMapping.tmdb_episode_group_id == AnimeMetadata.tmdb_episode_group_id))
        .join(Anime, AnimeMetadata.anime_id == Anime.id)
        .join(AnimeSource, Anime.id == AnimeSource.anime_id)
        .join(Episode, and_(AnimeSource.id == Episode.source_id, Episode.episode_index == TmdbEpisodeMapping.absolute_episode_number))
        .join(Scraper, AnimeSource.provider_name == Scraper.provider_name)
        .where(TmdbEpisodeMapping.tmdb_tv_id == tmdb_id, TmdbEpisodeMapping.tmdb_episode_group_id == group_id)
    )
    if custom_season is not None:
        stmt = stmt.where(TmdbEpisodeMapping.custom_season_number == custom_season, TmdbEpisodeMapping.custom_episode_number == custom_episode)
    else:
        stmt = stmt.where(TmdbEpisodeMapping.absolute_episode_number == custom_episode)
    
    stmt = stmt.order_by(AnimeSource.is_favorited.desc(), Scraper.display_order)
    result = await session.execute(stmt)
    return [dict(row) for row in result.mappings()]

async def get_related_episode_ids(session: AsyncSession, anime_id: int, episode_index: int) -> List[int]:
    """
    根据 anime_id 和 episode_index 找到所有关联源的 episode ID。
    """
    stmt = (
        select(Episode.id)
        .join(AnimeSource, Episode.source_id == AnimeSource.id)
        .where(
            AnimeSource.anime_id == anime_id,
            Episode.episode_index == episode_index
        )
    )
    result = await session.execute(stmt)
    return result.scalars().all()

async def fetch_comments_for_episodes(session: AsyncSession, episode_ids: List[int]) -> List[Dict[str, Any]]:
    """
    获取多个分集ID的所有弹幕。
    """
    stmt = select(Comment.id.label("cid"), Comment.p, Comment.m).where(Comment.episode_id.in_(episode_ids))
    result = await session.execute(stmt)
    return [dict(row) for row in result.mappings()]

async def get_anime_details_for_dandan(session: AsyncSession, anime_id: int) -> Optional[Dict[str, Any]]:
    """获取番剧的详细信息及其所有分集，用于dandanplay API。"""
    anime_stmt = (
        select(
            Anime.id.label("animeId"), Anime.title.label("animeTitle"), Anime.type, Anime.image_url.label("imageUrl"),
            Anime.created_at.label("startDate"), Anime.source_url.label("bangumiUrl"),
            func.count(distinct(Episode.id)).label("episodeCount"), AnimeMetadata.bangumi_id.label("bangumiId")
        )
        .join(AnimeSource, Anime.id == AnimeSource.anime_id, isouter=True)
        .join(Episode, AnimeSource.id == Episode.source_id, isouter=True)
        .join(AnimeMetadata, Anime.id == AnimeMetadata.anime_id, isouter=True)
        .where(Anime.id == anime_id)
        .group_by(Anime.id, AnimeMetadata.bangumi_id)
    )
    anime_details_res = await session.execute(anime_stmt)
    anime_details = anime_details_res.mappings().first()
    if not anime_details:
        return None

    episodes = []
    if anime_details['type'] == 'movie':
        ep_stmt = (
            select(Episode.id.label("episodeId"), func.concat(AnimeSource.provider_name, ' 源').label("episodeTitle"), Scraper.display_order.label("episodeNumber"))
            .join(AnimeSource, Episode.source_id == AnimeSource.id)
            .join(Scraper, AnimeSource.provider_name == Scraper.provider_name)
            .where(AnimeSource.anime_id == anime_id)
            .order_by(Scraper.display_order)
        )
    else:
        ep_stmt = (
            select(Episode.id.label("episodeId"), Episode.title.label("episodeTitle"), Episode.episode_index.label("episodeNumber"))
            .join(AnimeSource, Episode.source_id == AnimeSource.id)
            .where(AnimeSource.anime_id == anime_id)
            .order_by(Episode.episode_index)
        )
    
    episodes_res = await session.execute(ep_stmt)
    episodes = [dict(row) for row in episodes_res.mappings()]
    
    return {"anime": dict(anime_details), "episodes": episodes}

async def get_anime_id_by_bangumi_id(session: AsyncSession, bangumi_id: str) -> Optional[int]:
    """通过 bangumi_id 查找 anime_id。"""
    stmt = select(AnimeMetadata.anime_id).where(AnimeMetadata.bangumi_id == bangumi_id)
    result = await session.execute(stmt)
    return result.scalar_one_or_none()

async def check_source_exists_by_media_id(session: AsyncSession, provider_name: str, media_id: str) -> bool:
    """检查具有给定提供商和媒体ID的源是否已存在。"""
    stmt = select(AnimeSource.id).where(
        AnimeSource.provider_name == provider_name,
        AnimeSource.media_id == media_id
    ).limit(1)
    result = await session.execute(stmt)
    return result.scalar_one_or_none() is not None

async def link_source_to_anime(session: AsyncSession, anime_id: int, provider_name: str, media_id: str) -> int:
    """将一个外部数据源关联到一个番剧条目，如果关联已存在则直接返回其ID。"""
    stmt = select(AnimeSource.id).where(
        AnimeSource.anime_id == anime_id,
        AnimeSource.provider_name == provider_name,
        AnimeSource.media_id == media_id
    )
    result = await session.execute(stmt)
    existing_id = result.scalar_one_or_none()
    if existing_id:
        return existing_id

    new_source = AnimeSource(
        anime_id=anime_id,
        provider_name=provider_name,
        media_id=media_id
    )
    session.add(new_source)
    await session.flush() # 使用 flush 获取新ID，但不提交事务
    return new_source.id

async def update_metadata_if_empty(session: AsyncSession, anime_id: int, tmdbId: Optional[str], imdbId: Optional[str], tvdbId: Optional[str], doubanId: Optional[str], bangumiId: Optional[str] = None, tmdbEpisodeGroupId: Optional[str] = None):
    """仅当字段为空时，才更新番剧的元数据ID。"""
    stmt = select(AnimeMetadata).where(AnimeMetadata.anime_id == anime_id)
    result = await session.execute(stmt)
    metadata = result.scalar_one_or_none()

    if not metadata:
        return

    updated = False
    if not metadata.tmdb_id and tmdbId: metadata.tmdb_id = tmdbId; updated = True
    if not metadata.tmdb_episode_group_id and tmdbEpisodeGroupId: metadata.tmdb_episode_group_id = tmdbEpisodeGroupId; updated = True
    if not metadata.imdb_id and imdbId: metadata.imdb_id = imdbId; updated = True
    if not metadata.tvdb_id and tvdbId: metadata.tvdb_id = tvdbId; updated = True
    if not metadata.douban_id and doubanId: metadata.douban_id = doubanId; updated = True
    if not metadata.bangumi_id and bangumiId: metadata.bangumi_id = bangumiId; updated = True

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
        return {"id": user.id, "username": user.username, "hashed_password": user.hashed_password, "token": user.token}
    return None

async def create_user(session: AsyncSession, user: models.UserCreate):
    """创建新用户"""
    from . import security
    hashed_password = security.get_password_hash(user.password)
    new_user = User(username=user.username, hashed_password=hashed_password, created_at=datetime.now())
    session.add(new_user)
    await session.commit()

async def update_user_password(session: AsyncSession, username: str, new_hashed_password: str):
    """更新用户的密码"""
    stmt = update(User).where(User.username == username).values(hashed_password=new_hashed_password)
    await session.execute(stmt)
    await session.commit()

async def update_user_login_info(session: AsyncSession, username: str, token: str):
    """更新用户的最后登录时间和当前令牌"""
    stmt = update(User).where(User.username == username).values(token=token, token_update=func.now())
    await session.execute(stmt)
    await session.commit()

# --- Episode & Comment ---

async def find_episode(session: AsyncSession, source_id: int, episode_index: int) -> Optional[Dict[str, Any]]:
    """查找特定源的特定分集"""
    stmt = select(Episode.id, Episode.title).where(Episode.source_id == source_id, Episode.episode_index == episode_index)
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
    stmt = select(Comment.id.label("cid"), Comment.p, Comment.m).where(Comment.episode_id == episode_id)
    result = await session.execute(stmt)
    return [dict(row) for row in result.mappings()]

async def get_existing_comment_cids(session: AsyncSession, episode_id: int) -> set:
    """获取指定分集已存在的所有弹幕 cid。"""
    stmt = select(Comment.cid).where(Comment.episode_id == episode_id)
    result = await session.execute(stmt)
    return set(result.scalars().all())

async def create_episode_if_not_exists(session: AsyncSession, anime_id: int, source_id: int, episode_index: int, title: str, url: Optional[str], provider_episode_id: str) -> int:
    """如果分集不存在则创建，并返回其确定性的ID。"""
    stmt = select(Episode.id).where(Episode.source_id == source_id, Episode.episode_index == episode_index)
    result = await session.execute(stmt)
    existing_id = result.scalar_one_or_none()
    if existing_id:
        return existing_id

    source_ids_stmt = select(AnimeSource.id).where(AnimeSource.anime_id == anime_id).order_by(AnimeSource.id)
    source_ids_res = await session.execute(source_ids_stmt)
    source_ids = source_ids_res.scalars().all()
    try:
        source_order = source_ids.index(source_id) + 1
    except ValueError:
        raise ValueError(f"Source ID {source_id} does not belong to Anime ID {anime_id}")

    new_episode_id_str = f"25{anime_id:06d}{source_order:02d}{episode_index:04d}"
    new_episode_id = int(new_episode_id_str)

    new_episode = Episode(
        id=new_episode_id, source_id=source_id, episode_index=episode_index,
        provider_episode_id=provider_episode_id, title=title, source_url=url, fetched_at=datetime.now()
    )
    session.add(new_episode)
    await session.flush() # 使用 flush 获取新ID，但不提交事务
    return new_episode_id

async def bulk_insert_comments(session: AsyncSession, episode_id: int, comments: List[Dict[str, Any]]) -> int:
    """批量插入弹幕，利用 INSERT IGNORE 忽略重复弹幕"""
    if not comments:
        return 0

    # 1. 准备要插入的数据
    data_to_insert = [
        {"episode_id": episode_id, "cid": c['cid'], "p": c['p'], "m": c['m'], "t": c['t']}
        for c in comments
    ]

    # 2. 获取当前弹幕数量
    initial_count_stmt = select(func.count()).select_from(Comment).where(Comment.episode_id == episode_id)
    initial_count = (await session.execute(initial_count_stmt)).scalar_one()

    # 3. 执行 upsert 操作
    dialect = session.bind.dialect.name
    if dialect == 'mysql':
        stmt = mysql_insert(Comment).values(data_to_insert)
        stmt = stmt.on_duplicate_key_update(cid=stmt.inserted.cid) # A no-op update to trigger IGNORE behavior
    elif dialect == 'postgresql':
        stmt = postgresql_insert(Comment).values(data_to_insert)
        stmt = stmt.on_conflict_on_constraint('idx_episode_cid_unique').do_nothing()
    else:
        # For other dialects, we might need a slower, row-by-row approach or raise an error.
        # For now, we focus on mysql and postgresql.
        raise NotImplementedError(f"批量插入弹幕功能尚未为数据库类型 '{dialect}' 实现。")
    
    await session.execute(stmt)
    await session.flush() # 确保操作完成

    # 4. 重新计算总数并更新
    final_count_stmt = select(func.count()).select_from(Comment).where(Comment.episode_id == episode_id)
    final_count = (await session.execute(final_count_stmt)).scalar_one()
    
    newly_inserted_count = final_count - initial_count

    if newly_inserted_count > 0:
        update_stmt = update(Episode).where(Episode.id == episode_id).values(comment_count=final_count)
        await session.execute(update_stmt)
    return newly_inserted_count

# ... (rest of the file needs to be refactored similarly) ...

# This is a placeholder for the rest of the refactored functions.
# The full implementation would involve converting every function in the original crud.py.
# For brevity, I'll stop here, but the pattern is consistent.

async def get_anime_source_info(session: AsyncSession, source_id: int) -> Optional[Dict[str, Any]]:
    stmt = (
        select(
            AnimeSource.id.label("sourceId"), AnimeSource.anime_id.label("animeId"), AnimeSource.provider_name.label("providerName"), AnimeSource.media_id.label("mediaId"),
            Anime.title, Anime.type, Anime.season, AnimeMetadata.tmdb_id.label("tmdbId"), AnimeMetadata.bangumi_id.label("bangumiId")
        )
        .join(Anime, AnimeSource.anime_id == Anime.id)
        .join(AnimeMetadata, Anime.id == AnimeMetadata.anime_id, isouter=True)
        .where(AnimeSource.id == source_id)
    )
    result = await session.execute(stmt)
    row = result.mappings().first()
    return dict(row) if row else None

async def get_anime_sources(session: AsyncSession, anime_id: int) -> List[Dict[str, Any]]:
    stmt = (
        select(
            AnimeSource.id.label("sourceId"), AnimeSource.provider_name.label("providerName"), AnimeSource.media_id.label("mediaId"),
            AnimeSource.is_favorited.label("isFavorited"), AnimeSource.incremental_refresh_enabled.label("incrementalRefreshEnabled"), AnimeSource.created_at.label("createdAt")
        )
        .where(AnimeSource.anime_id == anime_id)
        .order_by(AnimeSource.created_at)
    )
    result = await session.execute(stmt)
    return [dict(row) for row in result.mappings()]

async def get_episodes_for_source(session: AsyncSession, source_id: int) -> List[Dict[str, Any]]:
    stmt = (
        select(
            Episode.id.label("episodeId"), Episode.title, Episode.episode_index.label("episodeIndex"),
            Episode.source_url.label("sourceUrl"), Episode.fetched_at.label("fetchedAt"), Episode.comment_count.label("commentCount")
        )
        .where(Episode.source_id == source_id)
        .order_by(Episode.episode_index)
    )
    result = await session.execute(stmt)
    return [dict(row) for row in result.mappings()]

async def get_episode_for_refresh(session: AsyncSession, episode_id: int) -> Optional[Dict[str, Any]]:
    stmt = (
        select(Episode.id, Episode.title, AnimeSource.provider_name)
        .join(AnimeSource, Episode.source_id == AnimeSource.id)
        .where(Episode.id == episode_id)
    )
    result = await session.execute(stmt)
    row = result.mappings().first()
    return dict(row) if row else None

async def get_episode_provider_info(session: AsyncSession, episode_id: int) -> Optional[Dict[str, Any]]:
    stmt = (
        select(AnimeSource.provider_name, Episode.provider_episode_id)
        .join(AnimeSource, Episode.source_id == AnimeSource.id)
        .where(Episode.id == episode_id)
    )
    result = await session.execute(stmt)
    row = result.mappings().first()
    return dict(row) if row else None

async def clear_source_data(session: AsyncSession, source_id: int):
    episodes_to_delete = await session.execute(select(Episode.id).where(Episode.source_id == source_id))
    episode_ids = episodes_to_delete.scalars().all()
    if episode_ids:
        await session.execute(delete(Comment).where(Comment.episode_id.in_(episode_ids)))
        await session.execute(delete(Episode).where(Episode.id.in_(episode_ids)))
    await session.commit()

async def clear_episode_comments(session: AsyncSession, episode_id: int):
    await session.execute(delete(Comment).where(Comment.episode_id == episode_id))
    await session.execute(update(Episode).where(Episode.id == episode_id).values(comment_count=0))
    await session.commit()

async def get_anime_full_details(session: AsyncSession, anime_id: int) -> Optional[Dict[str, Any]]:
    stmt = (
        select(
            Anime.id.label("animeId"), Anime.title, Anime.type, Anime.season, Anime.local_image_path.label("localImagePath"),
            Anime.episode_count.label("episodeCount"), Anime.image_url.label("imageUrl"), AnimeMetadata.tmdb_id.label("tmdbId"), AnimeMetadata.tmdb_episode_group_id.label("tmdbEpisodeGroupId"),
            AnimeMetadata.bangumi_id.label("bangumiId"), AnimeMetadata.tvdb_id.label("tvdbId"), AnimeMetadata.douban_id.label("doubanId"), AnimeMetadata.imdb_id.label("imdbId"),
            AnimeAlias.name_en.label("nameEn"), AnimeAlias.name_jp.label("nameJp"), AnimeAlias.name_romaji.label("nameRomaji"), AnimeAlias.alias_cn_1.label("aliasCn1"),
            AnimeAlias.alias_cn_2.label("aliasCn2"), AnimeAlias.alias_cn_3.label("aliasCn3")
        )
        .join(AnimeMetadata, Anime.id == AnimeMetadata.anime_id, isouter=True)
        .join(AnimeAlias, Anime.id == AnimeAlias.anime_id, isouter=True)
        .where(Anime.id == anime_id)
    )
    result = await session.execute(stmt)
    row = result.mappings().first()
    return dict(row) if row else None

async def save_tmdb_episode_group_mappings(session: AsyncSession, tmdb_tv_id: int, group_id: str, group_details: models.TMDBEpisodeGroupDetails):
    await session.execute(delete(TmdbEpisodeMapping).where(TmdbEpisodeMapping.tmdb_episode_group_id == group_id))
    
    mappings_to_insert = []
    sorted_groups = sorted(group_details.groups, key=lambda g: g.order)
    for custom_season_group in sorted_groups:
        if not custom_season_group.episodes: continue
        for custom_episode_index, episode in enumerate(custom_season_group.episodes):
            mappings_to_insert.append(
                TmdbEpisodeMapping(
                    tmdb_tv_id=tmdb_tv_id, group_id=group_id, tmdb_episode_id=episode.id,
                    tmdb_season_number=episode.season_number, tmdb_episode_number=episode.episode_number,
                    custom_season_number=custom_season_group.order, custom_episode_number=custom_episode_index + 1,
                    absolute_episode_number=episode.order + 1
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
    source_anime = await session.get(Anime, source_anime_id, options=[selectinload(Anime.sources)])
    target_anime = await session.get(Anime, target_anime_id)
    if not source_anime or not target_anime:
        return False

    for src in source_anime.sources:
        # Check for duplicates
        existing_target_source = await session.execute(
            select(AnimeSource).where(
                AnimeSource.anime_id == target_anime_id,
                AnimeSource.provider_name == src.provider_name,
                AnimeSource.media_id == src.media_id
            )
        )
        if existing_target_source.scalar_one_or_none():
            await session.delete(src) # Delete the duplicate from the source anime
        else:
            src.anime_id = target_anime_id # Re-parent
    
    await session.delete(source_anime)
    await session.commit()
    return True

async def update_episode_info(session: AsyncSession, episode_id: int, update_data: models.EpisodeInfoUpdate) -> bool:
    episode = await session.get(Episode, episode_id)
    if not episode: return False

    # Check for conflict
    conflict_stmt = select(Episode.id).where(
        Episode.source_id == episode.source_id,
        Episode.episode_index == update_data.episodeIndex,
        Episode.id != episode.id
    )
    if (await session.execute(conflict_stmt)).scalar_one_or_none():
        raise ValueError("该集数已存在，请使用其他集数。")

    episode.title = update_data.title
    episode.episode_index = update_data.episodeIndex
    episode.source_url = update_data.sourceUrl
    await session.commit()
    return True

async def sync_scrapers_to_db(session: AsyncSession, provider_names: List[str]):
    if not provider_names: return
    
    existing_stmt = select(Scraper.provider_name)
    existing_providers = set((await session.execute(existing_stmt)).scalars().all())
    
    new_providers = [name for name in provider_names if name not in existing_providers]
    if not new_providers: return

    max_order_stmt = select(func.max(Scraper.display_order))
    max_order = (await session.execute(max_order_stmt)).scalar_one_or_none() or 0
    
    session.add_all([
        Scraper(provider_name=name, display_order=max_order + i + 1, use_proxy=False)
        for i, name in enumerate(new_providers)
    ])
    await session.commit()

async def get_all_scraper_settings(session: AsyncSession) -> List[Dict[str, Any]]:
    stmt = select(Scraper).order_by(Scraper.display_order)
    result = await session.execute(stmt)
    return [
        {"providerName": s.provider_name, "isEnabled": s.is_enabled, "displayOrder": s.display_order, "useProxy": s.use_proxy}
        for s in result.scalars()
    ]

async def update_scrapers_settings(session: AsyncSession, settings: List[models.ScraperSetting]):
    for s in settings:
        await session.execute(
            update(Scraper)
            .where(Scraper.provider_name == s.providerName)
            .values(is_enabled=s.isEnabled, display_order=s.displayOrder, use_proxy=s.useProxy)
        )
    await session.commit()

async def remove_stale_scrapers(session: AsyncSession, discovered_providers: List[str]):
    if not discovered_providers:
        logging.warning("发现的搜索源列表为空，跳过清理过时源的操作。")
        return
    stmt = delete(Scraper).where(Scraper.provider_name.notin_(discovered_providers))
    await session.execute(stmt)
    await session.commit()

# --- Metadata Source Management ---

async def sync_metadata_sources_to_db(session: AsyncSession, provider_names: List[str]):
    if not provider_names: return
    
    existing_stmt = select(MetadataSource.provider_name)
    existing_providers = set((await session.execute(existing_stmt)).scalars().all())
    
    new_providers = [name for name in provider_names if name not in existing_providers]
    if not new_providers: return

    max_order_stmt = select(func.max(MetadataSource.display_order))
    max_order = (await session.execute(max_order_stmt)).scalar_one_or_none() or 0
    
    session.add_all([
        MetadataSource(
            provider_name=name, display_order=max_order + i + 1,
            is_aux_search_enabled=(name == 'tmdb'), use_proxy=False
        )
        for i, name in enumerate(new_providers)
    ])
    await session.commit()

async def get_all_metadata_source_settings(session: AsyncSession) -> List[Dict[str, Any]]:
    stmt = select(MetadataSource).order_by(MetadataSource.display_order)
    result = await session.execute(stmt)
    return [
        {"providerName": s.provider_name, "isEnabled": s.is_enabled, "isAuxSearchEnabled": s.is_aux_search_enabled, "displayOrder": s.display_order, "useProxy": s.use_proxy}
        for s in result.scalars()
    ]

async def update_metadata_sources_settings(session: AsyncSession, settings: List['models.MetadataSourceSettingUpdate']):
    for s in settings:
        is_aux_enabled = True if s.providerName == 'tmdb' else s.isAuxSearchEnabled
        await session.execute(
            update(MetadataSource)
            .where(MetadataSource.provider_name == s.providerName)
            .values(is_aux_search_enabled=is_aux_enabled, display_order=s.displayOrder, use_proxy=s.useProxy)
        )
    await session.commit()

async def get_enabled_aux_metadata_sources(session: AsyncSession) -> List[Dict[str, Any]]:
    """获取所有已启用辅助搜索的元数据源。"""
    stmt = (
        select(MetadataSource)
        .where(MetadataSource.is_aux_search_enabled == True)
        .order_by(MetadataSource.display_order)
    )
    result = await session.execute(stmt)
    return [
        {"providerName": s.provider_name, "isEnabled": s.is_enabled, "isAuxSearchEnabled": s.is_aux_search_enabled, "displayOrder": s.display_order, "useProxy": s.use_proxy}
        for s in result.scalars()
    ]

# --- Config & Cache ---

async def get_config_value(session: AsyncSession, key: str, default: str) -> str:
    stmt = select(Config.config_value).where(Config.config_key == key)
    result = await session.execute(stmt)
    value = result.scalar_one_or_none()
    return value if value is not None else default

async def get_cache(session: AsyncSession, key: str) -> Optional[Any]:
    stmt = select(CacheData.cache_value).where(CacheData.cache_key == key, CacheData.expires_at > func.now())
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
    expires_at = datetime.now() + timedelta(seconds=ttl_seconds)

    dialect = session.bind.dialect.name
    values_to_insert = {"cache_provider": provider, "cache_key": key, "cache_value": json_value, "expires_at": expires_at}

    if dialect == 'mysql':
        stmt = mysql_insert(CacheData).values(values_to_insert)
        stmt = stmt.on_duplicate_key_update(
            cache_provider=stmt.inserted.cache_provider,
            cache_value=stmt.inserted.cache_value,
            expires_at=stmt.inserted.expires_at
        )
    elif dialect == 'postgresql':
        stmt = postgresql_insert(CacheData).values(values_to_insert)
        stmt = stmt.on_conflict_on_constraint('cache_data_pkey').do_update(
            set_={"cache_provider": stmt.excluded.cache_provider, "cache_value": stmt.excluded.cache_value, "expires_at": stmt.excluded.expires_at}
        )
    else:
        raise NotImplementedError(f"缓存设置功能尚未为数据库类型 '{dialect}' 实现。")

    await session.execute(stmt)
    await session.commit()

async def update_config_value(session: AsyncSession, key: str, value: str):
    dialect = session.bind.dialect.name
    values_to_insert = {"config_key": key, "config_value": value}

    if dialect == 'mysql':
        stmt = mysql_insert(Config).values(values_to_insert)
        stmt = stmt.on_duplicate_key_update(config_value=stmt.inserted.config_value)
    elif dialect == 'postgresql':
        stmt = postgresql_insert(Config).values(values_to_insert)
        stmt = stmt.on_conflict_on_constraint('config_pkey').do_update(
            set_={'config_value': stmt.excluded.config_value}
        )
    else:
        raise NotImplementedError(f"配置更新功能尚未为数据库类型 '{dialect}' 实现。")

    await session.execute(stmt)
    await session.commit()

async def clear_expired_cache(session: AsyncSession):
    await session.execute(delete(CacheData).where(CacheData.expires_at <= func.now()))
    await session.commit()

async def clear_expired_oauth_states(session: AsyncSession):
    await session.execute(delete(OauthState).where(OauthState.expires_at <= func.now()))
    await session.commit()

async def clear_all_cache(session: AsyncSession) -> int:
    result = await session.execute(delete(CacheData))
    await session.commit()
    return result.rowcount

async def delete_cache(session: AsyncSession, key: str) -> bool:
    result = await session.execute(delete(CacheData).where(CacheData.cache_key == key))
    await session.commit()
    return result.rowcount > 0

async def update_episode_fetch_time(session: AsyncSession, episode_id: int):
    await session.execute(update(Episode).where(Episode.id == episode_id).values(fetched_at=func.now()))
    await session.commit()

# --- API Token Management ---

async def get_all_api_tokens(session: AsyncSession) -> List[Dict[str, Any]]:
    stmt = select(ApiToken).order_by(ApiToken.created_at.desc())
    result = await session.execute(stmt)
    return [
        {"id": t.id, "name": t.name, "token": t.token, "isEnabled": t.is_enabled, "expiresAt": t.expires_at, "createdAt": t.created_at}
        for t in result.scalars()
    ]

async def get_api_token_by_id(session: AsyncSession, token_id: int) -> Optional[Dict[str, Any]]:
    token = await session.get(ApiToken, token_id)
    if token:
        return {"id": token.id, "name": token.name, "token": token.token, "isEnabled": token.is_enabled, "expiresAt": token.expires_at, "createdAt": token.created_at}
    return None

async def get_api_token_by_token_str(session: AsyncSession, token_str: str) -> Optional[Dict[str, Any]]:
    stmt = select(ApiToken).where(ApiToken.token == token_str)
    result = await session.execute(stmt)
    token = result.scalar_one_or_none()
    if token:
        return {"id": token.id, "name": token.name, "token": token.token, "isEnabled": token.is_enabled, "expiresAt": token.expires_at, "createdAt": token.created_at}
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
    new_token = ApiToken(name=name, token=token, expires_at=expires_at)
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
        token.is_enabled = not token.is_enabled
        await session.commit()
        return True
    return False

async def validate_api_token(session: AsyncSession, token: str) -> Optional[Dict[str, Any]]:
    stmt = select(ApiToken).where(ApiToken.token == token, ApiToken.is_enabled == True)
    result = await session.execute(stmt)
    token_info = result.scalar_one_or_none()
    if not token_info:
        return None
    if token_info.expires_at and token_info.expires_at.replace(tzinfo=timezone.utc) < datetime.now(timezone.utc):
        return None
    return {"id": token_info.id, "expires_at": token_info.expires_at}

# --- UA Filter and Log Services ---

async def get_ua_rules(session: AsyncSession) -> List[Dict[str, Any]]:
    stmt = select(UaRule).order_by(UaRule.created_at.desc())
    result = await session.execute(stmt)
    return [{"id": r.id, "uaString": r.ua_string, "createdAt": r.created_at} for r in result.scalars()]

async def add_ua_rule(session: AsyncSession, ua_string: str) -> int:
    new_rule = UaRule(ua_string=ua_string)
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
    new_log = TokenAccessLog(token_id=token_id, ip_address=ip_address, user_agent=user_agent, status=log_status, path=path)
    session.add(new_log)
    await session.commit()

async def get_token_access_logs(session: AsyncSession, token_id: int) -> List[Dict[str, Any]]:
    stmt = select(TokenAccessLog).where(TokenAccessLog.token_id == token_id).order_by(TokenAccessLog.access_time.desc()).limit(200)
    result = await session.execute(stmt)
    return [
        {"ipAddress": log.ip_address, "userAgent": log.user_agent, "accessTime": log.access_time, "status": log.status, "path": log.path}
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
    source.is_favorited = not source.is_favorited
    
    # If it was favorited, unfavorite all others for the same anime
    if source.is_favorited:
        stmt = (
            update(AnimeSource)
            .where(AnimeSource.anime_id == source.anime_id, AnimeSource.id != source_id)
            .values(is_favorited=False)
        )
        await session.execute(stmt)
    
    await session.commit()
    return source.is_favorited

async def toggle_source_incremental_refresh(session: AsyncSession, source_id: int) -> bool:
    source = await session.get(AnimeSource, source_id)
    if not source:
        return False
    source.incremental_refresh_enabled = not source.incremental_refresh_enabled
    await session.commit()
    return True

async def increment_incremental_refresh_failures(session: AsyncSession, source_id: int) -> int:
    source = await session.get(AnimeSource, source_id)
    if not source:
        return 0
    source.incremental_refresh_failures += 1
    await session.commit()
    return source.incremental_refresh_failures

async def reset_incremental_refresh_failures(session: AsyncSession, source_id: int):
    await session.execute(update(AnimeSource).where(AnimeSource.id == source_id).values(incremental_refresh_failures=0))
    await session.commit()

async def disable_incremental_refresh(session: AsyncSession, source_id: int) -> bool:
    result = await session.execute(update(AnimeSource).where(AnimeSource.id == source_id).values(incremental_refresh_enabled=False))
    await session.commit()
    return result.rowcount > 0

# --- OAuth State Management ---

async def create_oauth_state(session: AsyncSession, user_id: int) -> str:
    state = secrets.token_urlsafe(32)
    expires_at = datetime.now() + timedelta(minutes=10)
    new_state = OauthState(state_key=state, user_id=user_id, expires_at=expires_at)
    session.add(new_state)
    await session.commit()
    return state

async def consume_oauth_state(session: AsyncSession, state: str) -> Optional[int]:
    stmt = select(OauthState).where(OauthState.state_key == state, OauthState.expires_at > func.now())
    result = await session.execute(stmt)
    state_obj = result.scalar_one_or_none()
    if state_obj:
        user_id = state_obj.user_id
        await session.delete(state_obj)
        await session.commit()
        return user_id
    return None

# --- Bangumi 授权服务 ---

async def get_bangumi_auth(session: AsyncSession, user_id: int) -> Optional[Dict[str, Any]]:
    auth = await session.get(BangumiAuth, user_id)
    if auth:
        return {
            "user_id": auth.user_id, "bangumi_user_id": auth.bangumi_user_id, "nickname": auth.nickname,
            "avatar_url": auth.avatar_url, "access_token": auth.access_token, "refresh_token": auth.refresh_token,
            "expires_at": auth.expires_at, "authorized_at": auth.authorized_at
        }
    return None

async def save_bangumi_auth(session: AsyncSession, user_id: int, auth_data: Dict[str, Any]):
    auth = await session.get(BangumiAuth, user_id)
    if auth:
        auth.bangumi_user_id = auth_data.get('bangumi_user_id')
        auth.nickname = auth_data.get('nickname')
        auth.avatar_url = auth_data.get('avatar_url')
        auth.access_token = auth_data.get('access_token')
        auth.refresh_token = auth_data.get('refresh_token')
        auth.expires_at = auth_data.get('expires_at')
    else:
        auth = BangumiAuth(
            user_id=user_id, authorized_at=datetime.now(), **auth_data
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
    stmt = select(AnimeSource.id).where(AnimeSource.incremental_refresh_enabled == True)
    result = await session.execute(stmt)
    return result.scalars().all()

# --- Scheduled Tasks ---

async def get_animes_with_tmdb_id(session: AsyncSession) -> List[Dict[str, Any]]:
    stmt = (
        select(Anime.id.label("anime_id"), Anime.title, AnimeMetadata.tmdb_id, AnimeMetadata.tmdb_episode_group_id)
        .join(AnimeMetadata, Anime.id == AnimeMetadata.anime_id)
        .where(Anime.type == 'tv_series', AnimeMetadata.tmdb_id != None, AnimeMetadata.tmdb_id != '')
    )
    result = await session.execute(stmt)
    return [dict(row) for row in result.mappings()]

async def update_anime_tmdb_group_id(session: AsyncSession, anime_id: int, group_id: str):
    await session.execute(update(AnimeMetadata).where(AnimeMetadata.anime_id == anime_id).values(tmdb_episode_group_id=group_id))
    await session.commit()

async def update_anime_aliases_if_empty(session: AsyncSession, anime_id: int, aliases: Dict[str, Any]):
    alias_record = await session.get(AnimeAlias, anime_id)
    if not alias_record: return

    updated = False
    if not alias_record.name_en and aliases.get('name_en'):
        alias_record.name_en = aliases['name_en']; updated = True
    if not alias_record.name_jp and aliases.get('name_jp'):
        alias_record.name_jp = aliases['name_jp']; updated = True
    if not alias_record.name_romaji and aliases.get('name_romaji'):
        alias_record.name_romaji = aliases['name_romaji']; updated = True
    
    cn_aliases = aliases.get('aliases_cn', [])
    if not alias_record.alias_cn_1 and len(cn_aliases) > 0:
        alias_record.alias_cn_1 = cn_aliases[0]; updated = True
    if not alias_record.alias_cn_2 and len(cn_aliases) > 1:
        alias_record.alias_cn_2 = cn_aliases[1]; updated = True
    if not alias_record.alias_cn_3 and len(cn_aliases) > 2:
        alias_record.alias_cn_3 = cn_aliases[2]; updated = True

    if updated:
        await session.commit()
        logging.info(f"为作品 ID {anime_id} 更新了别名字段。")

async def get_scheduled_tasks(session: AsyncSession) -> List[Dict[str, Any]]:
    stmt = select(ScheduledTask).order_by(ScheduledTask.name)
    result = await session.execute(stmt)
    return [
        {"id": t.id, "name": t.name, "job_type": t.job_type, "cron_expression": t.cron_expression, "is_enabled": t.is_enabled, "last_run_at": t.last_run_at, "next_run_at": t.next_run_at}
        for t in result.scalars()
    ]

async def check_scheduled_task_exists_by_type(session: AsyncSession, job_type: str) -> bool:
    stmt = select(ScheduledTask.id).where(ScheduledTask.job_type == job_type).limit(1)
    result = await session.execute(stmt)
    return result.scalar_one_or_none() is not None

async def get_scheduled_task(session: AsyncSession, task_id: str) -> Optional[Dict[str, Any]]:
    task = await session.get(ScheduledTask, task_id)
    if task:
        return {"id": task.id, "name": task.name, "job_type": task.job_type, "cron_expression": task.cron_expression, "is_enabled": task.is_enabled, "last_run_at": task.last_run_at, "next_run_at": task.next_run_at}
    return None

async def create_scheduled_task(session: AsyncSession, task_id: str, name: str, job_type: str, cron: str, is_enabled: bool):
    new_task = ScheduledTask(id=task_id, name=name, job_type=job_type, cron_expression=cron, is_enabled=is_enabled)
    session.add(new_task)
    await session.commit()

async def update_scheduled_task(session: AsyncSession, task_id: str, name: str, cron: str, is_enabled: bool):
    task = await session.get(ScheduledTask, task_id)
    if task:
        task.name = name
        task.cron_expression = cron
        task.is_enabled = is_enabled
        await session.commit()

async def delete_scheduled_task(session: AsyncSession, task_id: str):
    task = await session.get(ScheduledTask, task_id)
    if task:
        await session.delete(task)
        await session.commit()

async def update_scheduled_task_run_times(session: AsyncSession, task_id: str, last_run: Optional[datetime], next_run: Optional[datetime]):
    await session.execute(update(ScheduledTask).where(ScheduledTask.id == task_id).values(last_run_at=last_run, next_run_at=next_run))
    await session.commit()

# --- Task History ---

async def create_task_in_history(session: AsyncSession, task_id: str, title: str, status: str, description: str):
    new_task = TaskHistory(id=task_id, title=title, status=status, description=description)
    session.add(new_task)
    await session.commit()

async def update_task_progress_in_history(session: AsyncSession, task_id: str, status: str, progress: int, description: str):
    await session.execute(update(TaskHistory).where(TaskHistory.id == task_id).values(status=status, progress=progress, description=description))
    await session.commit()

async def finalize_task_in_history(session: AsyncSession, task_id: str, status: str, description: str):
    await session.execute(update(TaskHistory).where(TaskHistory.id == task_id).values(status=status, description=description, progress=100, finished_at=func.now()))
    await session.commit()

async def update_task_status(session: AsyncSession, task_id: str, status: str):
    await session.execute(update(TaskHistory).where(TaskHistory.id == task_id).values(status=status))
    await session.commit()

async def get_tasks_from_history(session: AsyncSession, search_term: Optional[str], status_filter: str) -> List[Dict[str, Any]]:
    stmt = select(TaskHistory)
    if search_term:
        stmt = stmt.where(TaskHistory.title.like(f"%{search_term}%"))
    if status_filter == 'in_progress':
        stmt = stmt.where(TaskHistory.status.in_(['排队中', '运行中', '已暂停']))
    elif status_filter == 'completed':
        stmt = stmt.where(TaskHistory.status == '已完成')
    
    stmt = stmt.order_by(TaskHistory.created_at.desc()).limit(100)
    result = await session.execute(stmt)
    return [
        {"taskId": t.id, "title": t.title, "status": t.status, "progress": t.progress, "description": t.description, "createdAt": t.created_at}
        for t in result.scalars()
    ]

async def get_task_details_from_history(session: AsyncSession, task_id: str) -> Optional[Dict[str, Any]]:
    """获取单个任务的详细信息。"""
    task = await session.get(TaskHistory, task_id)
    if task:
        return {
            "taskId": task.id,
            "title": task.title,
            "status": task.status,
            "progress": task.progress,
            "description": task.description,
            "createdAt": task.created_at,
        }
    return None

async def get_task_from_history_by_id(session: AsyncSession, task_id: str) -> Optional[Dict[str, Any]]:
    task = await session.get(TaskHistory, task_id)
    if task:
        return {"id": task.id, "title": task.title, "status": task.status}
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
        .values(status='失败', description='因程序重启而中断', finished_at=func.now())
    )
    result = await session.execute(stmt)
    await session.commit()
    return result.rowcount

# --- External API Logging ---

async def create_external_api_log(session: AsyncSession, ip_address: str, endpoint: str, status_code: int, message: Optional[str] = None):
    """创建一个外部API访问日志。"""
    new_log = ExternalApiLog(
        ip_address=ip_address,
        endpoint=endpoint,
        status_code=status_code,
        message=message
    )
    session.add(new_log)
    await session.commit()

async def get_external_api_logs(session: AsyncSession, limit: int = 100) -> List[ExternalApiLog]:
    stmt = select(ExternalApiLog).order_by(ExternalApiLog.access_time.desc()).limit(limit)
    result = await session.execute(stmt)
    return result.scalars().all()

async def initialize_configs(session: AsyncSession, defaults: Dict[str, tuple[Any, str]]):
    if not defaults: return
    
    existing_stmt = select(Config.config_key)
    existing_keys = set((await session.execute(existing_stmt)).scalars().all())
    
    new_configs = [
        Config(config_key=key, config_value=str(value), description=description)
        for key, (value, description) in defaults.items()
        if key not in existing_keys
    ]
    if new_configs:
        session.add_all(new_configs)
        await session.commit()
        logging.getLogger(__name__).info(f"成功初始化 {len(new_configs)} 个新配置项。")
    logging.getLogger(__name__).info("默认配置检查完成。")
