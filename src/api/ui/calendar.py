"""
日历视图 API
"""
import logging

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import RedirectResponse, Response

from src.api.dependencies import (
    get_metadata_service, get_task_manager, get_scraper_manager,
    get_config_service, get_rate_limiter, get_ai_service,
    get_title_recognition_manager,
)
from src.rate_limiter import RateLimiter
from src.schemas.auth import User
from src.schemas.calendar import SubscribeRequest, BatchSubscribeRequest, UnsubscribeRequest
from src.services.metadata_service import MetadataService
from src.services.scraper_manager import ScraperManager
from src.services.task_manager import TaskManager
from src.services.ai_service import AIService
from src.services.config_service import ConfigService
from src.utils.auth import security
from src.workflows.calendar.weekly_flow import get_weekly_calendar_flow
from src.workflows.calendar.cache_flow import clear_calendar_cache_flow
from src.workflows.calendar.schedule_flow import sync_calendar_schedule
from src.workflows.calendar.discover_flow import discover_current_season_flow
from src.workflows.calendar.subscription_flow import (
    CalendarSubscriptionContext,
    subscribe_calendar_item_flow,
    subscribe_calendar_items_batch_flow,
    unsubscribe_calendar_item_flow,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/calendar", tags=["Calendar"])


def _subscription_context(
    task_manager: TaskManager = Depends(get_task_manager),
    scraper_manager: ScraperManager = Depends(get_scraper_manager),
    metadata_service: MetadataService = Depends(get_metadata_service),
    config_service: ConfigService = Depends(get_config_service),
    rate_limiter: RateLimiter = Depends(get_rate_limiter),
    ai_service: AIService = Depends(get_ai_service),
    title_recognition_manager=Depends(get_title_recognition_manager),
) -> CalendarSubscriptionContext:
    """组合订阅编排需要的共享服务。"""
    return CalendarSubscriptionContext(
        task_manager, scraper_manager, metadata_service, config_service,
        rate_limiter, ai_service, title_recognition_manager,
    )




@router.get("/weekly")
async def get_weekly_calendar(
    user: User = Depends(security.get_current_user),
    metadata_manager: MetadataService = Depends(get_metadata_service),
    scraper_manager: ScraperManager = Depends(get_scraper_manager),
):
    """聚合本地追更、元数据源与弹幕源周历。"""
    return await get_weekly_calendar_flow(user, metadata_manager, scraper_manager)


@router.get("/tmdb-poster/{tmdb_id}", summary="按需获取 TMDB 海报（懒加载，免认证）")
async def get_tmdb_poster(
    tmdb_id: int,
    metadata_manager: MetadataService = Depends(get_metadata_service),
):
    """前端 <img> 直接指向此端点，浏览器天然懒加载/并发控制。
    委托 TMDB 源查海报 URL（查缓存→TMDB），拿到后 302 重定向（海报 URL 公开可直连）。
    无需 JWT：海报 URL 非敏感信息，且 <img> 标签不便携带 Authorization。"""
    try:
        poster_url = await metadata_manager.get_tmdb_poster_url(tmdb_id)
    except Exception as e:
        logger.debug(f"获取 TMDB 海报失败 (tmdb_id={tmdb_id}): {e}")
        poster_url = None
    if not poster_url:
        # 404 也缓存一小段时间，避免无海报的条目每次重渲染都打一次请求
        return Response(status_code=status.HTTP_404_NOT_FOUND, headers={"Cache-Control": "public, max-age=3600"})
    # 海报 URL 稳定不变 → 让浏览器长期缓存该 302，避免卡片重渲染/滚动时重复请求
    return RedirectResponse(
        url=poster_url,
        status_code=status.HTTP_302_FOUND,
        headers={"Cache-Control": "public, max-age=604800, immutable"},
    )


@router.get("/tmdb-title/{tmdb_id}", summary="按需获取 TMDB 中文标题与年份（懒加载，免认证）")
async def get_tmdb_title(
    tmdb_id: int,
    metadata_manager: MetadataService = Depends(get_metadata_service),
):
    """前端对 Trakt 日历条目按需请求，用 TMDB 中文标题/年份覆盖英文原标题。
    无需 JWT：标题/年份非敏感信息。"""
    try:
        return await metadata_manager.get_tmdb_title_year(tmdb_id) or {"title": None, "year": None}
    except Exception as e:
        logger.debug(f"获取 TMDB 标题失败 (tmdb_id={tmdb_id}): {e}")
        return {"title": None, "year": None}


@router.post("/clear-cache", summary="清除日历同步缓存（Trakt/Bangumi 日历结果）")
async def clear_calendar_cache(
    user: User = Depends(security.get_current_user),
) -> dict:
    """清除日历缓存，保留订阅意向及订阅扫描产生的子候选项。"""
    try:
        deleted = await clear_calendar_cache_flow()
    except Exception as exc:
        logger.error("清除日历缓存失败: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"清除缓存失败: {exc}",
        ) from exc
    return {"message": f"已清除 {deleted} 条日历缓存", "deletedCount": deleted}


@router.post("/sync-bangumi-schedule")
async def sync_bangumi_schedule(
    user: User = Depends(security.get_current_user),
    metadata_manager: MetadataService = Depends(get_metadata_service),
    scraper_manager: ScraperManager = Depends(get_scraper_manager),
):
    """手动同步播出日程，复用定时任务的元数据 Workflow。"""
    return await sync_calendar_schedule(
        user, metadata_manager, scraper_manager, force_refresh=True,
    )



@router.get("/discover")
async def discover_current_season(
    user: User = Depends(security.get_current_user),
    metadata_manager: MetadataService = Depends(get_metadata_service),
):
    """返回当季 Bangumi 日历及本地收录状态。"""
    return await discover_current_season_flow(user, metadata_manager)






@router.post("/subscribe", summary="订阅外部番（标记订阅意向，可选立即执行轮询）")
async def subscribe_calendar_item(
    body: SubscribeRequest,
    user: User = Depends(security.get_current_user),
    context: CalendarSubscriptionContext = Depends(_subscription_context),
):
    """登记外部日历订阅并按需提交导入任务。"""
    return await subscribe_calendar_item_flow(body, user, context)


@router.post("/subscribe/batch", summary="批量订阅外部番（可选立即执行轮询）")
async def subscribe_calendar_items_batch(
    body: BatchSubscribeRequest,
    user: User = Depends(security.get_current_user),
    context: CalendarSubscriptionContext = Depends(_subscription_context),
):
    """逐项提交日历订阅并返回每项结果。"""
    return await subscribe_calendar_items_batch_flow(body, user, context)


@router.post("/unsubscribe", summary="取消订阅（统一处理本地取消追更 + 外部取消订阅）")
async def unsubscribe_calendar_item(
    body: UnsubscribeRequest,
    user: User = Depends(security.get_current_user),
) -> dict:
    """取消外部日历订阅或本地追更。"""
    return await unsubscribe_calendar_item_flow(body)
