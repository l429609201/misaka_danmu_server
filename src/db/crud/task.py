"""
任务相关的CRUD操作
包括定时任务、任务历史、Webhook任务等
"""

import logging
import re
import json
from datetime import datetime, timedelta
from typing import List, Optional, Dict, Any
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, update, delete, func, or_, String

from ..orm_models import ScheduledTask, TaskHistory, WebhookTask, TaskStateCache
from src.core.timezone import get_now
from .. import orm_models

logger = logging.getLogger(__name__)


# --- Scheduled Tasks ---

async def is_system_task(session: AsyncSession, task_id: str) -> bool:
    """检查是否为系统内置任务"""
    system_task_ids = ["system_token_reset"]
    return task_id in system_task_ids


async def get_scheduled_tasks(session: AsyncSession) -> List[Dict[str, Any]]:
    """获取所有定时任务"""
    stmt = select(
        ScheduledTask.taskId.label("taskId"),
        ScheduledTask.name.label("name"),
        ScheduledTask.jobType.label("jobType"),
        ScheduledTask.cronExpression.label("cronExpression"),
        ScheduledTask.isEnabled.label("isEnabled"),
        ScheduledTask.taskConfig.label("taskConfig"),
        ScheduledTask.lastRunAt.label("lastRunAt"),
        ScheduledTask.nextRunAt.label("nextRunAt")
    ).order_by(ScheduledTask.name)
    result = await session.execute(stmt)
    rows = []
    for row in result.mappings():
        d = dict(row)
        d['taskConfig'] = _parse_task_config(d.get('taskConfig'))
        rows.append(d)
    return rows


def _parse_task_config(raw) -> dict:
    """将数据库中的 taskConfig TEXT 字段解析为 dict"""
    if raw is None:
        return {}
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        try:
            return json.loads(raw)
        except (json.JSONDecodeError, ValueError):
            return {}
    return {}


async def get_scheduled_task(session: AsyncSession, task_id: str) -> Optional[Dict[str, Any]]:
    """获取单个定时任务"""
    stmt = select(
        ScheduledTask.taskId.label("taskId"),
        ScheduledTask.name.label("name"),
        ScheduledTask.jobType.label("jobType"),
        ScheduledTask.cronExpression.label("cronExpression"),
        ScheduledTask.isEnabled.label("isEnabled"),
        ScheduledTask.taskConfig.label("taskConfig"),
        ScheduledTask.lastRunAt.label("lastRunAt"),
        ScheduledTask.nextRunAt.label("nextRunAt")
    ).where(ScheduledTask.taskId == task_id)
    result = await session.execute(stmt)
    row = result.mappings().first()
    if not row:
        return None
    d = dict(row)
    d['taskConfig'] = _parse_task_config(d.get('taskConfig'))
    return d


async def check_scheduled_task_exists_by_type(session: AsyncSession, job_type: str) -> bool:
    """检查指定类型的定时任务是否存在"""
    stmt = select(ScheduledTask.taskId).where(ScheduledTask.jobType == job_type).limit(1)
    result = await session.execute(stmt)
    return result.scalar_one_or_none() is not None


async def get_scheduled_task_id_by_type(session: AsyncSession, job_type: str) -> Optional[str]:
    """获取指定类型的定时任务ID"""
    stmt = select(ScheduledTask.taskId).where(ScheduledTask.jobType == job_type).limit(1)
    result = await session.execute(stmt)
    return result.scalar_one_or_none()


async def create_scheduled_task(
    session: AsyncSession,
    task_id: str,
    name: str,
    job_type: str,
    cron: str,
    is_enabled: bool,
    task_config: dict = None
):
    """创建定时任务"""
    new_task = ScheduledTask(
        taskId=task_id,
        name=name,
        jobType=job_type,
        cronExpression=cron,
        isEnabled=is_enabled,
        taskConfig=json.dumps(task_config or {}, ensure_ascii=False)
    )
    session.add(new_task)
    await session.commit()


async def update_scheduled_task(
    session: AsyncSession,
    task_id: str,
    name: str,
    cron: str,
    is_enabled: bool,
    task_config: dict = None
) -> bool:
    """更新定时任务,但不允许修改系统内置任务的关键属性"""
    task = await session.get(ScheduledTask, task_id)
    if not task:
        return False

    # 系统任务只允许修改启用状态
    if await is_system_task(session, task_id):
        task.isEnabled = is_enabled
    else:
        task.name = name
        task.cronExpression = cron
        task.isEnabled = is_enabled
        serialized_config = json.dumps(task_config or {}, ensure_ascii=False)
        logger.info(f"[调试] CRUD update_scheduled_task '{task_id}' - task_config 入参: {task_config}, 序列化后: {serialized_config}")
        task.taskConfig = serialized_config

    await session.commit()
    return True


