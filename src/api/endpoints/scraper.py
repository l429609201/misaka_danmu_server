"""
搜索源(Scraper)相关的API端点
"""

import logging
from typing import List, Dict, Any, Optional
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from pydantic import BaseModel

from ... import crud, models, security
from ...database import get_db_session
from ...scraper_manager import ScraperManager
from ...config_manager import ConfigManager
from ..dependencies import get_scraper_manager, get_config_manager

router = APIRouter()
logger = logging.getLogger(__name__)


@router.get("/scrapers", response_model=List[models.ScraperSettingWithConfig], summary="获取所有搜索源的设置")
async def get_scraper_settings(
    current_user: models.User = Depends(security.get_current_user),
    session: AsyncSession = Depends(get_db_session),
    manager: ScraperManager = Depends(get_scraper_manager),
    config_manager: ConfigManager = Depends(get_config_manager)
):
    """获取所有可用搜索源的列表及其配置(启用状态、顺序、可配置字段)"""
    all_settings = await crud.get_all_scraper_settings(session)

    # 不应在UI中显示 'custom' 源,因为它不是一个真正的刮削器
    settings = [s for s in all_settings if s.get('providerName') != 'custom']
    
    # 获取验证开关的全局状态
    verification_enabled_str = await config_manager.get("scraper_verification_enabled", "false")
    verification_enabled = verification_enabled_str.lower() == 'true'

    result = []
    for s in settings:
        provider_name = s['providerName']
        scraper_class = manager.get_scraper_class(provider_name)

        # Create a new dictionary with all required fields before validation
        full_setting_data = s.copy()

        if scraper_class:
            full_setting_data['isLoggable'] = getattr(scraper_class, "is_loggable", False)
            # 关键修复：复制类属性以避免修改共享的可变字典
            base_fields = getattr(scraper_class, "configurable_fields", None)
            configurable_fields = base_fields.copy() if base_fields is not None else {}

            # 为当前源动态添加其专属的黑名单配置字段
            blacklist_key = f"{provider_name}_episode_blacklist_regex"
            configurable_fields[blacklist_key] = ("分集标题黑名单 (正则)", "string", "使用正则表达式过滤不想要的分集标题。")
            full_setting_data['configurableFields'] = configurable_fields
            full_setting_data['actions'] = getattr(scraper_class, 'actions', [])
        else:
            # Provide defaults if scraper_class is not found to prevent validation errors
            full_setting_data['isLoggable'] = False
            full_setting_data['configurableFields'] = {}
            full_setting_data['actions'] = []

        full_setting_data['verificationEnabled'] = verification_enabled

        s_with_config = models.ScraperSettingWithConfig.model_validate(full_setting_data)
        result.append(s_with_config)

    return result


@router.put("/scrapers", status_code=status.HTTP_204_NO_CONTENT, summary="更新搜索源的设置")
async def update_scraper_settings(
    settings: List[models.ScraperSetting],
    current_user: models.User = Depends(security.get_current_user),
    manager: ScraperManager = Depends(get_scraper_manager)
):
    """批量更新搜索源的启用状态和显示顺序"""
    await manager.update_settings(settings)
    logger.info(f"用户 '{current_user.username}' 更新了搜索源设置,已重新加载。")
    return


