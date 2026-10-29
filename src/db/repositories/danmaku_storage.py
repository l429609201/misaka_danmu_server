"""
DanmakuStorageRepository - 弹幕存储数据访问层

注意：save_danmaku_for_episode 方法包含业务逻辑，不符合 Repository 层职责。
使用延迟导入临时解决循环依赖问题。
TODO: 应将该方法重构到编排层（Workflow）。
"""

import logging
from typing import Optional, List, Dict, Any
from pathlib import Path
from sqlalchemy import select
from sqlalchemy.orm import selectinload

# 环境检测模块不依赖业务层，顶层导入不会引入循环依赖。
from src.core.env import is_docker_environment
from ..orm_models import Anime, AnimeSource, Episode
from .base import BaseRepository

logger = logging.getLogger(__name__)

# 弹幕基础目录
def _get_base_dir() -> Path:
    """获取基础目录（Docker 环境或本地环境）"""
    if is_docker_environment():
        return Path("/app")
    return Path.cwd()

BASE_DIR = _get_base_dir()
DANMAKU_BASE_DIR = BASE_DIR / "config/danmaku"


class DanmakuStorageRepository(BaseRepository[Episode]):
    """弹幕存储 Repository（主要操作 Episode 表的弹幕路径）"""
    
    async def get_by_id(self, episode_id: int) -> Optional[Episode]:
        """根据 ID 获取分集（包含源和动漫信息）"""
        stmt = select(Episode).where(Episode.id == episode_id).options(
            selectinload(Episode.source).selectinload(AnimeSource.anime).selectinload(Anime.metadataRecord)
        )
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()
    
    async def get_all(self, **filters) -> List[Episode]:
        """获取所有分集"""
        stmt = select(Episode)
        
        if 'has_danmaku' in filters:
            if filters['has_danmaku']:
                stmt = stmt.where(Episode.danmakuFilePath.isnot(None))
            else:
                stmt = stmt.where(Episode.danmakuFilePath.is_(None))
        
        result = await self._session.execute(stmt)
        return list(result.scalars().all())
    
    async def create(self, **data) -> Episode:
        """创建分集（基础方法）"""
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
    
    async def get_episodes_for_anime(self, anime_id: int) -> List[Episode]:
        """获取指定动漫的所有分集"""
        stmt = (
            select(Episode)
            .join(AnimeSource, Episode.sourceId == AnimeSource.id)
            .where(AnimeSource.animeId == anime_id)
            .options(
                selectinload(Episode.source).selectinload(AnimeSource.anime).selectinload(Anime.metadataRecord)
            )
        )
        result = await self._session.execute(stmt)
        return list(result.scalars().all())
    
    async def update_danmaku_path(
        self,
        episode_id: int,
        new_path: str
    ) -> Optional[Episode]:
        """更新弹幕文件路径"""
        episode = await self.get_by_id(episode_id)
        if not episode:
            return None
        
        episode.danmakuFilePath = new_path
        await self._session.flush()
        return episode
    
    async def batch_update_danmaku_paths(
        self,
        path_updates: List[Dict[str, Any]]
    ) -> int:
        """批量更新弹幕路径"""
        updated_count = 0
        
        for update_info in path_updates:
            episode_id = update_info.get('episode_id')
            new_path = update_info.get('new_path')
            
            if episode_id and new_path:
                episode = await self.update_danmaku_path(episode_id, new_path)
                if episode:
                    updated_count += 1
        
        return updated_count
    
    async def get_episodes_with_danmaku(
        self,
        anime_id: Optional[int] = None
    ) -> List[Episode]:
        """获取有弹幕的分集"""
        stmt = select(Episode).where(Episode.danmakuFilePath.isnot(None))
        
        if anime_id:
            stmt = stmt.join(AnimeSource, Episode.sourceId == AnimeSource.id).where(
                AnimeSource.animeId == anime_id
            )
        
        stmt = stmt.options(
            selectinload(Episode.source).selectinload(AnimeSource.anime)
        )
        
        result = await self._session.execute(stmt)
        return list(result.scalars().all())
    
    async def clear_danmaku_path(self, episode_id: int) -> Optional[Episode]:
        """清除弹幕路径"""
        episode = await self.get_by_id(episode_id)
        if not episode:
            return None

        episode.danmakuFilePath = None
        await self._session.flush()
        return episode

    async def update_danmaku_info(
        self,
        episode_id: int,
        danmaku_path: str,
        comment_count: int
    ) -> Optional[Episode]:
        """更新分集的弹幕信息（路径和数量）

        这是一个纯数据库操作方法，符合 Repository 层职责。
        """
        episode = await self.get_by_id(episode_id)
        if not episode:
            return None

        episode.danmakuFilePath = danmaku_path
        episode.commentCount = comment_count
        await self._session.flush()
        return episode