async def delete_scheduled_task(session: AsyncSession, task_id: str) -> bool:
    """删除定时任务,但不允许删除系统内置任务"""
    # 检查是否为系统任务
    if await is_system_task(session, task_id):
        raise ValueError("不允许删除系统内置任务。")

    task = await session.get(ScheduledTask, task_id)
    if not task:
        return False

    await session.delete(task)
    await session.commit()
    return True


async def update_scheduled_task_run_times(
    session: AsyncSession,
    task_id: str,
    last_run: Optional[datetime],
    next_run: Optional[datetime]
):
    """更新定时任务的运行时间"""
    values_to_update = {
        "lastRunAt": last_run.replace(tzinfo=None) if last_run else None,
        "nextRunAt": next_run.replace(tzinfo=None) if next_run else None
    }
    await session.execute(
        update(ScheduledTask).where(ScheduledTask.taskId == task_id).values(**values_to_update)
    )
    await session.commit()


async def get_last_run_result_for_scheduled_task(
    session: AsyncSession,
    scheduled_task_id: str
) -> Optional[Dict[str, Any]]:
    """获取指定定时任务的最近一次运行结果"""
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
        "finishedAt": task_run.finishedAt,
    }


# --- Task History ---

async def create_task_in_history(
    session: AsyncSession,
    task_id: str,
    title: str,
    status: str,
    description: str,
    scheduled_task_id: Optional[str] = None,
    unique_key: Optional[str] = None,
    queue_type: str = "download",
    task_type: Optional[str] = None,
    task_parameters: Optional[str] = None
):
    """在任务历史中创建新任务

    Args:
        task_type: 任务类型，用于重启后恢复任务（如 'generic_import', 'webhook_search' 等）
        task_parameters: JSON格式的任务参数，用于重启后重建协程工厂
    """
    now = get_now()
    new_task = TaskHistory(
        taskId=task_id,
        title=title,
        status=status,
        description=description,
        scheduledTaskId=scheduled_task_id,
        createdAt=now,  # type: ignore
        updatedAt=now,  # type: ignore
        uniqueKey=unique_key,
        queueType=queue_type,
        taskType=task_type,
        taskParameters=task_parameters
    )
    session.add(new_task)
    await session.commit()


async def update_task_progress_in_history(
    session: AsyncSession,
    task_id: str,
    status: str,
    progress: Optional[int],
    description: str
):
    """更新任务进度

    Args:
        progress: 进度值，如果为 None 则保持当前进度不变
    """
    # 构建更新值字典
    values = {"status": status, "description": description, "updatedAt": get_now()}

    # 只有当 progress 不为 None 时才更新进度字段
    if progress is not None:
        values["progress"] = progress

    await session.execute(
        update(TaskHistory)
        .where(TaskHistory.taskId == task_id)
        .values(**values)
    )
    await session.commit()


async def finalize_task_in_history(session: AsyncSession, task_id: str, status: str, description: str):
    """完成任务"""
    await session.execute(
        update(TaskHistory)
        .where(TaskHistory.taskId == task_id)
        .values(
            status=status,
            description=description,
            progress=100,
            finishedAt=get_now(),
            updatedAt=get_now()
        )
    )
    await session.commit()

    # 任务完成后,清理任务状态缓存
    await clear_task_state_cache(session, task_id)


async def update_task_status(session: AsyncSession, task_id: str, status: str):
    """更新任务状态"""
    await session.execute(
        update(TaskHistory)
        .where(TaskHistory.taskId == task_id)
        .values(status=status, updatedAt=get_now().replace(tzinfo=None))
    )
    await session.commit()


async def get_tasks_from_history(
    session: AsyncSession,
    search_term: Optional[str],
    status_filter: str,
    queue_type_filter: str,
    page: int,
    page_size: int
) -> Dict[str, Any]:
    """获取任务历史列表(分页)"""
    # 修正:显式选择需要的列
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
        base_stmt = base_stmt.where(TaskHistory.status == '成功')
    elif status_filter == 'failed':
        base_stmt = base_stmt.where(TaskHistory.status == '失败')

    if queue_type_filter != 'all':
        base_stmt = base_stmt.where(TaskHistory.queueType == queue_type_filter)

    count_stmt = select(func.count()).select_from(base_stmt.alias("count_subquery"))
    total_count = (await session.execute(count_stmt)).scalar_one()

    stmt = base_stmt.order_by(TaskHistory.createdAt.desc()).offset((page - 1) * page_size).limit(page_size)
    result = await session.execute(stmt)
    items = [dict(row) for row in result.mappings()]

    return {"total": total_count, "list": items}