@router.get("/scrapers/{providerName}/config", response_model=Dict[str, Any], summary="获取指定搜索源的配置")
async def get_scraper_config(
    providerName: str,
    current_user: models.User = Depends(security.get_current_user),
    session: AsyncSession = Depends(get_db_session),
    manager: ScraperManager = Depends(get_scraper_manager),
    config_manager: ConfigManager = Depends(get_config_manager)
):
    """
    获取单个搜索源的详细配置,包括其在 `scrapers` 表中的设置(如 useProxy)
    和在 `config` 表中的键值对(如 cookie)
    """
    scraper_class = manager.get_scraper_class(providerName)
    if not scraper_class:
        raise HTTPException(status_code=404, detail="该搜索源不存在。")

    response_data = {}

    # 1. 从 scrapers 表获取 useProxy
    scraper_setting = await crud.get_scraper_setting_by_name(session, providerName)
    if scraper_setting:
        response_data['useProxy'] = scraper_setting.get('useProxy', False)

    # 2. 从 config 表获取其他配置字段
    # 注意: scraper 类中定义的是 configurable_fields,不是 config_fields
    configurable_fields = getattr(scraper_class, 'configurable_fields', {})
    for field_key in configurable_fields.keys():
        # field_key 就是配置键,例如 "gamerCookie"
        value = await config_manager.get(field_key, "")
        response_data[field_key] = value

    # 3. 添加分集黑名单字段(动态添加,每个源都有)
    # 数据库中使用下划线命名: gamer_episode_blacklist_regex
    # 前端期望驼峰命名: gamerEpisodeBlacklistRegex
    blacklist_key_db = f"{providerName}_episode_blacklist_regex"
    blacklist_key_camel = f"{providerName}EpisodeBlacklistRegex"
    blacklist_value = await config_manager.get(blacklist_key_db, "")
    response_data[blacklist_key_camel] = blacklist_value

    return response_data


@router.put("/scrapers/{providerName}/config", status_code=status.HTTP_204_NO_CONTENT, summary="更新指定搜索源的配置")
async def update_scraper_config(
    providerName: str,
    payload: Dict[str, Any],
    current_user: models.User = Depends(security.get_current_user),
    session: AsyncSession = Depends(get_db_session),
    config_manager: ConfigManager = Depends(get_config_manager),
    manager: ScraperManager = Depends(get_scraper_manager)
):
    """更新指定搜索源的配置,包括代理设置和其他可配置字段"""
    try:
        scraper_class = manager.get_scraper_class(providerName)
        if not scraper_class:
            raise HTTPException(status_code=404, detail="该搜索源不存在。")

        # 1. 单独处理 useProxy 字段,它更新的是 scrapers 表
        if 'useProxy' in payload:
            use_proxy = payload.pop('useProxy')
            await crud.update_scraper_proxy(session, providerName, use_proxy)
            await session.commit()

        # 2. 处理其他配置字段,它们更新的是 config 表
        # 注意: scraper 类中定义的是 configurable_fields,不是 config_fields
        configurable_fields = getattr(scraper_class, 'configurable_fields', {})
        for field_key in configurable_fields.keys():
            # field_key 就是配置键,例如 "gamerCookie"
            if field_key in payload:
                await config_manager.set(field_key, payload[field_key])

        # 3. 处理分集黑名单字段(动态字段,每个源都有)
        # 前端发送驼峰命名: gamerEpisodeBlacklistRegex
        # 数据库存储下划线命名: gamer_episode_blacklist_regex
        blacklist_key_camel = f"{providerName}EpisodeBlacklistRegex"
        blacklist_key_db = f"{providerName}_episode_blacklist_regex"
        if blacklist_key_camel in payload:
            await config_manager.set(blacklist_key_db, payload[blacklist_key_camel])

        # 4. 重新加载该搜索源
        manager.reload_scraper(providerName)
        logger.info(f"用户 '{current_user.username}' 更新了搜索源 '{providerName}' 的配置,已重新加载。")
        return

    except Exception as e:
        logger.error(f"更新搜索源 '{providerName}' 配置时出错: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"更新配置失败: {str(e)}")


@router.post("/scrapers/{providerName}/actions/{actionName}", summary="执行搜索源的自定义操作")
async def execute_scraper_action(
    providerName: str,
    actionName: str,
    payload: Dict[str, Any] = None,
    current_user: models.User = Depends(security.get_current_user),
    manager: ScraperManager = Depends(get_scraper_manager)
):
    """
    执行指定搜索源的特定操作
    例如,Bilibili的登录流程可以通过调用 'get_login_info', 'generate_qrcode', 'poll_login' 等操作来驱动
    """
    try:
        scraper = manager.get_scraper(providerName)
        result = await scraper.execute_action(actionName, payload or {})
        return result
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        logger.error(f"执行搜索源 '{providerName}' 的操作 '{actionName}' 时出错: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"操作执行失败: {str(e)}")

