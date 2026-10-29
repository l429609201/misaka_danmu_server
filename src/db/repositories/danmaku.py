"""
DanmakuRepository - 弹幕数据访问层
"""

import logging
from typing import Optional, List, Dict, Any
from pathlib import Path
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from ..orm_models import Episode, AnimeSource, Anime
from .base import BaseRepository
from src.core.timezone import get_now

logger = logging.getLogger(__name__)


class DanmakuRepository(BaseRepository[Episode]):
    """弹幕 Repository（操作 Episode 表）"""
    
    async def get_by_id(self, episode_id: int) -> Optional[Episode]:
        """根据 ID 获取分集（包含关联数据）"""
        stmt = select(Episode).where(Episode.id == episode_id).options(
            selectinload(Episode.source).selectinload(AnimeSource.anime)
        )
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()
    
    async def get_all(self, **filters) -> List[Episode]:
        """获取所有分集"""
        stmt = select(Episode)
        if 'source_id' in filters:
            stmt = stmt.where(Episode.sourceId == filters['source_id'])
        result = await self._session.execute(stmt)
        return list(result.scalars().all())
    
    async def create(self, **data) -> Episode:
        """创建分集"""
        episode = Episode(**data)
        self._session.add(episode)
        await self._session.flush()
        return episode
    
    async def update(self, episode_id: int, **data) -> Optional[Episode]:
        """更新分集"""
        episode = await self.get_by_id(episode_id)
        if not episode:
            return None
        
        for key, value in data.items():
            if hasattr(episode, key):
                setattr(episode, key, value)
        
        await self._session.flush()
        return episode
    
    async def delete(self, episode_id: int) -> bool:
        """删除分集"""
        episode = await self.get_by_id(episode_id)
        if not episode:
            return False
        
        await self._session.delete(episode)
        await self._session.flush()
        return True
    
    async def update_danmaku_info(
        self,
        episode_id: int,
        comment_count: int,
        danmaku_file_path: Optional[str] = None
    ) -> Optional[Episode]:
        """更新弹幕信息"""
        episode = await self.get_by_id(episode_id)
        if not episode:
            return None
        
        episode.commentCount = comment_count
        if danmaku_file_path:
            episode.danmakuFilePath = danmaku_file_path
        episode.fetchedAt = get_now()
        
        await self._session.flush()
        return episode
    
    async def get_episode_with_danmaku(
        self,
        source_id: int,
        episode_index: int
    ) -> Optional[Episode]:
        """获取指定源和集数的分集（有弹幕的）"""
        stmt = (
            select(Episode)
            .where(
                Episode.sourceId == source_id,
                Episode.episodeIndex == episode_index,
                Episode.danmakuFilePath.isnot(None)
            )
        )
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()
    
    async def get_episodes_by_source(
        self,
        source_id: int,
        has_danmaku: Optional[bool] = None
    ) -> List[Episode]:
        """获取指定源的所有分集"""
        stmt = select(Episode).where(Episode.sourceId == source_id)
        
        if has_danmaku is True:
            stmt = stmt.where(Episode.danmakuFilePath.isnot(None))
        elif has_danmaku is False:
            stmt = stmt.where(Episode.danmakuFilePath.is_(None))
        
        stmt = stmt.order_by(Episode.episodeIndex)
        result = await self._session.execute(stmt)
        return list(result.scalars().all())
