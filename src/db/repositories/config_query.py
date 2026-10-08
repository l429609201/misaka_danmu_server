"""
ConfigQueryRepository：配置项查询仓储

承接原 crud.py 中的配置项与通知渠道查询方法。
本层只做数据库读取，不负责事务提交。
"""

from typing import Any, Dict, List, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.orm_models import Config, NotificationChannel


def _channel_to_dict(channel: NotificationChannel) -> Dict[str, Any]:
    """将通知渠道 ORM 对象转为字典（字段名对齐 orm_models.NotificationChannel）"""
    return {
        "id": channel.id,
        "name": channel.name,
        "channelType": channel.channelType,
        "isEnabled": channel.isEnabled,
        "useProxy": channel.useProxy,
        "config": channel.config or "{}",
        "eventsConfig": channel.eventsConfig or "{}",
        "createdAt": channel.createdAt,
        "updatedAt": channel.updatedAt,
    }


class ConfigQueryRepository:
    """
    配置项查询仓储

    承接原 crud.py 中的:
    - get_config_value
    - get_all_notification_channels
    - get_notification_channel_by_id
    """

    def __init__(self, session: AsyncSession):
        self._session = session

    async def get_config_value(self, key: str, default: str = "") -> str:
        """
        获取配置项值

        Args:
            key: 配置键名
            default: 键不存在时返回的默认值

        Returns:
            配置值（字符串）
        """
        result = await self._session.execute(
            select(Config.configValue).where(Config.configKey == key)
        )
        row = result.scalar_one_or_none()
        return row if row is not None else default

    async def get_all_notification_channels(self) -> List[Dict[str, Any]]:
        """获取所有通知渠道配置"""
        result = await self._session.execute(select(NotificationChannel))
        channels = result.scalars().all()
        return [_channel_to_dict(ch) for ch in channels]

    async def get_notification_channel_by_id(self, channel_id: int) -> Optional[Dict[str, Any]]:
        """
        根据 ID 获取通知渠道配置

        Returns:
            渠道配置字典，不存在时返回 None
        """
        channel = await self._session.get(NotificationChannel, channel_id)
        if not channel:
            return None
        return _channel_to_dict(channel)
