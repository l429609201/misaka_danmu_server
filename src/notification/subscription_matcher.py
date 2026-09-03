"""
订阅匹配器 — 判断事件是否应该发送到指定渠道

SubscriptionMatcher 负责：
- 根据渠道的发送范围配置判断是否发送
- 不负责模板解析（由 TemplateResolver 处理）
"""
import logging
from typing import Dict, Any, Optional

from src.notification.events import (
    EventContext, NotificationEvent, TaskOperation, TaskSource, 
    TaskStatus, SystemEventType
)

logger = logging.getLogger(__name__)


class ScopeKey:
    """发送范围键常量 — 对应前端业务项"""
    # 弹幕入库
    IMPORT_SUCCESS = "import_success"
    IMPORT_FAILED = "import_failed"
    
    # 弹幕刷新
    REFRESH_SUCCESS = "refresh_success"
    REFRESH_FAILED = "refresh_failed"
    
    # 自动追更
    INCREMENTAL_REFRESH_SUCCESS = "incremental_refresh_success"  # 有更新
    INCREMENTAL_REFRESH_NO_CHANGE = "incremental_refresh_no_change"  # 无变化
    INCREMENTAL_REFRESH_FAILED = "incremental_refresh_failed"
    
    # 后备处理
    FALLBACK_SUCCESS = "fallback_success"
    FALLBACK_FAILED = "fallback_failed"
    
    # 媒体库扫描
    MEDIA_SCAN_SUCCESS = "media_scan_success"
    MEDIA_SCAN_FAILED = "media_scan_failed"
    
    # 系统通知
    SYSTEM_STARTUP = "system_startup"
    SYSTEM_EXCEPTION = "system_exception"
    
    # 批量结果（固定开启，不可配置）
    BATCH_RESULT = "batch_result"


class SubscriptionMatcher:
    """订阅匹配器 — 判断事件是否应该发送"""
    
    @staticmethod
    def should_send(event_ctx: EventContext, channel_scopes: Dict[str, bool]) -> bool:
        """判断事件是否应该发送到指定渠道
        
        Args:
            event_ctx: 事件上下文
            channel_scopes: 渠道的发送范围配置 {"scope_key": enabled}
            
        Returns:
            True=应该发送，False=不发送
        """
        scope_key = SubscriptionMatcher._get_scope_key(event_ctx)
        
        if scope_key is None:
            logger.debug(f"事件无法映射到发送范围: {event_ctx.to_dict()}")
            return False
        
        # 批量结果固定开启
        if scope_key == ScopeKey.BATCH_RESULT:
            return True
        
        # 查询渠道配置
        enabled = channel_scopes.get(scope_key, False)
        return enabled
    
    @staticmethod
    def _get_scope_key(event_ctx: EventContext) -> Optional[str]:
        """根据事件上下文获取发送范围键"""
        if event_ctx.event_type == NotificationEvent.TASK_EVENT:
            return SubscriptionMatcher._get_task_scope_key(event_ctx)
        elif event_ctx.event_type == NotificationEvent.SYSTEM_EVENT:
            return SubscriptionMatcher._get_system_scope_key(event_ctx)
        return None
    
    @staticmethod
    def _get_task_scope_key(event_ctx: EventContext) -> Optional[str]:
        """获取任务事件的发送范围键"""
        operation = event_ctx.operation
        status = event_ctx.status
        
        # 弹幕导入
        if operation == TaskOperation.IMPORT:
            if status == TaskStatus.SUCCESS:
                return ScopeKey.IMPORT_SUCCESS
            elif status == TaskStatus.FAILED:
                return ScopeKey.IMPORT_FAILED
        
        # 弹幕刷新（普通刷新）
        if operation == TaskOperation.REFRESH:
            if status == TaskStatus.SUCCESS:
                return ScopeKey.REFRESH_SUCCESS
            elif status == TaskStatus.FAILED:
                return ScopeKey.REFRESH_FAILED
        
        # 自动追更（特殊处理 NO_CHANGE 状态）
        if operation == TaskOperation.INCREMENTAL_REFRESH:
            if status == TaskStatus.SUCCESS:
                return ScopeKey.INCREMENTAL_REFRESH_SUCCESS
            elif status == TaskStatus.NO_CHANGE:
                return ScopeKey.INCREMENTAL_REFRESH_NO_CHANGE
            elif status == TaskStatus.FAILED:
                return ScopeKey.INCREMENTAL_REFRESH_FAILED
        
        # 后备处理（搜索、预下载、匹配统一处理）
        if operation in (
            TaskOperation.FALLBACK_SEARCH,
            TaskOperation.FALLBACK_PREDOWNLOAD,
            TaskOperation.FALLBACK_MATCH
        ):
            if status == TaskStatus.SUCCESS:
                return ScopeKey.FALLBACK_SUCCESS
            elif status == TaskStatus.FAILED:
                return ScopeKey.FALLBACK_FAILED
        
        # 媒体库扫描
        if operation == TaskOperation.MEDIA_SCAN:
            if status == TaskStatus.SUCCESS:
                return ScopeKey.MEDIA_SCAN_SUCCESS
            elif status == TaskStatus.FAILED:
                return ScopeKey.MEDIA_SCAN_FAILED
        
        return None
    
    @staticmethod
    def _get_system_scope_key(event_ctx: EventContext) -> Optional[str]:
        """获取系统事件的发送范围键"""
        system_type = event_ctx.system_type
        
        if system_type == SystemEventType.STARTUP:
            return ScopeKey.SYSTEM_STARTUP
        elif system_type == SystemEventType.EXCEPTION:
            return ScopeKey.SYSTEM_EXCEPTION
        
        return None
    
    @staticmethod
    def get_default_scopes() -> Dict[str, bool]:
        """获取默认发送范围配置"""
        return {
            # 弹幕入库：成功和失败都开启
            ScopeKey.IMPORT_SUCCESS: True,
            ScopeKey.IMPORT_FAILED: True,
            
            # 弹幕刷新：成功和失败都开启
            ScopeKey.REFRESH_SUCCESS: True,
            ScopeKey.REFRESH_FAILED: True,
            
            # 自动追更：有更新和失败开启，无变化关闭
            ScopeKey.INCREMENTAL_REFRESH_SUCCESS: True,
            ScopeKey.INCREMENTAL_REFRESH_NO_CHANGE: False,
            ScopeKey.INCREMENTAL_REFRESH_FAILED: True,
            
            # 后备处理：成功和失败都开启
            ScopeKey.FALLBACK_SUCCESS: True,
            ScopeKey.FALLBACK_FAILED: True,
            
            # 媒体库扫描：成功和失败都开启
            ScopeKey.MEDIA_SCAN_SUCCESS: True,
            ScopeKey.MEDIA_SCAN_FAILED: True,
            
            # 系统通知：启动关闭，异常开启
            ScopeKey.SYSTEM_STARTUP: False,
            ScopeKey.SYSTEM_EXCEPTION: True,
        }
