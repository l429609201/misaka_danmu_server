"""
播放历史记录模块
用于记录和管理用户最近播放的番剧，支持 @SXDM 刷新弹幕指令
"""

import logging
from typing import List, Dict
from datetime import datetime
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.db import orm_models
from src.services.service_container import get_database_service
# C4 注意：避免循环导入，cache_service 改为延迟导入（在函数内部 import）
# 循环链：utils/play_history → services.cache_service → services.__init__ → task_manager → db → crud → utils

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
        # 获取现有播放历史（C4：延迟导入避免循环，CacheService 内部已实现 L1/L2）
        cache_key = f"play_history_{token}"
        history = None
        try:
            from src.services.cache_service import get_cache_service
            cache_service = get_cache_service()
            history = await cache_service.get(key=cache_key, region="default")
        except Exception:
            pass
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
        try:
            cache_service = get_cache_service()
            await cache_service.set(key=cache_key, value=history, ttl=600, region="default")
        except Exception:
            pass
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
    try:
        from src.services.cache_service import get_cache_service
        cache_service = get_cache_service()
        history = await cache_service.get(key=cache_key, region="default")
    except Exception:
        pass
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
    try:
        cache_service = get_cache_service()
        await cache_service.delete(key=cache_key, region="default")
        return True
    except Exception:
        return False

