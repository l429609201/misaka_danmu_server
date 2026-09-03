"""
统一消息类 — 替代原有的 19 个具体消息类

UnifiedTaskMessage 和 UnifiedSystemMessage 基于通用事件上下文生成消息内容。
支持 Markdown 和纯文本双输出，使用 Jinja2 模板渲染。
"""
import logging
from dataclasses import dataclass
from typing import Optional, Tuple

from src.notification.messages.base import (
    NotificationMessage, MessageCategory, MessageSeverity, AggregationPolicy
)
from src.notification.events import EventContext, TaskStatus, SystemEventType

logger = logging.getLogger(__name__)


@dataclass
class UnifiedTaskMessage(NotificationMessage):
    """统一任务消息 — 处理所有 task_event"""
    
    event_ctx: Optional[EventContext] = None
    
    def __post_init__(self):
        self.category = MessageCategory.TASK
        
        # 根据任务状态设置严重级别
        if self.event_ctx and self.event_ctx.status:
            if self.event_ctx.status == TaskStatus.SUCCESS:
                self.severity = MessageSeverity.SUCCESS
            elif self.event_ctx.status == TaskStatus.FAILED:
                self.severity = MessageSeverity.ERROR
            elif self.event_ctx.status == TaskStatus.NO_CHANGE:
                self.severity = MessageSeverity.INFO
            else:
                self.severity = MessageSeverity.INFO
        else:
            self.severity = MessageSeverity.INFO
        
        # 订阅键由 SubscriptionMatcher 决定，这里不设置
        self.subscription_key = None
        
        # 聚合策略：支持批量聚合
        self.aggregation_policy = AggregationPolicy.TIME_WINDOW
    
    def to_markdown(self) -> Tuple[str, str]:
        """生成 Markdown 格式消息
        
        Returns:
            (title, body) 元组
        """
        if not self.event_ctx:
            return ("任务通知", "任务已完成")
        
        # 这里先返回基础格式，后续由 TemplateRenderer 使用 Jinja2 渲染
        ctx = self.event_ctx
        status_icon = self._get_status_icon(ctx.status)
        action_name = self._get_action_name(ctx.operation, ctx.source)
        status_name = self._get_status_name(ctx.status)
        
        title = f"{status_icon} {action_name}{status_name}"
        
        # 正文包含主体和结果
        body_parts = []
        
        # 主体信息
        if ctx.subject:
            anime_title = ctx.subject.get("anime_title", "")
            season = ctx.subject.get("season")
            episode = ctx.subject.get("episode")
            provider = ctx.subject.get("provider", "")
            
            if anime_title:
                body_parts.append(f"**作品**: {anime_title}")
            if season is not None:
                body_parts.append(f"**季**: {season}")
            if episode is not None:
                body_parts.append(f"**集**: {episode}")
            if provider:
                body_parts.append(f"**来源**: {provider}")
        
        # 结果信息
        if ctx.context:
            comment_count = ctx.context.get("comment_count")
            added_count = ctx.context.get("added_count")
            duration = ctx.context.get("duration")
            error = ctx.context.get("error", "")
            
            if comment_count is not None:
                body_parts.append(f"**弹幕数**: {comment_count}")
            if added_count is not None:
                body_parts.append(f"**新增**: {added_count}")
            if duration is not None:
                body_parts.append(f"**耗时**: {duration}秒")
            if error:
                body_parts.append(f"**错误**: {error}")
        
        body = "\n".join(body_parts) if body_parts else "操作已完成"
        
        return (title, body)
    
    def to_text(self) -> Tuple[str, str]:
        """生成纯文本格式消息"""
        title, body = self.to_markdown()
        # 移除 Markdown 标记
        body = body.replace("**", "")
        return (title, body)
    
    def _get_status_icon(self, status: Optional[TaskStatus]) -> str:
        """获取状态图标"""
        if status == TaskStatus.SUCCESS:
            return "✅"
        elif status == TaskStatus.FAILED:
            return "❌"
        elif status == TaskStatus.NO_CHANGE:
            return "ℹ️"
        return "📋"
    
    def _get_action_name(self, operation, source) -> str:
        """获取操作名称"""
        from src.notification.events import TaskOperation, TaskSource
        
        if operation == TaskOperation.IMPORT:
            if source == TaskSource.WEBHOOK:
                return "Webhook 导入"
            elif source == TaskSource.AUTO:
                return "自动导入"
            return "导入"
        elif operation == TaskOperation.REFRESH:
            return "刷新"
        elif operation == TaskOperation.INCREMENTAL_REFRESH:
            return "自动追更"
        elif operation in (TaskOperation.FALLBACK_SEARCH, TaskOperation.FALLBACK_PREDOWNLOAD, TaskOperation.FALLBACK_MATCH):
            return "后备处理"
        elif operation == TaskOperation.MEDIA_SCAN:
            return "媒体库扫描"
        return "任务"
    
    def _get_status_name(self, status: Optional[TaskStatus]) -> str:
        """获取状态名称"""
        if status == TaskStatus.SUCCESS:
            return "成功"
        elif status == TaskStatus.FAILED:
            return "失败"
        elif status == TaskStatus.NO_CHANGE:
            return "无变化"
        return ""


@dataclass
class UnifiedSystemMessage(NotificationMessage):
    """统一系统消息 — 处理所有 system_event"""
    
    event_ctx: Optional[EventContext] = None
    
    def __post_init__(self):
        self.category = MessageCategory.SYSTEM
        self.severity = MessageSeverity.INFO
        self.subscription_key = None
        self.aggregation_policy = AggregationPolicy.NONE  # 系统消息不聚合
    
    def to_markdown(self) -> Tuple[str, str]:
        """生成 Markdown 格式消息"""
        if not self.event_ctx:
            return ("系统通知", "系统事件")
        
        ctx = self.event_ctx
        
        if ctx.system_type == SystemEventType.STARTUP:
            title = "🚀 服务启动"
            body = "弹幕服务已成功启动"
        elif ctx.system_type == SystemEventType.EXCEPTION:
            title = "⚠️ 系统异常"
            error_msg = ctx.context.get("error", "未知错误") if ctx.context else "未知错误"
            body = f"**错误信息**: {error_msg}"
        else:
            title = "📢 系统通知"
            body = "系统事件"
        
        return (title, body)
    
    def to_text(self) -> Tuple[str, str]:
        """生成纯文本格式消息"""
        title, body = self.to_markdown()
        body = body.replace("**", "")
        return (title, body)
