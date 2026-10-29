"""
DanmakuEditRepository - 弹幕编辑数据访问层
"""

import logging
from typing import Optional, List, Dict, Any
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from ..orm_models import Episode, AnimeSource
from .base import BaseRepository

logger = logging.getLogger(__name__)


class DanmakuEditRepository(BaseRepository[Episode]):
    """弹幕编辑 Repository"""
    
    async def get_by_id(self, episode_id: int) -> Optional[Episode]:
        """根据 ID 获取分集（包含源信息）"""
        stmt = select(Episode).where(Episode.id == episode_id).options(
            selectinload(Episode.source)
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
    
    async def update_danmaku_path(
        self,
        episode_id: int,
        danmaku_file_path: str,
        comment_count: int
    ) -> Optional[Episode]:
        """更新弹幕文件路径和数量"""
        episode = await self.get_by_id(episode_id)
        if not episode:
            return None
        
        episode.danmakuFilePath = danmaku_file_path
        episode.commentCount = comment_count
        
        await self._session.flush()
        return episode
    
    async def get_episodes_by_source(
        self,
        source_id: int,
        order_by_index: bool = True
    ) -> List[Episode]:
        """获取指定源的所有分集"""
        stmt = select(Episode).where(Episode.sourceId == source_id)
        
        if order_by_index:
            stmt = stmt.order_by(Episode.episodeIndex)
        
        result = await self._session.execute(stmt)
        return list(result.scalars().all())
    
    async def get_episode_by_source_and_index(
        self,
        source_id: int,
        episode_index: int
    ) -> Optional[Episode]:
        """根据源 ID 和集数获取分集"""
        stmt = select(Episode).where(
            Episode.sourceId == source_id,
            Episode.episodeIndex == episode_index
        )
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()
