"""Webhook 搜索任务的接收、延迟持久化与重新派发编排。"""

import json
import logging
import re
from dataclasses import dataclass
from datetime import timedelta
from typing import Any, Callable

from fastapi import HTTPException, status

from src.rate_limiter import RateLimiter
from src.schemas.webhook import WebhookImportEvent
from src.services.ai_service import AIService
from src.services.config_service import ConfigService
from src.services.metadata_service import MetadataService
from src.services.scraper_manager import ScraperManager
from src.services.service_container import get_database_service
from src.services.task_manager import TaskManager
from src.workflows.title_recognition import TitleRecognitionWorkflow

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class WebhookDispatchContext:
    """接入 API 和延迟 Job 共同注入的执行能力。"""

    task_manager: TaskManager
    scraper_manager: ScraperManager
    metadata_manager: MetadataService
    config_service: ConfigService
    ai_service: AIService
    rate_limiter: RateLimiter
    title_recognition_manager: TitleRecognitionWorkflow
    notification_service: Any = None


async def submit_webhook_search(event: WebhookImportEvent, context: WebhookDispatchContext) -> None:
    """使用同一任务工厂提交即时、手动或到期的搜索任务。"""
    task_coro = context.task_manager.build_task_coro_factory(
        "webhook_search",
        webhookSource=event.webhook_source,
        manager=context.scraper_manager,
        task_manager=context.task_manager,
        metadata_manager=context.metadata_manager,
        config_service=context.config_service,
        ai_service=context.ai_service,
        rate_limiter=context.rate_limiter,
        title_recognition_manager=context.title_recognition_manager,
        **event.payload,
    )
    await context.task_manager.submit_task(
        task_coro, event.task_title, unique_key=event.unique_key,
        task_type="webhook_search",
        task_parameters={"webhookSource": event.webhook_source, **event.payload},
    )


async def dispatch_webhook_import(event: WebhookImportEvent, context: WebhookDispatchContext) -> None:
    """应用开关和过滤规则，发送通知，再持久化延迟或立即提交。"""
    config = context.config_service
    if (await config.get("webhookEnabled", "true")).lower() != "true":
        logger.info("Webhook 功能已全局禁用，忽略请求。")
        return

    filter_mode = await config.get("webhookFilterMode", "blacklist")
    filter_regex_str = await config.get("webhookFilterRegex", "")
    if filter_regex_str:
        try:
            filter_pattern = re.compile(filter_regex_str, re.IGNORECASE)
            anime_title = event.payload.get("animeTitle", "")
            if (filter_mode == "blacklist" and filter_pattern.search(anime_title)) or (
                filter_mode == "whitelist" and not filter_pattern.search(anime_title)
            ):
                logger.info("Webhook 请求 '%s' 因匹配过滤规则而被忽略。", anime_title)
                return
        except re.error as exc:
            logger.error("无效的 Webhook 过滤正则表达式 '%s': %s", filter_regex_str, exc)

    delayed = (await config.get("webhookDelayedImportEnabled", "false")).lower() == "true"
    delay_text = await config.get("webhookDelayedImportHours", "24")
    delay_hours = int(delay_text) if delay_text.isdigit() else 24

    if context.notification_service is not None:
        try:
            await context.notification_service.emit_event("webhook_triggered", {
                "anime_title": event.payload.get("animeTitle", "未知"),
                "webhook_source": event.webhook_source,
                "task_title": event.task_title,
                "delayed": delayed,
                "delay_hours": delay_hours if delayed else 0,
                "season": event.payload.get("season"),
                "episode": event.payload.get("currentEpisodeIndex"),
                "media_type": event.payload.get("mediaType", ""),
                "year": event.payload.get("year"),
            })
        except Exception as exc:
            logger.error("发射 webhook_triggered 事件失败: %s", exc)

    if delayed:
        db = get_database_service()
        async with db.transaction():
            await db.webhook_task.create(
                task_title=event.task_title,
                unique_key=event.unique_key,
                payload=event.payload,
                webhook_source=event.webhook_source,
                is_delayed=True,
                delay=timedelta(hours=delay_hours),
            )
        logger.info("Webhook 任务 '%s' 已加入延时队列。", event.task_title)
        return

    try:
        await submit_webhook_search(event, context)
    except HTTPException as exc:
        if exc.status_code != status.HTTP_409_CONFLICT:
            raise
        # 重复通知已在队列中；继续处理同一通知的其他季度。
        logger.info("Webhook 任务 %s 已在队列中，忽略重复提交。", event.unique_key)


async def dispatch_pending_webhooks(task_ids: list[int], context: WebhookDispatchContext) -> int:
    """手动提交选中的延迟记录，失败时保留以便重试。"""
    db = get_database_service()
    async with db.transaction():
        pending = await db.webhook_task.get_pending_by_ids(task_ids)
    submitted = 0
    for task in pending:
        try:
            event = _event_from_task(task)
            try:
                await submit_webhook_search(event, context)
            except HTTPException as exc:
                if exc.status_code != status.HTTP_409_CONFLICT:
                    raise
                logger.info("Webhook 任务 %s 已在队列中，清理延迟记录", task["id"])
            async with db.transaction():
                await db.webhook_task.delete(task["id"])
            submitted += 1
        except Exception:
            logger.exception("手动派发 Webhook 任务 %s 失败，保留记录", task["id"])
    return submitted


async def dispatch_due_webhooks(
    context: WebhookDispatchContext, progress_callback: Callable[[int, str], Any],
) -> None:
    """逐条提交到期任务，失败记录状态，成功或队列重复则清理。"""
    db = get_database_service()
    async with db.transaction():
        due_tasks = await db.webhook_task.get_due_snapshots()
    if not due_tasks:
        await progress_callback(100, "没有需要处理的 Webhook 任务。")
        return

    for index, task in enumerate(due_tasks, start=1):
        await progress_callback(int(index / len(due_tasks) * 100), f"正在处理任务 {index}/{len(due_tasks)}: {task['title']}")
        try:
            try:
                await submit_webhook_search(_event_from_task(task), context)
            except HTTPException as exc:
                if exc.status_code != status.HTTP_409_CONFLICT:
                    raise
                logger.info("Webhook 任务 %s 已在队列中，移除重复延迟请求", task["id"])
            async with db.transaction():
                await db.webhook_task.delete(task["id"])
        except Exception:
            logger.exception("处理 Webhook 任务 %s 失败，保留失败记录", task["id"])
            async with db.transaction():
                await db.webhook_task.update_status(task["id"], "failed")


def _event_from_task(task: dict[str, Any]) -> WebhookImportEvent:
    """将 Repository 快照还原为接收时的搜索请求。"""
    return WebhookImportEvent(
        task_title=task["title"], unique_key=task["unique_key"],
        payload=json.loads(task["payload"]), webhook_source=task["source"],
    )
