"""
模板解析器 — 将通用事件映射到 5 个场景模板

TemplateResolver 负责：
- 将 task_event/system_event 映射到模板 ID
- 不负责发送范围判断（由 SubscriptionMatcher 处理）
"""
import logging
from typing import Optional

from src.notification.events import (
    EventContext, NotificationEvent, TaskOperation, SystemEventType
)

logger = logging.getLogger(__name__)


class TemplateID:
    """模板 ID 常量"""
    DANMAKU_IMPORT = "danmaku_import"           # 弹幕入库
    DANMAKU_REFRESH = "danmaku_refresh"         # 弹幕刷新
    FALLBACK_PROCESSING = "fallback_processing" # 后备处理
    MEDIA_SCAN = "media_scan"                   # 媒体库扫描
    SYSTEM_NOTICE = "system_notice"             # 系统通知


class TemplateResolver:
    """模板解析器 — 事件到模板的映射"""
    
    @staticmethod
    def resolve(event_ctx: EventContext) -> Optional[str]:
        """根据事件上下文解析出模板 ID
        
        Args:
            event_ctx: 事件上下文对象
            
        Returns:
            模板 ID，无法映射时返回 None
        """
        if event_ctx.event_type == NotificationEvent.TASK_EVENT:
            return TemplateResolver._resolve_task_event(event_ctx)
        elif event_ctx.event_type == NotificationEvent.SYSTEM_EVENT:
            return TemplateResolver._resolve_system_event(event_ctx)
        else:
            logger.warning(f"未知事件类型: {event_ctx.event_type}")
            return None
    
    @staticmethod
    def _resolve_task_event(event_ctx: EventContext) -> Optional[str]:
        """解析任务事件到模板"""
        operation = event_ctx.operation
        
        # 弹幕导入：手动、自动、Webhook、URL/XML
        if operation == TaskOperation.IMPORT:
            return TemplateID.DANMAKU_IMPORT
        
        # 弹幕刷新：包含单集刷新、整源刷新、批量刷新和自动追更
        if operation in (TaskOperation.REFRESH, TaskOperation.INCREMENTAL_REFRESH):
            return TemplateID.DANMAKU_REFRESH
        
        # 后备处理：搜索、预下载、匹配
        if operation in (
            TaskOperation.FALLBACK_SEARCH,
            TaskOperation.FALLBACK_PREDOWNLOAD,
            TaskOperation.FALLBACK_MATCH
        ):
            return TemplateID.FALLBACK_PROCESSING
        
        # 媒体库扫描
        if operation == TaskOperation.MEDIA_SCAN:
            return TemplateID.MEDIA_SCAN
        
        logger.warning(f"无法映射任务操作到模板: {operation}")
        return None
    
    @staticmethod
    def _resolve_system_event(event_ctx: EventContext) -> Optional[str]:
        """解析系统事件到模板"""
        system_type = event_ctx.system_type
        
        # 服务启动和系统异常都使用系统通知模板
        if system_type in (SystemEventType.STARTUP, SystemEventType.EXCEPTION):
            return TemplateID.SYSTEM_NOTICE
        
        logger.warning(f"无法映射系统事件类型到模板: {system_type}")
        return None
    
    @staticmethod
    def get_all_template_ids() -> list:
        """获取所有模板 ID"""
        return [
            TemplateID.DANMAKU_IMPORT,
            TemplateID.DANMAKU_REFRESH,
            TemplateID.FALLBACK_PROCESSING,
            TemplateID.MEDIA_SCAN,
            TemplateID.SYSTEM_NOTICE,
        ]
    
    @staticmethod
    def get_template_display_name(template_id: str, lang: str = "zh") -> str:
        """获取模板显示名称
        
        Args:
            template_id: 模板 ID
            lang: 语言代码（zh/en/tw）
        """
        names = {
            TemplateID.DANMAKU_IMPORT: {
                "zh": "弹幕入库",
                "en": "Danmaku Import",
                "tw": "彈幕入庫",
            },
            TemplateID.DANMAKU_REFRESH: {
                "zh": "弹幕刷新",
                "en": "Danmaku Refresh",
                "tw": "彈幕刷新",
            },
            TemplateID.FALLBACK_PROCESSING: {
                "zh": "后备处理",
                "en": "Fallback Processing",
                "tw": "後備處理",
            },
            TemplateID.MEDIA_SCAN: {
                "zh": "媒体库扫描",
                "en": "Media Library Scan",
                "tw": "媒體庫掃描",
            },
            TemplateID.SYSTEM_NOTICE: {
                "zh": "系统通知",
                "en": "System Notice",
                "tw": "系統通知",
            },
        }
        
        template_names = names.get(template_id, {})
        return template_names.get(lang, template_id)
