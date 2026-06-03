"""
notification/messages — 消息对象体系

集中维护所有通知消息类型：
- 系统通知消息：任务完成、任务失败、追更失败、Webhook、系统启动等
- 交互回复消息：搜索结果、任务列表、确认操作、输入提示、分页结果等
- 每个消息类型声明字段、标题、订阅 key、聚合策略、按钮与渲染方法
"""
from .base import (
    MessageCategory,
    MessageSeverity,
    AggregationPolicy,
    NotificationMessage,
    RenderedMessage,
)
from .registry import MessageRegistry

__all__ = [
    "MessageCategory",
    "MessageSeverity",
    "AggregationPolicy",
    "NotificationMessage",
    "RenderedMessage",
    "MessageRegistry",
]
