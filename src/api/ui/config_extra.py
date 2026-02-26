"""
Config_extra相关的API端点 - 配置管理、代理设置、AI配置等
"""
import asyncio
import json
import logging
import secrets
import string
import time
from typing import Optional, List, Any, Dict
from urllib.parse import urlparse, quote, unquote

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from src import security
from src.db import crud, models, get_db_session, ConfigManager
from src.core import get_config_schema
from src.services import ScraperManager, MetadataSourceManager
from src.ai.ai_prompts import (
    DEFAULT_AI_MATCH_PROMPT,
    DEFAULT_AI_SEASON_MAPPING_PROMPT,
    DEFAULT_AI_RECOGNITION_PROMPT,
    DEFAULT_AI_ALIAS_VALIDATION_PROMPT,
    DEFAULT_AI_ALIAS_EXPANSION_PROMPT,
    DEFAULT_AI_NAME_CONVERSION_PROMPT,
    DEFAULT_AI_EPISODE_GROUP_SELECT_PROMPT,
)
from src.ai.ai_providers import get_all_providers, supports_balance_query, get_provider_config
from src.utils import DanmakuPathTemplate

from src.api.dependencies import (
    get_scraper_manager, get_metadata_manager, get_config_manager,
    get_ai_matcher_manager
)
from .models import (
    ProxyTestResult, ProxyTestRequest, FullProxyTestResponse,
    CustomDanmakuPathRequest, CustomDanmakuPathResponse,
    MatchFallbackTokensResponse, ConfigValueResponse, ConfigValueRequest,
    TmdbReverseLookupConfig, TmdbReverseLookupConfigRequest,
    AITestRequest, AITestResponse
)

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/config/schema/parameters", summary="获取参数配置的 Schema")
async def get_parameters_schema(
    current_user: models.User = Depends(security.get_current_user)
):
    """
    获取参数配置页面的 Schema 定义。
    前端根据此 Schema 动态渲染配置界面。
    """
    return get_config_schema()


@router.get("/config/proxy", response_model=models.ProxySettingsResponse, summary="获取代理配置")
async def get_proxy_settings(
    current_user: models.User = Depends(security.get_current_user),
    session: AsyncSession = Depends(get_db_session)
):
    """获取全局代理配置。"""
    # 并行获取所有代理配置
    proxy_mode_task = crud.get_config_value(session, "proxyMode", "none")
    proxy_url_task = crud.get_config_value(session, "proxyUrl", "")
    proxy_enabled_task = crud.get_config_value(session, "proxyEnabled", "false")
    accelerate_proxy_url_task = crud.get_config_value(session, "accelerateProxyUrl", "")

    proxy_mode, proxy_url, proxy_enabled_str, accelerate_proxy_url = await asyncio.gather(
        proxy_mode_task, proxy_url_task, proxy_enabled_task, accelerate_proxy_url_task
    )

    proxy_enabled = proxy_enabled_str.lower() == 'true'

    # 兼容旧配置：如果 proxyMode 为 none 但 proxyEnabled 为 true，则使用 http_socks 模式
    if proxy_mode == "none" and proxy_enabled:
        proxy_mode = "http_socks"

    # Parse the URL into components
    protocol, host, port, username, password = "http", None, None, None, None
    if proxy_url:
        try:
            p = urlparse(proxy_url)
            protocol = p.scheme or "http"
            host = p.hostname
            port = p.port
            username = unquote(p.username) if p.username else None
            password = unquote(p.password) if p.password else None
        except Exception as e:
            logger.error(f"解析存储的代理URL '{proxy_url}' 失败: {e}")

    return models.ProxySettingsResponse(
        proxyMode=proxy_mode,
        proxyProtocol=protocol,
        proxyHost=host,
        proxyPort=port,
        proxyUsername=username,
        proxyPassword=password,
        proxyEnabled=proxy_enabled,
        accelerateProxyUrl=accelerate_proxy_url
    )



@router.put("/config/proxy", status_code=status.HTTP_204_NO_CONTENT, summary="更新代理配置")
async def update_proxy_settings(
    payload: models.ProxySettingsUpdate,
    current_user: models.User = Depends(security.get_current_user),
    session: AsyncSession = Depends(get_db_session),
    config_manager: ConfigManager = Depends(get_config_manager)
):
    """更新全局代理配置。"""
    # 保存代理模式
    await crud.update_config_value(session, "proxyMode", payload.proxyMode)
    config_manager.invalidate("proxyMode")

    # 构建并保存 HTTP/SOCKS 代理 URL
    proxy_url = ""
    if payload.proxyHost and payload.proxyPort:
        # URL-encode username and password to handle special characters like '@'
        userinfo = ""
        if payload.proxyUsername:
            userinfo = quote(payload.proxyUsername)
            if payload.proxyPassword:
                userinfo += ":" + quote(payload.proxyPassword)
            userinfo += "@"

        proxy_url = f"{payload.proxyProtocol}://{userinfo}{payload.proxyHost}:{payload.proxyPort}"

    await crud.update_config_value(session, "proxyUrl", proxy_url)
    config_manager.invalidate("proxyUrl")

    # 保存兼容性字段 proxyEnabled（根据 proxyMode 自动设置）
    proxy_enabled = payload.proxyMode != "none"
    await crud.update_config_value(session, "proxyEnabled", str(proxy_enabled).lower())
    config_manager.invalidate("proxyEnabled")

    await crud.update_config_value(session, "proxySslVerify", str(payload.proxySslVerify).lower())
    config_manager.invalidate("proxySslVerify")

    # 保存加速代理地址
    await crud.update_config_value(session, "accelerateProxyUrl", payload.accelerateProxyUrl or "")
    config_manager.invalidate("accelerateProxyUrl")

    logger.info(f"用户 '{current_user.username}' 更新了代理配置 (mode={payload.proxyMode})。")



