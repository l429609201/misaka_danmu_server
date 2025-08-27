import asyncio
import logging
import traceback
from enum import Enum
import time
from typing import Any, Callable, Coroutine, Dict, List, Tuple, Optional
from uuid import uuid4, UUID

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from . import models, crud

logger = logging.getLogger(__name__)

class TaskStatus(str, Enum):
    PENDING = "排队中"
    RUNNING = "运行中"
    COMPLETED = "已完成"
    FAILED = "失败"
    PAUSED = "已暂停"

class TaskSuccess(Exception):
    """自定义异常，用于表示任务成功完成并附带一条最终消息。"""
    pass

class Task:
    def __init__(self, task_id: str, title: str, coro_factory: Callable[[Callable], Coroutine], scheduled_task_id: Optional[str] = None):
        self.task_id = task_id
        self.title = title
        self.coro_factory: Callable[[AsyncSession, Callable], Coroutine] = coro_factory
        self.done_event = asyncio.Event()
        self.pause_event = asyncio.Event()
        self.running_coro_task: Optional[asyncio.Task] = None
        self.scheduled_task_id = scheduled_task_id
        self.last_update_time: float = 0.0
        self.update_lock = asyncio.Lock()
        self.pause_event.set() # 默认为运行状态 (事件被设置)