async def get_task_details_from_history(session: AsyncSession, task_id: str) -> Optional[Dict[str, Any]]:
    """获取单个任务的详细信息"""
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


async def get_task_from_history_by_id(session: AsyncSession, task_id: str) -> Optional[Dict[str, Any]]:
    """通过ID获取任务"""
    task = await session.get(TaskHistory, task_id)
    if task:
        return {"taskId": task.taskId, "title": task.title, "status": task.status}
    return None


async def delete_task_from_history(session: AsyncSession, task_id: str) -> bool:
    """删除任务历史"""
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
        logger.error(f"删除任务失败: {task_id}, 错误: {e}")
        return False


async def force_delete_task_from_history(session: AsyncSession, task_id: str) -> bool:
    """强制删除任务,使用SQL直接删除,绕过ORM可能的锁定问题"""
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
            logger.warning(f"强制删除任务失败,任务不存在: {task_id}")
            return False
    except Exception as e:
        logger.error(f"强制删除任务失败: {task_id}, 错误: {e}")
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
            logger.info(f"强制标记任务为失败成功: {task_id}")
            return True
        else:
            logger.warning(f"强制标记任务为失败失败,任务不存在: {task_id}")
            return False
    except Exception as e:
        logger.error(f"强制标记任务为失败失败: {task_id}, 错误: {e}")
        return False


async def get_execution_task_id_from_scheduler_task(
    session: AsyncSession,
    scheduler_task_id: str
) -> tuple[Optional[str], Optional[str]]:
    """
    从一个调度任务的最终描述中,解析并返回其触发的执行任务ID和状态。

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

    # 如果调度任务本身失败了,直接返回
    if scheduler_status == "失败":
        return (None, "失败")

    # 尝试从描述中解析执行任务ID
    # 描述格式示例: "已触发执行任务: abc-123-def"
    if description:
        match = re.search(r"已触发执行任务:\s*([a-f0-9\-]+)", description)
        if match:
            execution_task_id = match.group(1)
            # 查询执行任务的状态
            exec_stmt = select(TaskHistory.status).where(TaskHistory.taskId == execution_task_id)
            exec_result = await session.execute(exec_stmt)
            exec_status = exec_result.scalar_one_or_none()
            return (execution_task_id, exec_status)

    return (None, None)


async def mark_interrupted_tasks_as_failed(session: AsyncSession) -> int:
    """将所有运行中的任务标记为失败(用于服务重启时)"""
    stmt = (
        update(TaskHistory)
        .where(TaskHistory.status.in_(['运行中', '已暂停']))
        .values(
            status='失败',
            description='因程序重启而中断',
            finishedAt=get_now(),
            updatedAt=get_now()
        )
    )
    result = await session.execute(stmt)
    await session.commit()
    return result.rowcount


async def find_recent_task_by_unique_key(
    session: AsyncSession,
    unique_key: str,
    within_hours: int
) -> Optional[TaskHistory]:
    """
    通过unique_key查找最近的任务
    查找当前活跃或在指定时间窗口内完成的任务
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


# --- Webhook Tasks ---

async def create_webhook_task(
    session: AsyncSession,
    task_title: str,
    unique_key: str,
    payload: Dict[str, Any],
    webhook_source: str,
    is_delayed: bool,
    delay: timedelta
):
    """创建一个新的待处理 Webhook 任务"""
    now = get_now()
    execute_time = now + delay if is_delayed else now

    try:
        # 将payload字典序列化为JSON字符串
        payload_json = json.dumps(payload, ensure_ascii=False)

        new_task = WebhookTask(
            receptionTime=now,
            executeTime=execute_time,
            taskTitle=task_title,
            uniqueKey=unique_key,
            payload=payload_json,
            webhookSource=webhook_source,
            status="pending"
        )
        session.add(new_task)
        await session.commit()
    except Exception as e:
        await session.rollback()
        logger.warning(f"检测到重复的 Webhook 请求 (unique_key: {unique_key}),已忽略。")


async def get_webhook_tasks(
    session: AsyncSession,
    page: int,
    page_size: int,
    search: Optional[str] = None
) -> Dict[str, Any]:
    """获取待处理的 Webhook 任务列表,支持分页"""
    base_stmt = select(WebhookTask)
    if search:
        base_stmt = base_stmt.where(WebhookTask.taskTitle.like(f"%{search}%"))

    count_stmt = select(func.count()).select_from(base_stmt.alias("count_subquery"))
    total = (await session.execute(count_stmt)).scalar_one()

    stmt = base_stmt.order_by(WebhookTask.receptionTime.desc()).offset((page - 1) * page_size).limit(page_size)
    result = await session.execute(stmt)
    return {"total": total, "list": result.scalars().all()}