@router.post("/proxy/test", response_model=FullProxyTestResponse, summary="测试代理连接和延迟")
async def test_proxy_latency(
    request: ProxyTestRequest,
    current_user: models.User = Depends(security.get_current_user),
    session: AsyncSession = Depends(get_db_session),
    scraper_manager: ScraperManager = Depends(get_scraper_manager),
    metadata_manager: MetadataSourceManager = Depends(get_metadata_manager)
):
    """
    测试代理连接和到各源的延迟。
    支持三种模式：
    - none: 直连测试
    - http_socks: HTTP/SOCKS 代理测试
    - accelerate: 加速代理测试
    """
    proxy_mode = request.proxy_mode
    proxy_url = request.proxy_url
    accelerate_proxy_url = request.accelerate_proxy_url

    # 根据代理模式确定使用的代理
    proxy_to_use = None
    if proxy_mode == "http_socks" and proxy_url:
        proxy_to_use = proxy_url

    # --- 步骤 1: 测试代理连通性 ---
    proxy_connectivity_result: ProxyTestResult
    test_url = "https://www.gstatic.com/generate_204"

    if proxy_mode == "none":
        # 直连模式：跳过代理连通性测试
        proxy_connectivity_result = ProxyTestResult(status="skipped", error="直连模式无需测试代理")

    elif proxy_mode == "http_socks":
        # HTTP/SOCKS 代理模式
        if not proxy_url:
            proxy_connectivity_result = ProxyTestResult(status="skipped", error="未配置代理地址")
        else:
            try:
                async with httpx.AsyncClient(proxy=proxy_to_use, timeout=10.0, follow_redirects=False) as client:
                    start_time = time.time()
                    response = await client.get(test_url)
                    latency = (time.time() - start_time) * 1000
                    if response.status_code == 204:
                        proxy_connectivity_result = ProxyTestResult(status="success", latency=latency)
                    else:
                        proxy_connectivity_result = ProxyTestResult(
                            status="failure",
                            error=f"连接成功但状态码异常: {response.status_code}"
                        )
            except Exception as e:
                proxy_connectivity_result = ProxyTestResult(status="failure", error=str(e))

    elif proxy_mode == "accelerate":
        # 加速代理模式：测试加速代理地址
        if not accelerate_proxy_url:
            proxy_connectivity_result = ProxyTestResult(status="skipped", error="未配置加速代理地址")
        else:
            # 构建加速代理格式的测试 URL
            # 格式: {proxy_base}/{protocol}/{host}/{path}
            # 使用 HTTPS 协议，因为部分云函数代理不支持 HTTP
            proxy_base = accelerate_proxy_url.rstrip('/')
            accelerated_test_url = f"{proxy_base}/https/www.gstatic.com/generate_204"
            try:
                async with httpx.AsyncClient(timeout=10.0, follow_redirects=False) as client:
                    start_time = time.time()
                    response = await client.get(accelerated_test_url)
                    latency = (time.time() - start_time) * 1000
                    if response.status_code == 204:
                        proxy_connectivity_result = ProxyTestResult(status="success", latency=latency)
                    else:
                        proxy_connectivity_result = ProxyTestResult(
                            status="failure",
                            error=f"连接成功但状态码异常: {response.status_code}"
                        )
            except Exception as e:
                proxy_connectivity_result = ProxyTestResult(status="failure", error=str(e))
    else:
        proxy_connectivity_result = ProxyTestResult(status="skipped", error=f"未知代理模式: {proxy_mode}")

    # --- 步骤 2: 动态构建要测试的目标域名列表 ---
    target_sites_results: Dict[str, ProxyTestResult] = {}
    test_domains = set()

    # 统一获取所有源的设置
    enabled_metadata_settings = await crud.get_all_metadata_source_settings(session)
    scraper_settings = await crud.get_all_scraper_settings(session)

    # 合并所有源的设置，并添加一个获取实例的函数
    all_sources_settings = [
        (s, lambda name=s['providerName']: metadata_manager.get_source(name)) for s in enabled_metadata_settings
    ] + [
        (s, lambda name=s['providerName']: scraper_manager.get_scraper(name)) for s in scraper_settings
    ]

    # 根据代理模式决定测试哪些源
    is_proxy_enabled = proxy_mode != "none"
    log_message = "代理未启用，将测试所有已启用的源。" if not is_proxy_enabled else "代理已启用，将仅测试已配置代理的源。"
    logger.info(log_message)

    for setting, get_instance_func in all_sources_settings:
        provider_name = setting.get('providerName')

        # 优化：只测试已启用的源
        # 如果代理启用，则只测试勾选了 useProxy 的源；如果代理未启用，则测试所有启用的源。
        should_test = setting['isEnabled'] and (not is_proxy_enabled or setting.get('useProxy', False))

        if should_test:
            try:
                instance = get_instance_func()
                # 修正：支持异步获取 test_url
                if hasattr(instance, 'test_url'):
                    test_url_attr = getattr(instance, 'test_url')
                    # 检查是否是协程对象 (async property 返回的)
                    if asyncio.iscoroutine(test_url_attr):
                        result = await test_url_attr
                        if result:
                            test_domains.add(result)
                    # 检查是否是异步方法（callable）
                    elif asyncio.iscoroutinefunction(test_url_attr):
                        result = await test_url_attr()
                        if result:
                            test_domains.add(result)
                    # 检查是否是普通方法
                    elif callable(test_url_attr):
                        result = test_url_attr()
                        if result:
                            test_domains.add(result)
                    # 普通属性
                    elif test_url_attr:
                        test_domains.add(test_url_attr)
            except ValueError:
                pass
            except Exception as e:
                logger.warning(f"获取 test_url 失败: {e}")

    # 添加 GitHub 相关的固定测试域名（用于资源下载）
    github_domains = [
        "https://github.com",
        "https://api.github.com",
        "https://raw.githubusercontent.com"
    ]
    test_domains.update(github_domains)

    # --- 步骤 3: 并发执行所有测试 ---
    # 定义 URL 转换函数（用于加速代理模式）
    def transform_url_for_test(url: str) -> str:
        if proxy_mode == "accelerate" and accelerate_proxy_url:
            proxy_base = accelerate_proxy_url.rstrip('/')
            protocol = "https" if url.startswith("https://") else "http"
            target = url.replace(f"{protocol}://", "")
            return f"{proxy_base}/{protocol}/{target}"
        return url

    async def test_domain(domain: str, client: httpx.AsyncClient) -> tuple[str, ProxyTestResult]:
        try:
            test_url = transform_url_for_test(domain)
            start_time = time.time()
            # 使用 HEAD 请求以提高效率，我们只关心连通性
            await client.head(test_url, timeout=10.0)
            latency = (time.time() - start_time) * 1000
            return domain, ProxyTestResult(status="success", latency=latency)
        except Exception as e:
            # 简化错误信息，避免在UI上显示过长的堆栈跟踪
            error_str = f"{type(e).__name__}"
            return domain, ProxyTestResult(status="failure", error=error_str)

    if test_domains:
        async with httpx.AsyncClient(proxy=proxy_to_use, timeout=10.0) as client:
            tasks = [test_domain(domain, client) for domain in test_domains]
            results = await asyncio.gather(*tasks)
            for domain, result in results:
                target_sites_results[domain] = result

    return FullProxyTestResponse(
        proxy_connectivity=proxy_connectivity_result,
        target_sites=target_sites_results
    )






