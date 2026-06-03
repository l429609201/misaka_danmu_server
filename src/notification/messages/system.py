"""
系统级通知消息类型

系统启动、Webhook 触发、任务进度等非任务类系统通知。
"""
from dataclasses import dataclass
from typing import Dict

from .base import (
    NotificationMessage, MessageCategory, MessageSeverity,
)

_esc = NotificationMessage._escape_markdown


@dataclass
class SystemStartMessage(NotificationMessage):
    """系统启动通知 (system_start)"""

    def __post_init__(self):
        self.message_type = "system_start"
        self.category = MessageCategory.SYSTEM
        self.severity = MessageSeverity.INFO
        self.subscription_key = "system_start"

    def to_markdown(self) -> tuple:
        return ("系统启动", "弹幕服务器已启动完成 ✓")


@dataclass
class WebhookTriggeredMessage(NotificationMessage):
    """Webhook 触发通知 (webhook_triggered)"""

    def __post_init__(self):
        self.message_type = "webhook_triggered"
        self.category = MessageCategory.SYSTEM
        self.severity = MessageSeverity.INFO
        self.subscription_key = "webhook_triggered"

    def to_markdown(self) -> tuple:
        d = self.payload
        anime_title = _esc(d.get("anime_title", "")) or "未知"
        source = _esc(d.get("webhook_source", ""))
        delayed = d.get("delayed", False)
        delay_hours = d.get("delay_hours", "")
        lines = [
            "📺 *媒体信息*",
            f"• 名称: {anime_title}",
            f"• 来源: {source}",
            f"• 操作: {'⏳ 延迟入库 ' + str(delay_hours) + ' 小时后执行' if delayed else '⚡ 即时导入'}",
        ]
        return ("Webhook 触发", "\n".join(lines))


@dataclass
class MediaScanCompleteMessage(NotificationMessage):
    """媒体库扫描完成 (media_scan_complete)"""

    def __post_init__(self):
        self.message_type = "media_scan_complete"
        self.category = MessageCategory.SYSTEM
        self.severity = MessageSeverity.SUCCESS
        self.subscription_key = "media_scan_complete"

    def to_markdown(self) -> tuple:
        d = self.payload
        message = d.get("message", "扫描完成")
        return ("✅ 媒体库扫描完成", message)


@dataclass
class TaskProgressMessage(NotificationMessage):
    """任务进度消息 — 仅用于 TG 渠道的 edit_message 场景"""

    def __post_init__(self):
        self.message_type = "task_progress"
        self.category = MessageCategory.TASK
        self.severity = MessageSeverity.INFO
        self.subscription_key = self.payload.get("check_event_key", "task_progress")

    def to_markdown(self) -> tuple:
        d = self.payload
        task_title = d.get("task_title", "")
        progress = d.get("progress", 0)
        description = d.get("description", "")

        filled = int(progress / 10)
        bar = "█" * filled + "░" * (10 - filled)
        safe_title = _esc(task_title) if task_title else ""
        safe_desc = _esc(description) if description else ""
        lines = ["⚙️ *执行进度*", ""]
        if safe_title:
            lines.append(f"• 任务: {safe_title}")
        lines.append(f"• 进度: `[{bar}]` {progress}%")
        if safe_desc:
            lines.append(f"• 状态: {safe_desc}")
        return ("⬇️ 任务进行中", "\n".join(lines))
