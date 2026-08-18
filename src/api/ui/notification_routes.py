"""
通知渠道管理 API 路由
"""

import base64
import logging
import secrets
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

import httpx

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from src.db import crud, get_db_session
from src import security
from src.core import settings as _settings
from src.services import apply_tunnel_from_notification_manager
from src.utils.image_utils import IMAGE_DIR, validate_custom_domain_format, probe_public_domain

logger = logging.getLogger(__name__)
router = APIRouter()
# webhook 端点单独注册，不带 /ui 前缀（企业微信/腾讯服务器回调不需要登录态）
webhook_router = APIRouter()


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


async def _reevaluate_tunnel(request: Request):
    """渠道配置变更后重新评估 VPS 隧道是否需要启停"""
    tunnel_service = getattr(request.app.state, "tunnel_service", None)
    notification_manager = getattr(request.app.state, "notification_manager", None)
    config_manager = getattr(request.app.state, "config_manager", None)
    if not tunnel_service or not notification_manager or not config_manager:
        return
    await apply_tunnel_from_notification_manager(
        tunnel_service=tunnel_service,
        notification_manager=notification_manager,
        config_manager=config_manager,
        local_port=_settings.server.port,
    )


async def _verify_webhook_api_key(api_key: str, session: AsyncSession):
    """验证 Webhook API Key，与 /api/webhook/* 端点保持一致"""
    stored_key = await crud.get_config_value(session, "webhookApiKey", "")
    if not stored_key or not secrets.compare_digest(api_key, stored_key):
        raise HTTPException(status_code=401, detail="无效的 API Key")


_PUBLIC_URL_PROBE_NAME = "notification_public_url_probe.png"
_PUBLIC_URL_PROBE_BYTES = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
)


def _validate_public_domain_format(raw_domain: str) -> str:
    """规范并校验外链模式域名（http/https 均接受，不含凭据），用于公网探测前的格式预检。
    格式不合规时抛 HTTPException(400)。
    """
    domain = validate_custom_domain_format(raw_domain)
    if not domain:
        raise HTTPException(status_code=400, detail="自定义域名格式不正确，需为 http(s)://hostname 格式且不含凭据")
    return domain


async def _probe_public_domain(session: AsyncSession) -> Dict[str, Any]:
    """验证自定义域名能通过真实外网地址读取本服务的图片静态路由。"""
    raw = await crud.get_config_value(session, "custom_api_domain", "")
    domain = _validate_public_domain_format(raw)

    probe_path = IMAGE_DIR / _PUBLIC_URL_PROBE_NAME
    try:
        IMAGE_DIR.mkdir(parents=True, exist_ok=True)
        if not probe_path.is_file():
            probe_path.write_bytes(_PUBLIC_URL_PROBE_BYTES)
    except OSError as e:
        logger.error(f"创建外链模式探测图片失败: {e}")
        raise HTTPException(status_code=500, detail="无法创建图片探测文件，请检查 config/image 目录权限")

    result = await probe_public_domain(domain)
    if not result["ok"]:
        raise HTTPException(status_code=400, detail=result["detail"])
    return result


@router.get("/notification/public-domain/validate", summary="校验外链模式自定义域名")
async def validate_public_domain(
    session: AsyncSession = Depends(get_db_session),
    current_user=Depends(security.get_current_user),
):
    """验证 Token 管理的自定义域名能否公开访问本服务的图片静态路由。"""
    return await _probe_public_domain(session)


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
    # why：外链模式依赖全局域名和真实图片静态路由，校验失败时禁止落库。
    if payload.config.get("image_mode") == "public_url":
        await _probe_public_domain(session)
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
    # config 未提交时保持原值；只有明确保存外链模式才触发验证。
    if payload.config and payload.config.get("image_mode") == "public_url":
        await _probe_public_domain(session)
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

    # 渠道配置变更后重新评估 VPS 隧道
    await _reevaluate_tunnel(request)

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



@webhook_router.get("/notification/channels/{channel_id}/webhook", summary="通知渠道 Webhook URL 验证", include_in_schema=False)
async def channel_webhook_verify(
    channel_id: int,
    request: Request,
    api_key: str = Query(..., description="Webhook API Key"),
    session: AsyncSession = Depends(get_db_session),
):
    """
    企业微信等渠道首次接入时的 GET 验证请求（需携带 ?api_key=）。
    - 有 echostr 参数：走企业微信签名验证，成功则返回明文 echostr
    - 无 echostr 参数：连通性探测，直接返回 200 OK（说明路由可达、api_key 有效）
    """
    await _verify_webhook_api_key(api_key, session)

    # 无 echostr：连通性探测，直接告知路由可达
    if not request.query_params.get("echostr"):
        return {"ok": True, "detail": "Webhook 路由可达，api_key 有效"}

    manager = _get_notification_manager(request)
    channel = manager.get_channel(channel_id)
    if not channel:
        raise HTTPException(status_code=404, detail="渠道不存在或未启用")

    params = dict(request.query_params)
    result = channel.process_webhook_update({"method": "GET", "params": params})
    if result and result is not True:
        # 返回明文 echostr（企业微信要求纯文本响应）
        return PlainTextResponse(content=str(result))
    raise HTTPException(status_code=403, detail="URL 验证失败：签名校验未通过，请检查 msg_token / encoding_aes_key 配置")


@webhook_router.post("/notification/channels/{channel_id}/webhook", summary="通知渠道 Webhook 回调", include_in_schema=False)
async def channel_webhook(
    channel_id: int,
    request: Request,
    api_key: str = Query(..., description="Webhook API Key"),
    session: AsyncSession = Depends(get_db_session),
):
    """通用 Webhook 回调入口，按 channel_id 路由到对应渠道实例（需携带 ?api_key=）"""
    await _verify_webhook_api_key(api_key, session)

    manager = _get_notification_manager(request)
    channel = manager.get_channel(channel_id)
    if not channel:
        raise HTTPException(status_code=404, detail="渠道不存在或未启用")

    params = dict(request.query_params)
    try:
        body = await request.body()
        body_str = body.decode("utf-8")
    except Exception:
        body_str = ""

    # 尝试 JSON 解析（Telegram 等），否则传原始字符串（企业微信 XML）
    try:
        update_json = await request.json()
        handled = channel.process_webhook_update(update_json)
    except Exception:
        handled = channel.process_webhook_update({"method": "POST", "params": params, "body": body_str})

    if handled:
        return {"ok": True}
    return {"ok": False, "detail": "该渠道不支持 Webhook 回调或未启用 Webhook 模式"}