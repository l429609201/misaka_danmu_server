"""
通知渠道管理 API 路由
"""

import logging
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from src.db import crud, get_db_session
from src import security

logger = logging.getLogger(__name__)
router = APIRouter()


# ==================== Pydantic Models ====================

class ChannelCreate(BaseModel):
    name: str
    channelType: str
    isEnabled: bool = True
    useProxy: bool = False
    config: Dict[str, Any] = {}
    eventsConfig: Dict[str, Any] = {}


class ChannelUpdate(BaseModel):
    name: Optional[str] = None
    channelType: Optional[str] = None
    isEnabled: Optional[bool] = None
    useProxy: Optional[bool] = None
    config: Optional[Dict[str, Any]] = None
    eventsConfig: Optional[Dict[str, Any]] = None


# ==================== Helper ====================

def _get_notification_manager(request: Request):
    manager = getattr(request.app.state, "notification_manager", None)
    if not manager:
        raise HTTPException(status_code=503, detail="通知服务未初始化")
    return manager


# ==================== Routes ====================

@router.get("/notification/channel-types", summary="获取可用渠道类型及 Schema")
async def get_channel_types(
    request: Request,
    current_user=Depends(security.get_current_user),
):
    manager = _get_notification_manager(request)
    return manager.get_available_channel_types()


@router.get("/notification/schema/{channel_type}", summary="获取指定渠道类型的配置 Schema")
async def get_channel_schema(
    channel_type: str,
    request: Request,
    current_user=Depends(security.get_current_user),
):
    manager = _get_notification_manager(request)
    schema = manager.get_channel_schema(channel_type)
    if schema is None:
        raise HTTPException(status_code=404, detail=f"未知的渠道类型: {channel_type}")
    return schema


@router.get("/notification/channels", summary="获取所有通知渠道")
async def list_channels(
    session: AsyncSession = Depends(get_db_session),
    current_user=Depends(security.get_current_user),
):
    channels = await crud.get_all_notification_channels(session)
    return channels


@router.post("/notification/channels", status_code=201, summary="新增通知渠道")
async def create_channel(
    payload: ChannelCreate,
    request: Request,
    session: AsyncSession = Depends(get_db_session),
    current_user=Depends(security.get_current_user),
):
    channel_id = await crud.create_notification_channel(
        session,
        name=payload.name,
        channel_type=payload.channelType,
        is_enabled=payload.isEnabled,
        use_proxy=payload.useProxy,
        config=payload.config,
        events_config=payload.eventsConfig,
    )
    await session.commit()

    # 如果启用，加载到管理器
    if payload.isEnabled:
        manager = _get_notification_manager(request)
        await manager.reload_channel(channel_id)

    channel = await crud.get_notification_channel_by_id(session, channel_id)
    return channel


@router.put("/notification/channels/{channel_id}", summary="更新通知渠道")
async def update_channel(
    channel_id: int,
    payload: ChannelUpdate,
    request: Request,
    session: AsyncSession = Depends(get_db_session),
    current_user=Depends(security.get_current_user),
):
    success = await crud.update_notification_channel(
        session, channel_id,
        name=payload.name,
        channel_type=payload.channelType,
        is_enabled=payload.isEnabled,
        use_proxy=payload.useProxy,
        config=payload.config,
        events_config=payload.eventsConfig,
    )
    if not success:
        raise HTTPException(status_code=404, detail="通知渠道不存在")
    await session.commit()

    manager = _get_notification_manager(request)
    await manager.reload_channel(channel_id)

    channel = await crud.get_notification_channel_by_id(session, channel_id)
    return channel


@router.delete("/notification/channels/{channel_id}", status_code=204, summary="删除通知渠道")
async def delete_channel(
    channel_id: int,
    request: Request,
    session: AsyncSession = Depends(get_db_session),
    current_user=Depends(security.get_current_user),
):
    success = await crud.delete_notification_channel(session, channel_id)
    if not success:
        raise HTTPException(status_code=404, detail="通知渠道不存在")
    await session.commit()

    manager = _get_notification_manager(request)
    await manager.remove_channel(channel_id)




@router.post("/notification/channels/{channel_id}/test", summary="测试通知渠道连接")
async def test_channel(
    channel_id: int,
    request: Request,
    session: AsyncSession = Depends(get_db_session),
    current_user=Depends(security.get_current_user),
):
    ch_data = await crud.get_notification_channel_by_id(session, channel_id)
    if not ch_data:
        raise HTTPException(status_code=404, detail="通知渠道不存在")

    manager = _get_notification_manager(request)
    # 优先用已加载的实例测试
    channel_instance = manager.get_channel(channel_id)
    if channel_instance:
        result = await channel_instance.test_connection()
        return result

    # 未加载时临时创建实例测试
    channel_type = ch_data["channelType"]
    cls_map = {ct["channelType"]: ct for ct in manager.get_available_channel_types()}
    if channel_type not in manager._channel_classes:
        raise HTTPException(status_code=400, detail=f"未知的渠道类型: {channel_type}")

    cls = manager._channel_classes[channel_type]
    config = ch_data.get("config", {})
    temp = cls(channel_id=channel_id, name=ch_data["name"], config=config, notification_service=manager.notification_service)
    result = await temp.test_connection()
    return result


@router.post("/notification/channels/{channel_id}/webhook", summary="通知渠道 Webhook 回调", include_in_schema=False)
async def channel_webhook(channel_id: int, request: Request):
    """通用 Webhook 回调入口，按 channel_id 路由到对应渠道实例"""
    manager = _get_notification_manager(request)
    channel = manager.get_channel(channel_id)
    if not channel:
        raise HTTPException(status_code=404, detail="渠道不存在或未启用")

    update_json = await request.json()
    handled = channel.process_webhook_update(update_json)
    if handled:
        return {"ok": True}
    return {"ok": False, "detail": "该渠道不支持 Webhook 回调或未启用 Webhook 模式"}