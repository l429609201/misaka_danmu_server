"""外部日历订阅、立即导入与本地追更状态编排。"""

import logging
from dataclasses import dataclass
from typing import Any

from fastapi import HTTPException, status

from src.rate_limiter import RateLimiter
from src.schemas.auth import User
from src.schemas.calendar import BatchSubscribeRequest, SubscribeRequest, UnsubscribeRequest
from src.schemas.control import ControlAutoImportRequest, AutoImportSearchType, AutoImportMediaType
from src.services.ai_service import AIService
from src.services.config_service import ConfigService
from src.services.metadata_service import MetadataService
from src.services.scraper_manager import ScraperManager
from src.services.service_container import get_database_service
from src.services.task_manager import TaskManager

logger = logging.getLogger(__name__)


async def update_metadata_provider_config(
    metadata_service: MetadataService, provider: str, payload: dict[str, Any],
) -> Any:
    """先保存并重载源配置，再独立同步配置驱动的私人订阅。"""
    result = await metadata_service.updateProviderConfig(provider, payload)
    source = metadata_service.sources.get(provider)
    if source is not None and hasattr(source, "sync_config_subscriptions"):
        try:
            await source.sync_config_subscriptions()
        except Exception as exc:
            logger.error("元数据源 '%s' 同步订阅目标失败: %s", provider, exc, exc_info=True)
    return result


@dataclass(frozen=True)
class CalendarSubscriptionContext:
    """从 HTTP 接入层注入订阅执行所需的共享服务。"""

    task_manager: TaskManager
    scraper_manager: ScraperManager
    metadata_service: MetadataService
    config_service: ConfigService
    rate_limiter: RateLimiter
    ai_service: AIService
    title_recognition_manager: Any


def resolve_provider_external_id(body: SubscribeRequest) -> tuple[str | None, str | None]:
    """根据显式标识或元数据 ID 定位外部日历条目。"""
    if body.provider and body.externalId:
        return body.provider, str(body.externalId)
    if body.bangumiId:
        return "bangumi", str(body.bangumiId)
    if body.traktTmdbId:
        return "trakt", str(body.traktTmdbId)
    return None, None


async def _submit_auto_import(body: SubscribeRequest, user: User, context: CalendarSubscriptionContext) -> str:
    """为元数据标识生成可恢复的自动导入任务。"""
    is_movie = body.mediaType == "movie"
    media_type = AutoImportMediaType.MOVIE if is_movie else AutoImportMediaType.TV_SERIES
    season = None if is_movie else (body.season or 1)
    if body.traktTmdbId:
        search_type = AutoImportSearchType.TMDB
        search_term = str(body.traktTmdbId)
    elif body.bangumiId:
        search_type = AutoImportSearchType.BANGUMI
        search_term = str(body.bangumiId)
    else:
        if not body.animeTitle.strip():
            raise HTTPException(status_code=400, detail="缺少标题或可用的元数据 ID")
        search_type = AutoImportSearchType.KEYWORD
        search_term = body.animeTitle.strip()

    payload = ControlAutoImportRequest(
        searchType=search_type,
        searchTerm=search_term,
        season=season,
        episode=None,
        mediaType=media_type,
        enableIncrementalRefresh=True,
    )
    db = get_database_service()
    try:
        async with db.transaction():
            matched = await db.source.set_calendar_tracking_by_metadata(
                bangumi_id=body.bangumiId, tmdb_id=body.traktTmdbId,
            )
        if matched:
            logger.info("日历订阅：已为 %s 个本地作品开启增量追更", matched)
    except Exception as exc:
        logger.warning("日历订阅兜底开启追更失败（不影响订阅任务）: %s", exc)

    unique_key_parts = [search_type.value, search_term, media_type.value]
    if season is not None:
        unique_key_parts.append(f"s{season}")
    task_title = f"订阅: {body.animeTitle}"
    if season is not None:
        task_title += f" S{season:02d}"
    task_coro = context.task_manager.build_task_coro_factory(
        "auto_import", payload=payload, config_service=context.config_service,
        scraper_manager=context.scraper_manager, metadata_manager=context.metadata_service,
        task_manager=context.task_manager, ai_service=context.ai_service,
        rate_limiter=context.rate_limiter,
        title_recognition_manager=context.title_recognition_manager,
        oauth_user=User.model_validate(user),
    )
    task_id, _ = await context.task_manager.submit_task(
        task_coro, task_title,
        unique_key=f"calendar-subscribe-{'-'.join(unique_key_parts)}",
        task_type="auto_import", task_parameters=payload.model_dump(),
    )
    return task_id


