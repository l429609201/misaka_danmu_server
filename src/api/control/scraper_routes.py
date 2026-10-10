"""
外部控制API - 弹幕源配置管理路由
包含: /scrapers, /scrapers/{provider}
"""

import logging
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from src.services.service_container import get_database_service
from src.services.config_service import ConfigService
from src.services.scraper_manager import ScraperManager
from src.schemas.control import ControlActionResponse, ScraperConfigItem, ScraperConfigUpdate
from .dependencies import get_scraper_manager, get_config_service

logger = logging.getLogger(__name__)

router = APIRouter()


# --- 接口 ---

@router.get("/scrapers", response_model=List[ScraperConfigItem], summary="获取所有弹幕源配置")
async def get_all_scraper_configs(
    manager: ScraperManager = Depends(get_scraper_manager),
    config_service: ConfigService = Depends(get_config_service),
):
    """
    获取所有**已加载**的弹幕源配置信息，包括启用状态、代理开关、分集黑名单、日志开关和搜索超时。
    只返回实际加载成功的源，不包含数据库中残留的无效记录和 'custom' 虚拟源。
    """
    db = get_database_service()
    async with db.transaction():
        # 服务代理同时暴露读写方法，不再访问废弃的独立查询域。
        all_settings = await db.scraper.get_all_scraper_settings()
    # 只返回实际加载了实例的源（交叉校验数据库 + 内存）
    loaded_providers = set(manager.scrapers.keys())

    result = []
    for s in all_settings:
        name = s['providerName']
        if name == 'custom' or name not in loaded_providers:
            continue

        # 分集黑名单
        blacklist = await config_service.get(f"{name}_episode_blacklist_regex", "")

        # 记录原始响应
        log_resp = await config_service.get(f"scraper_{name}_log_responses", "false")
        log_resp_bool = str(log_resp).lower() == "true"

        # 搜索超时
        timeout = await config_service.get(f"scraper_{name}_search_timeout", "30")
        try:
            timeout_int = int(timeout)
        except (ValueError, TypeError):
            timeout_int = 30

        # 信息增强开关
        enrich_enabled = await config_service.get(f"scraper_{name}_enrich_enabled", "false")
        enrich_enabled_bool = str(enrich_enabled).lower() == "true"

        # 信息增强字段列表
        enrich_fields = await config_service.get(f"scraper_{name}_enrich_fields", "")

        result.append(ScraperConfigItem(
            providerName=name,
            isEnabled=s.get('isEnabled', True),
            useProxy=s.get('useProxy', False),
            displayOrder=s.get('displayOrder', 0),
            episodeBlacklistRegex=str(blacklist),
            logRawResponses=log_resp_bool,
            searchTimeout=timeout_int,
            enrichEnabled=enrich_enabled_bool,
            enrichFields=str(enrich_fields),
        ))

    return result


@router.put("/scrapers/{provider}", response_model=ControlActionResponse, summary="更新单个弹幕源配置")
async def update_scraper_config(
    provider: str,
    payload: ScraperConfigUpdate,
    manager: ScraperManager = Depends(get_scraper_manager),
    config_service: ConfigService = Depends(get_config_service),
):
    """
    更新指定弹幕源的配置。只更新请求体中提供的字段，未提供的字段保持不变。

    ### 可更新的配置项
    - **useProxy**: 是否启用代理
    - **episodeBlacklistRegex**: 分集标题正则黑名单
    - **logRawResponses**: 是否记录原始响应到日志文件
    - **searchTimeout**: 搜索超时时间(秒), 范围 1-120
    - **enrichEnabled**: 搜索时是否拉取补齐缺失字段（如简介、海报等）
    - **enrichFields**: 增强字段列表，逗号分隔；留空则继承全局配置。
    """
    # 验证源是否存在（数据库 + 内存实例双校验）
    db = get_database_service()
    async with db.transaction():
        scraper_setting = await db.scraper.get_scraper_setting_by_name(provider)
    if not scraper_setting:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"弹幕源 '{provider}' 不存在。")
    if provider not in manager.scrapers:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"弹幕源 '{provider}' 数据库有记录但未加载，可能源文件已被移除。")

    updated_fields = []

    # 更新代理设置（写 scrapers 表）
    if payload.useProxy is not None:
        async with db.transaction():
            await db.scraper.update_proxy(provider, payload.useProxy)
        updated_fields.append(f"useProxy={payload.useProxy}")

    # 更新分集黑名单（写 config 表）
    if payload.episodeBlacklistRegex is not None:
        key = f"{provider}_episode_blacklist_regex"
        await config_service.set(key, payload.episodeBlacklistRegex)
        updated_fields.append(f"episodeBlacklistRegex='{payload.episodeBlacklistRegex}'")

    # 更新日志开关（写 config 表）
    if payload.logRawResponses is not None:
        key = f"scraper_{provider}_log_responses"
        await config_service.set(key, str(payload.logRawResponses).lower())
        updated_fields.append(f"logRawResponses={payload.logRawResponses}")

    # 更新搜索超时（写 config 表）
    if payload.searchTimeout is not None:
        key = f"scraper_{provider}_search_timeout"
        await config_service.set(key, str(payload.searchTimeout))
        updated_fields.append(f"searchTimeout={payload.searchTimeout}")

    # 更新信息增强开关（写 config 表）
    if payload.enrichEnabled is not None:
        key = f"scraper_{provider}_enrich_enabled"
        await config_service.set(key, str(payload.enrichEnabled).lower())
        updated_fields.append(f"enrichEnabled={payload.enrichEnabled}")

    # 更新信息增强字段列表（写 config 表）
    if payload.enrichFields is not None:
        key = f"scraper_{provider}_enrich_fields"
        await config_service.set(key, payload.enrichFields)
        updated_fields.append(f"enrichFields='{payload.enrichFields}'")

    if not updated_fields:
        return {"message": "未提供任何需要更新的字段。"}

    logger.info(f"外部API更新了弹幕源 '{provider}' 的配置: {', '.join(updated_fields)}")
    return {"message": f"弹幕源 '{provider}' 配置已更新: {', '.join(updated_fields)}"}
