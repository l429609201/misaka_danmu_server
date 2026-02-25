import asyncio
import logging
import traceback
from enum import Enum
import time
import json
from typing import Any, Callable, Coroutine, Dict, List, Tuple, Optional # Add HTTPException, status
from uuid import uuid4, UUID

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from fastapi import HTTPException, status

from src.db import models, crud, ConfigManager

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

class TaskPauseForRateLimit(Exception):
    """自定义异常，用于表示任务因速率限制需要暂停"""
    def __init__(self, retry_after_seconds: float, message: str = ""):
        self.retry_after_seconds = retry_after_seconds
        self.message = message
        super().__init__(message)

class Task:
    def __init__(self, task_id: str, title: str, coro_factory: Callable[[Callable], Coroutine], scheduled_task_id: Optional[str] = None, unique_key: Optional[str] = None, task_type: Optional[str] = None, task_parameters: Optional[Dict] = None, queue_type: str = "download"):
        self.task_id = task_id
        self.title = title
        self.coro_factory: Callable[[AsyncSession, Callable], Coroutine] = coro_factory
        self.done_event = asyncio.Event()
        self.pause_event = asyncio.Event()
        self.running_coro_task: Optional[asyncio.Task] = None
        self.scheduled_task_id = scheduled_task_id
        self.last_update_time: float = 0.0
        self.update_lock = asyncio.Lock()
        self.unique_key = unique_key
        self.task_type = task_type  # 任务类型，用于恢复
        self.task_parameters = task_parameters or {}  # 任务参数，用于恢复
        self.queue_type = queue_type  # 队列类型: "download" 或 "management"
        self.pause_event.set() # 默认为运行状态 (事件被设置)

