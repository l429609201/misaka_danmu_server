"""
TaskQueryRepository - 任务复杂查询层

职责：处理任务历史筛选、调度任务查询等读操作。
基础 CRUD 操作请使用 TaskRepository。
"""

import logging
import re
from typing import Optional, List, Dict, Any
from datetime import datetime, timedelta
from sqlalchemy import select, func, and_, or_, desc, update, delete, cast, String
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.timezone import get_now
from ..orm_models import TaskHistory, TaskStateCache

logger = logging.getLogger(__name__)


class TaskQueryRepository:
    """任务复杂查询 Repository"""

    def __init__(self, session: AsyncSession):
        """
        初始化查询 Repository

        Args:
            session: SQLAlchemy AsyncSession 实例
        """
        self._session = session

    async def get_command_task_status(
        self, status_filter: str, queue_filter: Optional[str], limit: int = 5,
    ) -> Dict[str, Any]:
        """按播放器指令口径筛选最新任务及匹配总数。"""
        statuses = {
            "RUNNING": ["排队中", "运行中", "已暂停"],
            "COMPLETED": ["已完成"],
            "FAILED": ["失败"],
            "PENDING": ["排队中"],
            "PAUSED": ["已暂停"],
        }
        conditions = []
        if status_filter in statuses:
            conditions.append(TaskHistory.status.in_(statuses[status_filter]))
        if queue_filter:
            conditions.append(TaskHistory.queueType == queue_filter)
        stmt = (
            select(TaskHistory.taskId, TaskHistory.title, TaskHistory.status,
                   TaskHistory.progress, TaskHistory.description, TaskHistory.createdAt,
                   TaskHistory.updatedAt, TaskHistory.queueType)
            .where(*conditions).order_by(TaskHistory.updatedAt.desc()).limit(limit)
        )
        tasks = [dict(row) for row in (await self._session.execute(stmt)).mappings().all()]
        if not tasks:
            return {"tasks": [], "total": 0}
        total_stmt = select(func.count()).select_from(TaskHistory).where(*conditions)
        total = (await self._session.execute(total_stmt)).scalar_one()
        return {"tasks": tasks, "total": total}

    async def get_paginated_tasks(
        self,
        search: Optional[str] = None,
        status_filter: Optional[str] = None,
        queue_type: Optional[str] = None,
        page: int = 1,
        page_size: int = 20,
        sort_by: str = "createdAt",
    ) -> Dict[str, Any]:
        """分页读取任务；播报按更新时间查询，普通列表保持创建时间顺序。"""
        # 历史表只存 title；系统任务标识由响应模型沿用默认值。
        conditions = []
        if search:
            conditions.append(TaskHistory.title.ilike(f'%{search}%'))
        status_groups = {
            'in_progress': ['排队中', '运行中', '已暂停'],
            'completed': ['已完成'],
            'failed': ['失败', '超时', '已取消'],
            'paused': ['已暂停'],
            'pending': ['排队中'],
            'running': ['运行中'],
        }
        if status_filter in status_groups:
            conditions.append(TaskHistory.status.in_(status_groups[status_filter]))
        elif status_filter and status_filter != 'all':
            conditions.append(TaskHistory.status == status_filter)
        if queue_type and queue_type != 'all':
            conditions.append(TaskHistory.queueType == queue_type)

        # 统计与分页共用过滤条件，不用当前页长度冒充总数，也不在内存中截取全表。
        count_stmt = select(func.count()).select_from(TaskHistory).where(*conditions)
        total = (await self._session.execute(count_stmt)).scalar_one()
        # 只允许固定排序字段；完成时间变化必须能回到播报查询的第一页。
        order_field = TaskHistory.updatedAt if sort_by == "updatedAt" else TaskHistory.createdAt
        stmt = (
            select(TaskHistory).where(*conditions)
            .order_by(desc(order_field), desc(TaskHistory.taskId))
            .offset((page - 1) * page_size).limit(page_size)
        )
        tasks = (await self._session.execute(stmt)).scalars().all()
        return {
            "total": total,
            "list": [
                {
                    "taskId": task.taskId,
                    "title": task.title,
                    "status": task.status,
                    "progress": task.progress or 0,
                    "description": task.description or "",
                    "createdAt": task.createdAt,
                    "updatedAt": task.updatedAt,
                    "queueType": task.queueType or "download",
                    "taskType": task.taskType,
                    "uniqueKey": task.uniqueKey,
                }
                for task in tasks
            ],
        }


    async def get_tasks_from_history(
        self,
        limit: int = 100,
        status_filter: Optional[str] = None,
        search: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """
        获取任务历史列表（带筛选）

        替代 crud.get_tasks_from_history / 旧 TaskRepository.get_recent_tasks

        Args:
            limit: 返回数量限制
            status_filter: 状态筛选 ('in_progress', 'completed', 'failed', None=全部)
            search: 标题搜索关键词

        Returns:
            任务信息列表
        """
        # 与分页入口共用状态映射，避免 UI 与通知菜单展示不一致。
        result = await self.get_paginated_tasks(
            search=search, status_filter=status_filter, page_size=limit,
        )
        return result["list"]

    async def get_task_details_from_history(self, task_id: str) -> Optional[Dict[str, Any]]:
        """
        获取单个任务的详细信息

        替代 crud.get_task_details_from_history

        Args:
            task_id: 任务ID

        Returns:
            任务详情字典或 None
        """
        stmt = select(TaskHistory).where(TaskHistory.taskId == task_id)
        result = await self._session.execute(stmt)
        task = result.scalar_one_or_none()

        if not task:
            return None

        return {
            "taskId": task.taskId,
            "title": task.title,
            "taskType": task.taskType,
            "status": task.status,
            "progress": task.progress or 0,
            "description": task.description or "",
            "createdAt": task.createdAt.isoformat() if task.createdAt else None,
            "updatedAt": task.updatedAt,
            "queueType": task.queueType or "download",
            "uniqueKey": task.uniqueKey,
        }

    async def get_task_by_id(self, task_id: str) -> Optional[Dict[str, Any]]:
        """
        通过 ID 获取任务基本信息（简化版本）

        替代 crud.get_task_from_history_by_id

        Args:
            task_id: 任务ID

        Returns:
            任务基本信息字典（包含 taskId, title, status）或 None
        """
        task = await self._session.get(TaskHistory, task_id)
        if task:
            return {"taskId": task.taskId, "title": task.title, "status": task.status}
        return None

    async def count_running_tasks(self) -> int:
        """
        统计正在运行的任务数量

        Returns:
            运行中的任务数
        """
        stmt = (
            select(func.count())
            .select_from(TaskHistory)
            .where(TaskHistory.status.in_(['运行中', '排队中', '等待中']))
        )
        result = await self._session.execute(stmt)
        return result.scalar_one()

    async def get_recent_failed_tasks(self, hours: int = 24, limit: int = 50) -> List[Dict[str, Any]]:
        """
        获取最近失败的任务

        Args:
            hours: 最近多少小时
            limit: 返回数量限制

        Returns:
            失败任务列表
        """

        since = get_now() - timedelta(hours=hours)

        stmt = (
            select(TaskHistory)
            .where(
                TaskHistory.status == '失败',
                TaskHistory.createdAt >= since
            )
            .order_by(desc(TaskHistory.createdAt))
            .limit(limit)
        )
        result = await self._session.execute(stmt)
        tasks = result.scalars().all()

        return [
            {
                "taskId": t.taskId,
                "title": t.title,
                "description": t.description,
                "createdAt": t.createdAt.isoformat() if t.createdAt else None,
            }
            for t in tasks
        ]

    async def create_task_in_history(
        self,
        task_id: str,
        title: str,
        status: str,
        description: str,
        task_type: Optional[str] = None,
        queue_type: str = "download",
        scheduled_task_id: Optional[str] = None,
        unique_key: Optional[str] = None,
        task_parameters: Optional[str] = None,
        parent_task_id: Optional[str] = None
    ) -> None:
        """
        创建任务历史记录

        替代 crud.create_task_in_history

        Args:
            task_id: 任务ID
            title: 任务标题
            status: 任务状态
            description: 任务描述
            task_type: 任务类型，用于重启后恢复任务
            queue_type: 队列类型
            scheduled_task_id: 关联的定时任务ID
            unique_key: 唯一键（用于去重）
            task_parameters: JSON格式的任务参数，用于重启后重建协程工厂
            parent_task_id: 父任务ID（如搜索任务ID），用于记录任务派发关系
        """

        new_task = TaskHistory(
            taskId=task_id,
            title=title,
            status=status,
            description=description,
            taskType=task_type,
            queueType=queue_type,
            scheduledTaskId=scheduled_task_id,
            uniqueKey=unique_key,
            taskParameters=task_parameters,
            parentTaskId=parent_task_id,
            createdAt=get_now(),
            updatedAt=get_now(),
        )
        self._session.add(new_task)
        await self._session.flush()  # Repository 只 flush，commit 由调用方负责

    async def update_task_progress_in_history(
        self,
        task_id: str,
        status: str,
        progress: Optional[int],
        description: str
    ) -> None:
        """
        更新任务进度

        替代 crud.update_task_progress_in_history

        Args:
            task_id: 任务ID
            status: 新状态
            progress: 进度百分比（0-100），None 表示保留已有进度
            description: 状态描述
        """
        # 暂停和恢复只更新状态时，不能把非空进度列覆盖为 NULL。
        stmt = (
            update(TaskHistory)
            .where(TaskHistory.taskId == task_id)
            .values(
                status=status,
                progress=progress if progress is not None else TaskHistory.progress,
                description=description,
                updatedAt=get_now(),
            )
        )
        await self._session.execute(stmt)
        await self._session.flush()  # Repository 只 flush，commit 由调用方负责

    async def finalize_task_in_history(
        self,
        task_id: str,
        status: str,
        description: str
    ) -> None:
        """
        完成任务（最终状态）

        替代 crud.finalize_task_in_history

        Args:
            task_id: 任务ID
            status: 最终状态
            description: 完成描述
        """

        stmt = (
            update(TaskHistory)
            .where(TaskHistory.taskId == task_id)
            .values(
                status=status,
                # 失败或取消时保留实际进度，避免向非空列写入 NULL。
                progress=100 if status == "已完成" else TaskHistory.progress,
                description=description,
                updatedAt=get_now(),
            )
        )
        await self._session.execute(stmt)
        await self._session.flush()  # Repository 只 flush，commit 由调用方负责

        # 终态写入与缓存清理共用调用方事务，使用 DELETE 语句而非 SQL 函数。
        await self._session.execute(
            delete(TaskStateCache).where(TaskStateCache.taskId == task_id)
        )
        await self._session.flush()  # Repository 只 flush，commit 由调用方负责

    async def update_task_status(self, task_id: str, status: str) -> None:
        """
        更新任务状态（不更新进度和描述）

        替代 crud.update_task_status

        Args:
            task_id: 任务ID
            status: 新状态
        """

        stmt = (
            update(TaskHistory)
            .where(TaskHistory.taskId == task_id)
            .values(status=status, updatedAt=get_now().replace(tzinfo=None))
        )
        await self._session.execute(stmt)
        await self._session.flush()  # Repository 只 flush，commit 由调用方负责

    async def update_task_queue_type(self, task_id: str, queue_type: str) -> None:
        """更新恢复任务的队列归属，使历史展示与实际调度保持一致。"""
        await self._session.execute(
            update(TaskHistory)
            .where(TaskHistory.taskId == task_id)
            .values(queueType=queue_type, updatedAt=get_now())
        )
        await self._session.flush()

    async def get_all_running_task_states(self) -> List[Dict[str, Any]]:
        """获取运行中或已暂停任务的缓存及历史信息，供重启恢复使用。"""
        # 以历史表为主，避免无缓存的手动暂停任务在重启后丢失。
        stmt = (
            select(
                TaskHistory.taskId.label("taskId"),
                func.coalesce(TaskStateCache.taskType, TaskHistory.taskType).label("taskType"),
                func.coalesce(TaskStateCache.taskParameters, TaskHistory.taskParameters).label("taskParameters"),
                TaskHistory.title.label("title"),
                TaskHistory.uniqueKey.label("uniqueKey"),
                TaskHistory.queueType.label("queueType"),
                TaskHistory.status.label("historyStatus"),
                TaskHistory.description.label("description"),
                TaskHistory.scheduledTaskId.label("scheduledTaskId"),
            )
            .select_from(TaskHistory)
            .outerjoin(TaskStateCache, TaskStateCache.taskId == TaskHistory.taskId)
            .where(TaskHistory.status.in_(['运行中', '已暂停']))
            .order_by(TaskHistory.createdAt)
        )
        result = await self._session.execute(stmt)
        return [dict(row) for row in result.mappings().all()]

    async def get_pending_recoverable_tasks(self) -> List[Dict[str, Any]]:
        """
        获取所有排队中且可恢复的任务（有 taskType 和 taskParameters 的任务）

        替代 crud.get_pending_recoverable_tasks

        Returns:
            可恢复的任务列表
        """
        stmt = (
            select(TaskHistory)
            .where(
                TaskHistory.status.in_(['排队中', '等待中']),
                TaskHistory.taskType.isnot(None),
                TaskHistory.taskParameters.isnot(None),
            )
            .order_by(TaskHistory.createdAt)
        )
        result = await self._session.execute(stmt)
        tasks = result.scalars().all()

        return [
            {
                "taskId": t.taskId,
                "taskType": t.taskType,
                "taskParameters": t.taskParameters,
                "title": t.title,
                "uniqueKey": t.uniqueKey,
                "queueType": t.queueType,
                "historyStatus": t.status,
                "description": t.description,
                "scheduledTaskId": t.scheduledTaskId,
            }
            for t in tasks
        ]

    async def get_task_for_retry(self, task_id: str) -> Optional[Dict[str, Any]]:
        """
        获取可重试的任务信息

        替代 crud.get_task_for_retry

        Args:
            task_id: 任务ID

        Returns:
            任务信息或 None
        """
        stmt = select(TaskHistory).where(TaskHistory.taskId == task_id)
        result = await self._session.execute(stmt)
        task = result.scalar_one_or_none()

        if not task:
            return None

        return {
            "taskId": task.taskId,
            "title": task.title,
            "uniqueKey": task.uniqueKey,
            "taskType": task.taskType,
            "taskParameters": task.taskParameters,
            "queueType": task.queueType,
            "status": task.status,
        }

    async def mark_interrupted_tasks_as_failed(self) -> int:
        """
        将所有运行中的任务标记为失败（用于服务重启时清理）

        替代 crud.mark_interrupted_tasks_as_failed

        Returns:
            标记失败的任务数量
        """

        stmt = (
            update(TaskHistory)
            .where(TaskHistory.status == '运行中')
            .values(
                status='失败',
                description='服务重启，任务被中断',
                updatedAt=get_now(),
            )
        )
        result = await self._session.execute(stmt)
        await self._session.flush()  # Repository 只 flush，commit 由调用方负责
        return result.rowcount

    async def mark_unrecoverable_pending_tasks_as_failed(self) -> int:
        """
        将不可恢复的排队任务标记为失败（缺少 taskType 或 taskParameters）

        替代 crud.mark_unrecoverable_pending_tasks_as_failed

        Returns:
            标记失败的任务数量
        """

        stmt = (
            update(TaskHistory)
            .where(
                TaskHistory.status.in_(['排队中', '等待中']),
                or_(
                    TaskHistory.taskType.is_(None),
                    TaskHistory.taskParameters.is_(None),
                )
            )
            .values(
                status='失败',
                description='服务重启，任务参数不完整，无法恢复',
                updatedAt=get_now(),
            )
        )
        result = await self._session.execute(stmt)
        await self._session.flush()  # Repository 只 flush，commit 由调用方负责
        return result.rowcount

    async def save_task_state_cache(self, task_id: str, state_data: str) -> None:
        """
        保存任务状态缓存

        替代 crud.save_task_state_cache

        Args:
            task_id: 任务ID
            state_data: 状态数据（JSON 字符串）
        """

        # 类型已在提交时写入历史表；沿用现有接口并在删除旧缓存前校验。
        task_type = await self._session.scalar(
            select(TaskHistory.taskType).where(TaskHistory.taskId == task_id)
        )
        if not task_type:
            raise ValueError(f"任务 {task_id} 缺少历史记录或任务类型，无法保存恢复缓存")

        await self._session.execute(
            delete(TaskStateCache).where(TaskStateCache.taskId == task_id)
        )

        # 按真实 ORM 字段保存类型、参数与时间，避免使用不存在的状态字段。
        now = get_now()
        new_cache = TaskStateCache(
            taskId=task_id,
            taskType=task_type,
            taskParameters=state_data,
            createdAt=now,
            updatedAt=now,
        )
        self._session.add(new_cache)
        await self._session.flush()  # Repository 只 flush，commit 由调用方负责

    async def delete_task(self, task_id: str) -> bool:
        """
        删除任务历史记录

        替代 crud.delete_task_from_history

        Args:
            task_id: 任务ID

        Returns:
            是否成功删除
        """
        task = await self._session.get(TaskHistory, task_id)
        if not task:
            logger.warning(f"尝试删除不存在的任务: {task_id}")
            return False

        logger.info(f"正在删除任务: {task_id}, 状态: {task.status}")
        await self._session.delete(task)
        await self._session.flush()  # Repository 只 flush，commit 由调用方负责

        logger.info(f"成功删除任务: {task_id}")
        return True

    async def force_delete_task(self, task_id: str) -> bool:
        """
        强制删除任务，使用 SQL 直接删除，绕过 ORM 可能的锁定问题

        替代 crud.force_delete_task_from_history

        Args:
            task_id: 任务ID

        Returns:
            是否成功删除
        """
        logger.info(f"强制删除任务: {task_id}")

        stmt = delete(TaskHistory).where(TaskHistory.taskId == task_id)
        result = await self._session.execute(stmt)
        await self._session.flush()  # Repository 只 flush，commit 由调用方负责

        deleted_count = result.rowcount
        if deleted_count > 0:
            logger.info(f"强制删除任务成功: {task_id}, 删除行数: {deleted_count}")
            return True
        else:
            logger.warning(f"强制删除任务失败，任务不存在: {task_id}")
            return False

    async def force_fail_task(self, task_id: str) -> bool:
        """
        强制将任务标记为失败状态

        替代 crud.force_fail_task

        Args:
            task_id: 任务ID

        Returns:
            是否成功更新
        """
        logger.info(f"强制标记任务为失败: {task_id}")

        stmt = update(TaskHistory).where(TaskHistory.taskId == task_id).values(
            status="失败",
            finishedAt=get_now(),
            updatedAt=get_now(),
            description="任务被强制中止"
        )
        result = await self._session.execute(stmt)
        await self._session.flush()  # Repository 只 flush，commit 由调用方负责

        updated_count = result.rowcount
        if updated_count > 0:
            logger.info(f"强制标记任务为失败成功: {task_id}")
            return True
        else:
            logger.warning(f"强制标记任务为失败失败，任务不存在: {task_id}")
            return False

    async def get_execution_task_id_from_scheduler_task(
        self,
        scheduler_task_id: str
    ) -> tuple[Optional[str], Optional[str]]:
        """
        从调度任务的最终描述中，解析并返回其触发的执行任务ID和状态

        替代 crud.get_execution_task_id_from_scheduler_task

        Args:
            scheduler_task_id: 调度任务ID

        Returns:
            (execution_task_id, status): 执行任务ID和状态，如果未找到则返回 (None, None)
        """
        # 先查询调度任务本身的状态
        stmt = select(TaskHistory.description, TaskHistory.status).where(
            TaskHistory.taskId == scheduler_task_id
        )
        result = await self._session.execute(stmt)
        row = result.one_or_none()

        if not row:
            return (None, None)

        description, scheduler_status = row

        # 如果调度任务本身失败了，直接返回
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
                exec_result = await self._session.execute(exec_stmt)
                exec_status = exec_result.scalar_one_or_none()
                return (execution_task_id, exec_status)

        return (None, None)

    async def find_recent_task_by_unique_key(
        self,
        unique_key: str,
        within_hours: int
    ) -> Optional[TaskHistory]:
        """
        通过 unique_key 查找最近的任务
        查找当前活跃或在指定时间窗口内完成的任务

        替代 crud.find_recent_task_by_unique_key

        Args:
            unique_key: 唯一键
            within_hours: 时间窗口（小时）

        Returns:
            任务对象或 None
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

        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()