@router.get("/config/customDanmakuPath", response_model=CustomDanmakuPathResponse, summary="获取自定义弹幕路径配置")
async def get_custom_danmaku_path(
    session: AsyncSession = Depends(get_db_session)
):
    """获取自定义弹幕路径配置"""
    enabled = await crud.get_config_value(session, "customDanmakuPathEnabled", "false")
    template = await crud.get_config_value(session, "customDanmakuPathTemplate", "/app/config/danmaku/${animeId}/${episodeId}")
    return CustomDanmakuPathResponse(enabled=enabled, template=template)



@router.put("/config/customDanmakuPath", response_model=CustomDanmakuPathResponse, summary="设置自定义弹幕路径配置")
async def set_custom_danmaku_path(
    request: CustomDanmakuPathRequest,
    current_user: models.User = Depends(security.get_current_user),
    session: AsyncSession = Depends(get_db_session),
    config_manager: ConfigManager = Depends(get_config_manager)
):
    """设置自定义弹幕路径配置"""
    logger.info(f"收到自定义弹幕路径配置请求: enabled={request.enabled}, template={request.template}")

    # 验证模板格式
    if request.enabled.lower() == 'true' and request.template:
        try:
            # 尝试创建模板对象来验证格式
            DanmakuPathTemplate(request.template)
            logger.info(f"模板验证成功: {request.template}")
        except Exception as e:
            logger.error(f"模板验证失败: {e}")
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"路径模板格式错误: {str(e)}")

    await crud.update_config_value(session, "customDanmakuPathEnabled", request.enabled)
    await crud.update_config_value(session, "customDanmakuPathTemplate", request.template)
    config_manager.invalidate("customDanmakuPathEnabled")
    config_manager.invalidate("customDanmakuPathTemplate")
    logger.info(f"自定义弹幕路径配置已保存")
    return CustomDanmakuPathResponse(enabled=request.enabled, template=request.template)

# --- 匹配后备Token配置 ---



@router.get("/config/matchFallbackTokens", response_model=MatchFallbackTokensResponse, summary="获取匹配后备允许的Token列表")
async def get_match_fallback_tokens(
    current_user: models.User = Depends(security.get_current_user),
    session: AsyncSession = Depends(get_db_session)
):
    """获取匹配后备允许的Token列表（JSON格式的token ID数组）"""
    value = await crud.get_config_value(session, "matchFallbackTokens", "[]")
    return MatchFallbackTokensResponse(value=value)



