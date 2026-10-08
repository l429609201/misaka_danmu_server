"""
MetadataSourceQueryRepository：元数据源查询仓储

取代 crud.py 中的元数据源相关查询方法
"""

from typing import List, Dict, Any, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.orm_models import MetadataSource
from src.schemas import MetadataSourceSettingUpdate


class MetadataSourceQueryRepository:
    """
    元数据源查询仓储

    取代 crud.py 中的:
    - sync_metadata_sources_to_db
    - get_all_metadata_source_settings
    - get_metadata_source_setting_by_name
    - update_metadata_sources_settings
    - update_metadata_source_specific_settings
    - get_enabled_failover_sources
    """

    def __init__(self, session: AsyncSession):
        self._session = session

    async def sync_metadata_sources_to_db(self, provider_names: List[str]) -> None:
        """
        同步元数据源到数据库（仅添加新的，不删除旧的）

        取代 crud_metadata_source.sync_metadata_sources_to_db

        Args:
            provider_names: 提供方名称列表
        """
        if not provider_names:
            return

        existing_stmt = select(MetadataSource.providerName)
        existing_names = set((await self._session.execute(existing_stmt)).scalars().all())

        to_insert = [
            {"providerName": name}
            for name in provider_names
            if name not in existing_names
        ]

        if to_insert:
            await self._session.execute(
                MetadataSource.__table__.insert(),
                to_insert
            )

    async def get_all_metadata_source_settings(self) -> List[Dict[str, Any]]:
        """
        获取所有元数据源的设置

        取代 crud_metadata_source.get_all_metadata_source_settings

        Returns:
            元数据源设置列表
        """
        stmt = select(MetadataSource).order_by(MetadataSource.displayOrder)
        result = await self._session.execute(stmt)
        return [
            {
                "providerName": s.providerName,
                "isEnabled": s.isEnabled,
                "isFailoverEnabled": s.isFailoverEnabled,
                "displayOrder": s.displayOrder,
                "logRawResponses": s.logRawResponses,
                "useProxy": s.useProxy,
            }
            for s in result.scalars().all()
        ]

    async def get_metadata_source_setting_by_name(
        self,
        provider_name: str
    ) -> Optional[Dict[str, Any]]:
        """
        获取单个元数据源的设置

        取代 crud.get_metadata_source_setting_by_name

        Args:
            provider_name: 提供方名称

        Returns:
            元数据源设置；不存在时返回 None
        """
        source = await self._session.get(MetadataSource, provider_name)
        if not source:
            return None
        return {
            "providerName": source.providerName,
            "isEnabled": source.isEnabled,
            "isFailoverEnabled": source.isFailoverEnabled,
            "displayOrder": source.displayOrder,
            "logRawResponses": source.logRawResponses,
            "useProxy": source.useProxy,
        }

    async def update_metadata_sources_settings(
        self,
        settings: List[MetadataSourceSettingUpdate]
    ) -> None:
        """
        批量更新元数据源设置

        取代 crud.update_metadata_sources_settings

        Args:
            settings: 设置更新列表
        """
        for s in settings:
            source = await self._session.get(MetadataSource, s.providerName)
            if source:
                source.isEnabled = s.isEnabled
                source.isFailoverEnabled = s.isFailoverEnabled
                source.displayOrder = s.displayOrder

    async def update_metadata_source_specific_settings(
        self,
        provider_name: str,
        settings: Dict[str, Any]
    ) -> None:
        """
        更新单个元数据源的特定设置（如 logRawResponses）

        取代 crud.update_metadata_source_specific_settings

        Args:
            provider_name: 提供方名称
            settings: 要更新的设置字典
        """
        source = await self._session.get(MetadataSource, provider_name)
        if source:
            for key, value in settings.items():
                if hasattr(source, key):
                    setattr(source, key, value)

    async def get_enabled_failover_sources(self) -> List[Dict[str, Any]]:
        """
        获取所有已启用故障转移的元数据源

        取代 crud.get_enabled_failover_sources

        Returns:
            启用故障转移的元数据源列表
        """
        stmt = (
            select(MetadataSource)
            .where(MetadataSource.isFailoverEnabled == True)
            .order_by(MetadataSource.displayOrder)
        )
        result = await self._session.execute(stmt)
        return [
            {
                "providerName": s.providerName,
                "displayOrder": s.displayOrder,
            }
            for s in result.scalars().all()
        ]
