"""Webhook 接收流程：统一衔接媒体事件解析与业务执行。"""

from typing import Any

from src.schemas.webhook import WebhookImportEvent
from src.services.webhook_service import WebhookService
from src.workflows.webhook_delete import handle_webhook_delete_flow
from src.workflows.webhook_dispatch import WebhookDispatchContext, dispatch_webhook_import


async def receive_webhook_event(
    webhook_service: WebhookService, webhook_type: str,
    request: Any, context: WebhookDispatchContext,
) -> None:
    """逐个执行解析出的新增或删除事件，跨季事件独立派发。"""
    events = await webhook_service.parse(webhook_type, request)
    for event in events:
        if isinstance(event, WebhookImportEvent):
            await dispatch_webhook_import(event, context)
        else:
            await handle_webhook_delete_flow(
                config_service=context.config_service,
                server_type=event.server_type,
                item_type=event.item_type,
                item_id=event.item_id,
                series_id=event.series_id,
                season_id=event.season_id,
                season_number=event.season_number,
                title=event.title,
            )