@router.put("/config/matchFallbackTokens", status_code=status.HTTP_204_NO_CONTENT, summary="设置匹配后备允许的Token列表")
async def set_match_fallback_tokens(
    request: MatchFallbackTokensResponse,
    current_user: models.User = Depends(security.get_current_user),
    session: AsyncSession = Depends(get_db_session),
    config_manager: ConfigManager = Depends(get_config_manager)
):
    """设置匹配后备允许的Token列表（JSON格式的token ID数组）"""
    await crud.update_config_value(session, "matchFallbackTokens", request.value)
    config_manager.invalidate("matchFallbackTokens")
    logger.info(f"匹配后备Token配置已保存: {request.value}")
    return

# --- 后备搜索配置 ---



@router.get("/config/searchFallbackEnabled", response_model=ConfigValueResponse, summary="获取后备搜索状态")
async def get_search_fallback(
    current_user: models.User = Depends(security.get_current_user),
    session: AsyncSession = Depends(get_db_session)
):
    """获取后备搜索功能的启用状态"""
    value = await crud.get_config_value(session, "searchFallbackEnabled", "false")
    return ConfigValueResponse(value=value)



@router.put("/config/searchFallbackEnabled", status_code=status.HTTP_204_NO_CONTENT, summary="设置后备搜索状态")
async def set_search_fallback(
    request: ConfigValueRequest,
    current_user: models.User = Depends(security.get_current_user),
    session: AsyncSession = Depends(get_db_session),
    config_manager: ConfigManager = Depends(get_config_manager)
):
    """设置后备搜索功能的启用状态"""
    await crud.update_config_value(session, "searchFallbackEnabled", request.value)
    config_manager.invalidate("searchFallbackEnabled")
    logger.info(f"后备搜索状态已保存: {request.value}")
    return

# --- TMDB反查配置 ---
# 注意：这些专用路由必须在通用的 /config/{config_key} 路由之前定义，
# 否则会被通用路由拦截



@router.get("/config/tmdbReverseLookup", response_model=TmdbReverseLookupConfig, summary="获取TMDB反查配置")
async def get_tmdb_reverse_lookup_config(
    current_user: models.User = Depends(security.get_current_user),
    session: AsyncSession = Depends(get_db_session)
):
    """获取TMDB反查配置"""
    enabled = await crud.get_config_value(session, "tmdbReverseLookupEnabled", "false")
    sources_json = await crud.get_config_value(session, "tmdbReverseLookupSources", '["imdb", "tvdb"]')

    try:
        sources = json.loads(sources_json)
    except:
        sources = ["imdb", "tvdb"]  # 默认值

    return TmdbReverseLookupConfig(
        enabled=enabled.lower() == "true",
        sources=sources
    )



@router.post("/config/tmdbReverseLookup", summary="保存TMDB反查配置")
async def save_tmdb_reverse_lookup_config(
    request: TmdbReverseLookupConfigRequest,
    current_user: models.User = Depends(security.get_current_user),
    config_manager: ConfigManager = Depends(get_config_manager)
):
    """保存TMDB反查配置"""
    # 修正：使用 config_manager.setValue 来确保在同一个事务中更新两个配置项
    # 这样可以避免多次 commit 导致的问题
    await config_manager.setValue("tmdbReverseLookupEnabled", str(request.enabled).lower())
    await config_manager.setValue("tmdbReverseLookupSources", json.dumps(request.sources))

    logger.info(f"用户 '{current_user.username}' 更新了TMDB反查配置: enabled={request.enabled}, sources={request.sources}")
    return {"message": "TMDB反查配置已保存"}



@router.get("/config/{config_key}", response_model=Dict[str, str], summary="获取指定配置项的值")
async def get_config_item(
    config_key: str,
    current_user: models.User = Depends(security.get_current_user),
    session: AsyncSession = Depends(get_db_session)
):
    """获取数据库中单个配置项的值。"""
    value = await crud.get_config_value(session, config_key, "") # 默认为空字符串
    return {"key": config_key, "value": value}



@router.put("/config/{config_key}", status_code=status.HTTP_204_NO_CONTENT, summary="更新指定配置项的值")
async def update_config_item(
    config_key: str,
    payload: Dict[str, Any],  # 修正：允许任意类型的值,避免前端传递undefined时报错
    current_user: models.User = Depends(security.get_current_user),
    session: AsyncSession = Depends(get_db_session),
    config_manager: ConfigManager = Depends(get_config_manager)
):
    """更新数据库中单个配置项的值。"""
    value = payload.get("value")
    if value is None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Missing 'value' in request body")

    # 确保value是字符串类型
    value_str = str(value) if value is not None else ""

    await crud.update_config_value(session, config_key, value_str)
    config_manager.invalidate(config_key)
    logger.info(f"用户 '{current_user.username}' 更新了配置项 '{config_key}'。")



