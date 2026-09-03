"""
通知事件枚举 — 通用化事件系统

将原有 19 个具体事件收敛为 task_event 和 system_event 两类，
通过结构化字段（operation/source/status/subject）表达多维语义。
"""
from enum import Enum
from typing import Optional, Dict, Any


class NotificationEvent(str, Enum):
    """通用通知事件类型"""
    TASK_EVENT = "task_event"       # 业务任务事件（导入、刷新、后备、扫描等）
    SYSTEM_EVENT = "system_event"   # 系统级事件（启动、异常等）


class TaskOperation(str, Enum):
    """任务操作类型"""
    IMPORT = "import"                       # 弹幕导入
    REFRESH = "refresh"                     # 弹幕刷新
    INCREMENTAL_REFRESH = "incremental_refresh"  # 自动追更
    FALLBACK_SEARCH = "fallback_search"     # 后备搜索
    FALLBACK_PREDOWNLOAD = "fallback_predownload"  # 后备预下载
    FALLBACK_MATCH = "fallback_match"       # 后备匹配
    MEDIA_SCAN = "media_scan"               # 媒体库扫描


class TaskSource(str, Enum):
    """任务触发来源"""
    MANUAL = "manual"           # 手动触发
    AUTO = "auto"               # 自动触发（如定时任务）
    WEBHOOK = "webhook"         # Webhook 触发
    API = "api"                 # API 调用
    SCHEDULER = "scheduler"     # 调度器触发


class TaskStatus(str, Enum):
    """任务执行状态"""
    SUCCESS = "success"         # 成功
    FAILED = "failed"           # 失败
    PARTIAL = "partial"         # 部分成功
    NO_CHANGE = "no_change"     # 无变化（自动追更专用）


class SystemEventType(str, Enum):
    """系统事件类型"""
    STARTUP = "startup"         # 服务启动
    EXCEPTION = "exception"     # 系统异常


class EventContext:
    """事件上下文 — 携带事件的详细信息"""
    
    def __init__(
        self,
        event_type: NotificationEvent,
        # 任务事件字段
        operation: Optional[TaskOperation] = None,
        source: Optional[TaskSource] = None,
        status: Optional[TaskStatus] = None,
        subject: Optional[Dict[str, Any]] = None,  # 任务主体信息（anime/episode/provider等）
        context: Optional[Dict[str, Any]] = None,  # 任务结果详情（count/duration/error等）
        # 系统事件字段
        system_type: Optional[SystemEventType] = None,
        # 通用字段
        batch_id: Optional[str] = None,   # 批次ID（用于聚合）
        task_id: Optional[str] = None,    # 任务ID
    ):
        self.event_type = event_type
        self.operation = operation
        self.source = source
        self.status = status
        self.subject = subject or {}
        self.context = context or {}
        self.system_type = system_type
        self.batch_id = batch_id
        self.task_id = task_id
    
    def to_dict(self) -> Dict[str, Any]:
        """转换为字典，用于序列化"""
        return {
            "event_type": self.event_type.value if self.event_type else None,
            "operation": self.operation.value if self.operation else None,
            "source": self.source.value if self.source else None,
            "status": self.status.value if self.status else None,
            "subject": self.subject,
            "context": self.context,
            "system_type": self.system_type.value if self.system_type else None,
            "batch_id": self.batch_id,
            "task_id": self.task_id,
        }
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "EventContext":
        """从字典创建事件上下文"""
        return cls(
            event_type=NotificationEvent(data["event_type"]) if data.get("event_type") else None,
            operation=TaskOperation(data["operation"]) if data.get("operation") else None,
            source=TaskSource(data["source"]) if data.get("source") else None,
            status=TaskStatus(data["status"]) if data.get("status") else None,
            subject=data.get("subject"),
            context=data.get("context"),
            system_type=SystemEventType(data["system_type"]) if data.get("system_type") else None,
            batch_id=data.get("batch_id"),
            task_id=data.get("task_id"),
        )
