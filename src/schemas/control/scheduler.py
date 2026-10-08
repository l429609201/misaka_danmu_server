"""
Control API - 定时任务管理相关模型
"""
from typing import Optional
from pydantic import BaseModel


class ScheduledTaskCreate(BaseModel):
    """创建定时任务"""
    name: str
    jobType: str
    cronExpression: str
    isEnabled: bool = True
    taskConfig: dict = {}


class ScheduledTaskUpdate(BaseModel):
    """更新定时任务"""
    name: str
    cronExpression: str
    isEnabled: Optional[bool] = None
    taskConfig: Optional[dict] = None


class AvailableJobInfo(BaseModel):
    """可用任务类型信息，与调度器输出及前端配置表单字段保持一致。"""
    jobType: str
    # 前端按语言读取名称和描述，不能用 displayName 替代或丢弃多语言字段。
    name: str
    name_en: str
    name_tw: str
    description: str
    description_en: str
    description_tw: str
    isSystemTask: bool
    # 配置默认值定义在各配置项中，保留完整结构供前端生成表单。
    configSchema: list[dict[str, object]]


__all__ = [
    "ScheduledTaskCreate",
    "ScheduledTaskUpdate",
    "AvailableJobInfo",
]
