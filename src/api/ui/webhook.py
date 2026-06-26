"""
Webhook相关的API端点
"""
import logging
from typing import Optional, List, Dict

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from src import security, tasks
from src.db import crud, models, get_db_session, ConfigManager
from src.rate_limiter import RateLimiter
from src.services import (
    TaskManager, ScraperManager, MetadataSourceManager,
    WebhookManager, TitleRecognitionManager
)
from src.ai import AIMatcherManager

from src.api.dependencies import (
    get_scraper_manager, get_task_manager, get_webhook_manager,
    get_metadata_manager, get_config_manager, get_ai_matcher_manager,
    get_rate_limiter, get_title_recognition_manager
)
from .models import PaginatedWebhookTasksResponse

logger = logging.getLogger(__name__)

router = APIRouter()

@router.get("/webhooks/available", response_model=List[str], summary="获取所有可用的Webhook类型")
async def get_available_webhook_types(
    current_user: models.User = Depends(security.get_current_user),
    webhook_manager: WebhookManager = Depends(get_webhook_manager)
):
    """获取所有已成功加载的、可供用户选择的Webhook处理器类型。"""
    return webhook_manager.get_available_handlers()



@router.get("/webhook-tasks", response_model=PaginatedWebhookTasksResponse, summary="获取待处理的Webhook任务列表")
async def get_webhook_tasks(
    page: int = Query(1, ge=1),
    pageSize: int = Query(100, ge=1),
    search: Optional[str] = Query(None, description="按任务标题搜索"),
    session: AsyncSession = Depends(get_db_session)
):
    result = await crud.get_webhook_tasks(session, page, pageSize, search)
    return PaginatedWebhookTasksResponse.model_validate(result)



@router.post("/webhook-tasks/delete-bulk", summary="批量删除Webhook任务")
async def delete_bulk_webhook_tasks(
    payload: Dict[str, List[int]],
    session: AsyncSession = Depends(get_db_session),
    current_user: models.User = Depends(security.get_current_user),
):
    deleted_count = await crud.delete_webhook_tasks(session, payload.get("ids", []))
    return {"message": f"成功删除 {deleted_count} 个任务。"}


@router.delete("/webhook-tasks/clear-all", summary="清空所有Webhook任务")
async def clear_all_webhook_tasks(
    session: AsyncSession = Depends(get_db_session),
    current_user: models.User = Depends(security.get_current_user),
):
    """一键清空所有待处理的 Webhook 任务，用于处理大量任务堆积的情况。"""
    deleted_count = await crud.delete_all_webhook_tasks(session)
    return {"message": f"已清空 {deleted_count} 个任务。", "deletedCount": deleted_count}


@router.post("/webhook-tasks/run-now", summary="立即执行选中的Webhook任务")
async def run_webhook_tasks_now(
    payload: Dict[str, List[int]],
    session: AsyncSession = Depends(get_db_session),
    task_manager: TaskManager = Depends(get_task_manager),
    scraper_manager: ScraperManager = Depends(get_scraper_manager),
    metadata_manager: MetadataSourceManager = Depends(get_metadata_manager),
    config_manager: ConfigManager = Depends(get_config_manager),
    ai_matcher_manager: AIMatcherManager = Depends(get_ai_matcher_manager),
    rate_limiter: RateLimiter = Depends(get_rate_limiter),
    title_recognition_manager: TitleRecognitionManager = Depends(get_title_recognition_manager)
):
    """立即执行指定的待处理Webhook任务。"""
    task_ids = payload.get("ids", [])
    if not task_ids:
        return {"message": "没有选中任何任务。"}

    submitted_count = await tasks.run_webhook_tasks_directly_manual(
        session=session,
        task_ids=task_ids,
        task_manager=task_manager,
        scraper_manager=scraper_manager,
        metadata_manager=metadata_manager,
        config_manager=config_manager,
        ai_matcher_manager=ai_matcher_manager,
        rate_limiter=rate_limiter,
        title_recognition_manager=title_recognition_manager
    )

    if submitted_count > 0:
        return {"message": f"已成功提交 {submitted_count} 个任务到执行队列。"}
    else:
        return {"message": "没有找到可执行的待处理任务。"}