class TaskManager:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]):
        self._session_factory = session_factory
        self._queue: asyncio.Queue = asyncio.Queue()
        self._worker_task: asyncio.Task | None = None
        self._current_task: Optional[Task] = None
        self._pending_titles: set[str] = set()
        self._lock = asyncio.Lock()
        self.logger = logging.getLogger(self.__class__.__name__)

    def start(self):
        """启动后台工作协程来处理任务队列。"""
        if self._worker_task is None:
            self._worker_task = asyncio.create_task(self._worker())
            self.logger.info("任务管理器已启动。")

    async def stop(self):
        """停止任务管理器。"""
        if self._worker_task:
            self._worker_task.cancel()
            try:
                await self._worker_task
            except asyncio.CancelledError:
                pass
            self._worker_task = None
            self.logger.info("任务管理器已停止。")

    async def _worker(self):
        """从队列中获取并执行任务。"""
        while True:
            self._current_task = None # 清理上一个任务
            task: Task = await self._queue.get()
            # 从待处理集合中移除
            async with self._lock:
                self._pending_titles.discard(task.title)
            self._current_task = task
            self.logger.info(f"开始执行任务 '{task.title}' (ID: {task.task_id})")

            try:
                async with self._session_factory() as session:
                    await crud.update_task_progress_in_history(
                        session, task.task_id, TaskStatus.RUNNING, 0, "正在初始化..."
                    )
                    progress_callback = self._get_progress_callback(task)
                    actual_coroutine = task.coro_factory(session, progress_callback)

                    # 将协程包装在Task中，以便可以取消它
                    running_task = asyncio.create_task(actual_coroutine)
                    task.running_coro_task = running_task
                    await running_task

                    # 对于没有引发 TaskSuccess 异常而正常结束的任务，使用通用成功消息
                    await crud.finalize_task_in_history(
                        session, task.task_id, TaskStatus.COMPLETED, "任务成功完成"
                    )
                    self.logger.info(f"任务 '{task.title}' (ID: {task.task_id}) 已成功完成。")
            except TaskSuccess as e:
                # 捕获 TaskSuccess 异常，使用其消息作为最终描述
                final_message = str(e) if str(e) else "任务成功完成"
                async with self._session_factory() as final_session:
                    await crud.finalize_task_in_history(
                        final_session, task.task_id, TaskStatus.COMPLETED, final_message
                    )
                self.logger.info(f"任务 '{task.title}' (ID: {task.task_id}) 已成功完成，消息: {final_message}")
            except asyncio.CancelledError:
                # 当任务被中止时，会捕获此异常
                self.logger.info(f"任务 '{task.title}' (ID: {task.task_id}) 已被用户取消。")
                async with self._session_factory() as final_session:
                    await crud.finalize_task_in_history(
                        final_session, task.task_id, TaskStatus.FAILED, "任务已被用户取消"
                    )
            except Exception as e:
                error_message = f"任务执行失败 - {e}"
                async with self._session_factory() as final_session:
                    await crud.finalize_task_in_history(
                        final_session, task.task_id, TaskStatus.FAILED, error_message
                    )
                self.logger.error(f"任务 '{task.title}' (ID: {task.task_id}) 执行失败: {traceback.format_exc()}")
            finally:
                self._queue.task_done()
                task.done_event.set()

    async def submit_task(self, coro_factory: Callable[[AsyncSession, Callable], Coroutine], title: str, scheduled_task_id: Optional[str] = None) -> Tuple[str, asyncio.Event]:
        """提交一个新任务到队列，并在数据库中创建记录。返回任务ID和完成事件。"""
        async with self._lock:
            # 检查是否有同名任务正在排队或运行
            if title in self._pending_titles:
                raise ValueError(f"任务 '{title}' 已在队列中，请勿重复提交。")
            if self._current_task and self._current_task.title == title:
                raise ValueError(f"任务 '{title}' 已在运行中，请勿重复提交。")
            self._pending_titles.add(title)

        task_id = str(uuid4())
        task = Task(task_id, title, coro_factory, scheduled_task_id=scheduled_task_id)
        
        async with self._session_factory() as session:
            await crud.create_task_in_history(
                session, task_id, title, TaskStatus.PENDING, "等待执行...", scheduled_task_id=scheduled_task_id
            )
        
        await self._queue.put(task)
        self.logger.info(f"任务 '{title}' 已提交，ID: {task_id}")
        return task_id, task.done_event

    def _get_progress_callback(self, task: Task) -> Callable:
        """为特定任务创建一个可暂停且带节流的回调闭包。"""
        async def pausable_callback(progress: int, description: str, status: Optional[TaskStatus] = None):
            # 核心暂停逻辑：在每次更新进度前，检查暂停事件。
            # 如果事件被清除 (cleared)，.wait() 将会阻塞，直到事件被重新设置 (set)。
            await task.pause_event.wait()

            now = time.time()
            # 节流逻辑：只在状态改变、首次、完成或距离上次更新超过0.5秒时才更新数据库
            is_status_change = status is not None
            force_update = progress == 0 or progress >= 100 or is_status_change
            
            # 使用锁来防止并发更新 last_update_time
            async with task.update_lock:
                if not force_update and (now - task.last_update_time < 0.5):
                    return
                task.last_update_time = now

            # 数据库更新现在是同步的（在回调的协程内），但由于节流，它不会频繁发生。
            # 这避免了创建大量并发任务，从而保护了数据库连接池。
            try:
                async with self._session_factory() as session:
                    await crud.update_task_progress_in_history(
                        session, task.task_id, status or TaskStatus.RUNNING, int(progress), description
                    )
            except Exception as e:
                self.logger.error(f"任务进度更新失败 (ID: {task.task_id}): {e}", exc_info=False)

        return pausable_callback

    async def cancel_pending_task(self, task_id: str) -> bool:
        """
        从队列中移除一个待处理的任务。
        注意：此操作不是线程安全的，但对于单工作线程模型是可接受的。
        """
        found_and_removed = False
        temp_list = []
        while not self._queue.empty():
            try:
                task = self._queue.get_nowait()
                if task.task_id == task_id:
                    found_and_removed = True
                    task.done_event.set()
                    self.logger.info(f"已从队列中取消待处理任务 '{task.title}' (ID: {task_id})。")
                else:
                    temp_list.append(task)
            except asyncio.QueueEmpty:
                break
        
        for task in temp_list:
            await self._queue.put(task)
            
        return found_and_removed

    async def abort_current_task(self, task_id: str) -> bool:
        """如果ID匹配，则中止当前正在运行或暂停的任务。"""
        if self._current_task and self._current_task.task_id == task_id and self._current_task.running_coro_task:
            self.logger.info(f"正在中止当前任务 '{self._current_task.title}' (ID: {task_id})")
            # 解除暂停，以便任务可以接收到取消异常
            self._current_task.pause_event.set()
            # 取消底层的协程
            self._current_task.running_coro_task.cancel()
            return True
        self.logger.warning(f"尝试中止任务 {task_id} 失败，因为它不是当前任务或未在运行。")
        return False

    async def pause_task(self, task_id: str) -> bool:
        """如果ID匹配，则暂停当前正在运行的任务。"""
        if self._current_task and self._current_task.task_id == task_id:
            async with self._session_factory() as session:
                self._current_task.pause_event.clear()
                await crud.update_task_status(session, self._current_task.task_id, TaskStatus.PAUSED)
                self.logger.info(f"已暂停任务 '{self._current_task.title}' (ID: {task_id})。")
                return True
        self.logger.warning(f"尝试暂停任务 {task_id} 失败，因为它不是当前正在运行的任务。")
        return False

    async def resume_task(self, task_id: str) -> bool:
        """如果ID匹配，则恢复当前已暂停的任务。"""
        if self._current_task and self._current_task.task_id == task_id:
            async with self._session_factory() as session:
                self._current_task.pause_event.set()
                await crud.update_task_status(session, self._current_task.task_id, TaskStatus.RUNNING)
                self.logger.info(f"已恢复任务 '{self._current_task.title}' (ID: {task_id})。")
                return True
        self.logger.warning(f"尝试恢复任务 {task_id} 失败，因为它不是当前已暂停的任务。")
        return False