"""Webhook 接入层输出给编排层的标准化事件。"""

from dataclasses import dataclass
from typing import Any, TypeAlias


@dataclass(frozen=True)
class WebhookImportEvent:
    """描述一次指定季度/集数的搜索导入请求。"""

    task_title: str
    unique_key: str
    payload: dict[str, Any]
    webhook_source: str


@dataclass(frozen=True)
class WebhookDeleteEvent:
    """描述一次媒体库删除联动请求，不执行任何数据库操作。"""

    server_type: str
    item_type: str
    item_id: str
    series_id: str | None = None
    season_id: str | None = None
    season_number: int | None = None
    title: str | None = None


WebhookEvent: TypeAlias = WebhookImportEvent | WebhookDeleteEvent
