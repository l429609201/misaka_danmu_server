"""
MediaServerRepository - 媒体服务器数据访问层
"""

import json
import logging
from typing import Optional, List, Dict, Any
from sqlalchemy import select, delete, func
from sqlalchemy.ext.asyncio import AsyncSession

from ..orm_models import MediaServer, MediaItem
from .base import BaseRepository
from src.core.timezone import get_now

logger = logging.getLogger(__name__)


class MediaServerRepository(BaseRepository[MediaServer]):
    """媒体服务器 Repository"""

    async def get_by_id(self, server_id: int) -> Optional[MediaServer]:
        """根据 ID 获取媒体服务器"""
        return await self._session.get(MediaServer, server_id)

    async def get_all(self, **filters) -> List[MediaServer]:
        """获取所有媒体服务器"""
        stmt = select(MediaServer).order_by(MediaServer.createdAt)

        if 'is_enabled' in filters:
            stmt = stmt.where(MediaServer.isEnabled == filters['is_enabled'])

        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def create(
        self,
        name: str,
        provider_name: str,
        url: str,
        api_token: str,
        is_enabled: bool = True,
        selected_libraries: Optional[List[str]] = None,
        filter_rules: Optional[Dict[str, Any]] = None
    ) -> MediaServer:
        """创建媒体服务器"""
        server = MediaServer(
            name=name,
            providerName=provider_name,
            url=url,
            apiToken=api_token,
            isEnabled=is_enabled,
            selectedLibraries=json.dumps(selected_libraries or []),
            filterRules=json.dumps(filter_rules or {}),
            createdAt=get_now(),
            updatedAt=get_now()
        )
        self._session.add(server)
        await self._session.flush()
        return server

    async def update(self, server_id: int, **data) -> Optional[MediaServer]:
        """更新媒体服务器"""
        server = await self.get_by_id(server_id)
        if not server:
            return None

        field_names = {
            "provider_name": "providerName",
            "api_token": "apiToken",
            "is_enabled": "isEnabled",
            "selected_libraries": "selectedLibraries",
            "filter_rules": "filterRules",
        }
        for key, value in data.items():
            if value is None:
                continue
            field = field_names.get(key, key)
            if field in ("selectedLibraries", "filterRules"):
                value = json.dumps(value)
            if hasattr(server, field):
                setattr(server, field, value)

        server.updatedAt = get_now()
        await self._session.flush()
        return server

    async def delete(self, server_id: int) -> bool:
        """删除媒体服务器"""
        server = await self.get_by_id(server_id)
        if not server:
            return False

        await self._session.delete(server)
        await self._session.flush()
        return True

    async def toggle_enabled(self, server_id: int) -> Optional[MediaServer]:
        """切换启用状态"""
        server = await self.get_by_id(server_id)
        if not server:
            return None

        server.isEnabled = not server.isEnabled
        server.updatedAt = get_now()
        await self._session.flush()
        return server

    async def get_enabled_servers(self) -> List[MediaServer]:
        """获取所有已启用的媒体服务器"""
        return await self.get_all(is_enabled=True)


class MediaItemRepository(BaseRepository[MediaItem]):
    """媒体项 Repository"""

    async def get_by_id(self, item_id: int) -> Optional[MediaItem]:
        """根据 ID 获取媒体项"""
        return await self._session.get(MediaItem, item_id)

    async def get_import_snapshots(self, item_ids: List[int]) -> List[Dict[str, Any]]:
        """分批读取导入字段与所属服务器类型，不返回依赖会话的 ORM 对象。"""
        snapshots = []
        fields = ("id", "title", "mediaType", "season", "episode", "year", "tmdbId",
                  "tvdbId", "imdbId", "posterUrl", "mediaId", "seriesId", "seasonId", "episodeId")
        for start in range(0, len(item_ids), 30000):
            result = await self._session.execute(
                select(MediaItem, MediaServer.providerName)
                .join(MediaServer, MediaServer.id == MediaItem.serverId)
                .where(MediaItem.id.in_(item_ids[start:start + 30000]))
            )
            for item, provider in result.all():
                snapshots.append({**{key: getattr(item, key) for key in fields},
                                  "serverId": item.serverId, "mediaServerType": provider})
        return snapshots


    async def get_all(self, **filters) -> List[MediaItem]:
        """获取所有媒体项"""
        stmt = select(MediaItem)

        if 'server_id' in filters:
            stmt = stmt.where(MediaItem.serverId == filters['server_id'])
        if 'anime_id' in filters:
            stmt = stmt.where(MediaItem.animeId == filters['anime_id'])

        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def create(
        self,
        server_id: int,
        item_id: str,
        item_type: str,
        title: str,
        anime_id: Optional[int] = None,
        **extra_data
    ) -> MediaItem:
        """创建媒体项"""
        item = MediaItem(
            serverId=server_id,
            itemId=item_id,
            itemType=item_type,
            title=title,
            animeId=anime_id,
            createdAt=get_now(),
            **extra_data
        )
        self._session.add(item)
        await self._session.flush()
        return item

    async def upsert_scanned_item(self, server_id: int, media_id: str, values: Dict[str, Any]) -> None:
        """按服务器与源站 ID 更新扫描结果，保留既有导入状态。"""
        result = await self._session.execute(
            select(MediaItem).where(MediaItem.serverId == server_id, MediaItem.mediaId == media_id)
        )
        item = result.scalar_one_or_none()
        if item is None:
            item = MediaItem(serverId=server_id, mediaId=media_id, **values)
            self._session.add(item)
        else:
            for key, value in values.items():
                setattr(item, key, value)
        await self._session.flush()


    async def update(self, item_id: int, **data) -> Optional[MediaItem]:
        """更新媒体项"""
        item = await self.get_by_id(item_id)
        if not item:
            return None

        for key, value in data.items():
            if hasattr(item, key):
                setattr(item, key, value)

        await self._session.flush()
        return item

    async def delete(self, item_id: int) -> bool:
        """删除媒体项"""
        item = await self.get_by_id(item_id)
        if not item:
            return False

        await self._session.delete(item)
        await self._session.flush()
        return True

    async def delete_by_server(self, server_id: int) -> int:
        """删除指定服务器的所有媒体项"""
        stmt = delete(MediaItem).where(MediaItem.serverId == server_id)
        result = await self._session.execute(stmt)
        await self._session.flush()
        return result.rowcount

    async def delete_batch(self, item_ids: List[int]) -> int:
        """批量删除媒体项（分批操作，避免 PostgreSQL 参数限制）"""
        if not item_ids:
            return 0

        # PostgreSQL 的 IN 子句有参数限制，分批处理
        PG_BATCH = 30000
        total_deleted = 0
        for i in range(0, len(item_ids), PG_BATCH):
            batch = item_ids[i:i + PG_BATCH]
            stmt = delete(MediaItem).where(MediaItem.id.in_(batch))
            result = await self._session.execute(stmt)
            total_deleted += int(result.rowcount or 0)

        await self._session.flush()
        return total_deleted
