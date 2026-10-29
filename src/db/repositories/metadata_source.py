"""
MetadataSourceRepository - 元数据源数据访问层
"""

import logging
from typing import Optional, List
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..orm_models import MetadataSource
from .base import BaseRepository

logger = logging.getLogger(__name__)


class MetadataSourceRepository(BaseRepository[MetadataSource]):
    """元数据源 Repository"""
    
    async def get_by_id(self, provider_name: str) -> Optional[MetadataSource]:
        """根据 provider 名称获取元数据源"""
        return await self._session.get(MetadataSource, provider_name)
    
    async def get_all(self, **filters) -> List[MetadataSource]:
        """获取所有元数据源"""
        stmt = select(MetadataSource)
        result = await self._session.execute(stmt)
        return list(result.scalars().all())
    
    async def get_enabled(self) -> List[MetadataSource]:
        """获取所有已启用的元数据源"""
        stmt = select(MetadataSource).where(MetadataSource.isEnabled == True)
        result = await self._session.execute(stmt)
        return list(result.scalars().all())
    
    async def create(self, provider_name: str, **kwargs) -> MetadataSource:
        """创建元数据源"""
        source = MetadataSource(providerName=provider_name, **kwargs)
        self._session.add(source)
        await self._session.flush()
        return source
    
    async def update(self, provider_name: str, **kwargs) -> Optional[MetadataSource]:
        """更新元数据源"""
        source = await self.get_by_id(provider_name)
        if not source:
            return None
        
        for key, value in kwargs.items():
            if hasattr(source, key):
                setattr(source, key, value)
        
        await self._session.flush()
        return source
    
    async def delete(self, provider_name: str) -> bool:
        """删除元数据源"""
        source = await self.get_by_id(provider_name)
        if not source:
            return False
        
        await self._session.delete(source)
        await self._session.flush()
        return True
    
    async def toggle_enable(self, provider_name: str) -> Optional[MetadataSource]:
        """切换元数据源启用状态"""
        source = await self.get_by_id(provider_name)
        if not source:
            return None
        
        source.isEnabled = not source.isEnabled
        await self._session.flush()
        return source