@router.post("/config/webhookApiKey/regenerate", response_model=Dict[str, str], summary="重新生成Webhook API Key")
async def regenerate_webhook_api_key(
    current_user: models.User = Depends(security.get_current_user),
    session: AsyncSession = Depends(get_db_session),
    config_manager: ConfigManager = Depends(get_config_manager)
):
    """生成一个新的、随机的Webhook API Key并保存到数据库。"""
    alphabet = string.ascii_letters + string.digits
    new_key = ''.join(secrets.choice(alphabet) for _ in range(20))
    await crud.update_config_value(session, "webhookApiKey", new_key)
    config_manager.invalidate("webhookApiKey")
    logger.info(f"用户 '{current_user.username}' 重新生成了 Webhook API Key。")
    return {"key": "webhookApiKey", "value": new_key}



@router.post("/config/externalApiKey/regenerate", response_model=Dict[str, str], summary="重新生成外部API Key")
async def regenerate_external_api_key(
    current_user: models.User = Depends(security.get_current_user),
    session: AsyncSession = Depends(get_db_session),
    config_manager: ConfigManager = Depends(get_config_manager)
):
    """生成一个新的、随机的外部API Key并保存到数据库。"""
    alphabet = string.ascii_letters + string.digits
    new_key = ''.join(secrets.choice(alphabet) for _ in range(32)) # 增加长度以提高安全性
    await crud.update_config_value(session, "externalApiKey", new_key)
    config_manager.invalidate("externalApiKey")
    logger.info(f"用户 '{current_user.username}' 重新生成了外部 API Key。")
    return {"key": "externalApiKey", "value": new_key}



@router.get("/config/provider/{providerName}", response_model=Dict[str, Any], summary="获取指定元数据源的配置")
async def get_provider_settings(
    providerName: str,
    current_user: models.User = Depends(security.get_current_user),
    metadata_manager: MetadataSourceManager = Depends(get_metadata_manager)
):
    """获取指定元数据源的所有相关配置。"""
    try:
        return await metadata_manager.getProviderConfig(providerName)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))



@router.put("/config/provider/{providerName}", status_code=status.HTTP_204_NO_CONTENT, summary="更新指定元数据源的配置")
async def set_provider_settings(
    providerName: str,
    settings: Dict[str, Any],
    current_user: models.User = Depends(security.get_current_user),
    config_manager: ConfigManager = Depends(get_config_manager)
):
    """批量更新指定元数据源的相关配置。"""
    config_keys_map = {
        "tmdb": ["tmdbApiKey", "tmdbApiBaseUrl", "tmdbImageBaseUrl"],
        "bangumi": ["bangumiClientId", "bangumiClientSecret", "bangumiToken", "authMode"],
        "douban": "doubanCookie", # 单值配置
        "tvdb": "tvdbApiKey",   # 单值配置
    }

    if providerName in ["douban", "tvdb"]:
        key = config_keys_map.get(providerName)
        if key:
            # 修复：使用正确的字段名获取配置值
            if providerName == "tvdb":
                value = settings.get("tvdbApiKey", "")
            elif providerName == "douban":
                value = settings.get("doubanCookie", "")
            else:
                value = settings.get("value", "")
            await config_manager.setValue(configKey=key, configValue=value)
    else:
        keys_to_update = config_keys_map.get(providerName, [])
        tasks = [
            config_manager.setValue(configKey=key, configValue=settings[key])
            for key in keys_to_update if key in settings
        ]
        if tasks:
            await asyncio.gather(*tasks)
    logger.info(f"用户 '{current_user.username}' 更新了元数据源 '{providerName}' 的配置。")



