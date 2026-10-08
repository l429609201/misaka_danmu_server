"""媒体库 Webhook 适配器基类，只收集标准化事件。"""

from abc import ABC, abstractmethod
import logging
from typing import Any

from fastapi import Request

from src.schemas.webhook import WebhookDeleteEvent, WebhookEvent, WebhookImportEvent


class BaseWebhook(ABC):
    """各媒体服务负责解析请求，不参与过滤、数据库写入或任务提交。"""

    def __init__(self) -> None:
        self.logger = logging.getLogger(self.__class__.__name__)
        self.events: list[WebhookEvent] = []

    @abstractmethod
    async def handle(self, request: Request, webhook_source: str) -> None:
        """解析请求并将有效的媒体事件加入 events。"""
        raise NotImplementedError

    def add_import(
        self, *, task_title: str, unique_key: str,
        payload: dict[str, Any], webhook_source: str,
    ) -> None:
        """收集搜索导入意图，实际派发由 Workflow 统一完成。"""
        self.events.append(WebhookImportEvent(task_title, unique_key, payload, webhook_source))

    def add_delete(
        self, *, server_type: str, item_type: str, item_id: str,
        series_id: str | None = None, season_id: str | None = None,
        season_number: int | None = None, title: str | None = None,
    ) -> None:
        """收集删除联动意图，实际操作由 Workflow 统一完成。"""
        self.events.append(WebhookDeleteEvent(
            server_type, item_type, item_id, series_id, season_id, season_number, title,
        ))
