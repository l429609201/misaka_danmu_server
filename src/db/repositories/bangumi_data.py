"""bangumi-data 离线索引的单表写入仓储。"""
from typing import Any
from sqlalchemy import delete, select
from src.db.orm_models import BangumiDataIndex
from src.db.repositories.base import BaseRepository

class BangumiDataRepository(BaseRepository[BangumiDataIndex]):
    """在调用方事务内原子替换或清空离线索引。"""
    async def replace_all(self, rows: list[dict[str, Any]]) -> int:
        """清表并写入新快照；提交由 DatabaseService 负责。"""
        await self._session.execute(delete(BangumiDataIndex))
        self._session.add_all(BangumiDataIndex(**row) for row in rows)
        await self._session.flush()
        return len(rows)

    async def clear(self) -> None:
        """清空索引，不提交调用方事务。"""
        await self._session.execute(delete(BangumiDataIndex))
        await self._session.flush()

    async def get_by_id(self, id: int) -> BangumiDataIndex | None:
        """按内部主键读取记录。"""
        return await self._session.get(BangumiDataIndex, id)

    async def get_all(self, **filters: Any) -> list[BangumiDataIndex]:
        """按单表字段读取记录。"""
        result = await self._session.execute(select(BangumiDataIndex).filter_by(**filters))
        return list(result.scalars().all())

    async def create(self, **data: Any) -> BangumiDataIndex:
        """在当前事务内创建记录。"""
        row = BangumiDataIndex(**data)
        self._session.add(row)
        await self._session.flush()
        return row

    async def update(self, id: int, **data: Any) -> BangumiDataIndex | None:
        """更新记录并 flush。"""
        row = await self.get_by_id(id)
        if row is not None:
            for key, value in data.items():
                setattr(row, key, value)
            await self._session.flush()
        return row

    async def delete(self, id: int) -> bool:
        """删除单条记录。"""
        row = await self.get_by_id(id)
        if row is None:
            return False
        await self._session.delete(row)
        await self._session.flush()
        return True
