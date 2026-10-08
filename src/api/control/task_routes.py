"""
外部控制API - 任务管理路由
包含: /tasks/*
"""

import logging
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status

from src.services.service_container import get_database_service
from src.schemas.ui_models import TaskInfo
from src.services.task_manager import TaskManager, TaskStatus

from src.schemas.control import ControlActionResponse, ExecutionTaskResponse
from .dependencies import get_task_manager

# 数据库访问统一在路由内开启事务，不把依赖工厂作为 session 传入。

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/tasks", response_model=List[TaskInfo], summary="获取后台任务列表")
async def get_tasks(
    search: Optional[str] = Query(None, description="按标题搜索"),
    status: str = Query("all", description="按状态过滤: all, in_progress, completed"),
) -> List[TaskInfo]:
    """获取后台任务列表，保持外部 API 的列表响应契约。"""
    db = get_database_service()
    async with db.transaction():
        paginated_result = await db.task.get_paginated_tasks(
            search=search, status_filter=status, queue_type="all", page=1, page_size=1000,
        )
    return [TaskInfo.model_validate(t) for t in paginated_result["list"]]


@router.get("/tasks/{taskId}", response_model=TaskInfo, summary="获取单个任务状态")
async def get_task_status(taskId: str) -> TaskInfo:
    """获取单个后台任务的详细状态。"""
    db = get_database_service()
    async with db.transaction():
        task_details = await db.task.get_task_details_from_history(taskId)
    if not task_details:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="任务未找到。")
    return TaskInfo.model_validate(task_details)


@router.delete("/tasks/{taskId}", response_model=ControlActionResponse, summary="删除一个历史任务")
async def delete_task(
    taskId: str,
    force: bool = False,
    task_manager: TaskManager = Depends(get_task_manager),
):
    """
    ### 功能
    删除一个后台任务。
    - **排队中**: 从队列中移除。
    - **运行中/已暂停**: 尝试中止任务，然后删除。
    - **已完成/失败**: 从历史记录中删除。
    - **force=true**: 强制删除，跳过中止逻辑直接删除历史记录。
    """
    db = get_database_service()
    async with db.transaction():
        task = await db.task.get_task_by_id(taskId)
    if not task:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="任务未找到。")

    task_status = task['status']

    if force:
        logger.info(f"强制删除任务 {taskId}，状态: {task_status}")
        async with db.transaction():
            deleted = await db.task.force_delete_task(taskId)
        if deleted:
            return {"message": f"强制删除任务 {taskId} 成功。"}
        else:
            return {"message": "强制删除失败，任务可能已被处理。"}

    if task_status == TaskStatus.PENDING:
        if await task_manager.cancel_pending_task(taskId):
            logger.info(f"已从队列中取消待处理任务 {taskId}。")
    elif task_status in [TaskStatus.RUNNING, TaskStatus.PAUSED]:
        if await task_manager.abort_current_task(taskId):
            logger.info(f"已发送中止信号到任务 {taskId}。")

    async with db.transaction():
        deleted = await db.task.delete_task(taskId)
    if deleted:
        return {"message": f"删除任务 {taskId} 的请求已处理。"}
    else:
        return {"message": "任务可能已被处理或不存在于历史记录中。"}


@router.post("/tasks/{taskId}/abort", response_model=ControlActionResponse, summary="中止正在运行的任务")
async def abort_task(
    taskId: str,
    force: bool = False,
    task_manager: TaskManager = Depends(get_task_manager),
):
    """
    尝试中止一个当前正在运行或已暂停的任务。
    - force=false: 正常中止，向任务发送取消信号
    - force=true: 强制中止，直接将任务标记为失败状态
    """
    if force:
        db = get_database_service()
        async with db.transaction():
            task = await db.task.get_task_by_id(taskId)
        if not task:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="任务不存在")

        await task_manager.abort_current_task(taskId)
        async with db.transaction():
            success = await db.task.force_fail_task(taskId)
        if success:
            return {"message": "任务已强制标记为失败状态。"}
        else:
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="强制中止任务失败")
    else:
        if not await task_manager.abort_current_task(taskId):
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="中止任务失败，可能任务已完成或不是当前正在执行的任务。")
        return {"message": "中止任务的请求已发送。"}


@router.post("/tasks/{taskId}/pause", response_model=ControlActionResponse, summary="暂停正在运行的任务")
async def pause_task(taskId: str, task_manager: TaskManager = Depends(get_task_manager)):
    """暂停一个当前正在运行的任务。任务将在下一次进度更新时暂停。"""
    if not await task_manager.pause_task(taskId):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="暂停任务失败，可能任务未在运行。")
    return {"message": "任务已暂停。"}


@router.post("/tasks/{taskId}/resume", response_model=ControlActionResponse, summary="恢复已暂停的任务")
async def resume_task(taskId: str, task_manager: TaskManager = Depends(get_task_manager)):
    """恢复一个已暂停的任务。"""
    if not await task_manager.resume_task(taskId):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="恢复任务失败，可能任务未被暂停。")
    return {"message": "任务已恢复。"}


@router.get("/tasks/{taskId}/execution", response_model=ExecutionTaskResponse, summary="获取调度任务触发的执行任务ID和状态")
async def get_execution_task_id(taskId: str) -> ExecutionTaskResponse:
    """
    在调用 `/import/auto` 等接口后，使用返回的调度任务ID来查询其触发的、
    真正执行下载导入工作的任务ID和状态。

    返回字段说明:
    - `schedulerTaskId`: 调度任务ID (输入的taskId)
    - `executionTaskId`: 执行任务ID,如果调度任务尚未触发执行任务则为null
    - `status`: 任务状态,可能的值:
        - `运行中`: 任务正在执行
        - `已完成`: 任务已成功完成
        - `失败`: 任务执行失败
        - `已取消`: 任务已被取消
        - `等待中`: 任务等待执行
        - `已暂停`: 任务已暂停
        - `null`: 调度任务尚未完成,无法获取状态

    您可以轮询此接口，直到获取到 `executionTaskId` 和最终状态。
    """
    db = get_database_service()
    async with db.transaction():
        execution_id, exec_status = await db.task.get_execution_task_id_from_scheduler_task(taskId)
    return ExecutionTaskResponse(schedulerTaskId=taskId, executionTaskId=execution_id, status=exec_status)
