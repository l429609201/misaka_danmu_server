"""
notification/messages — 消息对象体系

通用事件系统的消息抽象层：
- 基础消息类型：定义消息类别、严重性、聚合策略
- 统一消息类：UnifiedTaskMessage 和 UnifiedSystemMessage
- 交互回复消息：搜索结果、任务列表、确认操作、输入提示、分页结果等

注意：旧的 task.py、system.py、registry.py 已移除，
      所有通知现在通过 events.py 的 notify_event_v2() 发送。
"""
from .base import (
    MessageCategory,
    MessageSeverity,
    AggregationPolicy,
    NotificationMessage,
    RenderedMessage,
)
from .unified import UnifiedTaskMessage, UnifiedSystemMessage

__all__ = [
    "MessageCategory",
    "MessageSeverity",
    "AggregationPolicy",
    "NotificationMessage",
    "RenderedMessage",
    "UnifiedTaskMessage",
    "UnifiedSystemMessage",
]
