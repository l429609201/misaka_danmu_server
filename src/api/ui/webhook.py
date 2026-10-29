"""
Webhook相关的API端点
"""
import logging
from typing import Optional, List, Dict

from fastapi import APIRouter, Depends, Query

from src.services.service_container import get_database_service
from src.services.webhook_service import WebhookService
from src.workflows.webhook_dispatch import WebhookDispatchContext, dispatch_pending_webhooks
from src.api.dependencies import (
    get_webhook_service,
    get_task_manager,
    get_scraper_manager,
    get_metadata_service,
    get_config_service,
    get_ai_service,
    get_rate_limiter,
    get_title_recognition_manager
)
from src.schemas.ui_models import PaginatedWebhookTasksResponse

logger = logging.getLogger(__name__)

router = APIRouter()

@router.get("/webhooks/available", response_model=List[str], summary="获取所有可用的Webhook类型")
async def get_available_webhook_types(
    webhook_service: WebhookService = Depends(get_webhook_service)
):
    """获取显式注册的 Webhook 类型。"""
    return webhook_service.get_available_handlers()


@router.get("/webhook-tasks", response_model=PaginatedWebhookTasksResponse, summary="获取待处理的Webhook任务列表")
async def get_webhook_tasks(
    page: int = Query(1, ge=1),
    pageSize: int = Query(100, ge=1),
    search: Optional[str] = Query(None, description="搜索关键词"),
):
    db = get_database_service()
    async with db.transaction():
        result = await db.webhook_task.get_paginated(page, pageSize, search)
    return PaginatedWebhookTasksResponse.model_validate(result)


@router.post("/webhook-tasks/delete-bulk", summary="批量删除Webhook任务")
async def delete_bulk_webhook_tasks(
    payload: Dict[str, List[int]],
):
    db = get_database_service()
    async with db.transaction():
        deleted_count = await db.webhook_task.delete_by_ids(payload.get("ids", []))
    return {"message": f"成功删除 {deleted_count} 个任务"}


@router.delete("/webhook-tasks/clear-all", summary="清空所有Webhook任务")
async def clear_all_webhook_tasks():
    """一次性清空所有待处理的 Webhook 任务（适用于大量错误任务或已过期任务）"""
    db = get_database_service()
    async with db.transaction():
        deleted_count = await db.webhook_task.delete_all()
    return {"message": f"已清空 {deleted_count} 个任务", "deletedCount": deleted_count}


@router.post("/webhook-tasks/run-now", summary="立即执行选中的Webhook任务")
async def run_webhook_tasks_now(
    payload: Dict[str, List[int]],
    task_manager = Depends(get_task_manager),
    scraper_manager = Depends(get_scraper_manager),
    metadata_manager = Depends(get_metadata_service),
    config_service = Depends(get_config_service),
    ai_service = Depends(get_ai_service),
    rate_limiter = Depends(get_rate_limiter),
    title_recognition_manager = Depends(get_title_recognition_manager)
):
    """立即执行指定的待处理Webhook任务。"""
    task_ids = payload.get("ids", [])
    if not task_ids:
        return {"message": "未选择任何任务"}

    submitted_count = await dispatch_pending_webhooks(
        task_ids,
        WebhookDispatchContext(
            task_manager=task_manager,
            scraper_manager=scraper_manager,
            metadata_manager=metadata_manager,
            config_service=config_service,
            ai_service=ai_service,
            rate_limiter=rate_limiter,
            title_recognition_manager=title_recognition_manager,
        ),
    )

    if submitted_count > 0:
        return {"message": f"已成功提交 {submitted_count} 个任务到执行队列。"}
    else:
        return {"message": "未找到可执行的待处理任务"}

