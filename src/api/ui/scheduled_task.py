"""
Scheduled_task相关的API端点 - 定时任务管理
"""
import logging
from typing import List

from fastapi import APIRouter, Body, Depends, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.auth import get_current_user
from src.services.service_container import get_database_service
from src.services.scheduler import SchedulerManager
from src.workflows.scheduled_tasks import ScheduledTaskWorkflow
from src.schemas.auth import User
from src.schemas.common import ScheduledTaskInfo
from src.schemas.control.scheduler import (
    AvailableJobInfo,
    ScheduledTaskCreate,
    ScheduledTaskUpdate,
)
from src.api.dependencies import get_scheduler_manager

logger = logging.getLogger(__name__)

router = APIRouter()

@router.get("/scheduled-tasks/available-jobs", response_model=List[AvailableJobInfo])
async def get_available_jobs(
    request: Request,
    _current_user: User = Depends(get_current_user),  # 修复 CVE-2026-111644：枚举内部任务类型需登录鉴权
):
    """获取所有已加载的可用任务类型及其名称。"""
    scheduler_manager = request.app.state.scheduler_manager
    jobs = scheduler_manager.get_available_jobs()
    return jobs



@router.get("/scheduled-tasks", response_model=List[ScheduledTaskInfo], summary="获取所有定时任务")
async def get_scheduled_tasks(
    current_user: User = Depends(get_current_user),
    scheduler: SchedulerManager = Depends(get_scheduler_manager)
):
    tasks = await scheduler.get_all_tasks()
    db = get_database_service()
    result = []
    for t in tasks:
        info = ScheduledTaskInfo.model_validate(t)
        # 追更/刷新弹幕任务追加绑定数量
        if info.jobType in ('incrementalRefresh', 'refreshLatestEpisode'):
            try:
                async with db.transaction():
                    bound_ids = await db.task_binding.get_sources_by_task(info.taskId, enabled_only=False)
                    info.bindingCount = len(bound_ids)
            except Exception:
                pass
        result.append(info)
    return result



@router.post("/scheduled-tasks", response_model=ScheduledTaskInfo, status_code=201, summary="创建定时任务")
async def create_scheduled_task(
    task_data: ScheduledTaskCreate,
    current_user: User = Depends(get_current_user),
    scheduler: SchedulerManager = Depends(get_scheduler_manager)
):
    try:
        logger.info(f"[调试] 创建定时任务 - 接收到的 task_data: name={task_data.name}, jobType={task_data.jobType}, taskConfig={task_data.taskConfig}")
        new_task = await ScheduledTaskWorkflow(scheduler, get_database_service()).add_task(task_data.name, task_data.jobType, task_data.cronExpression, task_data.isEnabled, task_data.taskConfig)
        return ScheduledTaskInfo.model_validate(new_task)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"创建定时任务失败: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="创建定时任务时发生内部错误")



@router.put("/scheduled-tasks/{taskId}", response_model=ScheduledTaskInfo, summary="更新定时任务")
async def update_scheduled_task(
    taskId: str,
    task_data: ScheduledTaskUpdate,
    current_user: User = Depends(get_current_user),
    scheduler: SchedulerManager = Depends(get_scheduler_manager)
):
    logger.info(f"[调试] 更新定时任务 '{taskId}' - 接收到的 task_data: name={task_data.name}, cronExpression={task_data.cronExpression}, isEnabled={task_data.isEnabled}, taskConfig={task_data.taskConfig}")
    try:
        updated_task = await ScheduledTaskWorkflow(scheduler, get_database_service()).update_task(taskId, task_data.name, task_data.cronExpression, task_data.isEnabled, task_data.taskConfig)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not updated_task:
        raise HTTPException(status_code=404, detail="找不到指定的任务ID")
    return ScheduledTaskInfo.model_validate(updated_task)



