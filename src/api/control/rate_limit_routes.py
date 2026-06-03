"""
外部控制API - 流控状态路由
包含: /rate-limit/status
"""

import asyncio
import json
import logging
from typing import Union

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from src.db import crud, models, get_db_session
from src.core import get_now
from src.services import ScraperManager
from src.rate_limiter import RateLimiter, RateLimitExceededError

from .dependencies import get_scraper_manager, get_rate_limiter

logger = logging.getLogger(__name__)

router = APIRouter()


async def _get_rate_limit_status_data(
    session: AsyncSession,
    scraper_manager: ScraperManager,
    rate_limiter: RateLimiter
) -> models.ControlRateLimitStatusResponse:
    """
    获取流控状态数据的核心逻辑（可被普通响应和SSE流复用）
    """
    # 在获取状态前，先触发一次全局流控的检查，这会强制重置过期的计数器
    try:
        await rate_limiter.check("__control_api_status_check__")
    except RateLimitExceededError:
        pass
    except Exception as e:
        logger.error(f"在获取流控状态时，检查全局流控失败: {e}")

    global_enabled = rate_limiter.enabled
    global_limit = rate_limiter.global_limit
    period_seconds = rate_limiter.global_period_seconds

    all_states = await crud.get_all_rate_limit_states(session)
    states_map = {s.providerName: s for s in all_states}

    global_state = states_map.get("__global__")
    seconds_until_reset = 0
    if global_state:
        time_since_reset = get_now().replace(tzinfo=None) - global_state.lastResetTime
        seconds_until_reset = max(0, int(period_seconds - time_since_reset.total_seconds()))

    # 获取后备流控状态
    fallback_match_state = states_map.get("__fallback_match__")
    fallback_search_state = states_map.get("__fallback_search__")
    match_fallback_count = fallback_match_state.requestCount if fallback_match_state else 0
    search_fallback_count = fallback_search_state.requestCount if fallback_search_state else 0
    fallback_total = match_fallback_count + search_fallback_count
    fallback_limit = 50  # 固定50次

    provider_items = []
    all_scrapers_raw = await crud.get_all_scraper_settings(session)
    all_scrapers = [s for s in all_scrapers_raw if s['providerName'] != 'custom']

    for scraper_setting in all_scrapers:
        provider_name = scraper_setting['providerName']
        provider_state = states_map.get(provider_name)
        total_count = provider_state.requestCount if provider_state else 0

        fallback_count = 0
        direct_count = total_count - fallback_count

        quota: Union[int, str] = "∞"
        try:
            scraper_instance = scraper_manager.get_scraper(provider_name)
            provider_quota = getattr(scraper_instance, 'rate_limit_quota', None)
            if provider_quota is not None and provider_quota > 0:
                quota = provider_quota
        except ValueError:
            pass

        provider_items.append(models.ControlRateLimitProviderStatus(
            providerName=provider_name,
            directCount=direct_count,
            fallbackCount=fallback_count,
            requestCount=total_count,
            quota=quota
        ))

    global_period_str = f"{period_seconds} 秒"

    return models.ControlRateLimitStatusResponse(
        globalEnabled=global_enabled,
        globalRequestCount=global_state.requestCount if global_state else 0,
        globalLimit=global_limit,
        globalPeriod=global_period_str,
        secondsUntilReset=seconds_until_reset,
        fallbackTotalCount=fallback_total,
        fallbackTotalLimit=fallback_limit,
        fallbackMatchCount=match_fallback_count,
        fallbackSearchCount=search_fallback_count,
        providers=provider_items
    )


@router.get("/rate-limit/status", summary="获取流控状态")
async def get_rate_limit_status(
    request: Request,
    stream: bool = Query(False, description="是否使用SSE流式推送(每秒更新)"),
    session: AsyncSession = Depends(get_db_session),
    scraper_manager: ScraperManager = Depends(get_scraper_manager),
    rate_limiter: RateLimiter = Depends(get_rate_limiter)
):
    """
    获取所有流控规则的当前状态，包括全局和各源的配额使用情况。

    ## 使用方式

    ### 普通JSON响应 (默认)
    - 请求: `GET /api/control/rate-limit/status`
    - 响应: 单次JSON对象
    - Content-Type: `application/json`

    ### SSE流式推送
    - 请求: `GET /api/control/rate-limit/status?stream=true`
    - 响应: `text/event-stream`，每秒推送一次状态更新
    - 事件格式: `data: {JSON对象}`
    - 客户端断开连接时自动停止推送

    ## 参数
    - **stream**: 是否使用SSE流式推送。默认False返回JSON，True返回text/event-stream

    ## 响应格式
    两种模式返回的数据结构完全一致，只是传输方式不同:
    - 普通模式: 一次性返回完整JSON
    - SSE模式: 每秒推送一次相同格式的JSON数据
    """
    if not stream:
        return await _get_rate_limit_status_data(session, scraper_manager, rate_limiter)

    async def event_generator():
        """SSE事件生成器，每秒推送一次流控状态"""
        session_factory = request.app.state.db_session_factory
        try:
            while True:
                # 主动检测客户端是否断开，优雅退出避免 CancelledError 打断数据库操作
                if await request.is_disconnected():
                    logger.debug("SSE流控状态推送: 客户端已断开连接，停止推送")
                    break
                try:
                    async with session_factory() as loop_session:
                        status_data = await _get_rate_limit_status_data(loop_session, scraper_manager, rate_limiter)
                        status_dict = status_data.model_dump(mode='json')
                        yield f"data: {json.dumps(status_dict, ensure_ascii=False)}\n\n"

                    await asyncio.sleep(1)

                except asyncio.CancelledError:
                    break
                except Exception as e:
                    logger.error(f"SSE流控状态推送出错: {e}")
                    await asyncio.sleep(1)

        except (asyncio.CancelledError, GeneratorExit):
            pass

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        }
    )

