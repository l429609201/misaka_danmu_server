"""
LocalDanmakuRepository - 本地弹幕数据访问层
"""

import logging
from typing import Optional, List, Dict, Any
from sqlalchemy import select, func, and_, delete, cast, String

from sqlalchemy.ext.asyncio import AsyncSession

from ..orm_models import LocalDanmakuItem
from .base import BaseRepository
from src.core import settings

logger = logging.getLogger(__name__)


def _get_group_concat_func(column):
    """根据数据库类型返回正确的字符串聚合函数"""
    db_type = settings.database.type.lower()
    if db_type == 'postgresql':
        # PostgreSQL 使用 string_agg,需要将整数转换为字符串
        return func.string_agg(cast(column, String), ',')
    else:
        # MySQL/SQLite 使用 group_concat
        return func.group_concat(column)


class LocalDanmakuRepository(BaseRepository[LocalDanmakuItem]):
    """本地弹幕 Repository"""

    async def get_by_id(self, item_id: int) -> Optional[LocalDanmakuItem]:
        """根据 ID 获取本地弹幕项"""
        return await self._session.get(LocalDanmakuItem, item_id)

    async def get_all(self, **filters) -> List[LocalDanmakuItem]:
        """获取所有本地弹幕项"""
        stmt = select(LocalDanmakuItem)

        if 'is_imported' in filters:
            stmt = stmt.where(LocalDanmakuItem.isImported == filters['is_imported'])
        if 'media_type' in filters:
            stmt = stmt.where(LocalDanmakuItem.mediaType == filters['media_type'])

        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def create(
        self,
        file_path: str,
        title: str,
        media_type: str,
        season: Optional[int] = None,
        episode: Optional[int] = None,
        year: Optional[int] = None,
        tmdb_id: Optional[str] = None,
        tvdb_id: Optional[str] = None,
        imdb_id: Optional[str] = None,
        poster_url: Optional[str] = None,
        nfo_path: Optional[str] = None
    ) -> LocalDanmakuItem:
        """创建本地弹幕项"""
        item = LocalDanmakuItem(
            filePath=file_path,
            title=title,
            mediaType=media_type,
            season=season,
            episode=episode,
            year=year,
            tmdbId=tmdb_id,
            tvdbId=tvdb_id,
            imdbId=imdb_id,
            posterUrl=poster_url,
            nfoPath=nfo_path
        )
        self._session.add(item)
        await self._session.flush()
        return item

    async def update(self, item_id: int, **data) -> Optional[LocalDanmakuItem]:
        """更新本地弹幕项"""
        item = await self.get_by_id(item_id)
        if not item:
            return None

        for key, value in data.items():
            if hasattr(item, key):
                setattr(item, key, value)

        await self._session.flush()
        return item

    async def delete(self, item_id: int) -> bool:
        """删除本地弹幕项"""
        item = await self.get_by_id(item_id)
        if not item:
            return False

        await self._session.delete(item)
        await self._session.flush()
        return True

    async def get_paginated(
        self,
        is_imported: Optional[bool] = None,
        media_type: Optional[str] = None,
        page: int = 1,
        page_size: int = 100
    ) -> Dict[str, Any]:
        """分页获取本地弹幕项"""
        conditions = []
        if is_imported is not None:
            conditions.append(LocalDanmakuItem.isImported == is_imported)
        if media_type:
            conditions.append(LocalDanmakuItem.mediaType == media_type)

        # 查询总数
        count_stmt = select(func.count()).select_from(LocalDanmakuItem)
        if conditions:
            count_stmt = count_stmt.where(and_(*conditions))
        total_result = await self._session.execute(count_stmt)
        total = total_result.scalar_one()

        # 分页查询
        offset = (page - 1) * page_size
        stmt = select(LocalDanmakuItem)
        if conditions:
            stmt = stmt.where(and_(*conditions))
        stmt = stmt.order_by(LocalDanmakuItem.id.desc()).offset(offset).limit(page_size)

        result = await self._session.execute(stmt)
        items = list(result.scalars().all())

        return {
            "items": items,
            "total": total,
            "page": page,
            "page_size": page_size,
            "total_pages": (total + page_size - 1) // page_size
        }

    async def mark_as_imported(self, item_id: int) -> Optional[LocalDanmakuItem]:
        """标记为已导入"""
        return await self.update(item_id, isImported=True)

    async def get_file_path_references(self) -> List[str]:
        """获取本地弹幕项引用的所有文件路径。"""
        stmt = select(LocalDanmakuItem.filePath).where(LocalDanmakuItem.filePath.is_not(None))
        result = await self._session.execute(stmt)
        return [path for (path,) in result.all() if path]

    async def get_by_file_path(self, file_path: str) -> Optional[LocalDanmakuItem]:
        """根据文件路径获取项"""
        stmt = select(LocalDanmakuItem).where(LocalDanmakuItem.filePath == file_path)
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def delete_all(self) -> int:
        """删除所有本地弹幕项"""
        stmt = delete(LocalDanmakuItem)
        result = await self._session.execute(stmt)
        await self._session.flush()
        return result.rowcount

    async def batch_delete(self, item_ids: List[int]) -> int:
        """批量删除本地弹幕项"""
        stmt = delete(LocalDanmakuItem).where(LocalDanmakuItem.id.in_(item_ids))
        result = await self._session.execute(stmt)
        await self._session.flush()
        return result.rowcount

    async def delete_by_title(self, title: str, media_type: str) -> int:
        """根据标题和类型删除本地弹幕项"""
        stmt = delete(LocalDanmakuItem).where(
            and_(
                LocalDanmakuItem.title == title,
                LocalDanmakuItem.mediaType == media_type
            )
        )
        result = await self._session.execute(stmt)
        await self._session.flush()
        return result.rowcount

    async def delete_by_season(self, title: str, season: int) -> int:
        """根据标题和季度删除本地弹幕项"""
        stmt = delete(LocalDanmakuItem).where(
            and_(
                LocalDanmakuItem.title == title,
                LocalDanmakuItem.season == season
            )
        )
        result = await self._session.execute(stmt)
        await self._session.flush()
        return result.rowcount

    async def get_episode_ids_by_show(self, title: str) -> List[int]:
        """获取剧集所有集的ID"""
        stmt = select(LocalDanmakuItem.id).where(
            and_(
                LocalDanmakuItem.title == title,
                LocalDanmakuItem.mediaType == "tv_series"
            )
        )
        result = await self._session.execute(stmt)
        return [row[0] for row in result.all()]

    async def get_episode_ids_by_season(self, title: str, season: int) -> List[int]:
        """获取某一季所有集的ID"""
        stmt = select(LocalDanmakuItem.id).where(
            and_(
                LocalDanmakuItem.title == title,
                LocalDanmakuItem.season == season
            )
        )
        result = await self._session.execute(stmt)
        return [row[0] for row in result.all()]


