"""
搜索源(Scraper)相关的API端点
"""

import logging
import httpx
from typing import List, Dict, Any, Optional
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from pydantic import BaseModel

from src.db import crud, models, get_db_session, ConfigManager
from src import security
from src.services import ScraperManager
from src.api.dependencies import get_scraper_manager, get_config_manager

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
            # 读取 display_name，优先使用弹幕源自定义的友好名称
            full_setting_data['displayName'] = getattr(scraper_class, "display_name", None)
            # 关键修复：复制类属性以避免修改共享的可变字典
            base_fields = getattr(scraper_class, "configurable_fields", None)
            configurable_fields = base_fields.copy() if base_fields is not None else {}

            # 为当前源动态添加其专属的黑名单配置字段
            blacklist_key = f"{provider_name}_episode_blacklist_regex"
            configurable_fields[blacklist_key] = ("分集标题黑名单 (正则)", "string", "使用正则表达式过滤不想要的分集标题。")
            full_setting_data['configurableFields'] = configurable_fields
            full_setting_data['actions'] = getattr(scraper_class, 'actions', [])
            # 从 ScraperManager 获取版本号
            full_setting_data['version'] = manager.get_scraper_version(provider_name)
        else:
            # Provide defaults if scraper_class is not found to prevent validation errors
            full_setting_data['isLoggable'] = False
            full_setting_data['configurableFields'] = {}
            full_setting_data['actions'] = []
            full_setting_data['version'] = None

        full_setting_data['verificationEnabled'] = verification_enabled

        # 从 config 表读取该源的日志记录开关（DB key 为下划线格式）
        log_resp_key = f"scraper_{provider_name}_log_responses"
        log_resp_str = await config_manager.get(log_resp_key, "false")
        full_setting_data['logRawResponses'] = str(log_resp_str).lower() == "true"

        try:
            s_with_config = models.ScraperSettingWithConfig.model_validate(full_setting_data)
            result.append(s_with_config)
        except Exception as e:
            logger.warning(
                f"搜索源 '{provider_name}' 数据校验失败，已跳过（不影响其他源）: {e}"
            )

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
    for field_key, field_info in configurable_fields.items():
        # field_key 就是配置键,例如 "gamerCookie" 或 "dandanplay_app_id"
        value = await config_manager.get(field_key, "")

        # 获取字段类型 (label, type, tooltip)
        field_type = field_info[1] if isinstance(field_info, tuple) and len(field_info) > 1 else "string"

        # 布尔类型字段需要转换为布尔值返回给前端
        if field_type == "boolean":
            if isinstance(value, bool):
                pass  # 已经是布尔值
            else:
                value = str(value).lower() == 'true'

        # 对于dandanplay的下划线命名字段,转换为驼峰命名返回给前端
        if providerName == 'dandanplay' and '_' in field_key:
            # dandanplay_app_id -> dandanplayAppId
            # dandanplay_app_secret -> dandanplayAppSecret
            # dandanplay_app_secret_alt -> dandanplayAppSecretAlt
            # dandanplay_base_url -> dandanplayBaseUrl
            # dandanplay_proxy_config -> dandanplayProxyConfig
            parts = field_key.split('_')
            camel_key = parts[0] + ''.join(word.capitalize() for word in parts[1:])
            response_data[camel_key] = value
        else:
            response_data[field_key] = value

    # 3. 添加分集黑名单字段(动态添加,每个源都有)
    # 数据库中使用下划线命名: gamer_episode_blacklist_regex
    # 前端期望驼峰命名: gamerEpisodeBlacklistRegex
    blacklist_key_db = f"{providerName}_episode_blacklist_regex"
    blacklist_key_camel = f"{providerName}EpisodeBlacklistRegex"
    blacklist_value = await config_manager.get(blacklist_key_db, "")
    response_data[blacklist_key_camel] = blacklist_value

    # 4. 添加"记录原始响应"字段(动态添加,每个源都有)
    # 数据库中使用下划线命名: scraper_gamer_log_responses
    # 前端期望驼峰命名: scraperGamerLogResponses
    provider_name_capitalized = providerName[0].upper() + providerName[1:]
    log_responses_key_db = f"scraper_{providerName}_log_responses"
    log_responses_key_camel = f"scraper{provider_name_capitalized}LogResponses"
    log_responses_value = await config_manager.get(log_responses_key_db, "false")
    # 转换为布尔值
    if isinstance(log_responses_value, bool):
        response_data[log_responses_key_camel] = log_responses_value
    else:
        response_data[log_responses_key_camel] = str(log_responses_value).lower() == 'true'

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
            # field_key 就是配置键,例如 "gamerCookie" 或 "dandanplay_app_id"
            # 获取字段类型信息 (label, type, tooltip)
            field_info = configurable_fields[field_key]

            # 支持三种格式：字符串、元组、字典
            if isinstance(field_info, str):
                field_type = "string"
            elif isinstance(field_info, tuple) and len(field_info) > 1:
                field_type = field_info[1]
            elif isinstance(field_info, dict):
                field_type = field_info.get('type', 'string')
            else:
                field_type = "string"

            # 对于dandanplay的下划线命名字段,前端可能发送驼峰命名
            if providerName == 'dandanplay' and '_' in field_key:
                # 生成对应的驼峰命名键
                parts = field_key.split('_')
                camel_key = parts[0] + ''.join(word.capitalize() for word in parts[1:])
                # 检查payload中是否有驼峰命名的键
                if camel_key in payload:
                    value = payload[camel_key]
                    # 布尔类型转换为字符串存储
                    if field_type == "boolean":
                        # 先转换为标准 boolean，再转为 'true'/'false' 字符串
                        bool_value = bool(value) if not isinstance(value, str) else value.lower() in ('true', '1', 'yes', 'on')
                        value = 'true' if bool_value else 'false'
                    await config_manager.setValue(field_key, value)
                elif field_key in payload:
                    value = payload[field_key]
                    if field_type == "boolean":
                        # 先转换为标准 boolean，再转为 'true'/'false' 字符串
                        bool_value = bool(value) if not isinstance(value, str) else value.lower() in ('true', '1', 'yes', 'on')
                        value = 'true' if bool_value else 'false'
                    await config_manager.setValue(field_key, value)
            elif field_key in payload:
                value = payload[field_key]
                # 布尔类型转换为字符串存储
                if field_type == "boolean":
                    # 先转换为标准 boolean，再转为 'true'/'false' 字符串
                    bool_value = bool(value) if not isinstance(value, str) else value.lower() in ('true', '1', 'yes', 'on')
                    value = 'true' if bool_value else 'false'
                await config_manager.setValue(field_key, value)

        # 3. 处理分集黑名单字段(动态字段,每个源都有)
        # 前端发送驼峰命名: gamerEpisodeBlacklistRegex
        # 数据库存储下划线命名: gamer_episode_blacklist_regex
        blacklist_key_camel = f"{providerName}EpisodeBlacklistRegex"
        blacklist_key_db = f"{providerName}_episode_blacklist_regex"
        if blacklist_key_camel in payload:
            await config_manager.setValue(blacklist_key_db, payload[blacklist_key_camel])

        # 4. 处理"记录原始响应"字段(动态字段,每个源都有)
        # 前端发送驼峰命名: scraperGamerLogResponses
        # 数据库存储下划线命名: scraper_gamer_log_responses
        provider_name_capitalized = providerName[0].upper() + providerName[1:]
        log_responses_key_camel = f"scraper{provider_name_capitalized}LogResponses"
        log_responses_key_db = f"scraper_{providerName}_log_responses"
        if log_responses_key_camel in payload:
            # 转换布尔值为字符串存储
            value = payload[log_responses_key_camel]
            await config_manager.setValue(log_responses_key_db, str(value).lower())
            logger.info(f"[{providerName}] 记录原始响应设置已更新: {log_responses_key_db} = {str(value).lower()}")
        else:
            logger.warning(f"[{providerName}] payload 中未找到 '{log_responses_key_camel}' 字段，记录原始响应设置未更新。payload keys: {list(payload.keys())}")

        # 5. 重新加载该搜索源
        await manager.reload_scraper(providerName)
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
    except (httpx.ConnectError, httpx.ConnectTimeout) as e:
        # 从异常链中提取更具体的原因（如 TLS 握手失败、DNS 解析失败等）
        cause = e.__cause__
        detail_hint = ""
        if cause:
            cause_str = str(cause).lower()
            if "ssl" in cause_str or "tls" in cause_str or "certificate" in cause_str:
                detail_hint = "（TLS/SSL 握手失败，可能是 DNS 解析到了错误的 IP 或网络中间件干扰了 HTTPS）"
            elif "resolve" in cause_str or "getaddrinfo" in cause_str:
                detail_hint = "（DNS 解析失败）"
        logger.warning(f"执行搜索源 '{providerName}' 的操作 '{actionName}' 时网络连接失败: {type(e).__name__}{detail_hint}")
        raise HTTPException(status_code=502, detail=f"无法连接到 {providerName} 的服务器{detail_hint}，请检查网络连接或代理设置。")
    except (httpx.TimeoutException, httpx.ReadTimeout) as e:
        logger.warning(f"执行搜索源 '{providerName}' 的操作 '{actionName}' 时请求超时: {type(e).__name__}")
        raise HTTPException(status_code=504, detail=f"连接 {providerName} 超时，请检查网络连接或代理设置。")
    except Exception as e:
        logger.error(f"执行搜索源 '{providerName}' 的操作 '{actionName}' 时出错: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"操作执行失败: {str(e)}")


@router.get("/scrapers/{providerName}/default-blacklist", summary="获取搜索源的默认分集黑名单")
async def get_scraper_default_blacklist(
    providerName: str,
    current_user: models.User = Depends(security.get_current_user),
    manager: ScraperManager = Depends(get_scraper_manager)
):
    """
    获取指定搜索源的默认分集标题黑名单正则表达式。
    这个值来自源代码中的硬编码默认值，用于用户想要重置或填充默认规则时使用。
    """
    scraper_class = manager.get_scraper_class(providerName)
    if not scraper_class:
        raise HTTPException(status_code=404, detail=f"搜索源 '{providerName}' 不存在")

    default_blacklist = getattr(scraper_class, '_PROVIDER_SPECIFIC_BLACKLIST_DEFAULT', '')
    return {"providerName": providerName, "defaultBlacklist": default_blacklist}


@router.get("/scrapers/common-blacklist", summary="获取通用分集黑名单规则")
async def get_common_blacklist(
    current_user: models.User = Depends(security.get_current_user)
):
    """
    获取通用的分集标题黑名单正则表达式。
    这个值是一个通用的过滤规则，适用于大多数场景，用于用户想要快速填充规则时使用。
    """
    from src.scrapers.base import COMMON_EPISODE_BLACKLIST_REGEX
    return {"commonBlacklist": COMMON_EPISODE_BLACKLIST_REGEX}
