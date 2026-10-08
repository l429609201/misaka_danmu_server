"""
搜索后备流程

当主搜索流程失败时，尝试其他搜索策略
"""

import logging
import asyncio
from typing import Optional

from sqlalchemy.ext.asyncio import AsyncSession

from src.schemas.dandan import CommentResponse as DandanCommentsResponse
from src.services.service_container import (
    get_database_service,
    get_task_manager,
    get_scraper_manager,
    get_title_recognition_manager,
)
from src.db.orm_models import Episode, AnimeSource, Anime

logger = logging.getLogger(__name__)


async def handle_search_fallback_comments(
    episodeId: int,
    token: str,
    session: AsyncSession,
    async_mode: bool = False
) -> Optional[DandanCommentsResponse]:
    """
    搜索后备流程：尝试通过不同的搜索策略获取弹幕
    
    :param episodeId: 集数ID
    :param token: 用户token
    :param session: 数据库会话
    :param async_mode: 是否异步模式
    :return: 弹幕响应或None
    """
    try:
        episode = await session.get(Episode, episodeId)
        if not episode:
            logger.warning(f"Episode {episodeId} 不存在")
            return None
        
        # 使用 ORM 实际的驼峰字段，避免后备分支触发 AttributeError。
        anime_source = await session.get(AnimeSource, episode.sourceId)
        if not anime_source:
            return None

        anime = await session.get(Anime, anime_source.animeId)
        if not anime:
            return None
        
        # TODO: 实现具体的搜索后备逻辑
        # 1. 尝试使用别名搜索
        # 2. 尝试使用标题识别
        # 3. 尝试从元数据源搜索
        # 4. 触发后台下载任务
        
        logger.info(f"Episode {episodeId} 搜索后备流程未找到弹幕")
        return None
        
    except Exception as e:
        logger.error(f"搜索后备流程失败: {e}", exc_info=True)
        return None
