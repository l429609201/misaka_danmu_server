"""
Control API - 任务管理相关模型
"""
from datetime import datetime
from typing import List, Optional
from pydantic import BaseModel


class TaskInfo(BaseModel):
    """任务信息"""
    taskId: str
    title: str
    status: str
    progress: int
    description: str
    createdAt: datetime
    # 播报使用状态更新时间，不能用提交时间判断旧任务是否刚完成。
    updatedAt: Optional[datetime] = None
    isSystemTask: bool = False
    queueType: str = "download"  # 队列类型: "download"、"management" 或 "fallback"
    taskType: Optional[str] = None  # 任务类型，不为 None 时表示该任务支持重试
    uniqueKey: Optional[str] = None  # 前端按资源键展示活跃操作，不解析任务标题


class PaginatedTasksResponse(BaseModel):
    """用于任务列表分页的响应模型"""
    total: int
    list: List[TaskInfo]
    serverTime: Optional[datetime] = None


__all__ = [
    "TaskInfo",
    "PaginatedTasksResponse",
]
