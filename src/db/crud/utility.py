"""
Utility相关的CRUD操作
"""

import re
import logging
from typing import Optional, Dict, Any, List
from pathlib import Path
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, distinct, case, or_, and_, update, delete, String
from sqlalchemy.orm import selectinload, DeclarativeBase
from sqlalchemy.sql.elements import ColumnElement
from datetime import datetime, timedelta

from ..orm_models import Anime, Episode, TitleRecognition, OauthState, TaskHistory, TaskStateCache, WebhookTask, ScheduledTask
from .. import models, orm_models
from src.core.timezone import get_now
from .task import clear_task_state_cache

logger = logging.getLogger(__name__)


def _is_docker_environment():
    """检测是否在Docker容器中运行"""
    import os
    # 方法1: 检查 /.dockerenv 文件（Docker标准做法）
    if Path("/.dockerenv").exists():
        return True
    # 方法2: 检查环境变量
    if os.getenv("DOCKER_CONTAINER") == "true" or os.getenv("IN_DOCKER") == "true":
        return True
    # 方法3: 检查当前工作目录是否为 /app
    if Path.cwd() == Path("/app"):
        return True
    return False


def _get_base_dir():
    """获取基础目录，根据运行环境自动调整"""
    if _is_docker_environment():
        return Path("/app")
    else:
        # 源码运行环境，使用当前工作目录
        return Path(".")

BASE_DIR = _get_base_dir()
DANMAKU_BASE_DIR = BASE_DIR / "config/danmaku"


async def prune_logs(session: AsyncSession, model: type[DeclarativeBase], date_column: ColumnElement, cutoff_date: datetime) -> int:
    """通用函数，用于删除指定模型中早于截止日期的记录。"""
    stmt = delete(model).where(date_column < cutoff_date)
    result = await session.execute(stmt)
    # 提交由调用方（任务）处理
    return result.rowcount


async def clear_expired_oauth_states(session: AsyncSession):
    await session.execute(delete(OauthState).where(OauthState.expiresAt <= get_now()))
    await session.commit()


async def find_recent_task_by_unique_key(session: AsyncSession, unique_key: str, within_hours: int) -> Optional[TaskHistory]:
    """
    Finds a task by its unique_key that is either currently active 
    or was completed within the specified time window.
    """
    if not unique_key:
        return None

    cutoff_time = get_now() - timedelta(hours=within_hours)
    
    stmt = (
        select(TaskHistory)
        .where(
            TaskHistory.uniqueKey == unique_key,
            or_(
                TaskHistory.status.in_(['排队中', '运行中', '已暂停']),
                TaskHistory.finishedAt >= cutoff_time
            )
        )
        .order_by(TaskHistory.createdAt.desc())
        .limit(1)
    )
    result = await session.execute(stmt)
    return result.scalar_one_or_none()


async def get_all_running_task_states(session: AsyncSession) -> List[Dict[str, Any]]:
    """获取所有正在运行的任务状态缓存，用于服务重启后的任务恢复"""
    # 使用CAST强制字符集一致，解决字符集冲突问题
    result = await session.execute(
        select(orm_models.TaskStateCache, orm_models.TaskHistory)
        .join(orm_models.TaskHistory,
              func.cast(orm_models.TaskStateCache.taskId, String) ==
              func.cast(orm_models.TaskHistory.taskId, String))
        .where(orm_models.TaskHistory.status == "运行中")
    )

    task_states = []
    for task_state, task_history in result.all():
        task_states.append({
            "taskId": task_state.taskId,
            "taskType": task_state.taskType,
            "taskParameters": task_state.taskParameters,
            "createdAt": task_state.createdAt,
            "updatedAt": task_state.updatedAt,
            "taskTitle": task_history.title,
            "taskProgress": task_history.progress,
            "taskDescription": task_history.description
        })

    return task_states


async def mark_interrupted_tasks_as_failed(session: AsyncSession) -> int:
    stmt = (
        update(TaskHistory)
        .where(TaskHistory.status.in_(['运行中', '已暂停']))
        .values(status='失败', description='因程序重启而中断', finishedAt=get_now(), updatedAt=get_now()) # finishedAt and updatedAt are explicitly set here
    )
    result = await session.execute(stmt)
    await session.commit()
    return result.rowcount

# --- Webhook Tasks ---


async def get_due_webhook_tasks(session: AsyncSession) -> List[WebhookTask]:
    """获取所有已到执行时间的待处理任务。"""
    now = get_now()
    stmt = select(WebhookTask).where(WebhookTask.status == "pending", WebhookTask.executeTime <= now)
    result = await session.execute(stmt)
    return result.scalars().all()


