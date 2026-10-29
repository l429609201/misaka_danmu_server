"""Webhook 解析服务：统一访问已显式注册的媒体服务适配器。"""

import logging
from collections.abc import Mapping
from typing import Any, Protocol

from src.schemas.webhook import WebhookEvent

logger = logging.getLogger(__name__)


class UnknownWebhookTypeError(ValueError):
    """请求的媒体服务类型没有注册解析器。"""


class WebhookAdapter(Protocol):
    """接入层解析器需要满足的结构化契约。"""

    events: list[WebhookEvent]

    async def handle(self, request: Any, webhook_source: str) -> None:
        """将请求解析为标准化事件。"""
        ...


class WebhookService:
    """统一注册适配器并解析事件，不依赖 Webhook 或 Workflow 实现。"""

    def __init__(self, handlers: Mapping[str, type[WebhookAdapter]]) -> None:
        if not handlers:
            raise ValueError("Webhook 处理器列表不能为空")
        self._handlers = dict(handlers)
        logger.info("已注册 %s 个 Webhook 处理器: %s", len(handlers), ", ".join(sorted(handlers)))

    async def parse(self, webhook_type: str, request: Any) -> list[WebhookEvent]:
        """为每个请求建立独立解析器，避免并发事件混入其他请求。"""
        handler_class = self._handlers.get(webhook_type)
        if handler_class is None:
            raise UnknownWebhookTypeError(f"未找到类型为 '{webhook_type}' 的 Webhook 处理器")
        handler = handler_class()
        await handler.handle(request, webhook_source=webhook_type)
        return handler.events

    def get_available_handlers(self) -> list[str]:
        """返回已显式注册的处理器名称。"""
        return sorted(self._handlers)
