"""
WebhookTaskRepository - Webhook 任务数据访问层

职责：处理 WebhookTask 表的 CRUD 操作
"""

import logging
import json
from typing import List, Optional, Dict, Any
from datetime import datetime, timedelta

from sqlalchemy import select, update, delete, func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from ..orm_models import WebhookTask
from .base import BaseRepository
from src.core.timezone import get_now

logger = logging.getLogger(__name__)


class WebhookTaskRepository(BaseRepository[WebhookTask]):
    """Webhook 任务 Repository"""

    # 补齐基类抽象契约，确保 DatabaseService 能正常实例化该仓储。
    async def get_by_id(self, id: int) -> Optional[WebhookTask]:
        """根据主键获取 Webhook 任务，不存在时返回 None。"""
        stmt = select(WebhookTask).where(WebhookTask.id == id)
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def get_all(self, **filters: Any) -> List[WebhookTask]:
        """获取符合字段等值过滤条件的全部 Webhook 任务。"""
        stmt = select(WebhookTask).filter_by(**filters)
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def update(self, id: int, **data: Any) -> Optional[WebhookTask]:
        """更新 Webhook 任务，不存在时返回 None，事务由调用方提交。"""
        task = await self.get_by_id(id)
        if task is None:
            return None

        for key, value in data.items():
            if hasattr(task, key):
                setattr(task, key, value)

        await self._session.flush()
        return task

    async def delete(self, id: int) -> bool:
        """删除指定 Webhook 任务，不存在时返回 False，事务由调用方提交。"""
        task = await self.get_by_id(id)
        if task is None:
            return False

        await self._session.delete(task)
        await self._session.flush()
        return True

    async def create(
        self,
        task_title: str,
        unique_key: str,
        payload: Dict[str, Any],
        webhook_source: str,
        is_delayed: bool,
        delay: timedelta
    ) -> Optional[WebhookTask]:
        """创建一个新的待处理 Webhook 任务

        Args:
            task_title: 任务标题
            unique_key: 唯一键，用于去重
            payload: 任务负载（字典）
            webhook_source: Webhook 来源
            is_delayed: 是否延时执行
            delay: 延时时长

        Returns:
            创建成功返回 WebhookTask，重复则返回 None
        """
        now = get_now()
        execute_time = now + delay if is_delayed else now

        # 预先识别重复；其他写入异常交由事务所有者回滚，不能伪装成成功。
        existing = await self._session.execute(
            select(WebhookTask.id).where(WebhookTask.uniqueKey == unique_key)
        )
        if existing.scalar_one_or_none() is not None:
            logger.info(f"检测到重复的 Webhook 请求 (unique_key: {unique_key})，已忽略。")
            return None

        payload_json = json.dumps(payload, ensure_ascii=False)
        new_task = WebhookTask(
            receptionTime=now,
            executeTime=execute_time,
            taskTitle=task_title,
            uniqueKey=unique_key,
            payload=payload_json,
            webhookSource=webhook_source,
            status="pending",
        )
        try:
            # 并发重复写入由保存点隔离，不能让外层事务进入失败状态。
            async with self._session.begin_nested():
                self._session.add(new_task)
                await self._session.flush()
        except IntegrityError as exc:
            original = exc.orig
            code = getattr(original, "sqlstate", None) or getattr(original, "pgcode", None)
            args = getattr(original, "args", ())
            if code != "23505" and (not args or args[0] != 1062):
                raise
            logger.info(f"检测到并发重复的 Webhook 请求 (unique_key: {unique_key})，已忽略。")
            return None
        return new_task

    async def get_paginated(
        self,
        page: int,
        page_size: int,
        search: Optional[str] = None
    ) -> Dict[str, Any]:
        """获取待处理的 Webhook 任务列表，支持分页"""
        base_stmt = select(WebhookTask)
        if search:
            base_stmt = base_stmt.where(WebhookTask.taskTitle.like(f"%{search}%"))

        # 计算总数
        count_stmt = select(func.count()).select_from(base_stmt.alias("count_subquery"))
        total = (await self._session.execute(count_stmt)).scalar_one()

        # 获取分页数据
        stmt = base_stmt.order_by(WebhookTask.receptionTime.desc()).offset((page - 1) * page_size).limit(page_size)
        result = await self._session.execute(stmt)
        return {"total": total, "list": list(result.scalars().all())}

    async def delete_by_ids(self, task_ids: List[int]) -> int:
        """批量删除指定的 Webhook 任务"""
        if not task_ids:
            return 0
        stmt = delete(WebhookTask).where(WebhookTask.id.in_(task_ids))
        result = await self._session.execute(stmt)
        await self._session.flush()  # Repository 只 flush，不 commit
        return result.rowcount

    async def delete_all(self) -> int:
        """清空所有 Webhook 任务"""
        stmt = delete(WebhookTask)
        result = await self._session.execute(stmt)
        await self._session.flush()  # Repository 只 flush，不 commit
        return result.rowcount

    async def get_due_snapshots(self) -> List[Dict[str, Any]]:
        """读取全部到期待处理记录，返回可在事务外使用的值快照。"""
        result = await self._session.execute(
            select(WebhookTask).where(
                WebhookTask.status == "pending",
                WebhookTask.executeTime <= get_now(),
            )
        )
        return [
            {"id": task.id, "payload": task.payload, "title": task.taskTitle,
             "unique_key": task.uniqueKey, "source": task.webhookSource}
            for task in result.scalars().all()
        ]

    async def get_pending_by_ids(self, task_ids: List[int]) -> List[Dict[str, Any]]:
        """读取指定待处理任务的值快照，防止调度时持有数据库会话。"""
        if not task_ids:
            return []
        result = await self._session.execute(
            select(WebhookTask).where(
                WebhookTask.id.in_(task_ids), WebhookTask.status == "pending",
            )
        )
        return [
            {"id": task.id, "payload": task.payload, "title": task.taskTitle,
             "unique_key": task.uniqueKey, "source": task.webhookSource}
            for task in result.scalars().all()
        ]


    async def update_status(self, task_id: int, status: str) -> None:
        """更新 Webhook 任务的状态"""
        await self._session.execute(
            update(WebhookTask).where(WebhookTask.id == task_id).values(status=status)
        )
        await self._session.flush()  # Repository 只 flush，不 commit


