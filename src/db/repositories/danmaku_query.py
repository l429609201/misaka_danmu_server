"""
DanmakuQueryRepository - 弹幕复杂查询层

职责：处理弹幕统计、跨表查询等读操作。
基础 CRUD 操作请使用 DanmakuRepository / DanmakuStorageRepository。

注意：项目弹幕存储为文件形式（Episode.danmakuFilePath），
本 Query 类主要处理元数据统计，不涉及实际弹幕内容读写。
"""

import logging
from typing import Optional, List, Dict, Any
from sqlalchemy import select, func, and_, or_
from sqlalchemy.ext.asyncio import AsyncSession

from ..orm_models import Episode, AnimeSource, Anime

logger = logging.getLogger(__name__)


class DanmakuQueryRepository:
    """弹幕复杂查询 Repository"""
    
    def __init__(self, session: AsyncSession):
        """
        初始化查询 Repository
        
        Args:
            session: SQLAlchemy AsyncSession 实例
        """
        self._session = session
    
    async def count_danmaku_episodes_by_anime(self, anime_id: int) -> int:
        """
        统计作品下已有弹幕的集数
        
        Args:
            anime_id: 作品ID
            
        Returns:
            已有弹幕的集数
        """
        stmt = (
            select(func.count(func.distinct(Episode.id)))
            .join(AnimeSource, Episode.sourceId == AnimeSource.id)
            .where(
                AnimeSource.animeId == anime_id,
                Episode.danmakuFilePath.isnot(None),
                Episode.danmakuFilePath != '',
            )
        )
        result = await self._session.execute(stmt)
        return result.scalar_one()
    
    async def get_danmaku_coverage_by_source(self, source_id: int) -> Dict[str, Any]:
        """
        获取数据源的弹幕覆盖率统计
        
        Args:
            source_id: 数据源ID
            
        Returns:
            {"total": int, "with_danmaku": int, "coverage_rate": float}
        """
        # 总集数
        total_stmt = select(func.count()).select_from(Episode).where(Episode.sourceId == source_id)
        total_result = await self._session.execute(total_stmt)
        total = total_result.scalar_one()
        
        # 有弹幕的集数
        with_danmaku_stmt = (
            select(func.count())
            .select_from(Episode)
            .where(
                Episode.sourceId == source_id,
                Episode.danmakuFilePath.isnot(None),
                Episode.danmakuFilePath != '',
            )
        )
        with_danmaku_result = await self._session.execute(with_danmaku_stmt)
        with_danmaku = with_danmaku_result.scalar_one()
        
        coverage_rate = (with_danmaku / total * 100) if total > 0 else 0.0
        
        return {
            "total": total,
            "with_danmaku": with_danmaku,
            "coverage_rate": round(coverage_rate, 2),
        }
    
    async def get_episodes_without_danmaku(
        self,
        anime_id: Optional[int] = None,
        source_id: Optional[int] = None,
        limit: int = 100
    ) -> List[Dict[str, Any]]:
        """
        获取缺少弹幕的分集列表
        
        Args:
            anime_id: 作品ID过滤（可选）
            source_id: 数据源ID过滤（可选）
            limit: 返回数量限制
            
        Returns:
            分集列表
        """
        stmt = (
            select(Episode, AnimeSource, Anime)
            .join(AnimeSource, Episode.sourceId == AnimeSource.id)
            .join(Anime, AnimeSource.animeId == Anime.id)
            .where(
                or_(
                    Episode.danmakuFilePath.is_(None),
                    Episode.danmakuFilePath == '',
                )
            )
        )
        
        if anime_id:
            stmt = stmt.where(Anime.id == anime_id)
        
        if source_id:
            stmt = stmt.where(AnimeSource.id == source_id)
        
        stmt = stmt.order_by(Episode.episodeIndex).limit(limit)
        
        result = await self._session.execute(stmt)
        rows = result.all()
        
        return [
            {
                "episodeId": row.Episode.id,
                "episodeIndex": row.Episode.episodeIndex,
                "title": row.Episode.title,
                "sourceId": row.AnimeSource.id,
                "animeId": row.Anime.id,
                "animeTitle": row.Anime.title,
            }
            for row in rows
        ]
    
    async def get_total_danmaku_count_in_library(self) -> int:
        """
        获取媒体库中所有弹幕总数（基于 Episode.commentCount）
        
        Returns:
            总弹幕数
        """
        stmt = select(func.sum(Episode.commentCount)).select_from(Episode)
        result = await self._session.execute(stmt)
        total = result.scalar_one()
        return total if total is not None else 0