@router.post("/config/ai/test", response_model=AITestResponse, summary="测试AI连接可用性")
async def test_ai_connection(
    request: AITestRequest,
    current_user: models.User = Depends(security.get_current_user),
    config_manager: ConfigManager = Depends(get_config_manager)
):
    """测试AI连接配置是否可用"""
    try:
        start_time = time.time()

        # Gemini 使用官方 SDK 测试
        if request.provider == "gemini":
            try:
                from google import genai

                logger.info(f"测试 Gemini 连接: model={request.model}")

                client = genai.Client(api_key=request.apiKey)
                response = client.models.generate_content(
                    model=request.model,
                    contents="Hello, please respond with a greeting.",
                    config={
                        "temperature": 0.0,
                        "max_output_tokens": 50
                    }
                )

                latency = (time.time() - start_time) * 1000

                # 检查响应
                if hasattr(response, 'text') and response.text:
                    return AITestResponse(
                        success=True,
                        message=f"AI连接测试成功 (响应: {response.text[:50]}...)",
                        latency=round(latency, 2)
                    )
                else:
                    # 尝试获取更多响应信息
                    error_detail = f"响应对象: {type(response).__name__}"
                    if hasattr(response, '__dict__'):
                        error_detail += f", 属性: {list(response.__dict__.keys())}"

                    return AITestResponse(
                        success=False,
                        message="Gemini API 返回空响应",
                        error=error_detail
                    )

            except ImportError:
                return AITestResponse(
                    success=False,
                    message="Gemini SDK 未安装",
                    error="请运行: pip install google-genai"
                )
            except Exception as e:
                logger.error(f"Gemini 测试失败: {e}", exc_info=True)
                return AITestResponse(
                    success=False,
                    message="Gemini API 调用失败",
                    error=f"{type(e).__name__}: {str(e)}"
                )

        # 其他提供商使用 OpenAI 兼容接口测试
        # 根据provider确定base_url
        if request.baseUrl:
            base_url = request.baseUrl.rstrip('/')
        else:
            # 1. 先从数据库读取用户保存的配置
            saved_base_url = await config_manager.get("aiBaseUrl")

            if saved_base_url:
                base_url = saved_base_url.rstrip('/')
            else:
                # 2. 从 ai_providers.py 获取默认值
                provider_config = get_provider_config(request.provider)
                if not provider_config:
                    return AITestResponse(
                        success=False,
                        message="不支持的AI提供商",
                        error=f"未知的provider: {request.provider}"
                    )
                base_url = provider_config.get('defaultBaseUrl', '').rstrip('/')

        # 构建API URL
        api_url = f"{base_url}/chat/completions"

        # 构建测试请求
        headers = {
            "Authorization": f"Bearer {request.apiKey}",
            "Content-Type": "application/json"
        }

        payload = {
            "model": request.model,
            "messages": [
                {"role": "user", "content": "Hello"}
            ],
            "max_tokens": 10
        }

        # 发送测试请求
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(api_url, json=payload, headers=headers)

            latency = (time.time() - start_time) * 1000  # 转换为毫秒

            if response.status_code == 200:
                return AITestResponse(
                    success=True,
                    message="AI连接测试成功",
                    latency=round(latency, 2)
                )
            else:
                error_detail = response.text
                try:
                    error_json = response.json()
                    error_detail = error_json.get('error', {}).get('message', error_detail)
                except:
                    pass

                return AITestResponse(
                    success=False,
                    message=f"AI API返回错误 (HTTP {response.status_code})",
                    error=error_detail,
                    latency=round(latency, 2)
                )

    except httpx.TimeoutException:
        return AITestResponse(
            success=False,
            message="连接超时",
            error="请求超过30秒未响应,请检查网络连接或Base URL配置"
        )
    except httpx.ConnectError as e:
        return AITestResponse(
            success=False,
            message="无法连接到AI服务",
            error=f"连接失败: {str(e)}"
        )
    except Exception as e:
        logger.error(f"AI连接测试失败: {e}", exc_info=True)
        return AITestResponse(
            success=False,
            message="测试过程中发生错误",
            error=str(e)
        )


# --- Media Server API ---


@router.get("/config/ai/default-prompts", response_model=Dict[str, str], summary="获取AI默认提示词")
async def get_default_ai_prompts(
    current_user: models.User = Depends(security.get_current_user)
):
    """
    获取所有AI提示词的硬编码默认值

    Returns:
        包含所有默认提示词的字典
    """
    return {
        "aiPrompt": DEFAULT_AI_MATCH_PROMPT,
        "aiRecognitionPrompt": DEFAULT_AI_RECOGNITION_PROMPT,
        "aiAliasValidationPrompt": DEFAULT_AI_ALIAS_VALIDATION_PROMPT,
        "aiAliasExpansionPrompt": DEFAULT_AI_ALIAS_EXPANSION_PROMPT,
        "aiNameConversionPrompt": DEFAULT_AI_NAME_CONVERSION_PROMPT,
        "seasonMappingPrompt": DEFAULT_AI_SEASON_MAPPING_PROMPT,
        "aiEpisodeGroupPrompt": DEFAULT_AI_EPISODE_GROUP_SELECT_PROMPT
    }


@router.get("/config/ai/metrics", summary="获取AI调用统计")
async def get_ai_metrics(
    hours: int = 24,
    source: str = "db",  # db: 从数据库读取, memory: 从内存读取
    current_user: models.User = Depends(security.get_current_user),
    ai_matcher_manager = Depends(get_ai_matcher_manager),
    session: AsyncSession = Depends(get_db_session)
):
    """
    获取AI调用的统计信息

    Args:
        hours: 统计最近多少小时的数据,默认24小时
        source: 数据来源, db=数据库(持久化), memory=内存(实时)

    Returns:
        AI调用统计数据
    """
    from src.db.crud.ai_metrics import get_ai_metrics_stats, get_ai_metrics_summary

    # 优先从数据库读取（持久化数据）
    if source == "db":
        try:
            stats = await get_ai_metrics_stats(session, hours)
            summary = await get_ai_metrics_summary(session)
            stats["summary"] = summary
        except Exception as e:
            logger.warning(f"从数据库读取 AI 统计失败，回退到内存: {e}")
            source = "memory"  # 回退到内存

    # 从内存读取（实时数据）
    if source == "memory":
        matcher = await ai_matcher_manager.get_matcher()
        if not matcher:
            return {
                "error": "AI匹配器未初始化或未启用",
                "ai_stats": None,
                "cache_stats": None
            }
        stats = matcher.metrics.get_stats(hours)

    # 添加缓存统计
    cache_stats = None
    matcher = await ai_matcher_manager.get_matcher()
    if matcher and matcher.cache:
        cache_stats = matcher.cache.get_stats()

    return {
        "ai_stats": stats,
        "cache_stats": cache_stats,
        "source": source
    }


