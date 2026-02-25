"""
播放历史记录模块
用于记录和管理用户最近播放的番剧，支持 @SXDM 刷新弹幕指令
"""

import logging
from typing import List, Dict
from datetime import datetime
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.db import crud, orm_models
from src.core.cache import get_cache_backend

# ORM 模型别名
Anime = orm_models.Anime
AnimeSource = orm_models.AnimeSource
Episode = orm_models.Episode

logger = logging.getLogger(__name__)


async def record_play_history(
    session: AsyncSession,
    token: str,
    episode_id: int
) -> None:
    """
    记录播放历史到缓存（只记录番剧，不记录具体集数）
    保留最近5部番剧，使用 #A #B #C #D #E 标识

    此函数在独立的数据库事务中执行，不会影响主请求的事务

    Args:
        session: 主请求的数据库会话（仅用于查询番剧信息）
        token: 用户 token
        episode_id: 分集 ID
    """
    # 查询分集所属的番剧信息（使用主 session，只读操作）
    # 包含 imageUrl 和 localImagePath 用于显示海报
    stmt = (
        select(Anime.id, Anime.title, Anime.imageUrl, Anime.localImagePath)
        .join(AnimeSource, AnimeSource.animeId == Anime.id)
        .join(Episode, Episode.sourceId == AnimeSource.id)
        .where(Episode.id == episode_id)
    )
    result = await session.execute(stmt)
    row = result.first()
    if not row:
        logger.debug(f"未找到分集信息: episodeId={episode_id}")
        return

    anime_id, anime_title, image_url, local_image_path = row

    # 在独立的 session 中更新缓存（避免影响主请求的事务）
    from src.db import get_db_session_factory
    session_factory = get_db_session_factory()

    async with session_factory() as cache_session:
        # 获取现有播放历史
        cache_key = f"play_history_{token}"
        history = None
        _backend = get_cache_backend()
        if _backend is not None:
            try:
                history = await _backend.get(cache_key, region="default")
            except Exception:
                pass
        if history is None:
            history = await crud.get_cache(cache_session, cache_key)
        if not history:
            history = []

        # 移除相同 animeId 的旧记录（去重）
        history = [h for h in history if h.get("animeId") != anime_id]

        # 插入到最前面（#A 位置）
        new_record = {
            "animeId": anime_id,
            "animeTitle": anime_title,
            "imageUrl": image_url,  # 远程海报URL
            "localImagePath": local_image_path,  # 本地海报路径
            "updateTime": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        }
        history.insert(0, new_record)

        # 只保留最近5条
        history = history[:5]

        # 保存回缓存（10分钟）
        if _backend is not None:
            try:
                await _backend.set(cache_key, history, ttl=600, region="default")
            except Exception:
                await crud.set_cache(cache_session, cache_key, history, 600)
        else:
            await crud.set_cache(cache_session, cache_key, history, 600)
        logger.info(f"✓ 已记录播放历史: token={token[:8]}..., anime={anime_title}")


async def get_play_history(
    session: AsyncSession,
    token: str
) -> List[Dict]:
    """
    获取播放历史

    Args:
        session: 数据库会话
        token: 用户 token

    Returns:
        播放历史列表，每项包含 animeId, animeTitle, updateTime
    """
    cache_key = f"play_history_{token}"
    history = None
    _backend = get_cache_backend()
    if _backend is not None:
        try:
            history = await _backend.get(cache_key, region="default")
        except Exception:
            pass
    if history is None:
        history = await crud.get_cache(session, cache_key)
    return history if history else []


async def clear_play_history(
    session: AsyncSession,
    token: str
) -> bool:
    """
    清除播放历史
    
    Args:
        session: 数据库会话
        token: 用户 token
        
    Returns:
        是否成功清除
    """
    cache_key = f"play_history_{token}"
    _backend = get_cache_backend()
    if _backend is not None:
        try:
            await _backend.delete(cache_key, region="default")
            return True
        except Exception:
            pass
    return await crud.delete_cache(session, cache_key)

