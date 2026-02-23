"""
通知渠道相关的CRUD操作
"""

import json
import logging
from typing import List, Optional, Dict, Any
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from src.core.timezone import get_now
from .. import orm_models

logger = logging.getLogger(__name__)


async def get_all_notification_channels(session: AsyncSession) -> List[Dict[str, Any]]:
    """获取所有通知渠道"""
    stmt = select(orm_models.NotificationChannel).order_by(orm_models.NotificationChannel.createdAt)
    result = await session.execute(stmt)
    channels = result.scalars().all()
    return [
        {
            "id": c.id,
            "name": c.name,
            "channelType": c.channelType,
            "isEnabled": c.isEnabled,
            "useProxy": c.useProxy,
            "config": json.loads(c.config) if c.config else {},
            "eventsConfig": json.loads(c.eventsConfig) if c.eventsConfig else {},
            "createdAt": c.createdAt,
            "updatedAt": c.updatedAt,
        }
        for c in channels
    ]


async def get_notification_channel_by_id(session: AsyncSession, channel_id: int) -> Optional[Dict[str, Any]]:
    """根据ID获取通知渠道"""
    channel = await session.get(orm_models.NotificationChannel, channel_id)
    if not channel:
        return None
    return {
        "id": channel.id,
        "name": channel.name,
        "channelType": channel.channelType,
        "isEnabled": channel.isEnabled,
        "useProxy": channel.useProxy,
        "config": json.loads(channel.config) if channel.config else {},
        "eventsConfig": json.loads(channel.eventsConfig) if channel.eventsConfig else {},
        "createdAt": channel.createdAt,
        "updatedAt": channel.updatedAt,
    }


async def create_notification_channel(
    session: AsyncSession,
    name: str,
    channel_type: str,
    is_enabled: bool = True,
    use_proxy: bool = False,
    config: Optional[Dict[str, Any]] = None,
    events_config: Optional[Dict[str, Any]] = None,
) -> int:
    """创建新的通知渠道"""
    new_channel = orm_models.NotificationChannel(
        name=name,
        channelType=channel_type,
        isEnabled=is_enabled,
        useProxy=use_proxy,
        config=json.dumps(config or {}),
        eventsConfig=json.dumps(events_config or {}),
        createdAt=get_now(),
        updatedAt=get_now(),
    )
    session.add(new_channel)
    await session.flush()
    return new_channel.id


async def update_notification_channel(
    session: AsyncSession,
    channel_id: int,
    name: Optional[str] = None,
    channel_type: Optional[str] = None,
    is_enabled: Optional[bool] = None,
    use_proxy: Optional[bool] = None,
    config: Optional[Dict[str, Any]] = None,
    events_config: Optional[Dict[str, Any]] = None,
) -> bool:
    """更新通知渠道"""
    channel = await session.get(orm_models.NotificationChannel, channel_id)
    if not channel:
        return False

    if name is not None:
        channel.name = name
    if channel_type is not None:
        channel.channelType = channel_type
    if is_enabled is not None:
        channel.isEnabled = is_enabled
    if use_proxy is not None:
        channel.useProxy = use_proxy
    if config is not None:
        channel.config = json.dumps(config)
    if events_config is not None:
        channel.eventsConfig = json.dumps(events_config)

    channel.updatedAt = get_now()
    await session.flush()
    return True


async def delete_notification_channel(session: AsyncSession, channel_id: int) -> bool:
    """删除通知渠道"""
    channel = await session.get(orm_models.NotificationChannel, channel_id)
    if not channel:
        return False
    await session.delete(channel)
    await session.flush()
    return True

