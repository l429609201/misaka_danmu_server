"""
AnimeGroupRepository - 动漫分组数据访问层
"""

import logging
from typing import Optional, List, Dict, Any
from sqlalchemy import select, update, delete
from sqlalchemy.ext.asyncio import AsyncSession

from ..orm_models import AnimeGroup, Anime
from .base import BaseRepository

logger = logging.getLogger(__name__)


class AnimeGroupRepository(BaseRepository[AnimeGroup]):
    """动漫分组 Repository"""

    async def get_by_id(self, group_id: int) -> Optional[AnimeGroup]:
        """根据 ID 获取分组"""
        return await self._session.get(AnimeGroup, group_id)

    async def get_by_name(self, name: str) -> Optional[AnimeGroup]:
        """根据名称获取分组"""
        stmt = select(AnimeGroup).where(AnimeGroup.name == name)
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def get_all(self, **filters) -> List[AnimeGroup]:
        """获取所有分组"""
        stmt = select(AnimeGroup).order_by(AnimeGroup.id)
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def get_all_groups(self) -> List[Dict[str, Any]]:
        """获取所有分组，按 sortOrder 升序排列，返回字典格式"""
        stmt = select(AnimeGroup).order_by(AnimeGroup.sortOrder, AnimeGroup.createdAt)
        result = await self._session.execute(stmt)
        groups = result.scalars().all()
        return [
            {
                "id": g.id,
                "name": g.name,
                "sortOrder": g.sortOrder,
                "createdAt": g.createdAt,
            }
            for g in groups
        ]

    async def create_group(self, name: str) -> Dict[str, Any]:
        """创建新分组，自动计算 sortOrder"""
        # 计算当前最大 sortOrder
        max_order_stmt = select(AnimeGroup.sortOrder).order_by(AnimeGroup.sortOrder.desc()).limit(1)
        max_order = (await self._session.execute(max_order_stmt)).scalar()
        next_order = (max_order or 0) + 1

        group = AnimeGroup(name=name, sortOrder=next_order)
        self._session.add(group)
        await self._session.flush()
        await self._session.refresh(group)
        return {
            "id": group.id,
            "name": group.name,
            "sortOrder": group.sortOrder,
            "createdAt": group.createdAt,
        }

    async def rename_group(self, group_id: int, name: str) -> bool:
        """重命名分组，返回是否成功"""
        stmt = (
            update(AnimeGroup)
            .where(AnimeGroup.id == group_id)
            .values(name=name)
        )
        result = await self._session.execute(stmt)
        await self._session.flush()
        return result.rowcount > 0

    async def delete_group(self, group_id: int) -> bool:
        """
        删除分组。
        由于 FK 设置了 ON DELETE SET NULL，
        删除后关联的 Anime.groupId 自动置为 null。
        """
        stmt = delete(AnimeGroup).where(AnimeGroup.id == group_id)
        result = await self._session.execute(stmt)
        await self._session.flush()
        return result.rowcount > 0

    async def reorder_groups(self, group_ids: List[int]) -> bool:
        """
        批量更新分组排序。
        group_ids 为前端传来的有序列表，index 即为新的 sortOrder。
        """
        for order, group_id in enumerate(group_ids):
            stmt = (
                update(AnimeGroup)
                .where(AnimeGroup.id == group_id)
                .values(sortOrder=order)
            )
            await self._session.execute(stmt)
        await self._session.flush()
        return True

    async def get_group_by_id(self, group_id: int) -> Optional[Dict[str, Any]]:
        """按 ID 获取分组，返回字典格式"""
        stmt = select(AnimeGroup).where(AnimeGroup.id == group_id)
        result = await self._session.execute(stmt)
        group = result.scalar_one_or_none()
        if group is None:
            return None
        return {
            "id": group.id,
            "name": group.name,
            "sortOrder": group.sortOrder,
            "createdAt": group.createdAt,
        }

    async def set_anime_group(self, anime_id: int, group_id: Optional[int]) -> bool:
        """
        设置或清除条目的分组。
        group_id=None 时表示从分组中移除（ungrouped）。
        """
        stmt = (
            update(Anime)
            .where(Anime.id == anime_id)
            .values(groupId=group_id)
        )
        result = await self._session.execute(stmt)
        await self._session.flush()
        return result.rowcount > 0

    async def create(self, name: str, description: str = None) -> AnimeGroup:
        """创建分组（旧接口）"""
        group = AnimeGroup(name=name, description=description)
        self._session.add(group)
        await self._session.flush()
        return group

    async def update(self, group_id: int, name: str = None, description: str = None) -> Optional[AnimeGroup]:
        """更新分组（旧接口）"""
        group = await self.get_by_id(group_id)
        if not group:
            return None

        if name is not None:
            group.name = name
        if description is not None:
            group.description = description

        await self._session.flush()
        return group

    async def delete(self, group_id: int) -> bool:
        """删除分组（旧接口）"""
        group = await self.get_by_id(group_id)
        if not group:
            return False

        await self._session.delete(group)
        await self._session.flush()
        return True