async def delete_webhook_tasks(session: AsyncSession, task_ids: List[int]) -> int:
    """批量删除指定的 Webhook 任务。"""
    if not task_ids:
        return 0
    stmt = delete(WebhookTask).where(WebhookTask.id.in_(task_ids))
    result = await session.execute(stmt)
    await session.commit()
    return result.rowcount


async def get_last_run_result_for_scheduled_task(session: AsyncSession, scheduled_task_id: str) -> Optional[Dict[str, Any]]:
    """获取指定定时任务的最近一次运行结果。"""
    stmt = (
        select(TaskHistory)
        .where(TaskHistory.scheduledTaskId == scheduled_task_id)
        .order_by(TaskHistory.createdAt.desc())
        .limit(1)
    )
    result = await session.execute(stmt)
    task_run = result.scalar_one_or_none()
    if not task_run:
        return None
    
    # 返回一个与 models.TaskInfo 兼容的字典
    return {
        "taskId": task_run.taskId,
        "title": task_run.title,
        "status": task_run.status,
        "progress": task_run.progress,
        "description": task_run.description,
        "createdAt": task_run.createdAt,
        "updatedAt": task_run.updatedAt,
        "finishedAt": task_run.finishedAt,
    }

# --- External API Logging ---


async def get_execution_task_id_from_scheduler_task(session: AsyncSession, scheduler_task_id: str) -> tuple[Optional[str], Optional[str]]:
    """
    从一个调度任务的最终描述中，解析并返回其触发的执行任务ID和状态。

    Returns:
        (execution_task_id, status): 执行任务ID和状态,如果未找到则返回(None, None)
    """
    # 先查询调度任务本身的状态
    stmt = select(TaskHistory.description, TaskHistory.status).where(
        TaskHistory.taskId == scheduler_task_id
    )
    result = await session.execute(stmt)
    row = result.one_or_none()

    if not row:
        return (None, None)

    description, scheduler_status = row

    # 如果调度任务失败,直接返回失败状态
    if scheduler_status == '失败':
        return (None, '失败')

    # 如果调度任务已取消,直接返回已取消状态
    if scheduler_status == '已取消':
        return (None, '已取消')

    # 如果调度任务还在运行中或等待中,返回对应状态
    if scheduler_status in ['运行中', '等待中', '已暂停']:
        return (None, scheduler_status)

    # 如果调度任务已完成,尝试解析执行任务ID
    if scheduler_status == '已完成' and description:
        match = re.search(r'执行任务ID:\s*([a-f0-9\-]+)', description)
        if match:
            execution_task_id = match.group(1)

            # 查询执行任务的状态
            exec_stmt = select(TaskHistory.status).where(
                TaskHistory.taskId == execution_task_id
            )
            exec_result = await session.execute(exec_stmt)
            exec_status = exec_result.scalar_one_or_none()

            return (execution_task_id, exec_status if exec_status else '未知')

    return (None, None)


async def force_delete_task_from_history(session: AsyncSession, task_id: str) -> bool:
    """强制删除任务，使用SQL直接删除，绕过ORM可能的锁定问题"""
    try:
        logger.info(f"强制删除任务: {task_id}")

        # 使用SQL直接删除
        stmt = delete(TaskHistory).where(TaskHistory.taskId == task_id)
        result = await session.execute(stmt)
        await session.commit()

        deleted_count = result.rowcount
        if deleted_count > 0:
            logger.info(f"强制删除任务成功: {task_id}, 删除行数: {deleted_count}")
            return True
        else:
            logger.warning(f"强制删除任务失败，任务不存在: {task_id}")
            return False
    except Exception as e:
        logger.error(f"强制删除任务 {task_id} 失败: {e}", exc_info=True)
        await session.rollback()
        return False


async def force_fail_task(session: AsyncSession, task_id: str) -> bool:
    """强制将任务标记为失败状态"""
    try:
        logger.info(f"强制标记任务为失败: {task_id}")

        # 使用SQL直接更新任务状态
        stmt = update(TaskHistory).where(TaskHistory.taskId == task_id).values(
            status="失败",
            finishedAt=get_now(),
            updatedAt=get_now(),
            description="任务被强制中止"
        )
        result = await session.execute(stmt)
        await session.commit()

        updated_count = result.rowcount
        if updated_count > 0:
            logger.info(f"强制标记任务失败成功: {task_id}, 更新行数: {updated_count}")
            return True
        else:
            logger.warning(f"强制标记任务失败，任务不存在: {task_id}")
            return False
    except Exception as e:
        logger.error(f"强制标记任务失败 {task_id} 失败: {e}", exc_info=True)
        await session.rollback()
        return False


