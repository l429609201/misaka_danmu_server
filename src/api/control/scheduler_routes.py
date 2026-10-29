"""
外部控制API - 定时任务管理路由
包含: /scheduler/*
"""

import logging
from typing import List, Dict, Any

from fastapi import APIRouter, Depends, HTTPException, Path, status

from src.services.service_container import get_database_service
from src.schemas.ui_models import TaskInfo
from src.services.scheduler import SchedulerManager

# 共用依赖负责真实会话的创建、事务提交与异常回滚。
from .dependencies import get_scheduler_manager

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/scheduler/tasks", response_model=List[Dict[str, Any]], summary="获取所有定时任务")
async def list_scheduled_tasks(
    scheduler_manager: SchedulerManager = Depends(get_scheduler_manager),
):
    """获取所有已配置的定时任务及其当前状态。"""
    tasks = await scheduler_manager.get_all_tasks()
    return tasks


@router.get("/scheduler/{taskId}/last_result", response_model=TaskInfo, summary="获取定时任务的最近一次运行结果")
async def get_scheduled_task_last_result(
    taskId: str = Path(..., description="定时任务的ID"),
):
    """
    获取指定定时任务的最近一次运行结果。
    如果任务从未运行过，将返回 404 Not Found。
    """
    db = get_database_service()
    async with db.transaction():
        # 定时任务查询由已注册的 scheduled_task 数据域提供。
        result = await db.scheduled_task.get_last_run_result_for_scheduled_task(taskId)
    if not result:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="未找到该定时任务的运行记录。")
    return TaskInfo.model_validate(result)
