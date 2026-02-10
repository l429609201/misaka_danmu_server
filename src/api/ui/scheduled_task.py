"""
Scheduled_task相关的API端点 - 定时任务管理
"""
import logging
from typing import List

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession

from src import security
from src.db import models, get_db_session
from src.services import SchedulerManager

from src.api.dependencies import get_scheduler_manager

# 为了兼容性，创建别名
api_models = models

logger = logging.getLogger(__name__)

router = APIRouter()

@router.get("/scheduled-tasks/available-jobs", response_model=List[api_models.AvailableJobInfo])
async def get_available_jobs(request: Request):
    """获取所有已加载的可用任务类型及其名称。"""
    scheduler_manager = request.app.state.scheduler_manager
    jobs = scheduler_manager.get_available_jobs()
    logger.info(f"可用的任务类型: {jobs}")
    return jobs



@router.get("/scheduled-tasks", response_model=List[models.ScheduledTaskInfo], summary="获取所有定时任务")
async def get_scheduled_tasks(
    current_user: models.User = Depends(security.get_current_user),
    scheduler: SchedulerManager = Depends(get_scheduler_manager)
):
    tasks = await scheduler.get_all_tasks()
    return [models.ScheduledTaskInfo.model_validate(t) for t in tasks]



@router.post("/scheduled-tasks", response_model=models.ScheduledTaskInfo, status_code=201, summary="创建定时任务")
async def create_scheduled_task(
    task_data: models.ScheduledTaskCreate,
    current_user: models.User = Depends(security.get_current_user),
    scheduler: SchedulerManager = Depends(get_scheduler_manager)
):
    try:
        logger.info(f"[调试] 创建定时任务 - 接收到的 task_data: name={task_data.name}, jobType={task_data.jobType}, taskConfig={task_data.taskConfig}")
        new_task = await scheduler.add_task(task_data.name, task_data.jobType, task_data.cronExpression, task_data.isEnabled, task_data.taskConfig)
        return models.ScheduledTaskInfo.model_validate(new_task)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"创建定时任务失败: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="创建定时任务时发生内部错误")



@router.put("/scheduled-tasks/{taskId}", response_model=models.ScheduledTaskInfo, summary="更新定时任务")
async def update_scheduled_task(
    taskId: str,
    task_data: models.ScheduledTaskUpdate,
    current_user: models.User = Depends(security.get_current_user),
    scheduler: SchedulerManager = Depends(get_scheduler_manager)
):
    logger.info(f"[调试] 更新定时任务 '{taskId}' - 接收到的 task_data: name={task_data.name}, cronExpression={task_data.cronExpression}, isEnabled={task_data.isEnabled}, taskConfig={task_data.taskConfig}")
    updated_task = await scheduler.update_task(taskId, task_data.name, task_data.cronExpression, task_data.isEnabled, task_data.taskConfig)
    if not updated_task:
        raise HTTPException(status_code=404, detail="找不到指定的任务ID")
    return models.ScheduledTaskInfo.model_validate(updated_task)



@router.delete("/scheduled-tasks/{taskId}", status_code=204, summary="删除定时任务")
async def delete_scheduled_task(taskId: str, current_user: models.User = Depends(security.get_current_user), scheduler: SchedulerManager = Depends(get_scheduler_manager)):
    await scheduler.delete_task(taskId)



@router.post("/scheduled-tasks/{taskId}/run", status_code=202, summary="立即运行一次定时任务")
async def run_scheduled_task_now(taskId: str, current_user: models.User = Depends(security.get_current_user), scheduler: SchedulerManager = Depends(get_scheduler_manager)):
    try:
        await scheduler.run_task_now(taskId)
        return {"message": "任务已触发运行"}
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))



