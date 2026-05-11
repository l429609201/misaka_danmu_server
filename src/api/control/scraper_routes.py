"""
外部控制API - 弹幕源配置管理路由
包含: /scrapers, /scrapers/{provider}
"""

import logging
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from src.db import crud, get_db_session, ConfigManager
from src.services import ScraperManager

from .dependencies import get_scraper_manager, get_config_manager
from .models import ControlActionResponse

logger = logging.getLogger(__name__)

router = APIRouter()


# --- 响应/请求模型 ---

class ScraperConfigItem(BaseModel):
    """单个弹幕源的配置信息"""
    providerName: str = Field(..., description="弹幕源名称")
    isEnabled: bool = Field(..., description="是否启用")
    useProxy: bool = Field(..., description="是否启用代理")
    displayOrder: int = Field(..., description="显示顺序")
    episodeBlacklistRegex: str = Field("", description="分集标题正则黑名单")
    logRawResponses: bool = Field(False, description="是否记录原始响应")
    searchTimeout: int = Field(15, description="搜索超时时间(秒)")


class ScraperConfigUpdate(BaseModel):
    """更新单个弹幕源配置的请求体"""
    useProxy: Optional[bool] = Field(None, description="是否启用代理")
    episodeBlacklistRegex: Optional[str] = Field(None, description="分集标题正则黑名单")
    logRawResponses: Optional[bool] = Field(None, description="是否记录原始响应")
    searchTimeout: Optional[int] = Field(None, ge=1, le=120, description="搜索超时时间(秒), 1-120")


# --- 接口 ---

@router.get("/scrapers", response_model=List[ScraperConfigItem], summary="获取所有弹幕源配置")
async def get_all_scraper_configs(
    session: AsyncSession = Depends(get_db_session),
    manager: ScraperManager = Depends(get_scraper_manager),
    config_manager: ConfigManager = Depends(get_config_manager),
):
    """
    获取所有弹幕源的配置信息，包括启用状态、代理开关、分集黑名单、日志开关和搜索超时。
    不包含 'custom' 虚拟源。
    """
    all_settings = await crud.get_all_scraper_settings(session)
    settings = [s for s in all_settings if s.get('providerName') != 'custom']

    result = []
    for s in settings:
        name = s['providerName']

        # 分集黑名单
        blacklist = await config_manager.get(f"{name}_episode_blacklist_regex", "")

        # 记录原始响应
        log_resp = await config_manager.get(f"scraper_{name}_log_responses", "false")
        log_resp_bool = str(log_resp).lower() == "true"

        # 搜索超时
        timeout = await config_manager.get(f"scraper_{name}_search_timeout", "15")
        try:
            timeout_int = int(timeout)
        except (ValueError, TypeError):
            timeout_int = 15

        result.append(ScraperConfigItem(
            providerName=name,
            isEnabled=s.get('isEnabled', True),
            useProxy=s.get('useProxy', False),
            displayOrder=s.get('displayOrder', 0),
            episodeBlacklistRegex=str(blacklist),
            logRawResponses=log_resp_bool,
            searchTimeout=timeout_int,
        ))

    return result


@router.put("/scrapers/{provider}", response_model=ControlActionResponse, summary="更新单个弹幕源配置")
async def update_scraper_config(
    provider: str,
    payload: ScraperConfigUpdate,
    session: AsyncSession = Depends(get_db_session),
    manager: ScraperManager = Depends(get_scraper_manager),
    config_manager: ConfigManager = Depends(get_config_manager),
):
    """
    更新指定弹幕源的配置。只更新请求体中提供的字段，未提供的字段保持不变。

    ### 可更新的配置项
    - **useProxy**: 是否启用代理
    - **episodeBlacklistRegex**: 分集标题正则黑名单
    - **logRawResponses**: 是否记录原始响应到日志文件
    - **searchTimeout**: 搜索超时时间(秒), 范围 1-120
    """
    # 验证源是否存在
    scraper_setting = await crud.get_scraper_setting_by_name(session, provider)
    if not scraper_setting:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"弹幕源 '{provider}' 不存在。")

    updated_fields = []

    # 更新代理设置（写 scrapers 表）
    if payload.useProxy is not None:
        await crud.update_scraper_proxy(session, provider, payload.useProxy)
        await session.commit()
        updated_fields.append(f"useProxy={payload.useProxy}")

    # 更新分集黑名单（写 config 表）
    if payload.episodeBlacklistRegex is not None:
        key = f"{provider}_episode_blacklist_regex"
        await config_manager.setValue(key, payload.episodeBlacklistRegex)
        updated_fields.append(f"episodeBlacklistRegex='{payload.episodeBlacklistRegex}'")

    # 更新日志开关（写 config 表）
    if payload.logRawResponses is not None:
        key = f"scraper_{provider}_log_responses"
        await config_manager.setValue(key, str(payload.logRawResponses).lower())
        updated_fields.append(f"logRawResponses={payload.logRawResponses}")

    # 更新搜索超时（写 config 表）
    if payload.searchTimeout is not None:
        key = f"scraper_{provider}_search_timeout"
        await config_manager.setValue(key, str(payload.searchTimeout))
        updated_fields.append(f"searchTimeout={payload.searchTimeout}")

    if not updated_fields:
        return {"message": "未提供任何需要更新的字段。"}

    logger.info(f"外部API更新了弹幕源 '{provider}' 的配置: {', '.join(updated_fields)}")
    return {"message": f"弹幕源 '{provider}' 配置已更新: {', '.join(updated_fields)}"}