@router.post("/config/ai/cache/clear", summary="清空AI缓存")
async def clear_ai_cache(
    current_user: models.User = Depends(security.get_current_user),
    ai_matcher_manager = Depends(get_ai_matcher_manager)
):
    """
    清空AI响应缓存

    Returns:
        操作结果
    """
    matcher = await ai_matcher_manager.get_matcher()
    if not matcher:
        raise HTTPException(status_code=400, detail="AI匹配器未初始化或未启用")

    if not matcher.cache:
        raise HTTPException(status_code=400, detail="AI缓存未启用")

    matcher.cache.clear()

    return {
        "success": True,
        "message": "AI缓存已清空"
    }


@router.get("/config/ai/balance", summary="获取AI账户余额")
async def get_ai_balance(
    current_user: models.User = Depends(security.get_current_user),
    config_manager: ConfigManager = Depends(get_config_manager)
):
    """
    获取AI账户余额 (通用接口,根据 aiProvider 自动选择)

    注意: 此接口不依赖 AI 匹配是否启用,只要配置了 API Key 就可以查询余额

    Returns:
        {
            "supported": true/false,  # 是否支持余额查询
            "provider": "deepseek",   # 当前提供商
            "data": {                 # 仅当 supported=true 时存在
                "currency": "CNY",
                "total_balance": "110.00",
                "granted_balance": "10.00",
                "topped_up_balance": "100.00"
            },
            "error": null             # 错误信息 (如果有)
        }
    """
    # 获取当前 AI 提供商
    provider = await config_manager.get("aiProvider", "deepseek")

    # 检查是否支持余额查询
    if not supports_balance_query(provider):
        return {
            "supported": False,
            "provider": provider,
            "data": None,
            "error": f"{provider} 不支持余额查询"
        }

    # 获取 API 配置
    api_key = await config_manager.get("aiApiKey", "")
    base_url = await config_manager.get("aiBaseUrl", "")

    if not api_key:
        return {
            "supported": True,
            "provider": provider,
            "data": None,
            "error": "未配置 API Key"
        }

    # 获取提供商配置
    provider_config = get_provider_config(provider)
    if not provider_config:
        return {
            "supported": True,
            "provider": provider,
            "data": None,
            "error": f"无法获取提供商配置: {provider}"
        }

    # 如果未配置 base_url,使用默认值
    if not base_url:
        base_url = provider_config.get("defaultBaseUrl", "")

    # 直接调用余额 API
    try:
        balance_api_path = provider_config.get("balanceApiPath", "/user/balance")
        url = f"{base_url}{balance_api_path}"

        headers = {
            "Accept": "application/json",
            "Authorization": f"Bearer {api_key}"
        }

        async with httpx.AsyncClient() as client:
            response = await client.get(url, headers=headers, timeout=10.0)
            response.raise_for_status()

            data = response.json()

            # 根据提供商类型解析响应
            parser_type = provider_config.get("balanceResponseParser", "deepseek")
            balance_data = _parse_balance_response(data, parser_type)

            return {
                "supported": True,
                "provider": provider,
                "data": balance_data,
                "error": None
            }
    except Exception as e:
        logger.error(f"获取AI余额失败: {e}")
        return {
            "supported": True,
            "provider": provider,
            "data": None,
            "error": str(e)
        }


def _parse_balance_response(data: Dict[str, Any], parser_type: str) -> Dict[str, Any]:
    """
    解析余额响应数据

    Args:
        data: API响应数据
        parser_type: 解析器类型 (deepseek/siliconflow)

    Returns:
        标准化的余额信息字典
    """
    if parser_type == "deepseek":
        # DeepSeek 响应格式
        if not data.get("is_available"):
            raise Exception("账户余额不足或不可用")

        balance_infos = data.get("balance_infos", [])
        if not balance_infos:
            raise Exception("未返回余额信息")

        balance_info = balance_infos[0]
        return {
            "currency": balance_info.get("currency", "CNY"),
            "total_balance": balance_info.get("total_balance", "0.00"),
            "granted_balance": balance_info.get("granted_balance", "0.00"),
            "topped_up_balance": balance_info.get("topped_up_balance", "0.00")
        }

    elif parser_type == "siliconflow":
        # SiliconFlow 响应格式
        user_data = data.get("data", {})
        return {
            "currency": "CNY",
            "total_balance": user_data.get("totalBalance", "0.00"),
            "granted_balance": user_data.get("balance", "0.00"),
            "topped_up_balance": user_data.get("chargeBalance", "0.00")
        }

    else:
        raise ValueError(f"不支持的解析器类型: {parser_type}")


@router.get("/config/ai/providers", summary="获取AI提供商列表")
async def get_ai_providers(
    current_user: models.User = Depends(security.get_current_user)
):
    """
    获取所有可用的AI提供商配置列表

    Returns:
        [
            {
                "id": "deepseek",
                "name": "DeepSeek",
                "displayName": "DeepSeek (推荐)",
                "description": "DeepSeek AI - 性价比高的国产大模型",
                "defaultBaseUrl": "https://api.deepseek.com",
                "defaultModel": "deepseek-chat",
                "modelPlaceholder": "deepseek-chat",
                "baseUrlPlaceholder": "https://api.deepseek.com (默认)",
                "supportBalance": true,
                "apiKeyPrefix": "sk-",
                "website": "https://platform.deepseek.com"
            },
            ...
        ]
    """
    return get_all_providers()