class LocalDanmakuQueryRepository:
    """本地弹幕查询 Repository - 复杂查询操作"""

    def __init__(self, session: AsyncSession) -> None:
        """绑定调用方管理的会话，查询仓储不承担 CRUD 抽象接口。"""
        # 与其他 QueryRepository 保持一致，CRUD 由代理中的 LocalDanmakuRepository 提供。
        self._session = session

    async def get_works(
        self,
        is_imported: Optional[bool] = None,
        media_type: Optional[str] = None,
        year_from: Optional[int] = None,
        year_to: Optional[int] = None,
        page: int = 1,
        page_size: int = 100
    ) -> Dict[str, Any]:
        """按作品分组获取本地弹幕项"""
        # 构建查询条件
        conditions = []
        if is_imported is not None:
            conditions.append(LocalDanmakuItem.isImported == is_imported)
        if media_type:
            conditions.append(LocalDanmakuItem.mediaType == media_type)
        if year_from is not None:
            conditions.append(LocalDanmakuItem.year >= year_from)
        if year_to is not None:
            conditions.append(LocalDanmakuItem.year <= year_to)

        # 查询作品列表(按title分组,同时获取每组的ID列表)
        stmt = select(
            LocalDanmakuItem.title,
            LocalDanmakuItem.mediaType,
            LocalDanmakuItem.year,
            func.max(LocalDanmakuItem.tmdbId).label('tmdbId'),
            func.max(LocalDanmakuItem.tvdbId).label('tvdbId'),
            func.max(LocalDanmakuItem.imdbId).label('imdbId'),
            func.max(LocalDanmakuItem.posterUrl).label('posterUrl'),
            func.count(LocalDanmakuItem.id).label('itemCount'),
            func.max(LocalDanmakuItem.season).label('seasonCount'),
            func.max(LocalDanmakuItem.episode).label('episodeCount'),
            _get_group_concat_func(LocalDanmakuItem.id).label('ids')
        )
        if conditions:
            stmt = stmt.where(and_(*conditions))
        stmt = stmt.group_by(
            LocalDanmakuItem.title,
            LocalDanmakuItem.mediaType,
            LocalDanmakuItem.year
        )
        stmt = stmt.order_by(LocalDanmakuItem.title)

        # 分页
        count_stmt = select(func.count()).select_from(stmt.subquery())
        total_result = await self._session.execute(count_stmt)
        total = total_result.scalar_one()

        stmt = stmt.offset((page - 1) * page_size).limit(page_size)
        result = await self._session.execute(stmt)
        works = result.all()

        return {
            "list": [
                {
                    "title": work.title,
                    "type": "movie" if work.mediaType == "movie" else "tv_show",
                    "mediaType": work.mediaType,
                    "year": work.year,
                    "tmdbId": work.tmdbId,
                    "tvdbId": work.tvdbId,
                    "imdbId": work.imdbId,
                    "posterUrl": work.posterUrl,
                    "itemCount": work.itemCount,
                    "seasonCount": work.seasonCount if work.mediaType == "tv_series" else None,
                    "episodeCount": work.episodeCount if work.mediaType == "tv_series" else None,
                    "ids": [int(id_str) for id_str in work.ids.split(',')] if work.ids else [],
                }
                for work in works
            ],
            "total": total,
            "page": page,
            "page_size": page_size
        }

    async def get_show_seasons(self, title: str) -> List[Dict[str, Any]]:
        """获取剧集的季度信息"""
        stmt = select(
            LocalDanmakuItem.season,
            func.count(LocalDanmakuItem.id).label('episodeCount'),
            func.max(LocalDanmakuItem.episode).label('maxEpisode'),
            _get_group_concat_func(LocalDanmakuItem.id).label('ids')
        ).where(
            and_(
                LocalDanmakuItem.title == title,
                LocalDanmakuItem.mediaType == "tv_series"
            )
        ).group_by(
            LocalDanmakuItem.season
        ).order_by(
            LocalDanmakuItem.season
        )

        result = await self._session.execute(stmt)
        seasons = result.all()

        return [
            {
                "season": s.season,
                "episodeCount": s.episodeCount,
                "maxEpisode": s.maxEpisode,
                "ids": [int(id_str) for id_str in s.ids.split(',')] if s.ids else []
            }
            for s in seasons
        ]

    async def get_movie_files(
        self,
        title: str,
        year: Optional[int] = None,
        page: int = 1,
        page_size: int = 100
    ) -> Dict[str, Any]:
        """获取电影文件列表"""
        conditions = [
            LocalDanmakuItem.title == title,
            LocalDanmakuItem.mediaType == "movie"
        ]
        if year is not None:
            conditions.append(LocalDanmakuItem.year == year)

        # 查询总数
        count_stmt = select(func.count()).select_from(LocalDanmakuItem).where(and_(*conditions))
        total_result = await self._session.execute(count_stmt)
        total = total_result.scalar_one()

        # 分页查询
        stmt = select(LocalDanmakuItem).where(and_(*conditions))
        stmt = stmt.order_by(LocalDanmakuItem.createdAt.desc())
        stmt = stmt.offset((page - 1) * page_size).limit(page_size)

        result = await self._session.execute(stmt)
        items = result.scalars().all()

        return {
            "list": [
                {
                    "id": item.id,
                    "filePath": item.filePath,
                    "title": item.title,
                    "year": item.year,
                    "tmdbId": item.tmdbId,
                    "imdbId": item.imdbId,
                    "posterUrl": item.posterUrl,
                    "isImported": item.isImported,
                }
                for item in items
            ],
            "total": total,
            "page": page,
            "page_size": page_size
        }

    async def get_season_episodes(
        self,
        title: str,
        season: int,
        page: int = 1,
        page_size: int = 100
    ) -> Dict[str, Any]:
        """获取某一季的分集列表"""
        conditions = [
            LocalDanmakuItem.title == title,
            LocalDanmakuItem.season == season
        ]

        # 查询总数
        count_stmt = select(func.count()).select_from(LocalDanmakuItem).where(and_(*conditions))
        total_result = await self._session.execute(count_stmt)
        total = total_result.scalar_one()

        # 分页查询
        stmt = select(LocalDanmakuItem).where(and_(*conditions))
        stmt = stmt.order_by(LocalDanmakuItem.episode)
        stmt = stmt.offset((page - 1) * page_size).limit(page_size)

        result = await self._session.execute(stmt)
        items = result.scalars().all()

        return {
            "list": [
                {
                    "id": item.id,
                    "filePath": item.filePath,
                    "title": item.title,
                    "season": item.season,
                    "episode": item.episode,
                    "tmdbId": item.tmdbId,
                    "tvdbId": item.tvdbId,
                    "posterUrl": item.posterUrl,
                    "isImported": item.isImported,
                }
                for item in items
            ],
            "total": total,
            "page": page,
            "page_size": page_size
        }
