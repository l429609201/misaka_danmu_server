"""
交互回复消息类型（P4 阶段填充）

预留框架，后续迁移：
- help 菜单回复
- tasks 列表回复
- task detail 回复
- cache 菜单回复
- search 搜索结果与分页
- refresh 选择番剧/数据源/集数
- auto 自动导入多步输入
- token 管理与确认删除

迁移完成后，这些交互回复将不再通过 CommandResult 构造，
而是作为独立 ReplyMessage 对象由 NotificationManager 统一输出。
"""
from dataclasses import dataclass
from typing import Dict, List

from .base import NotificationMessage, MessageCategory, MessageSeverity


@dataclass
class InteractionReplyMessage(NotificationMessage):
    """交互回复消息基类 — P4 阶段迁移 CommandResult 回复时使用

    子类示例（后续实现）：
    - HelpReplyMessage
    - TaskListReplyMessage
    - SearchResultReplyMessage
    - RefreshReplyMessage
    - AutoImportReplyMessage
    - TokenReplyMessage
    - CacheReplyMessage
    """

    def __post_init__(self):
        self.category = MessageCategory.INTERACTION
        self.severity = MessageSeverity.INFO