class TaskManager:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession], config_manager: ConfigManager):
        self._session_factory = session_factory
        # 三队列架构: 下载队列、管理队列、后备队列
        self._download_queue: asyncio.Queue = asyncio.Queue()
        self._management_queue: asyncio.Queue = asyncio.Queue()
        self._fallback_queue: asyncio.Queue = asyncio.Queue()
        self._download_worker_task: asyncio.Task | None = None
        self._management_worker_task: asyncio.Task | None = None
        self._fallback_worker_task: asyncio.Task | None = None
        self._paused_tasks_monitor_task: asyncio.Task | None = None
        self._current_download_task: Optional[Task] = None
        self._current_management_task: Optional[Task] = None
        self._current_fallback_task: Optional[Task] = None
        self._pending_titles: set[str] = set()
        self._active_unique_keys: set[str] = set()
        self._paused_tasks: Dict[str, Tuple[Task, float]] = {}  # {task_id: (task, resume_time)}
        self._lock = asyncio.Lock()
        self.config_manager = config_manager
        self.logger = logging.getLogger(self.__class__.__name__)

        # 受限源集合：记录因配额满而暂停的源
        # {provider_name: expire_time} - expire_time 用于自动清除过期的受限记录
        self._rate_limited_providers: Dict[str, float] = {}

        # 任务恢复所需的依赖，通过 set_recovery_dependencies 方法注入
        self._recovery_dependencies: Optional[Dict[str, Any]] = None
        # 通知服务引用，通过 set_notification_service 方法注入
        self._notification_service = None

    def set_recovery_dependencies(self, dependencies: Dict[str, Any]):
        """设置任务恢复所需的依赖

        Args:
            dependencies: 包含以下键的字典：
                - scraper_manager: ScraperManager 实例
                - rate_limiter: RateLimiter 实例
                - metadata_manager: MetadataSourceManager 实例
                - ai_matcher_manager: AIMatcherManager 实例
                - title_recognition_manager: TitleRecognitionManager 实例
        """
        self._recovery_dependencies = dependencies
        self.logger.info("任务恢复依赖已设置")

    def set_notification_service(self, notification_service):
        """设置通知服务引用"""
        self._notification_service = notification_service
        self.logger.info("通知服务已注入 TaskManager")

    def _determine_event_type(self, task: Task, is_success: bool) -> Optional[str]:
        """根据任务的 unique_key 和 title 判断应触发的通知事件类型"""
        key = task.unique_key or ""
        title = task.title or ""
        suffix = "_success" if is_success else "_failed"

        # 删除任务不发通知
        if key.startswith("delete-source-") or key.startswith("delete-bulk-sources-"):
            return None

        # 定时任务（有 scheduled_task_id）
        if task.scheduled_task_id:
            return "scheduled_task_complete" if is_success else "scheduled_task_failed"

        # Webhook 导入
        if key.startswith("webhook-search-"):
            return f"webhook_import{suffix}"

        # 数据源刷新
        if key.startswith("refresh-episode-") or key.startswith("full-refresh-") or key.startswith("bulk-refresh-"):
            return f"refresh{suffix}"

        # 追更刷新（增量刷新 job 提交的导入任务，通过 title 识别）
        if "追更" in title or "增量刷新" in title:
            return f"incremental_refresh{suffix}"

        # 自动导入
        if key.startswith("auto-import-"):
            return f"auto_import{suffix}"

        # 媒体库扫描
        if key.startswith("scan-media-server-"):
            return "media_scan_complete" if is_success else None

        # 通用导入（UI导入、URL导入、手动导入、批量导入、编辑后导入等）
        if key.startswith(("ui-import-", "url-import-", "manual-import-", "batch-manual-import-", "import-")):
            return f"import{suffix}"

        # 兜底：有 unique_key 但未匹配到的，按导入处理
        if key:
            return f"import{suffix}"

        return None

    async def _emit_task_event(self, task: Task, is_success: bool, message: str = ""):
        """发射任务完成/失败的通知事件"""
        if not self._notification_service:
            return
        event_type = self._determine_event_type(task, is_success)
        if not event_type:
            return
        try:
            await self._notification_service.emit_event(event_type, {
                "task_title": task.title,
                "message": message,
                "task_id": task.task_id,
                "unique_key": task.unique_key or "",
            })
        except Exception as e:
            self.logger.error(f"发射通知事件 {event_type} 失败: {e}")

    def start(self):
        """启动后台工作协程来处理任务队列。"""
        if self._download_worker_task is None:
            self._download_worker_task = asyncio.create_task(self._download_worker())
            self._management_worker_task = asyncio.create_task(self._management_worker())
            self._fallback_worker_task = asyncio.create_task(self._fallback_worker())
            self._paused_tasks_monitor_task = asyncio.create_task(self._paused_tasks_monitor())
            # 启动时处理中断的任务
            asyncio.create_task(self._handle_interrupted_tasks())
            self.logger.info("任务管理器已启动 (下载队列 + 管理队列 + 后备队列 + 暂停任务监控)。")

    async def _run_task_wrapper(self, task: Task, queue_type: str = "download"):
        """
        一个独立的包装器，用于在后台安全地执行单个任务。
        这可以防止单个任务的失败或阻塞影响到整个任务管理器。

        Args:
            task: 要执行的任务
            queue_type: 队列类型 ("download" 或 "management")
        """
        self.logger.info(f"开始执行任务 '{task.title}' (ID: {task.task_id}) [队列: {queue_type}]")
        try:
            # This task is now running, remove it from pending titles
            # This is now the single point of responsibility for this cleanup.
            async with self._lock:
                self._pending_titles.discard(task.title)

            async with self._session_factory() as session:
                await crud.update_task_progress_in_history(
                    session, task.task_id, TaskStatus.RUNNING, 0, "正在初始化..."
                )

                # 保存任务状态到缓存表（如果有任务类型和参数）
                if task.task_type and task.task_parameters:
                    await crud.save_task_state_cache(
                        session, task.task_id, task.task_type, json.dumps(task.task_parameters)
                    )

                progress_callback = self._get_progress_callback(task)
                actual_coroutine = task.coro_factory(session, progress_callback)

                running_task = asyncio.create_task(actual_coroutine)
                task.running_coro_task = running_task
                await running_task

                await crud.finalize_task_in_history(
                    session, task.task_id, TaskStatus.COMPLETED, "任务成功完成"
                )
                self.logger.info(f"任务 '{task.title}' (ID: {task.task_id}) 已成功完成 [队列: {queue_type}]。")
                await self._emit_task_event(task, True, "任务成功完成")
        except TaskPauseForRateLimit as e:
            # 任务因速率限制需要暂停
            self.logger.info(f"任务 '{task.title}' (ID: {task.task_id}) 因速率限制暂停 {e.retry_after_seconds:.0f} 秒")

            # 尝试从任务参数中提取源名称，记录受限源
            provider_name = None
            if task.task_parameters:
                provider_name = task.task_parameters.get("provider") or task.task_parameters.get("providerName")

            if provider_name:
                # 记录受限源及其过期时间
                expire_time = time.time() + e.retry_after_seconds
                async with self._lock:
                    self._rate_limited_providers[provider_name] = expire_time
                self.logger.info(f"源 '{provider_name}' 已标记为受限，将在 {e.retry_after_seconds:.0f} 秒后自动清除")

            async with self._session_factory() as final_session:
                await crud.update_task_progress_in_history(
                    final_session, task.task_id, TaskStatus.PAUSED,
                    None,  # 保持当前进度
                    e.message or f"速率受限，将在 {e.retry_after_seconds:.0f} 秒后自动重试..."
                )
            # 将任务放入暂停列表
            await self.pause_task_for_rate_limit(task, e.retry_after_seconds)
            # 不设置 done_event，因为任务还会继续
            return  # 提前返回，不执行 finally 块
        except TaskSuccess as e:
            self.logger.debug(f"捕获到 TaskSuccess 异常: {e}")
            final_message = str(e) if str(e) else "任务成功完成"
            async with self._session_factory() as final_session:
                await crud.finalize_task_in_history(
                    final_session, task.task_id, TaskStatus.COMPLETED, final_message
                )
            self.logger.info(f"任务 '{task.title}' (ID: {task.task_id}) 已成功完成，消息: {final_message}")
            await self._emit_task_event(task, True, final_message)
        except asyncio.CancelledError:
            self.logger.info(f"任务 '{task.title}' (ID: {task.task_id}) 已被用户取消。")
            async with self._session_factory() as final_session:
                await crud.finalize_task_in_history(
                    final_session, task.task_id, TaskStatus.FAILED, "任务已被用户取消"
                )
        except Exception:
            error_message = f"任务执行失败 - {traceback.format_exc()}"
            async with self._session_factory() as final_session:
                await crud.finalize_task_in_history(
                    final_session, task.task_id, TaskStatus.FAILED, error_message.splitlines()[-1]
                )
            self.logger.error(f"任务 '{task.title}' (ID: {task.task_id}) 执行失败: {traceback.format_exc()}")
            await self._emit_task_event(task, False, error_message.splitlines()[-1])
        finally:
            async with self._lock:
                if task.unique_key:
                    self._active_unique_keys.discard(task.unique_key)
                # Also remove from pending_titles again just in case of race conditions.
                self._pending_titles.discard(task.title)
            task.done_event.set()

    async def stop(self):
        """停止任务管理器。"""
        if self._download_worker_task:
            self._download_worker_task.cancel()
            try:
                await self._download_worker_task
            except asyncio.CancelledError:
                pass
            self._download_worker_task = None

        if self._management_worker_task:
            self._management_worker_task.cancel()
            try:
                await self._management_worker_task
            except asyncio.CancelledError:
                pass
            self._management_worker_task = None

        if self._fallback_worker_task:
            self._fallback_worker_task.cancel()
            try:
                await self._fallback_worker_task
            except asyncio.CancelledError:
                pass
            self._fallback_worker_task = None

        self.logger.info("任务管理器已停止。")

    async def _check_task_provider_limited(self, task: Task) -> tuple[bool, float]:
        """检查任务使用的源是否受限

        Returns:
            (is_limited, retry_after):
                - is_limited: True 表示源受限，任务应该被暂停跳过
                - retry_after: 需要等待的秒数
        """
        if not task.task_parameters:
            return False, 0.0  # 没有参数，无法判断，允许执行

        provider_name = task.task_parameters.get("provider") or task.task_parameters.get("providerName")
        if not provider_name:
            return False, 0.0  # 没有源信息，允许执行

        current_time = time.time()
        async with self._lock:
            # 清理过期的受限记录
            expired_providers = [p for p, expire_time in self._rate_limited_providers.items() if expire_time <= current_time]
            for p in expired_providers:
                del self._rate_limited_providers[p]

            # 检查该源是否受限
            if provider_name in self._rate_limited_providers:
                expire_time = self._rate_limited_providers[provider_name]
                retry_after = expire_time - current_time
                if retry_after > 0:
                    self.logger.info(f"任务 '{task.title}' 使用的源 '{provider_name}' 当前受限，暂停任务 {retry_after:.0f} 秒")
                    return True, retry_after

        return False, 0.0

    async def _download_worker(self):
        """从下载队列中获取并执行任务。"""
        while True:
            task: Task = await self._download_queue.get()
            try:
                self._current_download_task = task

                # 检查任务使用的源是否受限
                is_limited, retry_after = await self._check_task_provider_limited(task)
                if is_limited:
                    # 源受限，暂停任务并继续处理下一个
                    await self.pause_task_for_rate_limit(task, retry_after)
                    continue  # 跳过该任务，处理下一个

                # 执行前检查全局限制，避免频繁暂停
                await self._wait_for_global_limit()
                # The wrapper now handles removing the title from the pending set.
                await self._run_task_wrapper(task, queue_type="download")
            except Exception as e:
                # 防止 worker 崩溃 - 捕获所有未被 _run_task_wrapper 处理的异常
                self.logger.error(f"❌ Download Worker 捕获到未处理的异常: {type(e).__name__}: {e}", exc_info=True)
            finally:
                self._current_download_task = None
                self._download_queue.task_done()

    async def _management_worker(self):
        """从管理队列中获取并执行任务。"""
        while True:
            task: Task = await self._management_queue.get()
            try:
                self._current_management_task = task

                # 检查任务使用的源是否受限（管理任务通常不涉及单源限制，但保持一致性）
                is_limited, retry_after = await self._check_task_provider_limited(task)
                if is_limited:
                    # 源受限，暂停任务并继续处理下一个
                    await self.pause_task_for_rate_limit(task, retry_after)
                    continue  # 跳过该任务，处理下一个

                # The wrapper now handles removing the title from the pending set.
                await self._run_task_wrapper(task, queue_type="management")
            except Exception as e:
                # 防止 worker 崩溃 - 捕获所有未被 _run_task_wrapper 处理的异常
                self.logger.error(f"❌ Management Worker 捕获到未处理的异常: {type(e).__name__}: {e}", exc_info=True)
            finally:
                self._current_management_task = None
                self._management_queue.task_done()

    async def _fallback_worker(self):
        """从后备队列中获取并执行任务。"""
        while True:
            task: Task = await self._fallback_queue.get()
            try:
                self._current_fallback_task = task

                # 检查任务使用的源是否受限
                is_limited, retry_after = await self._check_task_provider_limited(task)
                if is_limited:
                    # 源受限，暂停任务并继续处理下一个
                    await self.pause_task_for_rate_limit(task, retry_after)
                    continue  # 跳过该任务，处理下一个

                # 后备队列不消耗全局配额，所以不需要等待全局流控
                # The wrapper now handles removing the title from the pending set.
                await self._run_task_wrapper(task, queue_type="fallback")
            except Exception as e:
                # 防止 worker 崩溃 - 捕获所有未被 _run_task_wrapper 处理的异常
                self.logger.error(f"❌ Fallback Worker 捕获到未处理的异常: {type(e).__name__}: {e}", exc_info=True)
            finally:
                self._current_fallback_task = None
                self._fallback_queue.task_done()

    async def _paused_tasks_monitor(self):
        """监控暂停的任务，到时间后重新放回队列"""
        while True:
            try:
                await asyncio.sleep(1)  # 每秒检查一次
                current_time = time.time()
                tasks_to_resume = []

                async with self._lock:
                    for task_id, (task, resume_time) in list(self._paused_tasks.items()):
                        if current_time >= resume_time:
                            tasks_to_resume.append((task_id, task))

                    # 从暂停列表中移除
                    for task_id, _ in tasks_to_resume:
                        del self._paused_tasks[task_id]

                # 重新放回队列
                for task_id, task in tasks_to_resume:
                    self.logger.info(f"任务 '{task.title}' (ID: {task_id}) 暂停时间已到，重新放回 {task.queue_type} 队列")

                    # 更新数据库状态为排队中，让前端能看到任务已恢复
                    try:
                        async with self._session_factory() as session:
                            await crud.update_task_status(session, task_id, TaskStatus.PENDING)
                    except Exception as e:
                        self.logger.warning(f"更新任务 '{task.title}' 状态失败: {e}")

                    if task.queue_type == "download":
                        await self._download_queue.put(task)
                    elif task.queue_type == "management":
                        await self._management_queue.put(task)
                    elif task.queue_type == "fallback":
                        await self._fallback_queue.put(task)

            except Exception as e:
                self.logger.error(f"❌ 暂停任务监控器发生错误: {type(e).__name__}: {e}", exc_info=True)

    async def _wait_for_global_limit(self):
        """在执行任务前检查全局限制，如果已满则等待"""
        if not self._recovery_dependencies:
            return

        rate_limiter = self._recovery_dependencies.get("rate_limiter")
        if not rate_limiter:
            return

        is_limited, wait_seconds = await rate_limiter.get_global_limit_status()
        if is_limited and wait_seconds > 0:
            self.logger.info(f"全局速率限制已满，等待 {wait_seconds:.0f} 秒后继续...")
            await asyncio.sleep(wait_seconds + 1)  # 多等1秒确保限制已重置

    async def pause_task_for_rate_limit(self, task: Task, retry_after_seconds: float):
        """将任务暂停指定时间，然后重新放回队列"""
        resume_time = time.time() + retry_after_seconds
        async with self._lock:
            self._paused_tasks[task.task_id] = (task, resume_time)
        self.logger.info(f"任务 '{task.title}' (ID: {task.task_id}) 因速率限制暂停 {retry_after_seconds:.0f} 秒")

    async def submit_task(
        self,
        coro_factory: Callable[[AsyncSession, Callable], Coroutine],
        title: str,
        scheduled_task_id: Optional[str] = None,
        unique_key: Optional[str] = None,
        run_immediately: bool = False,
        task_type: Optional[str] = None,
        task_parameters: Optional[Dict] = None,
        queue_type: str = "download"
    ) -> Tuple[str, asyncio.Event]:
        """提交一个新任务到队列，并在数据库中创建记录。返回任务ID和完成事件。

        Args:
            queue_type: 队列类型，"download" (下载队列)、"management" (管理队列) 或 "fallback" (后备队列)
        """
        async with self._lock:
            # 新增：检查唯一键，防止同一资源的多个任务同时进行
            # unique_key 是精确的去重机制，优先于 title 去重
            if unique_key:
                if unique_key in self._active_unique_keys:
                    # 根据unique_key的前缀提供更友好的错误消息
                    if unique_key.startswith("scan-media-server-"):
                        error_msg = "该媒体服务器的扫描任务正在进行中，请等待当前任务完成后再试。"
                    else:
                        error_msg = "一个针对此资源的相似任务已在队列中或正在运行，请勿重复提交。"
                    raise HTTPException(
                        status_code=status.HTTP_409_CONFLICT,
                        detail=error_msg
                    )
                self._active_unique_keys.add(unique_key)
            else:
                # 没有 unique_key 时，使用 title 作为兜底去重
                if title in self._pending_titles:
                    raise HTTPException(
                        status_code=status.HTTP_409_CONFLICT,
                        detail=f"任务 '{title}' 已在队列中，请勿重复提交。"
                    )
                # 检查三个队列的当前任务
                if (self._current_download_task and self._current_download_task.title == title) or \
                   (self._current_management_task and self._current_management_task.title == title) or \
                   (self._current_fallback_task and self._current_fallback_task.title == title):
                    raise HTTPException(
                        status_code=status.HTTP_409_CONFLICT,
                        detail=f"任务 '{title}' 已在运行中，请勿重复提交。"
                    )
                self._pending_titles.add(title)

        task_id = str(uuid4())
        task = Task(task_id, title, coro_factory, scheduled_task_id=scheduled_task_id, unique_key=unique_key, task_type=task_type, task_parameters=task_parameters, queue_type=queue_type)

        # 将任务参数序列化为JSON字符串，用于重启后恢复任务
        task_parameters_json = json.dumps(task_parameters, ensure_ascii=False) if task_parameters else None

        async with self._session_factory() as session:
            await crud.create_task_in_history(
                session, task_id, title, TaskStatus.PENDING, "等待执行...",
                scheduled_task_id=scheduled_task_id, unique_key=unique_key, queue_type=queue_type,
                task_type=task_type, task_parameters=task_parameters_json
            )

        if run_immediately:
            self.logger.info(f"立即执行任务 '{title}' (ID: {task_id})，绕过队列 [{queue_type}]。")
            asyncio.create_task(self._run_task_wrapper(task, queue_type=queue_type))
        else:
            # 根据队列类型选择队列
            if queue_type == "download":
                target_queue = self._download_queue
            elif queue_type == "management":
                target_queue = self._management_queue
            elif queue_type == "fallback":
                target_queue = self._fallback_queue
            else:
                raise ValueError(f"无效的队列类型: {queue_type}")

            await target_queue.put(task)
            self.logger.info(f"任务 '{title}' 已提交到 {queue_type} 队列，ID: {task_id}")
        return task_id, task.done_event

    def _get_progress_callback(self, task: Task) -> Callable:
        """为特定任务创建一个可暂停的回调闭包。"""
        async def pausable_callback(progress: int, description: str, status: Optional[TaskStatus] = None):
            # 核心暂停逻辑：在每次更新进度前，检查暂停事件。
            # 如果事件被清除 (cleared)，.wait() 将会阻塞，直到事件被重新设置 (set)。
            await task.pause_event.wait()

            now = time.time()
            # 只在状态改变、首次、完成或距离上次更新超过0.5秒时才更新数据库
            is_status_change = status is not None
            force_update = progress == 0 or progress >= 100 or is_status_change
            
            # 使用锁来防止并发更新 last_update_time
            async with task.update_lock:
                if not force_update and (now - task.last_update_time < 0.5):
                    return
                task.last_update_time = now

            # 数据库更新现在是同步的（在回调的协程内），但由于此逻辑，它不会频繁发生。
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
        task_to_remove: Optional[Task] = None

        # 检查下载队列
        temp_list = []
        while not self._download_queue.empty():
            try:
                task = self._download_queue.get_nowait()
                if task.task_id == task_id:
                    found_and_removed = True
                    task_to_remove = task
                    task.done_event.set()
                    self.logger.info(f"已从下载队列中取消待处理任务 '{task.title}' (ID: {task_id})。")
                else:
                    temp_list.append(task)
            except asyncio.QueueEmpty:
                break

        for task in temp_list:
            await self._download_queue.put(task)

        # 如果在下载队列中没找到，检查管理队列
        if not found_and_removed:
            temp_list = []
            while not self._management_queue.empty():
                try:
                    task = self._management_queue.get_nowait()
                    if task.task_id == task_id:
                        found_and_removed = True
                        task_to_remove = task
                        task.done_event.set()
                        self.logger.info(f"已从管理队列中取消待处理任务 '{task.title}' (ID: {task_id})。")
                    else:
                        temp_list.append(task)
                except asyncio.QueueEmpty:
                    break

            for task in temp_list:
                await self._management_queue.put(task)

        # 如果在管理队列中也没找到，检查后备队列
        if not found_and_removed:
            temp_list = []
            while not self._fallback_queue.empty():
                try:
                    task = self._fallback_queue.get_nowait()
                    if task.task_id == task_id:
                        found_and_removed = True
                        task_to_remove = task
                        task.done_event.set()
                        self.logger.info(f"已从后备队列中取消待处理任务 '{task.title}' (ID: {task_id})。")
                    else:
                        temp_list.append(task)
                except asyncio.QueueEmpty:
                    break

            for task in temp_list:
                await self._fallback_queue.put(task)

        # 修正：如果一个待处理任务被取消，必须同时清理其在管理器中的状态（任务标题和唯一键），
        # 以允许用户重新提交该任务。
        if found_and_removed and task_to_remove:
            async with self._lock:
                self._pending_titles.discard(task_to_remove.title)
                if task_to_remove.unique_key:
                    self._active_unique_keys.discard(task_to_remove.unique_key)
                    self.logger.info(f"已为已取消的待处理任务释放唯一键: {task_to_remove.unique_key}")

        return found_and_removed

    async def abort_current_task(self, task_id: str) -> bool:
        """如果ID匹配，则中止当前正在运行或暂停的任务。"""
        # 检查下载队列的当前任务
        if self._current_download_task and self._current_download_task.task_id == task_id and self._current_download_task.running_coro_task:
            self.logger.info(f"正在中止下载队列任务 '{self._current_download_task.title}' (ID: {task_id})")
            # 解除暂停，以便任务可以接收到取消异常
            self._current_download_task.pause_event.set()
            # 取消底层的协程
            self._current_download_task.running_coro_task.cancel()
            return True

        # 检查管理队列的当前任务
        if self._current_management_task and self._current_management_task.task_id == task_id and self._current_management_task.running_coro_task:
            self.logger.info(f"正在中止管理队列任务 '{self._current_management_task.title}' (ID: {task_id})")
            # 解除暂停，以便任务可以接收到取消异常
            self._current_management_task.pause_event.set()
            # 取消底层的协程
            self._current_management_task.running_coro_task.cancel()
            return True

        # 检查后备队列的当前任务
        if self._current_fallback_task and self._current_fallback_task.task_id == task_id and self._current_fallback_task.running_coro_task:
            self.logger.info(f"正在中止后备队列任务 '{self._current_fallback_task.title}' (ID: {task_id})")
            # 解除暂停，以便任务可以接收到取消异常
            self._current_fallback_task.pause_event.set()
            # 取消底层的协程
            self._current_fallback_task.running_coro_task.cancel()
            return True

        self.logger.warning(f"尝试中止任务 {task_id} 失败，因为它不是当前任务或未在运行。")
        return False

    async def pause_task(self, task_id: str) -> bool:
        """如果ID匹配，则暂停当前正在运行的任务。"""
        # 检查下载队列的当前任务
        if self._current_download_task and self._current_download_task.task_id == task_id:
            async with self._session_factory() as session:
                self._current_download_task.pause_event.clear()
                await crud.update_task_status(session, self._current_download_task.task_id, TaskStatus.PAUSED)
                self.logger.info(f"已暂停下载队列任务 '{self._current_download_task.title}' (ID: {task_id})。")
                return True

        # 检查管理队列的当前任务
        if self._current_management_task and self._current_management_task.task_id == task_id:
            async with self._session_factory() as session:
                self._current_management_task.pause_event.clear()
                await crud.update_task_status(session, self._current_management_task.task_id, TaskStatus.PAUSED)
                self.logger.info(f"已暂停管理队列任务 '{self._current_management_task.title}' (ID: {task_id})。")
                return True

        # 检查后备队列的当前任务
        if self._current_fallback_task and self._current_fallback_task.task_id == task_id:
            async with self._session_factory() as session:
                self._current_fallback_task.pause_event.clear()
                await crud.update_task_status(session, self._current_fallback_task.task_id, TaskStatus.PAUSED)
                self.logger.info(f"已暂停后备队列任务 '{self._current_fallback_task.title}' (ID: {task_id})。")
                return True

        self.logger.warning(f"尝试暂停任务 {task_id} 失败，因为它不是当前正在运行的任务。")
        return False

    async def resume_task(self, task_id: str) -> bool:
        """如果ID匹配，则恢复当前已暂停的任务。"""
        # 检查下载队列的当前任务
        if self._current_download_task and self._current_download_task.task_id == task_id:
            async with self._session_factory() as session:
                self._current_download_task.pause_event.set()
                await crud.update_task_status(session, self._current_download_task.task_id, TaskStatus.RUNNING)
                self.logger.info(f"已恢复下载队列任务 '{self._current_download_task.title}' (ID: {task_id})。")
                return True

        # 检查管理队列的当前任务
        if self._current_management_task and self._current_management_task.task_id == task_id:
            async with self._session_factory() as session:
                self._current_management_task.pause_event.set()
                await crud.update_task_status(session, self._current_management_task.task_id, TaskStatus.RUNNING)
                self.logger.info(f"已恢复管理队列任务 '{self._current_management_task.title}' (ID: {task_id})。")
                return True

        # 检查后备队列的当前任务
        if self._current_fallback_task and self._current_fallback_task.task_id == task_id:
            async with self._session_factory() as session:
                self._current_fallback_task.pause_event.set()
                await crud.update_task_status(session, self._current_fallback_task.task_id, TaskStatus.RUNNING)
                self.logger.info(f"已恢复后备队列任务 '{self._current_fallback_task.title}' (ID: {task_id})。")
                return True

        self.logger.warning(f"尝试恢复任务 {task_id} 失败，因为它不是当前已暂停的任务。")
        return False

    async def retry_task(self, task_id: str) -> str:
        """手动重试一个失败的任务。

        从数据库中读取任务的 taskType 和 taskParameters，重建协程工厂并重新提交到队列。

        Args:
            task_id: 要重试的任务 ID

        Returns:
            新任务的 ID

        Raises:
            HTTPException: 任务不存在、状态不允许重试、或无法重建协程工厂时
        """
        async with self._session_factory() as session:
            task_info = await crud.get_task_for_retry(session, task_id)

        if not task_info:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="任务不存在")

        if task_info["status"] != "失败":
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="只有失败的任务才能重试")

        task_type = task_info.get("taskType")
        task_parameters_raw = task_info.get("taskParameters")

        if not task_type or not task_parameters_raw:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="该任务缺少恢复所需信息（任务类型或参数），无法重试"
            )

        try:
            task_parameters = json.loads(task_parameters_raw) if isinstance(task_parameters_raw, str) else task_parameters_raw
        except (json.JSONDecodeError, TypeError):
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="任务参数格式错误，无法重试"
            )

        coro_factory = await self._rebuild_coro_factory(task_type, task_parameters)
        if not coro_factory:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"无法为任务类型 '{task_type}' 重建执行逻辑，该类型暂不支持重试"
            )

        new_task_id, _ = await self.submit_task(
            coro_factory,
            task_info["title"],
            unique_key=task_info.get("uniqueKey"),
            task_type=task_type,
            task_parameters=task_parameters,
            queue_type=task_info.get("queueType", "download")
        )
        self.logger.info(f"手动重试任务 '{task_info['title']}' 成功，原ID: {task_id}，新ID: {new_task_id}")
        return new_task_id

    async def _handle_interrupted_tasks(self):
        """处理服务重启时中断的任务（包括运行中和排队中的任务）"""
        try:
            async with self._session_factory() as session:
                # 1. 处理运行中的任务（这些任务有 TaskStateCache 记录）
                running_tasks = await crud.get_all_running_task_states(session)

                if running_tasks:
                    self.logger.info(f"发现 {len(running_tasks)} 个运行中被中断的任务，正在处理...")
                    for task_info in running_tasks:
                        await self._try_recover_task(task_info, is_running=True)
                    # 将运行中的任务标记为失败
                    await crud.mark_interrupted_tasks_as_failed(session)
                    self.logger.info("已处理所有运行中被中断的任务")

                # 2. 处理排队中的任务（这些任务直接从 TaskHistory 表恢复）
                pending_tasks = await crud.get_pending_recoverable_tasks(session)

                if pending_tasks:
                    self.logger.info(f"发现 {len(pending_tasks)} 个排队中的任务，正在尝试恢复...")
                    recovered_count = 0
                    failed_count = 0

                    for task_info in pending_tasks:
                        if await self._try_recover_task(task_info, is_running=False):
                            recovered_count += 1
                        else:
                            failed_count += 1
                            # 将无法恢复的任务标记为失败
                            await crud.update_task_in_history(
                                session, task_info["taskId"], "失败",
                                description="因服务重启且无法恢复而取消"
                            )

                    self.logger.info(f"排队中的任务处理完成: {recovered_count} 个已恢复, {failed_count} 个无法恢复")

                # 3. 清理剩余的排队中任务（无 taskType 的任务，无法恢复，标记为失败）
                unrecoverable_count = await crud.mark_unrecoverable_pending_tasks_as_failed(session)
                if unrecoverable_count > 0:
                    self.logger.info(f"已将 {unrecoverable_count} 个无法恢复的排队中任务标记为失败（缺少任务类型信息）")

                if not running_tasks and not pending_tasks and not unrecoverable_count:
                    self.logger.info("没有发现需要处理的中断任务")

        except Exception as e:
            self.logger.error(f"处理中断任务时发生错误: {e}", exc_info=True)

    async def _try_recover_task(self, task_info: Dict, is_running: bool = True) -> bool:
        """尝试恢复单个任务

        Args:
            task_info: 任务信息字典
            is_running: True表示是运行中被中断的任务，False表示是排队中的任务

        Returns:
            bool: 恢复成功返回True，失败返回False
        """
        task_id = task_info["taskId"]
        task_type = task_info.get("taskType")
        task_title = task_info.get("taskTitle", "未知任务")
        unique_key = task_info.get("uniqueKey")
        queue_type = task_info.get("queueType", "download")

        # 如果没有任务类型或参数，无法恢复
        if not task_type or not task_info.get("taskParameters"):
            self.logger.warning(f"任务 '{task_title}' (ID: {task_id}) 缺少恢复所需信息，无法恢复")
            return False

        try:
            # 解析任务参数
            task_parameters = json.loads(task_info["taskParameters"]) if isinstance(task_info["taskParameters"], str) else task_info["taskParameters"]

            # 检查是否有恢复依赖
            if not self._recovery_dependencies:
                self.logger.warning(f"任务恢复依赖未设置，无法恢复任务 '{task_title}' (ID: {task_id})")
                return False

            # 对于运行中的任务，只记录日志（可能已部分完成，不安全重启）
            if is_running:
                self.logger.info(f"运行中的任务 '{task_title}' (ID: {task_id}) 因服务中断而失败，类型: {task_type}")
                return False

            # 对于排队中的任务，尝试重建协程工厂并重新提交
            coro_factory = await self._rebuild_coro_factory(task_type, task_parameters)
            if not coro_factory:
                self.logger.warning(f"无法为任务类型 '{task_type}' 重建协程工厂，任务 '{task_title}' (ID: {task_id}) 恢复失败")
                return False

            # 重新提交任务到队列（使用新的任务ID，保留原有标题）
            new_task_id, _ = await self.submit_task(
                coro_factory,
                task_title,
                unique_key=unique_key,
                task_type=task_type,
                task_parameters=task_parameters,
                queue_type=queue_type
            )
            self.logger.info(f"成功恢复任务 '{task_title}'，新任务ID: {new_task_id}")
            return True

        except HTTPException as e:
            if e.status_code == 409:
                # 任务已存在，视为恢复成功
                self.logger.info(f"任务 '{task_title}' 已在队列中，跳过恢复")
                return True
            self.logger.error(f"尝试恢复任务 '{task_title}' (ID: {task_id}) 时发生HTTP错误: {e.detail}")
            return False
        except Exception as e:
            self.logger.error(f"尝试恢复任务 '{task_title}' (ID: {task_id}) 时发生错误: {e}", exc_info=True)
            return False

    async def _rebuild_coro_factory(self, task_type: str, task_parameters: Dict) -> Optional[Callable]:
        """根据任务类型和参数重建协程工厂

        Args:
            task_type: 任务类型
            task_parameters: 任务参数

        Returns:
            协程工厂函数，如果无法重建则返回None
        """
        if not self._recovery_dependencies:
            return None

        deps = self._recovery_dependencies
        scraper_manager = deps.get("scraper_manager")
        rate_limiter = deps.get("rate_limiter")
        metadata_manager = deps.get("metadata_manager")
        ai_matcher_manager = deps.get("ai_matcher_manager")
        title_recognition_manager = deps.get("title_recognition_manager")

        try:
            if task_type == "generic_import":
                # 动态导入以避免循环依赖
                from . import tasks

                return lambda session, callback: tasks.generic_import_task(
                    provider=task_parameters.get("provider"),
                    mediaId=task_parameters.get("mediaId"),
                    animeTitle=task_parameters.get("animeTitle"),
                    mediaType=task_parameters.get("mediaType"),
                    season=task_parameters.get("season"),
                    year=task_parameters.get("year"),
                    currentEpisodeIndex=task_parameters.get("currentEpisodeIndex"),
                    imageUrl=task_parameters.get("imageUrl"),
                    doubanId=task_parameters.get("doubanId"),
                    config_manager=self.config_manager,
                    tmdbId=task_parameters.get("tmdbId"),
                    imdbId=task_parameters.get("imdbId"),
                    tvdbId=task_parameters.get("tvdbId"),
                    bangumiId=task_parameters.get("bangumiId"),
                    metadata_manager=metadata_manager,
                    task_manager=self,
                    progress_callback=callback,
                    session=session,
                    manager=scraper_manager,
                    rate_limiter=rate_limiter,
                    title_recognition_manager=title_recognition_manager,
                    selectedEpisodes=task_parameters.get("selectedEpisodes"),
                )

            elif task_type == "webhook_search":
                from .tasks import webhook as webhook_tasks

                return lambda session, callback: webhook_tasks.webhook_search_and_dispatch_task(
                    animeTitle=task_parameters.get("animeTitle"),
                    mediaType=task_parameters.get("mediaType"),
                    season=task_parameters.get("season"),
                    currentEpisodeIndex=task_parameters.get("currentEpisodeIndex"),
                    searchKeyword=task_parameters.get("searchKeyword"),
                    doubanId=task_parameters.get("doubanId"),
                    tmdbId=task_parameters.get("tmdbId"),
                    imdbId=task_parameters.get("imdbId"),
                    tvdbId=task_parameters.get("tvdbId"),
                    bangumiId=task_parameters.get("bangumiId"),
                    webhookSource=task_parameters.get("webhookSource"),
                    year=task_parameters.get("year"),
                    progress_callback=callback,
                    session=session,
                    manager=scraper_manager,
                    task_manager=self,
                    metadata_manager=metadata_manager,
                    config_manager=self.config_manager,
                    ai_matcher_manager=ai_matcher_manager,
                    rate_limiter=rate_limiter,
                    title_recognition_manager=title_recognition_manager,
                    selectedEpisodes=task_parameters.get("selectedEpisodes"),
                )

            elif task_type == "full_refresh":
                from . import tasks as all_tasks
                source_id = task_parameters.get("sourceId")
                if not source_id:
                    return None
                return lambda session, callback: all_tasks.full_refresh_task(
                    source_id, session, scraper_manager, self, rate_limiter,
                    callback, metadata_manager, self.config_manager,
                )

            elif task_type == "incremental_refresh":
                from . import tasks as all_tasks
                source_id = task_parameters.get("sourceId")
                next_ep = task_parameters.get("nextEpisodeIndex")
                if not source_id or next_ep is None:
                    return None
                return lambda session, callback: all_tasks.incremental_refresh_task(
                    sourceId=source_id,
                    nextEpisodeIndex=next_ep,
                    session=session,
                    manager=scraper_manager,
                    task_manager=self,
                    config_manager=self.config_manager,
                    progress_callback=callback,
                    rate_limiter=rate_limiter,
                    metadata_manager=metadata_manager,
                    title_recognition_manager=title_recognition_manager,
                )

            elif task_type == "auto_import":
                from . import tasks as all_tasks
                from src.api.control.models import (
                    ControlAutoImportRequest, AutoImportSearchType, AutoImportMediaType,
                )
                try:
                    payload = ControlAutoImportRequest(**task_parameters)
                except Exception:
                    self.logger.warning(f"auto_import 任务参数解析失败，无法重建")
                    return None
                return lambda session, callback: all_tasks.auto_search_and_import_task(
                    payload, callback, session,
                    self.config_manager, scraper_manager,
                    metadata_manager, self,
                    ai_matcher_manager=ai_matcher_manager,
                    rate_limiter=rate_limiter,
                    title_recognition_manager=title_recognition_manager,
                )

            else:
                self.logger.warning(f"未知的任务类型 '{task_type}'，无法重建协程工厂")
                return None

        except Exception as e:
            self.logger.error(f"重建任务类型 '{task_type}' 的协程工厂时发生错误: {e}", exc_info=True)
            return None