async def delete_webhook_tasks(session: AsyncSession, task_ids: List[int]) -> int:
    """批量删除指定的 Webhook 任务"""
    if not task_ids:
        return 0
    stmt = delete(WebhookTask).where(WebhookTask.id.in_(task_ids))
    result = await session.execute(stmt)
    await session.commit()
    return result.rowcount


async def delete_all_webhook_tasks(session: AsyncSession) -> int:
    """清空所有 Webhook 任务"""
    stmt = delete(WebhookTask)
    result = await session.execute(stmt)
    await session.commit()
    return result.rowcount


async def get_due_webhook_tasks(session: AsyncSession) -> List[WebhookTask]:
    """获取所有已到执行时间的待处理任务"""
    now = get_now()
    stmt = select(WebhookTask).where(
        WebhookTask.status == "pending",
        WebhookTask.executeTime <= now
    )
    result = await session.execute(stmt)
    return result.scalars().all()


async def update_webhook_task_status(session: AsyncSession, task_id: int, status: str):
    """更新 Webhook 任务的状态"""
    await session.execute(
        update(WebhookTask).where(WebhookTask.id == task_id).values(status=status)
    )


# --- Task State Cache ---

async def save_task_state_cache(
    session: AsyncSession,
    task_id: str,
    task_type: str,
    task_parameters: str
):
    """保存任务状态到缓存表"""
    now = get_now()

    # 使用 merge 来处理插入或更新
    task_state = TaskStateCache(
        taskId=task_id,
        taskType=task_type,
        taskParameters=task_parameters,
        createdAt=now,
        updatedAt=now
    )

    await session.merge(task_state)
    await session.commit()


async def get_task_state_cache(session: AsyncSession, task_id: str) -> Optional[Dict[str, Any]]:
    """获取任务状态缓存"""
    result = await session.execute(
        select(TaskStateCache).where(TaskStateCache.taskId == task_id)
    )
    task_state = result.scalar_one_or_none()

    if task_state:
        return {
            "taskId": task_state.taskId,
            "taskType": task_state.taskType,
            "taskParameters": task_state.taskParameters,
            "createdAt": task_state.createdAt,
            "updatedAt": task_state.updatedAt
        }
    return None


async def clear_task_state_cache(session: AsyncSession, task_id: str):
    """清理任务状态缓存"""
    await session.execute(
        delete(TaskStateCache).where(TaskStateCache.taskId == task_id)
    )
    await session.commit()


async def get_all_running_task_states(session: AsyncSession) -> List[Dict[str, Any]]:
    """获取所有正在运行的任务状态缓存,用于服务重启后的任务恢复"""
    # 使用CAST强制字符集一致,解决字符集冲突问题
    result = await session.execute(
        select(TaskStateCache, TaskHistory)
        .join(TaskHistory,
              func.cast(TaskStateCache.taskId, String) ==
              func.cast(TaskHistory.taskId, String))
        .where(TaskHistory.status == "运行中")
    )

    task_states = []
    for task_state, task_history in result.all():
        task_states.append({
            "taskId": task_state.taskId,
            "taskType": task_state.taskType,
            "taskParameters": task_state.taskParameters,
            "createdAt": task_state.createdAt,
            "updatedAt": task_state.updatedAt,
            "historyStatus": task_history.status,
            "taskTitle": task_history.title,
            "uniqueKey": task_history.uniqueKey,
            "queueType": task_history.queueType
        })

    return task_states


async def get_pending_recoverable_tasks(session: AsyncSession) -> List[Dict[str, Any]]:
    """获取所有排队中且可恢复的任务（有taskType和taskParameters的任务）

    用于服务重启后恢复排队中的任务。只有同时具备 taskType 和 taskParameters 的任务才能被恢复。
    """
    result = await session.execute(
        select(TaskHistory)
        .where(
            TaskHistory.status == "排队中",
            TaskHistory.taskType.isnot(None),
            TaskHistory.taskParameters.isnot(None)
        )
        .order_by(TaskHistory.createdAt.asc())  # 按创建时间排序，先提交的先恢复
    )

    pending_tasks = []
    for task_history in result.scalars().all():
        pending_tasks.append({
            "taskId": task_history.taskId,
            "taskType": task_history.taskType,
            "taskParameters": task_history.taskParameters,
            "taskTitle": task_history.title,
            "uniqueKey": task_history.uniqueKey,
            "queueType": task_history.queueType,
            "createdAt": task_history.createdAt
        })

    return pending_tasks

