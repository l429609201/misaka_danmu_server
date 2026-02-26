"""
Task相关的API端点
"""
import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from src import security
from src.db import crud, models, get_db_session
from src.services import TaskManager, TaskStatus

from src.api.dependencies import get_task_manager

logger = logging.getLogger(__name__)

router = APIRouter()

@router.get("/tasks", response_model=models.PaginatedTasksResponse, summary="获取所有后台任务的状态")
async def get_all_tasks(
    current_user: models.User = Depends(security.get_current_user),
    session: AsyncSession = Depends(get_db_session),
    search: Optional[str] = Query(None, description="按标题搜索"),
    status: Optional[str] = Query("all", description="按状态过滤: all, in_progress, completed"),
    queueType: Optional[str] = Query("all", description="按队列类型过滤: all, download, management, fallback"),
    page: int = Query(1, ge=1, description="页码"),
    pageSize: int = Query(20, ge=1, description="每页数量")
):
    """获取后台任务的列表和状态，支持搜索和过滤。"""
    paginated_result = await crud.get_tasks_from_history(session, search, status, queueType, page, pageSize)
    return models.PaginatedTasksResponse(
        total=paginated_result["total"],
        list=[models.TaskInfo.model_validate(t) for t in paginated_result["list"]]
    )




@router.post("/tasks/{task_id}/pause", status_code=status.HTTP_204_NO_CONTENT, summary="暂停一个正在运行的任务")
async def pause_task_endpoint(
    task_id: str,
    current_user: models.User = Depends(security.get_current_user),
    task_manager: TaskManager = Depends(get_task_manager)
):
    """暂停一个正在运行的任务。"""
    paused = await task_manager.pause_task(task_id)
    if not paused:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="任务未找到或无法暂停。")
    return




@router.post("/tasks/{task_id}/resume", status_code=status.HTTP_204_NO_CONTENT, summary="恢复一个已暂停的任务")
async def resume_task_endpoint(
    task_id: str,
    current_user: models.User = Depends(security.get_current_user),
    task_manager: TaskManager = Depends(get_task_manager)
):
    """恢复一个已暂停的任务。"""
    resumed = await task_manager.resume_task(task_id)
    if not resumed:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="任务未找到或无法恢复。")
    return




@router.post("/tasks/{task_id}/abort", status_code=status.HTTP_204_NO_CONTENT, summary="中止一个正在运行的任务")
async def abort_task_endpoint(
    task_id: str,
    request: dict,
    current_user: models.User = Depends(security.get_current_user),
    task_manager: TaskManager = Depends(get_task_manager),
    session: AsyncSession = Depends(get_db_session)
):
    """
    中止一个正在运行或暂停的任务。
    - force=false: 正常中止，向任务发送取消信号
    - force=true: 强制中止，直接将任务标记为失败状态
    """
    force = request.get('force', False)
    if force:
        # 强制中止：先尝试取消协程，再将任务标记为失败
        task = await crud.get_task_from_history_by_id(session, task_id)
        if not task:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="任务不存在")

        # 先尝试取消正在运行的协程（如果任务正在运行）
        # 这是关键：确保 worker 不会继续等待被终止的任务
        await task_manager.abort_current_task(task_id)

        # 然后更新数据库状态为失败
        success = await crud.force_fail_task(session, task_id)
        if not success:
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="强制中止任务失败")
    else:
        # 正常中止
        aborted = await task_manager.abort_current_task(task_id)
        if not aborted:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="任务未找到或无法中止。")
    return



@router.post("/tasks/{task_id}/retry", summary="重试一个失败的任务")
async def retry_task_endpoint(
    task_id: str,
    current_user: models.User = Depends(security.get_current_user),
    task_manager: TaskManager = Depends(get_task_manager)
):
    """重试一个失败的任务。从历史记录中读取任务参数，重建并重新提交到队列。"""
    new_task_id = await task_manager.retry_task(task_id)
    return {"message": "任务已重新提交", "newTaskId": new_task_id}



@router.delete("/tasks/{task_id}", status_code=status.HTTP_204_NO_CONTENT, summary="删除一个历史任务")
async def delete_task_from_history_endpoint(
    task_id: str,
    force: bool = False,
    current_user: models.User = Depends(security.get_current_user),
    session: AsyncSession = Depends(get_db_session),
    task_manager: TaskManager = Depends(get_task_manager)
):
    """从历史记录中删除一个任务。如果任务正在运行或暂停，会先尝试中止它。force=true时强制删除。"""
    task = await crud.get_task_from_history_by_id(session, task_id)
    if not task:
        # 如果任务不存在，直接返回成功，因为最终状态是一致的
        return

    task_status = task['status']

    if force:
        # 强制删除模式：使用SQL直接删除，绕过可能的锁定问题
        logger.info(f"用户 '{current_user.username}' 强制删除任务 {task_id}，状态: {task_status}")
        deleted = await crud.force_delete_task_from_history(session, task_id)
        if not deleted:
            logger.warning(f"强制删除失败，任务 {task_id} 可能已不存在于历史记录中。")
        return

    # 正常删除模式
    if task_status == TaskStatus.PENDING:
        await task_manager.cancel_pending_task(task_id)
    elif task_status in [TaskStatus.RUNNING, TaskStatus.PAUSED]:
        aborted = await task_manager.abort_current_task(task_id)
        if not aborted:
            # 这可能是一个竞态条件：在我们检查和中止之间，任务可能已经完成。
            # 重新检查数据库中的状态以确认。
            task_after_check = await crud.get_task_from_history_by_id(session, task_id)
            if task_after_check and task_after_check['status'] in [TaskStatus.RUNNING, TaskStatus.PAUSED]:
                # 如果它仍然在运行/暂停，说明中止失败，可能因为它不是当前任务。
                raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="中止任务失败，可能它不是当前正在执行的任务。")
            logger.info(f"任务 {task_id} 在中止前已完成，将直接删除历史记录。")

    deleted = await crud.delete_task_from_history(session, task_id)
    if not deleted:
        # 这不是一个严重错误，可能意味着任务在处理过程中已被删除。
        logger.info(f"在尝试删除时，任务 {task_id} 已不存在于历史记录中。")
        return
    logger.info(f"用户 '{current_user.username}' 删除了任务 ID: {task_id} (原状态: {task_status})。")
    return



