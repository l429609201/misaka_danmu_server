"""
Episode相关的CRUD操作
"""

import logging
from typing import Optional, Dict, Any, List
from pathlib import Path
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, distinct, case, or_, and_, update, delete
from sqlalchemy.orm import selectinload
from datetime import datetime, timedelta

from ..orm_models import Anime, AnimeSource, Episode, AnimeAlias
from .. import models
from ..timezone import get_now
from ..danmaku_parser import parse_dandan_xml_to_comments
from ..config import settings

logger = logging.getLogger(__name__)

# 弹幕基础目录
DANMAKU_BASE_DIR = Path(settings.config_dir) / "danmaku"


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


async def find_episode_by_index(session: AsyncSession, anime_id: int, episode_index: int) -> bool:
    """
    检查指定作品的所有数据源中，是否存在特定集数的分集。
    返回 True 如果存在，否则返回 False。
    """
    stmt = (
        select(Episode.id)
        .join(AnimeSource, Episode.sourceId == AnimeSource.id)
        .where(
            AnimeSource.animeId == anime_id,
            Episode.episodeIndex == episode_index
        )
        .limit(1)
    )
    result = await session.execute(stmt)
    return result.scalar_one_or_none() is not None


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
        # 增强：当只有集数时，匹配正片（排除特别季）
        # 使用absoluteEpisodeNumber（TMDB的episode_number）和季度条件
        stmt = stmt.where(
            or_(
                # 匹配剧集组正片：customSeasonNumber >= 1
                and_(
                    MappingFromFile.customSeasonNumber >= 1,
                    or_(
                        MappingFromFile.absoluteEpisodeNumber == custom_episode,
                        MappingFromFile.customEpisodeNumber == custom_episode
                    )
                ),
                # 匹配TMDB官方正片：tmdbSeasonNumber >= 1
                and_(
                    MappingFromFile.tmdbSeasonNumber >= 1,
                    or_(
                        MappingFromFile.tmdbEpisodeNumber == custom_episode,
                        MappingFromFile.absoluteEpisodeNumber == custom_episode
                    )
                )
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


async def create_episode_if_not_exists(session: AsyncSession, anime_id: int, source_id: int, episode_index: int, title: str, url: Optional[str], provider_episode_id: str) -> int:
    """如果分集不存在则创建，并返回其确定性的ID。"""
    # 1. 从数据库获取该源的持久化 sourceOrder
    source_order_stmt = select(AnimeSource.sourceOrder).where(AnimeSource.id == source_id)
    source_order_res = await session.execute(source_order_stmt)
    source_order = source_order_res.scalar_one_or_none()

    if source_order is None:
        # 这是一个重要的回退和迁移逻辑。如果一个旧的源没有 sourceOrder，
        # 我们就为其分配一个新的、持久的序号。
        logger.warning(f"源 ID {source_id} 缺少 sourceOrder，将为其分配一个新的。这通常发生在从旧版本升级后。")
        source_order = await _assign_source_order_if_missing(session, anime_id, source_id)

    new_episode_id_str = f"25{anime_id:06d}{source_order:02d}{episode_index:04d}"
    new_episode_id = int(new_episode_id_str)

    # 2. 直接检查这个ID是否存在
    existing_episode_stmt = select(Episode).where(Episode.id == new_episode_id)
    existing_episode_result = await session.execute(existing_episode_stmt)
    existing_episode = existing_episode_result.scalar_one_or_none()
    if existing_episode:
        return existing_episode.id

    # 3. 如果ID不存在，则创建新分集
    new_episode = Episode(
        id=new_episode_id, sourceId=source_id, episodeIndex=episode_index, providerEpisodeId=provider_episode_id,
        title=title, sourceUrl=url, fetchedAt=get_now() # fetchedAt is explicitly set here
    )
    session.add(new_episode)
    await session.flush()
    return new_episode_id


async def get_episode_provider_info(session: AsyncSession, episode_id: int) -> Optional[Dict[str, Any]]:
    stmt = (
        select(
            AnimeSource.providerName,
            AnimeSource.animeId,
            Episode.providerEpisodeId,
            Episode.danmakuFilePath,
            Episode.episodeIndex
        )
        .join(AnimeSource, Episode.sourceId == AnimeSource.id)
        .where(Episode.id == episode_id)
    )
    result = await session.execute(stmt)
    row = result.mappings().first()
    return dict(row) if row else None


async def delete_episode(session: AsyncSession, episode_id: int) -> bool:
    """删除一个分集及其弹幕文件。"""
    from .danmaku import _get_fs_path_from_web_path

    episode = await session.get(Episode, episode_id)
    if episode:
        if episode.danmakuFilePath:
            fs_path = _get_fs_path_from_web_path(episode.danmakuFilePath)
            if fs_path and fs_path.is_file():
                fs_path.unlink(missing_ok=True)
        await session.delete(episode)
        await session.commit()
        return True
    return False


async def update_episode_info(session: AsyncSession, episode_id: int, update_data: models.EpisodeInfoUpdate) -> bool:
    """更新单个分集的信息。如果集数被修改，将重命名弹幕文件并更新路径。"""
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

    # 情况2: 集数已改变，需要重新生成ID并移动文件
    # 1. 检查新集数是否已存在，避免冲突
    conflict_stmt = select(Episode.id).where(
        Episode.sourceId == episode.sourceId,
        Episode.episodeIndex == update_data.episodeIndex
    )
    if (await session.execute(conflict_stmt)).scalar_one_or_none():
        raise ValueError("该集数已存在，请使用其他集数。")

    # 2. 计算新的确定性ID
    source_order = episode.source.sourceOrder
    if source_order is None:
        # 这是一个重要的回退和迁移逻辑。如果一个旧的源没有 sourceOrder，
        # 我们就为其分配一个新的、持久的序号。
        logger.warning(f"源 ID {episode.sourceId} 缺少 sourceOrder，将为其分配一个新的。")
        source_order = await _assign_source_order_if_missing(session, episode.source.animeId, episode.sourceId)
    new_episode_id_str = f"25{episode.source.animeId:06d}{source_order:02d}{update_data.episodeIndex:04d}"
    new_episode_id = int(new_episode_id_str)

    # 3. 重命名弹幕文件（如果存在）
    from .danmaku import _get_fs_path_from_web_path
    new_web_path = None
    if episode.danmakuFilePath:
        old_absolute_path = _get_fs_path_from_web_path(episode.danmakuFilePath)
        
        # 修正：新的Web路径和文件系统路径应与 tasks.py 保持一致（不包含 source_id）
        new_web_path = f"/app/config/danmaku/{episode.source.animeId}/{new_episode_id}.xml"
        new_absolute_path = DANMAKU_BASE_DIR / str(episode.source.animeId) / f"{new_episode_id}.xml"
        
        if old_absolute_path and old_absolute_path.exists():
            try:
                new_absolute_path.parent.mkdir(parents=True, exist_ok=True)
                old_absolute_path.rename(new_absolute_path)
                logger.info(f"弹幕文件已重命名: {old_absolute_path} -> {new_absolute_path}")
            except OSError as e:
                logger.error(f"重命名弹幕文件失败: {e}")
                new_web_path = episode.danmakuFilePath # 如果重命名失败，则保留旧路径
    
    # 4. 创建一个新的分集对象
    new_episode = Episode(
        id=new_episode_id, sourceId=episode.sourceId, episodeIndex=update_data.episodeIndex,
        title=update_data.title, sourceUrl=update_data.sourceUrl,
        providerEpisodeId=episode.providerEpisodeId, fetchedAt=episode.fetchedAt, 
        commentCount=episode.commentCount, danmakuFilePath=new_web_path
    )
    session.add(new_episode)
    
    # 5. 删除旧的分集记录 (由于没有弹幕关联，可以直接删除)
    await session.delete(episode)
    await session.commit()
    return True

# --- Metadata Source Management ---

# --- Config & Cache ---

# get_config_value - 已迁移到 crud/config.py


async def update_episode_fetch_time(session: AsyncSession, episode_id: int):
    await session.execute(update(Episode).where(Episode.id == episode_id).values(fetchedAt=get_now()))
    await session.commit()


async def update_episode_danmaku_info(session: AsyncSession, episode_id: int, file_path: str, count: int):
    """更新分集的弹幕文件路径和弹幕数量。"""
    stmt = update(Episode).where(Episode.id == episode_id).values(
        danmakuFilePath=file_path, commentCount=count, fetchedAt=get_now()
    )
    await session.execute(stmt) # type: ignore
    await session.flush()
# --- API Token Management ---


async def clear_episode_comments(session: AsyncSession, episode_id: int):
    """Deletes the danmaku file for an episode and resets its count."""
    from .danmaku import _get_fs_path_from_web_path

    episode = await session.get(Episode, episode_id)
    if not episode:
        return

    if episode.danmakuFilePath:
        fs_path = _get_fs_path_from_web_path(episode.danmakuFilePath)
        if fs_path and fs_path.is_file():
            try:
                fs_path.unlink()
            except OSError as e:
                logger.error(f"删除弹幕文件失败: {fs_path}。错误: {e}")
    
    episode.danmakuFilePath = None
    episode.commentCount = 0
    await session.commit()


async def get_existing_episodes_for_source(
    session: AsyncSession,
    provider: str,
    media_id: str
) -> List[orm_models.Episode]:
    """获取指定数据源的所有已存在分集"""
    # 先找到对应的源
    source_stmt = select(orm_models.Source).where(
        orm_models.Source.providerName == provider,
        orm_models.Source.mediaId == media_id
    )
    source_result = await session.execute(source_stmt)
    source = source_result.scalar_one_or_none()
    
    if not source:
        return []
    
    # 获取该源的所有分集
    episodes_stmt = select(orm_models.Episode).where(
        orm_models.Episode.sourceId == source.id
    )
    episodes_result = await session.execute(episodes_stmt)
    return episodes_result.scalars().all()

# ==================== 任务状态缓存相关函数 ====================


async def fetch_comments(session: AsyncSession, episode_id: int) -> List[Dict[str, Any]]:
    """从XML文件获取弹幕。"""
    episode_stmt = select(Episode).where(Episode.id == episode_id)
    episode_result = await session.execute(episode_stmt)
    from .danmaku import _get_fs_path_from_web_path

    episode = episode_result.scalar_one_or_none()
    if not episode or not episode.danmakuFilePath:
        return []

    try:
        absolute_path = _get_fs_path_from_web_path(episode.danmakuFilePath)
        if not absolute_path:
            return [] # 辅助函数会记录警告
        
        if not absolute_path.exists():
            logger.warning(f"数据库记录了弹幕文件路径，但文件不存在: {absolute_path}")
            return []
            
        xml_content = absolute_path.read_text(encoding='utf-8')
        return parse_dandan_xml_to_comments(xml_content)
    except Exception as e:
        logger.error(f"读取或解析弹幕文件失败: {episode.danmakuFilePath}。错误: {e}", exc_info=True)
        return []


async def add_comments_from_xml(session: AsyncSession, episode_id: int, xml_content: str) -> int:
    """
    Parses XML content and adds the comments to a given episode.
    Returns the number of comments added.
    """
    comments = parse_dandan_xml_to_comments(xml_content)
    if not comments:
        return 0

    # 导入danmaku模块的函数
    from .danmaku import save_danmaku_for_episode
    added_count = await save_danmaku_for_episode(session, episode_id, comments)
    await session.commit()

    return added_count


async def check_duplicate_import(
    session: AsyncSession,
    provider: str,
    media_id: str,
    anime_title: str,
    media_type: str,
    season: Optional[int] = None,
    year: Optional[int] = None,
    is_single_episode: bool = False,
    episode_index: Optional[int] = None,
    title_recognition_manager=None
) -> Optional[str]:
    """
    统一的重复导入检查函数 - 精确检查模式
    只有完全重复（相同provider + media_id + 集数 + 已有弹幕）时才阻止导入
    返回None表示可以导入，返回字符串表示重复原因
    """
    # 检查数据源是否已存在
    source_exists = await check_source_exists_by_media_id(session, provider, media_id)
    if not source_exists:
        # 数据源不存在，允许导入
        return None

    # 数据源存在，获取anime_id
    anime_id = await get_anime_id_by_source_media_id(session, provider, media_id)
    if not anime_id:
        # 理论上不应该发生，但为了安全起见
        return None

    # 对于单集导入，检查该数据源的该集是否已有弹幕
    if is_single_episode and episode_index is not None:
        # 获取该数据源的该集的详细信息（必须是相同 provider + media_id）
        stmt = select(Episode).join(
            AnimeSource, Episode.sourceId == AnimeSource.id
        ).where(
            AnimeSource.providerName == provider,
            AnimeSource.mediaId == media_id,
            Episode.episodeIndex == episode_index
        ).limit(1)
        result = await session.execute(stmt)
        episode_exists = result.scalar_one_or_none()

        if episode_exists and episode_exists.danmakuFilePath and episode_exists.commentCount > 0:
            return f"作品 '{anime_title}' 的第 {episode_index} 集（{provider}源）已在媒体库中且已有 {episode_exists.commentCount} 条弹幕，无需重复导入"
        else:
            # 该数据源的该集不存在或没有弹幕，允许导入
            return None

    # 对于全量导入，检查该数据源下已有弹幕的集数
    stmt = select(func.count(Episode.id)).join(
        AnimeSource, Episode.sourceId == AnimeSource.id
    ).where(
        AnimeSource.providerName == provider,
        AnimeSource.mediaId == media_id,
        Episode.danmakuFilePath.isnot(None),
        Episode.commentCount > 0
    )
    result = await session.execute(stmt)
    episode_count = result.scalar_one()

    if episode_count > 0:
        # 有弹幕的集数，给出提示但不完全阻止
        # 因为可能有新增的集数需要导入
        logger.info(f"数据源 ({provider}/{media_id}) 已有 {episode_count} 集弹幕，但允许导入新集数")
        return None
    else:
        # 数据源存在但没有弹幕，允许导入
        return None

# update_config_value - 已迁移到 crud/config.py

