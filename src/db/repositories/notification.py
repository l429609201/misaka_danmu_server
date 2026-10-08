"""
NotificationRepository - 通知渠道数据访问层

负责通知渠道的数据库操作，返回 ORM 对象。
数据转换由 Service 层负责。
"""

import logging
from typing import List, Optional, Dict, Any
from sqlalchemy import select, delete
from sqlalchemy.ext.asyncio import AsyncSession

from ..orm_models import NotificationChannel
from .base import BaseRepository
from src.core.timezone import get_now

logger = logging.getLogger(__name__)


class NotificationRepository(BaseRepository[NotificationChannel]):
    """
    通知渠道 Repository
    
    职责：
    - 封装通知渠道的数据库访问
    - 返回 NotificationChannel ORM 对象
    - 不处理 JSON 序列化等业务逻辑
    """
    
    async def get_by_id(self, channel_id: int) -> Optional[NotificationChannel]:
        """
        根据 ID 获取通知渠道
        
        Args:
            channel_id: 渠道 ID
            
        Returns:
            NotificationChannel ORM 对象，不存在时返回 None
        """
        return await self._session.get(NotificationChannel, channel_id)
    
    async def get_all(self, **filters) -> List[NotificationChannel]:
        """
        获取所有通知渠道
        
        Args:
            **filters: 过滤条件（暂未实现）
            
        Returns:
            NotificationChannel ORM 对象列表
        """
        stmt = select(NotificationChannel).order_by(NotificationChannel.createdAt)
        result = await self._session.execute(stmt)
        return list(result.scalars().all())
    
    async def get_enabled_channels(self) -> List[NotificationChannel]:
        """
        获取所有已启用的通知渠道
        
        Returns:
            已启用的 NotificationChannel ORM 对象列表
        """
        stmt = select(NotificationChannel).where(
            NotificationChannel.isEnabled == True
        ).order_by(NotificationChannel.createdAt)
        result = await self._session.execute(stmt)
        return list(result.scalars().all())
    
    async def find_by_type(self, channel_type: str) -> List[NotificationChannel]:
        """
        根据类型查找通知渠道
        
        Args:
            channel_type: 渠道类型（telegram, wechat, bark 等）
            
        Returns:
            匹配的 NotificationChannel ORM 对象列表
        """
        stmt = select(NotificationChannel).where(
            NotificationChannel.channelType == channel_type
        ).order_by(NotificationChannel.createdAt)
        result = await self._session.execute(stmt)
        return list(result.scalars().all())
    
    async def create(
        self,
        name: str,
        channel_type: str,
        is_enabled: bool = True,
        use_proxy: bool = False,
        config: str = "{}",
        events_config: str = "{}",
    ) -> NotificationChannel:
        """
        创建新的通知渠道
        
        Args:
            name: 渠道名称
            channel_type: 渠道类型
            is_enabled: 是否启用
            use_proxy: 是否使用代理
            config: 配置（JSON 字符串）
            events_config: 事件配置（JSON 字符串）
            
        Returns:
            创建的 NotificationChannel ORM 对象
        """
        now = get_now()
        new_channel = NotificationChannel(
            name=name,
            channelType=channel_type,
            isEnabled=is_enabled,
            useProxy=use_proxy,
            config=config,
            eventsConfig=events_config,
            createdAt=now,
            updatedAt=now,
        )
        self._session.add(new_channel)
        await self._session.flush()
        return new_channel
    
    async def update(
        self,
        channel_id: int,
        name: Optional[str] = None,
        channel_type: Optional[str] = None,
        is_enabled: Optional[bool] = None,
        use_proxy: Optional[bool] = None,
        config: Optional[str] = None,
        events_config: Optional[str] = None,
    ) -> Optional[NotificationChannel]:
        """
        更新通知渠道
        
        Args:
            channel_id: 渠道 ID
            name: 新名称
            channel_type: 新类型
            is_enabled: 是否启用
            use_proxy: 是否使用代理
            config: 新配置（JSON 字符串）
            events_config: 新事件配置（JSON 字符串）
            
        Returns:
            更新后的 NotificationChannel ORM 对象，不存在时返回 None
        """
        channel = await self.get_by_id(channel_id)
        if not channel:
            return None
        
        if name is not None:
            channel.name = name
        if channel_type is not None:
            channel.channelType = channel_type
        if is_enabled is not None:
            channel.isEnabled = is_enabled
        if use_proxy is not None:
            channel.useProxy = use_proxy
        if config is not None:
            channel.config = config
        if events_config is not None:
            channel.eventsConfig = events_config
        
        channel.updatedAt = get_now()
        await self._session.flush()
        return channel

    async def delete(self, channel_id: int) -> bool:
        """
        删除通知渠道

        Args:
            channel_id: 渠道 ID

        Returns:
            是否删除成功
        """
        channel = await self.get_by_id(channel_id)
        if not channel:
            return False

        await self._session.delete(channel)
        await self._session.flush()
        return True

    async def toggle_enable(self, channel_id: int) -> Optional[NotificationChannel]:
        """
        切换渠道启用状态

        Args:
            channel_id: 渠道 ID

        Returns:
            更新后的 NotificationChannel ORM 对象，不存在时返回 None
        """
        channel = await self.get_by_id(channel_id)
        if not channel:
            return None

        channel.isEnabled = not channel.isEnabled
        channel.updatedAt = get_now()
        await self._session.flush()
        return channel