async def _fetch_models_from_provider(
    provider_id: str,
    api_key: str,
    base_url: str = None
) -> List[Dict[str, Any]]:
    """
    从AI提供商API获取模型列表

    Args:
        provider_id: 提供商ID
        api_key: API密钥
        base_url: 自定义Base URL (可选)

    Returns:
        模型列表，格式: [{"id": "model-name", "name": "Model Name", ...}, ...]
    """
    provider_config = get_provider_config(provider_id)
    if not provider_config:
        return []

    models_api_path = provider_config.get("modelsApiPath")
    if not models_api_path:
        return []

    # 构建完整的API URL
    if models_api_path.startswith("http"):
        # Gemini 使用完整URL
        api_url = models_api_path
    else:
        # 其他提供商使用相对路径
        if not base_url:
            base_url = provider_config.get("defaultBaseUrl", "")
        api_url = f"{base_url.rstrip('/')}{models_api_path}"

    # 构建请求头
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json"
    }

    # Gemini 使用不同的认证方式
    if provider_id == "gemini":
        api_url = f"{api_url}?key={api_key}"
        headers = {"Content-Type": "application/json"}

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(api_url, headers=headers)
            response.raise_for_status()
            data = response.json()

            # 解析不同提供商的响应格式
            models = []
            if provider_id == "gemini":
                # Gemini 返回格式: {"models": [{"name": "models/gemini-...", ...}]}
                for model in data.get("models", []):
                    model_name = model.get("name", "")
                    # 移除 "models/" 前缀
                    if model_name.startswith("models/"):
                        model_name = model_name[7:]
                    models.append({
                        "id": model_name,
                        "name": model.get("displayName", model_name)
                    })
            else:
                # OpenAI 兼容格式: {"data": [{"id": "model-name", ...}]}
                for model in data.get("data", []):
                    models.append({
                        "id": model.get("id", ""),
                        "name": model.get("id", "")
                    })

            return models
    except Exception as e:
        logger.warning(f"从 {provider_id} 获取模型列表失败: {e}")
        return []


def _merge_model_lists(
    hardcoded_models: List[Dict[str, Any]],
    dynamic_models: List[Dict[str, Any]]
) -> List[Dict[str, Any]]:
    """
    合并硬编码模型列表和动态获取的模型列表

    Args:
        hardcoded_models: 硬编码的模型列表 (带描述和标签)
        dynamic_models: 从API动态获取的模型列表

    Returns:
        合并后的模型列表，硬编码模型在前，新模型在后
    """
    # 提取硬编码模型的ID集合
    hardcoded_ids = {m["value"] for m in hardcoded_models}

    # 过滤出新模型（不在硬编码列表中的）
    new_models = []
    for model in dynamic_models:
        model_id = model.get("id", "")
        if model_id and model_id not in hardcoded_ids:
            new_models.append({
                "value": model_id,
                "label": model.get("name", model_id),
                "description": "官方模型",
                "isNew": True  # 标记为新模型
            })

    # 合并：硬编码模型 + 新模型（按字母排序）
    new_models.sort(key=lambda x: x["value"])
    return hardcoded_models + new_models


@router.get("/config/ai/models", summary="获取AI模型列表")
async def get_ai_models(
    provider: str = Query(..., description="AI提供商ID"),
    refresh: bool = Query(False, description="是否刷新动态模型列表"),
    current_user: models.User = Depends(security.get_current_user),
    config_manager: ConfigManager = Depends(get_config_manager)
):
    """
    获取指定AI提供商的模型列表

    返回硬编码模型列表 + 动态获取的模型列表（如果启用refresh）

    Args:
        provider: AI提供商ID (deepseek, siliconflow, openai, gemini)
        refresh: 是否从API动态获取最新模型列表

    Returns:
        {
            "models": [
                {
                    "value": "deepseek-chat",
                    "label": "deepseek-chat (推荐)",
                    "description": "DeepSeek V3.2 对话模型",
                    "isNew": false
                },
                ...
            ],
            "source": "hardcoded" | "merged"
        }
    """
    # 获取提供商配置
    provider_config = get_provider_config(provider)
    if not provider_config:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"未找到提供商: {provider}"
        )

    # 获取硬编码的模型列表
    hardcoded_models = provider_config.get("availableModels", [])

    # 如果不需要刷新，直接返回硬编码列表
    if not refresh:
        return {
            "models": hardcoded_models,
            "source": "hardcoded"
        }

    # 获取API配置
    api_key = await config_manager.get("aiApiKey", "")
    if not api_key:
        # 没有API Key，返回硬编码列表
        return {
            "models": hardcoded_models,
            "source": "hardcoded",
            "error": "未配置API Key"
        }

    base_url = await config_manager.get("aiBaseUrl", "")

    # 从API获取动态模型列表
    dynamic_models = await _fetch_models_from_provider(provider, api_key, base_url)

    # 合并模型列表
    merged_models = _merge_model_lists(hardcoded_models, dynamic_models)

    return {
        "models": merged_models,
        "source": "merged",
        "dynamicCount": len(dynamic_models),
        "newCount": len(merged_models) - len(hardcoded_models)
    }









































