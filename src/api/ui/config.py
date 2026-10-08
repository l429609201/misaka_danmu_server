"""
配置相关的API端点
"""

import logging
import json
import secrets
import string
from typing import Dict, Any, List, Optional
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel

from src.utils.auth import security
from src.services.service_container import get_database_service
from src.core import get_config_schema
from src.api.dependencies import get_config_service
from src.services.config_service import ConfigService
from src.schemas.auth import User

logger = logging.getLogger(__name__)
router = APIRouter()


# --- Pydantic Models ---

class ConfigValueResponse(BaseModel):
    value: str


class ConfigValueRequest(BaseModel):
    value: str


# --- API Endpoints ---

@router.get("/schema/parameters", summary="获取参数配置的 Schema")
async def get_parameters_schema(
    current_user: User = Depends(security.get_current_user)
):
    """
    获取参数配置页面的 Schema 定义。
    前端根据此 Schema 动态渲染配置界面。
    """
    return get_config_schema()


@router.get("/{config_key}", response_model=Dict[str, str], summary="获取指定配置项的值")
async def get_config_item(
    config_key: str,
    current_user: User = Depends(security.get_current_user)
):
    """获取数据库中单个配置项的值。"""
    db = get_database_service()
    async with db.transaction():
        value = await db.config.get_value(config_key, "")  # 默认为空字符串
    return {"key": config_key, "value": value}


@router.put("/{config_key}", status_code=status.HTTP_204_NO_CONTENT, summary="更新指定配置项的值")
async def update_config_item(
    config_key: str,
    payload: Dict[str, Any],  # 修正：允许任意类型的值,避免前端传递undefined时报错
    current_user: User = Depends(security.get_current_user),
    config_service: ConfigService = Depends(get_config_service)
):
    """更新数据库中单个配置项的值。"""
    value = payload.get("value")
    if value is None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Missing 'value' in request body")

    # 确保value是字符串类型
    value_str = str(value) if value is not None else ""

    # 输入框的最小值不能替代服务端校验，避免保存成功却使用了不同的缓存寿命。
    if config_key == "searchTtlSeconds":
        try:
            ttl = int(value_str.strip())
        except (ValueError, TypeError) as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="搜索缓存时间必须是整数，且不得小于10800秒（3小时）",
            ) from exc
        if ttl < 10800:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="搜索缓存时间不得小于10800秒（3小时）",
            )
        value_str = str(ttl)


    db = get_database_service()
    async with db.transaction():
        await db.config.upsert(config_key, value_str)

    config_service.invalidate(config_key)
    logger.info(f"用户 '{current_user.username}' 更新了配置项 '{config_key}'。")


@router.post("/webhookApiKey/regenerate", response_model=Dict[str, str], summary="重新生成Webhook API Key")
async def regenerate_webhook_api_key(
    current_user: User = Depends(security.get_current_user),
    config_service: ConfigService = Depends(get_config_service)
):
    """生成一个新的、随机的Webhook API Key并保存到数据库。"""
    alphabet = string.ascii_letters + string.digits
    new_key = ''.join(secrets.choice(alphabet) for _ in range(20))

    db = get_database_service()
    async with db.transaction():
        await db.config.upsert("webhookApiKey", new_key)

    config_service.invalidate("webhookApiKey")
    logger.info(f"用户 '{current_user.username}' 重新生成了 Webhook API Key。")
    return {"key": "webhookApiKey", "value": new_key}


@router.post("/externalApiKey/regenerate", response_model=Dict[str, str], summary="重新生成外部API Key")
async def regenerate_external_api_key(
    current_user: User = Depends(security.get_current_user),
    config_service: ConfigService = Depends(get_config_service)
):
    """生成一个新的、随机的外部API Key并保存到数据库。"""
    alphabet = string.ascii_letters + string.digits
    new_key = ''.join(secrets.choice(alphabet) for _ in range(32))  # 增加长度以提高安全性

    db = get_database_service()
    async with db.transaction():
        await db.config.upsert("externalApiKey", new_key)

    config_service.invalidate("externalApiKey")
    logger.info(f"用户 '{current_user.username}' 重新生成了外部 API Key。")
    return {"key": "externalApiKey", "value": new_key}