async def get_task_from_history_by_id(session: AsyncSession, task_id: str) -> Optional[Dict[str, Any]]:
    task = await session.get(TaskHistory, task_id)
    if task:
        return {"taskId": task.taskId, "title": task.title, "status": task.status}
    return None


async def delete_task_from_history(session: AsyncSession, task_id: str) -> bool:
    try:
        # 先查询任务是否存在
        task = await session.get(TaskHistory, task_id)
        if not task:
            logger.warning(f"尝试删除不存在的任务: {task_id}")
            return False

        logger.info(f"正在删除任务: {task_id}, 状态: {task.status}")

        # 删除任务
        await session.delete(task)
        await session.commit()

        logger.info(f"成功删除任务: {task_id}")
        return True
    except Exception as e:
        logger.error(f"删除任务 {task_id} 失败: {e}", exc_info=True)
        await session.rollback()
        return False


async def get_task_details_from_history(session: AsyncSession, task_id: str) -> Optional[Dict[str, Any]]:
    """获取单个任务的详细信息。"""
    task = await session.get(TaskHistory, task_id)
    if task:
        return {
            "taskId": task.taskId,
            "title": task.title,
            "status": task.status,
            "progress": task.progress,
            "description": task.description,
            "createdAt": task.createdAt,
        }
    return None


async def get_tasks_from_history(session: AsyncSession, search_term: Optional[str], status_filter: str, queue_type_filter: str, page: int, page_size: int) -> Dict[str, Any]:
    # 修正：显式选择需要的列，以避免在旧的数据库模式上查询不存在的列（如 scheduled_task_id）
    base_stmt = select(
        TaskHistory.taskId,
        TaskHistory.title,
        TaskHistory.status,
        TaskHistory.progress,
        TaskHistory.description,
        TaskHistory.createdAt,
        TaskHistory.scheduledTaskId,
        TaskHistory.queueType
    )

    if search_term:
        base_stmt = base_stmt.where(TaskHistory.title.like(f"%{search_term}%"))
    if status_filter == 'in_progress':
        base_stmt = base_stmt.where(TaskHistory.status.in_(['排队中', '运行中', '已暂停']))
    elif status_filter == 'completed':
        base_stmt = base_stmt.where(TaskHistory.status == '已完成')

    # 添加队列类型筛选
    if queue_type_filter and queue_type_filter != 'all':
        base_stmt = base_stmt.where(TaskHistory.queueType == queue_type_filter)

    count_stmt = select(func.count()).select_from(base_stmt.alias("count_subquery"))
    total_count = (await session.execute(count_stmt)).scalar_one()

    offset = (page - 1) * page_size
    data_stmt = base_stmt.order_by(TaskHistory.createdAt.desc()).offset(offset).limit(page_size)

    result = await session.execute(data_stmt)
    items = [
        {
            "taskId": row.taskId,
            "title": row.title,
            "status": row.status,
            "progress": row.progress,
            "description": row.description,
            "createdAt": row.createdAt,
            "isSystemTask": row.scheduledTaskId == "system_token_reset",
            "queueType": row.queueType or "download"  # 如果为NULL则默认为"download"
        }
        for row in result.mappings()
    ]
    return {"total": total_count, "list": items}


async def finalize_task_in_history(session: AsyncSession, task_id: str, status: str, description: str):
    await session.execute(
        update(TaskHistory)
        .where(TaskHistory.taskId == task_id)
        .values(status=status, description=description, progress=100, finishedAt=get_now(), updatedAt=get_now())
    )
    await session.commit()

    # 任务完成后，清理任务状态缓存
    await clear_task_state_cache(session, task_id)


async def update_task_progress_in_history(session: AsyncSession, task_id: str, status: str, progress: Optional[int], description: str):
    values = {"status": status, "description": description, "updatedAt": get_now()}
    if progress is not None:
        values["progress"] = progress
    await session.execute(
        update(TaskHistory)
        .where(TaskHistory.taskId == task_id)
        .values(**values)
    )
    await session.commit()


async def update_scheduled_task_run_times(session: AsyncSession, task_id: str, last_run: Optional[datetime], next_run: Optional[datetime]):
    values_to_update = {
        "lastRunAt": last_run.replace(tzinfo=None) if last_run else None,
        "nextRunAt": next_run.replace(tzinfo=None) if next_run else None
    }
    await session.execute(update(ScheduledTask).where(ScheduledTask.taskId == task_id).values(**values_to_update))
    await session.commit()

# --- Task History ---
# 注意: get_scheduled_task, get_scheduled_task_id_by_type, check_scheduled_task_exists_by_type
# 已统一迁移至 task.py，此处不再重复定义

