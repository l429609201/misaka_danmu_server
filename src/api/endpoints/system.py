"""
System相关的API端点
"""

import re
from typing import Optional, List, Any, Dict, Callable, Union
import asyncio
import secrets
import hashlib
import importlib
import string
import time
import json
from urllib.parse import urlparse, urlunparse, quote, unquote
import logging

from datetime import datetime
from sqlalchemy import update, select, func, exc
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
import httpx
from ...rate_limiter import RateLimiter, RateLimitExceededError
from ...config_manager import ConfigManager
from pydantic import BaseModel, Field, model_validator
from fastapi import APIRouter, Body, Depends, HTTPException, Query, Request, status, Response
from fastapi.security import OAuth2PasswordRequestForm

from ... import crud, models, orm_models, security, scraper_manager
from src import models as api_models
from ...log_manager import get_logs
from ...task_manager import TaskManager, TaskSuccess, TaskStatus
from ...metadata_manager import MetadataSourceManager
from ...scraper_manager import ScraperManager
from ... import tasks
from ...utils import parse_search_keyword
from ...webhook_manager import WebhookManager
from ...image_utils import download_image
from ...scheduler import SchedulerManager
from ...title_recognition import TitleRecognitionManager
from ..._version import APP_VERSION
from thefuzz import fuzz
from ...config import settings
from ...timezone import get_now
from ...database import get_db_session
from ...search_utils import unified_search

logger = logging.getLogger(__name__)


from ..dependencies import (
    get_scraper_manager, get_task_manager, get_scheduler_manager,
    get_webhook_manager, get_metadata_manager, get_config_manager,
    get_rate_limiter, get_title_recognition_manager
)

from ..ui_models import (
    UITaskResponse, UIProviderSearchResponse, RefreshPosterRequest,
    ReassociationRequest, BulkDeleteEpisodesRequest, BulkDeleteRequest,
    ProxyTestResult, ProxyTestRequest, FullProxyTestResponse,
    TitleRecognitionContent, TitleRecognitionUpdateResponse,
    ApiTokenUpdate, CustomDanmakuPathRequest, CustomDanmakuPathResponse,
    MatchFallbackTokensResponse, ConfigValueResponse, ConfigValueRequest,
    TmdbReverseLookupConfig, TmdbReverseLookupConfigRequest,
    ImportFromUrlRequest, GlobalFilterSettings,
    RateLimitProviderStatus, FallbackRateLimitStatus, RateLimitStatusResponse,
    WebhookSettings, WebhookTaskItem, PaginatedWebhookTasksResponse,
    AITestRequest, AITestResponse
)
router = APIRouter()

@router.get(
    "/comment/{episodeId}",
    response_model=models.PaginatedCommentResponse,
    summary="获取指定分集的弹幕",
)
async def get_comments(
    episodeId: int,
    page: int = Query(1, ge=1, description="页码"),
    pageSize: int = Query(100, ge=1, description="每页数量"),
    session: AsyncSession = Depends(get_db_session)
):
    # 检查episode是否存在，如果不存在则返回404
    if not await crud.check_episode_exists(session, episodeId):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Episode not found")

    comments_data = await crud.fetch_comments(session, episodeId)

    total = len(comments_data)
    start = (page - 1) * pageSize
    end = start + pageSize
    paginated_data = comments_data[start:end]

    comments = [
        models.Comment(cid=i + start, p=item.get("p", ""), m=item.get("m", ""))
        for i, item in enumerate(paginated_data)
    ]
    return models.PaginatedCommentResponse(total=total, list=comments)

@router.get("/version", response_model=Dict[str, str], summary="获取应用版本号")
async def get_app_version():
    """获取当前后端应用的版本号。"""
    return {"version": APP_VERSION}



@router.get("/logs", response_model=List[str], summary="获取最新的服务器日志")
async def get_server_logs(current_user: models.User = Depends(security.get_current_user)):
    """获取存储在内存中的最新日志条目。"""
    return get_logs()








@router.post("/cache/clear", status_code=status.HTTP_200_OK, summary="清除所有缓存")

async def clear_all_caches(
    current_user: models.User = Depends(security.get_current_user),
    session: AsyncSession = Depends(get_db_session)
): #noqa
    """清除数据库中存储的所有缓存数据（如搜索结果、分集列表）。"""
    deleted_count = await crud.clear_all_cache(session)
    logger.info(f"用户 '{current_user.username}' 清除了所有缓存，共 {deleted_count} 条。")
    return {"message": f"成功清除了 {deleted_count} 条缓存记录。"}



@router.get("/external-logs", response_model=List[models.ExternalApiLogInfo], summary="获取最新的外部API访问日志")
async def get_external_api_logs(
    current_user: models.User = Depends(security.get_current_user),
    session: AsyncSession = Depends(get_db_session)
):
    logs = await crud.get_external_api_logs(session)
    return [models.ExternalApiLogInfo.model_validate(log) for log in logs]