async def subscribe_calendar_item_flow(
    body: SubscribeRequest, user: User, context: CalendarSubscriptionContext,
) -> dict[str, Any]:
    """登记订阅意向并在需要时提交可恢复的扫描或导入任务。"""
    provider, external_id = resolve_provider_external_id(body)
    if not provider or not external_id:
        raise HTTPException(
            status_code=400,
            detail="无法定位外部条目（缺少 provider/externalId 或 bangumiId/traktTmdbId）",
        )
    db = get_database_service()
    async with db.transaction():
        marked = await db.external_calendar.mark_subscribed(
            provider, external_id,
            status="importing" if body.runNow else "pending",
            item={
                "animeTitle": body.animeTitle, "animeType": body.mediaType,
                "season": body.season, "bangumiId": body.bangumiId,
                "traktId": body.traktId, "traktTmdbId": body.traktTmdbId,
            },
        )
    if not marked:
        raise HTTPException(status_code=500, detail="订阅标记失败")
    if not body.runNow:
        return {
            "message": f"'{body.animeTitle}' 已加入订阅，等待定时任务自动处理",
            "taskId": None, "subscriptionStatus": "pending",
        }

    try:
        is_limited, retry_after = await context.rate_limiter.get_global_limit_status()
        if is_limited:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail=f"系统流控中，暂时无法立即执行轮询，请约 {retry_after:.0f} 秒后再试。订阅已加入待处理队列。",
            )
        scraper = context.scraper_manager.get_scraper(provider)
        if scraper is not None and getattr(scraper, "supports_subscription", False):
            task_coro = context.task_manager.build_task_coro_factory(
                "scan_and_import_target",
                scraper_manager=context.scraper_manager,
                config_service=context.config_service,
                provider=provider, external_id=external_id,
                title_recognition_manager=context.title_recognition_manager,
                selected_episodes=body.selectedEpisodes or None,
            )
            task_id, _ = await context.task_manager.submit_task(
                task_coro, title=f"立即导入订阅: {body.animeTitle}",
                unique_key=f"scan-import-{provider}-{external_id}",
                task_type="scan_and_import_target",
                task_parameters={
                    "provider": provider, "externalId": external_id,
                    "title": body.animeTitle, "selectedEpisodes": body.selectedEpisodes,
                },
                run_immediately=True,
            )
        else:
            task_id = await _submit_auto_import(body, user, context)
    except HTTPException:
        async with db.transaction():
            await db.external_calendar.update_subscription_status(provider, external_id, "pending")
        raise
    except Exception as exc:
        async with db.transaction():
            await db.external_calendar.update_subscription_status(
                provider, external_id, "failed", increment_failure=True,
            )
        logger.exception("提交订阅任务失败: %s", exc)
        raise HTTPException(status_code=500, detail=f"提交订阅任务失败: {exc}") from exc
    return {
        "message": f"'{body.animeTitle}' 的订阅任务已提交",
        "taskId": task_id, "subscriptionStatus": "importing",
    }


async def subscribe_calendar_items_batch_flow(
    body: BatchSubscribeRequest, user: User, context: CalendarSubscriptionContext,
) -> dict[str, Any]:
    """复用单项流程提交批量订阅，逐项保留错误结果。"""
    results: list[dict[str, Any]] = []
    success_count = 0
    for item in body.items:
        current = item.model_copy(update={"runNow": body.runNow})
        try:
            result = await subscribe_calendar_item_flow(current, user, context)
            success_count += 1
            results.append({
                "animeTitle": item.animeTitle, "success": True,
                "taskId": result["taskId"], "subscriptionStatus": result["subscriptionStatus"],
            })
        except HTTPException as exc:
            results.append({"animeTitle": item.animeTitle, "success": False, "error": exc.detail})
        except Exception as exc:
            logger.exception("批量订阅 %s 失败: %s", item.animeTitle, exc)
            results.append({"animeTitle": item.animeTitle, "success": False, "error": str(exc)})
    failure_count = len(body.items) - success_count
    return {
        "successCount": success_count, "failureCount": failure_count,
        "results": results,
        "message": f"批量订阅完成：成功 {success_count} 项，失败 {failure_count} 项",
    }


async def unsubscribe_calendar_item_flow(body: UnsubscribeRequest) -> dict[str, str]:
    """在同一事务内取消外部订阅与本地作品追更。"""
    db = get_database_service()
    did_something = False
    async with db.transaction():
        if body.sourceId:
            source = await db.source.update(body.sourceId, isFinished=True)
            did_something = source is not None
        if body.provider and body.externalId:
            ok = await db.external_calendar.unsubscribe(body.provider, body.externalId)
            did_something = did_something or ok
        if not did_something and (body.bangumiId or body.traktId or body.traktTmdbId):
            matched = await db.source.set_calendar_tracking_by_metadata(
                bangumi_id=body.bangumiId, trakt_id=body.traktId,
                tmdb_id=body.traktTmdbId, finished=True,
            )
            did_something = matched > 0
    if not did_something:
        raise HTTPException(status_code=404, detail="未找到该订阅记录")
    return {"message": "已取消订阅"}