@router.delete("/scheduled-tasks/{taskId}", status_code=204, summary="删除定时任务")
async def delete_scheduled_task(taskId: str, current_user: User = Depends(get_current_user), scheduler: SchedulerManager = Depends(get_scheduler_manager)):
    await scheduler.delete_task(taskId)



@router.post("/scheduled-tasks/{taskId}/run", status_code=202, summary="立即运行一次定时任务")
async def run_scheduled_task_now(taskId: str, current_user: User = Depends(get_current_user), scheduler: SchedulerManager = Depends(get_scheduler_manager)):
    try:
        await scheduler.run_task_now(taskId)
        return {"message": "任务已触发运行"}
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


# =========================================================
# 任务绑定 (Task Bindings) API
# =========================================================

@router.get(
    "/scheduled-tasks/{taskId}/bindings",
    summary="获取定时任务绑定的数据源列表",
    description="返回该定时任务绑定的所有数据源，包含番剧标题、平台、剧集信息等。"
)
async def get_task_bindings(
    taskId: str,
    current_user: User = Depends(get_current_user)
):
    """获取定时任务绑定的数据源列表。"""
    db = get_database_service()
    async with db.transaction():
        bindings = await db.task_binding.get_task_bindings(taskId)
    return {"taskId": taskId, "bindings": bindings, "total": len(bindings)}


@router.post(
    "/scheduled-tasks/{taskId}/bindings",
    status_code=201,
    summary="添加绑定数据源",
    description="将一组数据源绑定到指定定时任务。重复绑定会被忽略（幂等操作）。"
)
async def add_task_bindings(
    taskId: str,
    source_ids: List[int] = Body(..., embed=True, alias="sourceIds"),
    current_user: User = Depends(get_current_user)
):
    """向定时任务添加绑定的数据源（支持批量）。"""
    if not source_ids:
        raise HTTPException(status_code=400, detail="sourceIds 不能为空")
    db = get_database_service()
    async with db.transaction():
        # 检查任务是否存在
        task_info = await db.scheduled_task.get_scheduled_task(taskId)
        if not task_info:
            raise HTTPException(status_code=404, detail="找不到指定的定时任务")
        added_count = await db.task_binding.add_bindings(taskId, source_ids)
        bindings = await db.task_binding.get_task_bindings(taskId)
    return {
        "taskId": taskId,
        "addedCount": added_count,
        "bindings": bindings,
        "total": len(bindings)
    }


@router.delete(
    "/scheduled-tasks/{taskId}/bindings",
    status_code=200,
    summary="移除绑定数据源",
    description="从定时任务中移除指定的数据源绑定。不传 sourceIds 则清除该任务所有绑定。"
)
async def remove_task_bindings(
    taskId: str,
    source_ids: List[int] = Body(None, embed=True, alias="sourceIds"),
    current_user: User = Depends(get_current_user)
):
    """从定时任务中移除绑定的数据源（支持批量或全部清除）。"""
    db = get_database_service()
    async with db.transaction():
        task_info = await db.scheduled_task.get_scheduled_task(taskId)
        if not task_info:
            raise HTTPException(status_code=404, detail="找不到指定的定时任务")
        await db.task_binding.remove_bindings(taskId, source_ids)
        bindings = await db.task_binding.get_task_bindings(taskId)
    return {
        "taskId": taskId,
        "removedSourceIds": source_ids,
        "bindings": bindings,
        "total": len(bindings)
    }


@router.get(
    "/sources/{sourceId}/task-bindings",
    summary="获取数据源绑定的定时任务列表",
    description="返回绑定了该数据源的所有定时任务信息（任务名称、类型、执行计划等）。"
)
async def get_source_task_bindings(
    sourceId: int,
    current_user: User = Depends(get_current_user)
):
    """获取某个数据源绑定的所有定时任务。"""
    db = get_database_service()
    async with db.transaction():
        bindings = await db.task_binding.get_source_bindings(sourceId)
    return {"sourceId": sourceId, "bindings": bindings, "total": len(bindings)}