@router.get("/ua-rules", response_model=List[models.UaRule], summary="获取所有UA规则")
async def get_ua_rules(
    current_user: models.User = Depends(security.get_current_user),
    session: AsyncSession = Depends(get_db_session)
):
    rules = await crud.get_ua_rules(session)
    return [models.UaRule.model_validate(r) for r in rules]




@router.post("/ua-rules", response_model=models.UaRule, status_code=201, summary="添加UA规则")
async def add_ua_rule(
    ruleData: models.UaRuleCreate,
    currentUser: models.User = Depends(security.get_current_user),
    session: AsyncSession = Depends(get_db_session)
):
    try:
        rule_id = await crud.add_ua_rule(session, ruleData.uaString)
        # This is a bit inefficient but ensures we return the full object
        rules = await crud.get_ua_rules(session)
        new_rule = next((r for r in rules if r['id'] == rule_id), None)
        return models.UaRule.model_validate(new_rule)
    except Exception:
        raise HTTPException(status_code=409, detail="该UA规则已存在。")



@router.delete("/ua-rules/{ruleId}", status_code=204, summary="删除UA规则")
async def delete_ua_rule(
    ruleId: str,
    currentUser: models.User = Depends(security.get_current_user),
    session: AsyncSession = Depends(get_db_session)
):
    try:
        rule_id_int = int(ruleId)
    except ValueError:
        raise HTTPException(status_code=400, detail="规则ID必须是有效的整数。")

    deleted = await crud.delete_ua_rule(session, rule_id_int)
    if not deleted:
        raise HTTPException(status_code=404, detail="找不到指定的规则ID。")



@router.get("/rate-limit/status", response_model=RateLimitStatusResponse, summary="获取所有流控规则的状态")
async def get_rate_limit_status(
    session: AsyncSession = Depends(get_db_session),
    scraper_manager: ScraperManager = Depends(get_scraper_manager),
    rate_limiter: RateLimiter = Depends(get_rate_limiter)
):
    """获取所有流控规则的当前状态，包括全局和各源的配额使用情况。"""
    # 在获取状态前，先触发一次全局流控的检查，这会强制重置过期的计数器
    try:
        await rate_limiter.check("__ui_status_check__")
    except RateLimitExceededError:
        # 我们只关心检查和重置的副作用，不关心它是否真的超限，所以忽略此错误
        pass
    except Exception as e:
        # 记录其他潜在错误，但不中断状态获取
        logger.error(f"在获取流控状态时，检查全局流控失败: {e}")

    global_enabled = rate_limiter.enabled
    global_limit = rate_limiter.global_limit
    period_seconds = rate_limiter.global_period_seconds

    all_states = await crud.get_all_rate_limit_states(session)
    states_map = {s.providerName: s for s in all_states}

    global_state = states_map.get("__global__")
    seconds_until_reset = 0
    if global_state:
        # 使用 get_now() 确保时区一致性
        time_since_reset = get_now().replace(tzinfo=None) - global_state.lastResetTime
        seconds_until_reset = max(0, int(period_seconds - time_since_reset.total_seconds()))

    provider_items = []
    # 修正：从数据库获取所有已配置的搜索源，而不是调用一个不存在的方法
    all_scrapers_raw = await crud.get_all_scraper_settings(session)
    # 修正：在显示流控状态时，排除不产生网络请求的 'custom' 源
    all_scrapers = [s for s in all_scrapers_raw if s['providerName'] != 'custom']
    for scraper_setting in all_scrapers:
        provider_name = scraper_setting['providerName']
        provider_state = states_map.get(provider_name)
        
        quota: Union[int, str] = "∞"
        try:
            scraper_instance = scraper_manager.get_scraper(provider_name)
            provider_quota = getattr(scraper_instance, 'rate_limit_quota', None)
            if provider_quota is not None and provider_quota > 0:
                quota = provider_quota
        except ValueError:
            pass

        provider_items.append(RateLimitProviderStatus(
            providerName=provider_name,
            requestCount=provider_state.requestCount if provider_state else 0,
            quota=quota
        ))

    # 修正：将秒数转换为可读的字符串以匹配响应模型
    global_period_str = f"{period_seconds} 秒"

    # 获取后备流控状态 (合并match和search)
    fallback_match_state = states_map.get("__fallback_match__")
    fallback_search_state = states_map.get("__fallback_search__")

    match_count = fallback_match_state.requestCount if fallback_match_state else 0
    search_count = fallback_search_state.requestCount if fallback_search_state else 0
    total_fallback_count = match_count + search_count

    fallback_status = FallbackRateLimitStatus(
        totalCount=total_fallback_count,
        totalLimit=rate_limiter.fallback_limit,
        matchCount=match_count,
        searchCount=search_count
    )

    return RateLimitStatusResponse(
        enabled=global_enabled,
        verificationFailed=rate_limiter._verification_failed,
        globalRequestCount=global_state.requestCount if global_state else 0,
        globalLimit=global_limit,
        globalPeriod=global_period_str,
        secondsUntilReset=seconds_until_reset,
        providers=provider_items,
        fallback=fallback_status
    )



