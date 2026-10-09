import asyncio
import datetime
import re
import logging
import traceback
from enum import Enum
import time
import json
from typing import Any, Callable, Coroutine, Dict, List, Tuple, Optional # Add HTTPException, status
from uuid import uuid4, UUID

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from fastapi import HTTPException, status


# ? 改用 DatabaseService 替代直接调用 crud
from src.services.service_container import get_database_service
from src.services.database_service import DatabaseService
# why：在每个任务启动前注入 session_factory，让 TaskProfiler.flush 能开独立 session
# 写入 task_perf_events，避免外层 async with session 异常退出时 rollback 覆盖 perf 数据。
from src.utils.diagnostics.task_context import set_task_session_factory
# why: 异常类已移至 src.utils.diagnostics.task_exceptions（零依赖），此处 re-export 保持对外接口不变。
# 所有调用方（tasks/jobs/api 等）从 src.services 或 src.services.task_manager 导入均可正常工作。
from src.utils.diagnostics.task_exceptions import TaskSuccess, TaskFailed, TaskPauseForRateLimit
# why: _run_task 中兜底捕获流控异常（见 except RateLimitExceededError），
# rate_limiter 不反向依赖 src.services，顶部导入无循环依赖风险。
from src.rate_limiter import ConfigVerificationError, RateLimitExceededError

logger = logging.getLogger(__name__)


class TaskStatus(str, Enum):
    """任务状态枚举"""
    PENDING = "排队中"
    RUNNING = "运行中"
    COMPLETED = "已完成"
    FAILED = "失败"
    PAUSED = "已暂停"
    CANCELLED = "已取消"  # 用户主动取消的任务
    TIMEOUT = "超时"      # 执行超时的任务



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
    def __init__(self, session_factory: async_sessionmaker[AsyncSession], config_service, max_concurrent_tasks: int = 10, max_search_workers: int = 10, database_service: Optional[DatabaseService] = None):
        self._session_factory = session_factory
        # 生产环境统一取服务容器；测试或组合根可显式注入同一服务契约替身。
        self._db = database_service or get_database_service()

        self._download_queue: asyncio.Queue = asyncio.Queue()
        self._management_queue: asyncio.Queue = asyncio.Queue()
        self._fallback_queue: asyncio.Queue = asyncio.Queue()
        self._search_queue: asyncio.Queue = asyncio.Queue()

        self._max_concurrent_tasks = max_concurrent_tasks
        self._max_search_workers = max_search_workers
        self._download_workers: List[asyncio.Task] = []
        self._search_workers: List[asyncio.Task] = []       # 搜索队列多 worker
        self._management_worker_task: asyncio.Task | None = None  # 管理队列保持单worker
        self._fallback_worker_task: asyncio.Task | None = None    # 后备队列保持单worker
        self._paused_tasks_monitor_task: asyncio.Task | None = None

        # 当前运行的任务（用于监控和中止）
        self._current_download_tasks: Dict[int, Optional[Task]] = {}  # {worker_id: Task}
        self._current_search_tasks: Dict[int, Optional[Task]] = {}    # {worker_id: Task}
        self._current_management_task: Optional[Task] = None
        self._current_fallback_task: Optional[Task] = None

        self._pending_titles: set[str] = set()
        self._active_unique_keys: set[str] = set()
        self._paused_tasks: Dict[str, Tuple[Task, float]] = {}  # {task_id: (task, resume_time)}
        self._resuming_tasks: set[str] = set()
        # run_immediately=True 的任务不经过队列 worker，单独注册以支持暂停/终止
        self._immediate_tasks: Dict[str, Task] = {}  # {task_id: Task}
        self._lock = asyncio.Lock()
        self.config_service = config_service
        self.logger = logging.getLogger(self.__class__.__name__)

        # 受限源集合：记录因配额满而暂停的源
        # {provider_name: expire_time} - expire_time 用于自动清除过期的受限记录
        self._rate_limited_providers: Dict[str, float] = {}

        # 具体业务恢复由组合根注入，队列只处理恢复后的执行工厂。
        self._recovery_callback: Optional[Callable] = None
        self._recovery_queue_resolver: Optional[Callable] = None
        # 具体任务实现由组合根注册，避免 Service 反向导入 Tasks。
        self._task_handlers: Dict[str, Callable[..., Coroutine]] = {}
        self._handler_dependencies: Dict[str, Any] = {}
        self._rate_limiter = None
        # 生命周期事件订阅由组合根注入，通知业务由上层处理。
        self._task_event_callback: Optional[Callable] = None
        self._task_progress_callback: Optional[Callable] = None
        # 关闭标志位：优雅关闭时设为 True，区分「程序关闭」和「用户主动取消」
        self._is_shutting_down: bool = False

    def set_recovery_callback(self, callback: Callable, queue_resolver: Optional[Callable] = None) -> None:
        """注入任务工厂与历史队列迁移端口。"""
        self._recovery_callback = callback
        self._recovery_queue_resolver = queue_resolver

    def set_execution_dependencies(self, dependencies: Dict[str, Any]) -> None:
        """保存注册处理器使用的通用依赖端口，并注入队列流控能力。"""
        self._handler_dependencies = dict(dependencies)
        self._rate_limiter = dependencies.get("rate_limiter")

    def get_execution_dependencies(self) -> Dict[str, Any]:
        """返回处理器依赖快照，调用方不再读取恢复私有状态。"""
        return dict(self._handler_dependencies)

    def register_task_handler(
        self,
        task_type: str,
        handler: Callable[..., Coroutine],
    ) -> None:
        """注册可由上层按名称调用的任务处理器。"""
        self._task_handlers[task_type] = handler

    def build_task_coro_factory(
        self,
        task_type: str,
        **handler_kwargs: Any,
    ) -> Callable[[AsyncSession, Callable], Coroutine]:
        """根据已注册处理器创建符合 submit_task 约定的协程工厂。"""
        handler = self._task_handlers.get(task_type)
        if handler is None:
            raise RuntimeError(f"任务处理器未注册: {task_type}")

        return lambda session, callback: handler(
            session=session,
            progress_callback=callback,
            **handler_kwargs,
        )

    def set_task_event_callbacks(self, completed: Callable, progress: Callable) -> None:
        """订阅通用完成和进度生命周期，不让队列识别作品业务。"""
        self._task_event_callback = completed
        self._task_progress_callback = progress

    async def _emit_task_event(self, task: Task, is_success: bool, message: str = "") -> None:
        """发出通用任务结果，通知失败不改变任务终态。"""
        if self._task_event_callback is not None:
            try:
                await self._task_event_callback(task, is_success, message)
            except Exception as exc:
                self.logger.warning("任务结果订阅者失败: %s", exc)

    async def _safe_finalize_task(self, task_id: str, status, message: str):
        """安全地写入任务最终状态。

        DB 暂时不可用时只记录 WARNING，不向上传播异常，
        避免在 except/finally 清理路径中引发二次异常。
        """
        try:
            async with self._db.transaction():
                await self._db.task.finalize_task_in_history(task_id, status, message)
        except Exception as e:
            self.logger.warning(
                f"?? 任务 {task_id} 最终状态（{status}）未能写入DB"
                f"（DB 可能暂时不可用，重启后将由中断恢复机制处理）: {e}"
            )

    async def _safe_update_task_status(self, task_id: str, status, progress, message: str):
        """安全地更新任务进度状态。

        DB 暂时不可用时只记录 WARNING，不向上传播异常。
        """
        try:
            async with self._db.transaction():
                await self._db.task.update_task_progress_in_history(task_id, status, progress, message)
        except Exception as e:
            self.logger.warning(
                f"?? 任务 {task_id} 进度状态（{status}）未能写入DB"
                f"（DB 可能暂时不可用）: {e}"
            )

    def update_task_parameters(self, task_id: str, params: Dict) -> bool:
        """更新指定任务对象内存中的 task_parameters（合并更新）。

        供 coro_factory 在运行中将匹配详情等动态数据写入 task 对象，
        确保后续 _emit_task_event 发通知时能携带最新参数。

        Returns:
            True 表示找到并更新了, False 表示未找到该任务。
        """
        # 依次查找：立即执行任务 → 各队列当前任务
        task: Optional[Task] = self._immediate_tasks.get(task_id)
        if not task:
            # 检查下载队列的所有 worker
            for worker_task in self._current_download_tasks.values():
                if worker_task and worker_task.task_id == task_id:
                    task = worker_task
                    break
            # 检查搜索队列的所有 worker
            if not task:
                for worker_task in self._current_search_tasks.values():
                    if worker_task and worker_task.task_id == task_id:
                        task = worker_task
                        break
            # 检查管理队列和后备队列
            if not task:
                for t in (self._current_management_task, self._current_fallback_task):
                    if t and t.task_id == task_id:
                        task = t
                        break
        if not task:
            self.logger.debug(f"update_task_parameters: 未找到任务 {task_id}")
            return False

        if task.task_parameters is None:
            task.task_parameters = {}
        task.task_parameters.update(params)
        self.logger.debug(f"update_task_parameters: 任务 {task_id} 参数已更新: {list(params.keys())}")
        return True

    def start(self):
        """启动后台工作协程来处理任务队列。

        注意：任务恢复逻辑改为异步方法，需在外部 await start_async()
        """
        if not self._download_workers:
            # 启动多个下载队列worker，支持并发执行
            for worker_id in range(self._max_concurrent_tasks):
                worker_task = asyncio.create_task(self._download_worker(worker_id))
                self._download_workers.append(worker_task)
                self._current_download_tasks[worker_id] = None

            # 启动多个搜索队列worker
            for worker_id in range(self._max_search_workers):
                worker_task = asyncio.create_task(self._search_worker(worker_id))
                self._search_workers.append(worker_task)
                self._current_search_tasks[worker_id] = None

            self._management_worker_task = asyncio.create_task(self._management_worker())
            self._fallback_worker_task = asyncio.create_task(self._fallback_worker())
            self._paused_tasks_monitor_task = asyncio.create_task(self._paused_tasks_monitor())
            self.logger.info(
                f"任务管理器已启动 ({self._max_concurrent_tasks}个下载worker + "
                f"{self._max_search_workers}个搜索worker + 管理队列 + 后备队列 + 暂停任务监控)。"
            )

    async def start_async(self):
        """异步启动任务管理器，包含任务恢复逻辑。

        先执行任务恢复（阻塞等待完成），再启动后台 worker。
        这样可以避免恢复过程中与新任务提交产生竞态条件。
        """
        # 先恢复中断的任务（阻塞等待完成）
        await self._handle_interrupted_tasks()

        # 再启动后台 worker
        self.start()

    async def _run_task_wrapper(self, task: Task, queue_type: str = "download"):
        """
        一个独立的包装器，用于在后台安全地执行单个任务。
        这可以防止单个任务的失败或阻塞影响到整个任务管理器。

        Args:
            task: 要执行的任务
            queue_type: 队列类型 ("download" 或 "management")
        """
        self.logger.info(f"开始执行任务 '{task.title}' (ID: {task.task_id}) [队列: {queue_type}]")
        paused_for_rate_limit = False
        try:
            # This task is now running, remove it from pending titles
            # This is now the single point of responsibility for this cleanup.
            async with self._lock:
                self._pending_titles.discard(task.title)

            async with self._session_factory() as session:
                # ? 改用 DatabaseService（任务历史记录部分）
                async with self._db.transaction():
                    await self._db.task.update_task_progress_in_history(
                        task.task_id, TaskStatus.RUNNING, 0, "正在初始化..."
                    )

                    # 保存任务状态到缓存表（如果有任务类型和参数）
                    if task.task_type and task.task_parameters:
                        await self._db.task.save_task_state_cache(
                            task.task_id, json.dumps(task.task_parameters)
                        )

                progress_callback = self._get_progress_callback(task)
                # why：在协程启动前注入 session_factory 到 ContextVar，让任务内所有
                # TaskProfiler.flush 调用自动使用独立 session 写入 task_perf_events，
                # 避免外层 async with session 遇异常（TaskSuccess 等）退出时的 rollback
                # 把已 commit 的 perf 数据一并清掉。
                set_task_session_factory(self._session_factory)
                actual_coroutine = task.coro_factory(session, progress_callback)

                running_task = asyncio.create_task(actual_coroutine)
                task.running_coro_task = running_task
                task_result = await running_task

                # why：允许任务以非空字符串返回业务完成消息，避免正常返回路径把
                # “新增 N 条弹幕”等结果覆盖成笼统的“任务成功完成”。
                final_message = task_result if isinstance(task_result, str) and task_result.strip() else "任务成功完成"

                # ? 改用 DatabaseService
                async with self._db.transaction():
                    await self._db.task.finalize_task_in_history(
                        task.task_id, TaskStatus.COMPLETED, final_message
                    )
                self.logger.info(
                    f"任务 '{task.title}' (ID: {task.task_id}) 已成功完成，消息: {final_message} "
                    f"[队列: {queue_type}]。"
                )
                await self._emit_task_event(task, True, final_message)
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

            await self.pause_task_for_rate_limit(task, e.retry_after_seconds, reason=e.message)
            paused_for_rate_limit = True
            return
        except TaskSuccess as e:
            self.logger.debug(f"捕获到 TaskSuccess 异常: {e}")
            final_message = str(e) if str(e) else "任务成功完成"
            await self._safe_finalize_task(task.task_id, TaskStatus.COMPLETED, final_message)
            self.logger.info(f"任务 '{task.title}' (ID: {task.task_id}) 已成功完成，消息: {final_message}")
            await self._emit_task_event(task, True, final_message)
        except TaskFailed as e:
            # 业务失败：标记 FAILED 并发"失败"通知，但不打印 traceback（失败原因已在消息中）
            final_message = str(e) if str(e) else "任务失败"
            await self._safe_finalize_task(task.task_id, TaskStatus.FAILED, final_message)
            self.logger.warning(f"任务 '{task.title}' (ID: {task.task_id}) 业务失败，消息: {final_message}")
            await self._emit_task_event(task, False, final_message)
        except asyncio.CancelledError:
            if self._is_shutting_down:
                # 程序优雅关闭导致的取消，不修改数据库状态
                # 保留「运行中」状态，重启后 _handle_interrupted_tasks 会自动恢复该任务
                self.logger.info(f"程序关闭，任务 '{task.title}' (ID: {task.task_id}) 将在重启后自动恢复")
                task.done_event.set()
                raise
            else:
                # 用户主动取消（abort_current_task 触发）
                self.logger.info(f"任务 '{task.title}' (ID: {task.task_id}) 已被用户取消。")
                await self._safe_finalize_task(task.task_id, TaskStatus.FAILED, "任务已被用户取消")
        except ConfigVerificationError as e:
            await self.pause_task_for_rate_limit(task, 60, reason=f"流控配置校验失败，需修复配置：{e}")
            paused_for_rate_limit = True
            return
        except RateLimitExceededError as e:
            # 兜底：如果某个任务模块漏掉了 RateLimitExceededError → TaskPauseForRateLimit 的转换
            # 在此统一处理，确保任务被暂停而非失败
            self.logger.warning(f"任务 '{task.title}' (ID: {task.task_id}) 触发流控（兜底捕获），暂停任务 {e.retry_after_seconds:.0f} 秒")
            provider_name = None
            if task.task_parameters:
                provider_name = task.task_parameters.get("provider") or task.task_parameters.get("providerName")
            if provider_name:
                expire_time = time.time() + e.retry_after_seconds
                async with self._lock:
                    self._rate_limited_providers[provider_name] = expire_time
            await self.pause_task_for_rate_limit(task, e.retry_after_seconds, reason=str(e))
            paused_for_rate_limit = True
            return
        except Exception:
            error_message = f"任务执行失败 - {traceback.format_exc()}"
            await self._safe_finalize_task(
                task.task_id, TaskStatus.FAILED, error_message.splitlines()[-1]
            )
            self.logger.error(f"任务 '{task.title}' (ID: {task.task_id}) 执行失败: {traceback.format_exc()}")
            await self._emit_task_event(task, False, error_message.splitlines()[-1])
        finally:
            async with self._lock:
                # 暂停分支的 return 仍会执行 finally；暂停不是任务终态。
                if not paused_for_rate_limit:
                    if task.unique_key:
                        self._active_unique_keys.discard(task.unique_key)
                    self._pending_titles.discard(task.title)
                    task.done_event.set()

    async def stop(self):
        """停止任务管理器。"""
        # 标记正在关闭，使运行中任务因 CancelledError 中断时不覆盖数据库状态
        # 这样重启后 _handle_interrupted_tasks 能找到这些任务并恢复
        self._is_shutting_down = True
        if self._paused_tasks_monitor_task:
            self._paused_tasks_monitor_task.cancel()
            try:
                await self._paused_tasks_monitor_task
            except asyncio.CancelledError:
                pass
            self._paused_tasks_monitor_task = None

        # 停止所有下载worker
        for worker_task in self._download_workers:
            if worker_task and not worker_task.done():
                worker_task.cancel()
                try:
                    await worker_task
                except asyncio.CancelledError:
                    pass
        self._download_workers.clear()
        self._current_download_tasks.clear()

        # 停止所有搜索worker
        for worker_task in self._search_workers:
            if worker_task and not worker_task.done():
                worker_task.cancel()
                try:
                    await worker_task
                except asyncio.CancelledError:
                    pass
        self._search_workers.clear()
        self._current_search_tasks.clear()

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

    async def _run_queue_worker(
        self,
        queue_name: str,
        queue: asyncio.Queue,
        set_current: Callable[[Optional[Task]], None],
        worker_id: Optional[int] = None,
        wait_global_limit: bool = False,
    ):
        """通用队列 worker 执行骨架，供 download/management/fallback（后续含 search）复用。

        why：三个 worker 的循环结构完全一致，仅在「当前任务追踪方式、是否有 worker_id、
        是否消耗全局配额」三点上不同。抽出公共骨架后，新增队列只需再调用一次本方法，
        避免复制整段 while 循环并保持流控/取消/task_done 语义完全一致。

        Args:
            queue_name: 队列名称（用于日志），如 "download"/"management"/"fallback"。
            queue: 该队列的 asyncio.Queue 实例。
            set_current: 回调，用于把「当前正在执行的任务」写入对应追踪结构；
                         传入 None 表示清空。下载队列按 worker_id 写入 dict，
                         管理/后备队列写入单个属性。
            worker_id: 下载队列的 worker 编号；管理/后备队列为 None。
            wait_global_limit: 是否在执行前等待全局流控（仅下载队列为 True）。
        """
        label = f"{queue_name} 队列 Worker" + (f" {worker_id}" if worker_id is not None else "")
        # 注释掉单个 Worker 启动日志，避免日志冗余（已在 start() 中汇总输出）
        # self.logger.info(f"{label} 已启动")
        while True:
            task: Task = await queue.get()
            try:
                set_current(task)

                # 检查任务使用的源是否受限
                is_limited, retry_after = await self._check_task_provider_limited(task)
                if is_limited:
                    # 源受限，暂停任务并继续处理下一个
                    await self.pause_task_for_rate_limit(task, retry_after)
                    continue  # 跳过该任务，处理下一个

                # 流控等待释放 worker，由暂停监控器到期重排并再次检查配额。
                if wait_global_limit:
                    is_limited, retry_after = await self._get_global_limit_status()
                    if is_limited:
                        await self.pause_task_for_rate_limit(task, retry_after, reason="全局流控限制或校验异常")
                        continue

                # wrapper 负责从 pending 集合移除标题
                await self._run_task_wrapper(task, queue_type=queue_name)
            except Exception as e:
                # 防止 worker 崩溃 - 捕获所有未被 _run_task_wrapper 处理的异常
                self.logger.error(f"? {label} 捕获到未处理的异常: {type(e).__name__}: {e}", exc_info=True)
            finally:
                set_current(None)
                queue.task_done()

    async def _download_worker(self, worker_id: int):
        """从下载队列中获取并执行任务（复用通用骨架）。

        Args:
            worker_id: Worker的唯一标识符，用于跟踪和管理当前运行的任务
        """
        def _set_current(t: Optional[Task]):
            self._current_download_tasks[worker_id] = t

        await self._run_queue_worker(
            queue_name="download",
            queue=self._download_queue,
            set_current=_set_current,
            worker_id=worker_id,
            wait_global_limit=True,
        )

    async def _management_worker(self):
        """从管理队列中获取并执行任务（复用通用骨架）。"""
        def _set_current(t: Optional[Task]):
            self._current_management_task = t

        await self._run_queue_worker(
            queue_name="management",
            queue=self._management_queue,
            set_current=_set_current,
        )

    async def _fallback_worker(self):
        """从后备队列中获取并执行任务（复用通用骨架）。

        后备队列不消耗全局配额，因此 wait_global_limit=False。
        """
        def _set_current(t: Optional[Task]):
            self._current_fallback_task = t

        await self._run_queue_worker(
            queue_name="fallback",
            queue=self._fallback_queue,
            set_current=_set_current,
        )

    async def _search_worker(self, worker_id: int):
        """从搜索队列中获取并执行任务（复用通用骨架）。

        Args:
            worker_id: Worker的唯一标识符，用于跟踪和管理当前运行的任务

        why：搜索任务耗时长（15s+），与下载任务（2-5s）混在同一队列会导致
        慢速搜索占满 worker，下载任务饿死。独立 search 队列实现彻底隔离。
        搜索队列不消耗全局下载配额，因此 wait_global_limit=False。
        """
        def _set_current(t: Optional[Task]):
            self._current_search_tasks[worker_id] = t

        await self._run_queue_worker(
            queue_name="search",
            queue=self._search_queue,
            set_current=_set_current,
            worker_id=worker_id,
            wait_global_limit=False,  # 搜索队列不消耗下载配额
        )



    async def _resume_due_tasks(self) -> None:
        """将到期任务重新排队；状态落库成功前保留暂停记录。"""
        queues = {
            "download": self._download_queue, "management": self._management_queue,
            "fallback": self._fallback_queue, "search": self._search_queue,
        }
        async with self._lock:
            due_tasks = [
                (task_id, task, resume_time)
                for task_id, (task, resume_time) in self._paused_tasks.items()
                if time.time() >= resume_time and task_id not in self._resuming_tasks
            ]
            self._resuming_tasks.update(task_id for task_id, _, _ in due_tasks)

        for task_id, task, resume_time in due_tasks:
            try:
                async with self._db.transaction():
                    await self._db.task.update_task_progress_in_history(
                        task_id, TaskStatus.PENDING, None, "流控等待结束，已重新排队，执行前将再次检查流控",
                    )
                    if task.task_type:
                        await self._db.task.save_task_state_cache(task_id, json.dumps(task.task_parameters))
                task.pause_event.set()
                queues[task.queue_type].put_nowait(task)
                async with self._lock:
                    current = self._paused_tasks.get(task_id)
                    if current and current[0] is task:
                        del self._paused_tasks[task_id]
                self.logger.info("任务 '%s' 流控等待结束，已重新排队", task.title)
            except Exception:
                self.logger.exception("恢复暂停任务 '%s' 失败，保留暂停状态稍后重试", task.title)
            finally:
                async with self._lock:
                    self._resuming_tasks.discard(task_id)

    async def _paused_tasks_monitor(self) -> None:
        """监控暂停截止时间，不将等待流控误标为完成。"""
        while True:
            await asyncio.sleep(1)
            await self._resume_due_tasks()

    async def _get_global_limit_status(self) -> Tuple[bool, float]:
        """读取全局流控状态，不在 worker 内睡眠等待。"""
        limiter = self._rate_limiter
        if limiter is None:
            return False, 0.0
        return await limiter.get_global_limit_status()

    async def pause_task_for_rate_limit(
        self, task: Task, retry_after_seconds: float, reason: str = "",
    ) -> None:
        """保存暂停原因与恢复时间，释放 worker，等待流控结束后重新检查并恢复。"""
        delay = max(1.0, retry_after_seconds)
        resume_time = time.time() + delay
        resume_text = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(resume_time))
        description = f"已暂停：{reason or '触发流控'}；等待流控结束后自动恢复，预计 {resume_text} 重试"
        # 调度信息只进入恢复缓存，避免污染业务协程参数。
        state = dict(task.task_parameters)
        state.update(_rate_limit_resume_at=resume_time, _rate_limit_reason=reason)
        async with self._db.transaction():
            await self._db.task.update_task_progress_in_history(
                task.task_id, TaskStatus.PAUSED, None, description,
            )
            if task.task_type:
                await self._db.task.save_task_state_cache(task.task_id, json.dumps(state))
        async with self._lock:
            task.done_event.clear()
            self._pending_titles.add(task.title)
            self._paused_tasks[task.task_id] = (task, resume_time)
        self.logger.info("任务 '%s' (ID: %s) %s", task.title, task.task_id, description)

    async def submit_task(
        self,
        coro_factory: Callable[[AsyncSession, Callable], Coroutine],
        title: str,
        scheduled_task_id: Optional[str] = None,
        unique_key: Optional[str] = None,
        run_immediately: bool = False,
        task_type: Optional[str] = None,
        task_parameters: Optional[Dict] = None,
        queue_type: str = "download",
        parent_task_id: Optional[str] = None
    ) -> Tuple[str, asyncio.Event]:
        """提交一个新任务到队列，并在数据库中创建记录。返回任务ID和完成事件。

        Args:
            queue_type: 队列类型，"download" (下载队列)、"management" (管理队列)、"fallback" (后备队列) 或 "search" (搜索队列)
            parent_task_id: 父任务ID（如搜索任务ID），用于记录任务派发关系
        """
        # why: 所有可能失败的纯校验必须在占用去重标记前完成，避免无效请求留下永久锁。
        queue_map = {
            "download": self._download_queue,
            "management": self._management_queue,
            "fallback": self._fallback_queue,
            "search": self._search_queue,
        }
        if queue_type not in queue_map:
            raise ValueError(f"无效的队列类型: {queue_type}")

        # 提前验证任务参数可序列化；否则 json.dumps 异常会发生在去重标记占用之后。
        task_parameters_json = json.dumps(task_parameters, ensure_ascii=False) if task_parameters is not None else None

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
                # 检查四个队列的当前任务
                # 检查下载队列的所有 worker
                for worker_task in self._current_download_tasks.values():
                    if worker_task and worker_task.title == title:
                        raise HTTPException(
                            status_code=status.HTTP_409_CONFLICT,
                            detail=f"任务 '{title}' 已在运行中，请勿重复提交。"
                        )
                # 检查搜索队列的所有 worker
                for worker_task in self._current_search_tasks.values():
                    if worker_task and worker_task.title == title:
                        raise HTTPException(
                            status_code=status.HTTP_409_CONFLICT,
                            detail=f"任务 '{title}' 已在运行中，请勿重复提交。"
                        )
                # 检查管理队列和后备队列
                if (self._current_management_task and self._current_management_task.title == title) or \
                   (self._current_fallback_task and self._current_fallback_task.title == title):
                    raise HTTPException(
                        status_code=status.HTTP_409_CONFLICT,
                        detail=f"任务 '{title}' 已在运行中，请勿重复提交。"
                    )
                self._pending_titles.add(title)

        task_id = str(uuid4())
        task = Task(task_id, title, coro_factory, scheduled_task_id=scheduled_task_id, unique_key=unique_key, task_type=task_type, task_parameters=task_parameters, queue_type=queue_type)
        history_created = False
        accepted = False

        try:
            async with self._db.transaction():
                # 标记为“可能已落库”，即使取消恰好发生在数据库提交返回边界，
                # 补偿逻辑也会尝试把该记录置为失败；记录不存在时更新是安全空操作。
                history_created = True
                await self._db.task.create_task_in_history(
                    task_id, title, TaskStatus.PENDING, "等待执行...",
                    scheduled_task_id=scheduled_task_id, unique_key=unique_key, queue_type=queue_type,
                    task_type=task_type, task_parameters=task_parameters_json,
                    parent_task_id=parent_task_id
                )

            if run_immediately:
                self.logger.info(f"立即执行任务 '{title}' (ID: {task_id})，绕过队列 [{queue_type}]。")
                # 注册到 _immediate_tasks，使 pause/abort/resume 能找到该任务
                async with self._lock:
                    self._immediate_tasks[task_id] = task

                # 定时任务超时保护：管理队列任务 15 分钟，下载队列任务 30 分钟
                immediate_timeout = 900 if queue_type == "management" else 1800

                async def _run_and_cleanup():
                    try:
                        await asyncio.wait_for(
                            self._run_task_wrapper(task, queue_type=queue_type),
                            timeout=immediate_timeout
                        )
                    except asyncio.TimeoutError:
                        self.logger.error(f"立即执行任务 '{title}' (ID: {task_id}) 超时（{immediate_timeout}秒），强制终止")
                        # 确保内部 running_coro_task 被取消
                        if task.running_coro_task and not task.running_coro_task.done():
                            task.running_coro_task.cancel()
                        await self._safe_finalize_task(
                            task_id, TaskStatus.FAILED, f"任务执行超时（{immediate_timeout // 60}分钟），已强制终止"
                        )
                        task.done_event.set()
                    finally:
                        async with self._lock:
                            self._immediate_tasks.pop(task_id, None)

                asyncio.create_task(_run_and_cleanup())
                accepted = True
            else:
                # why: 三个任务队列均为无界 asyncio.Queue；同步入队可消除取消发生在
                # “put 已完成、accepted 尚未赋值”之间而错误释放去重标记的窗口。
                queue_map[queue_type].put_nowait(task)
                accepted = True
                self.logger.info(f"任务 '{title}' 已提交到 {queue_type} 队列，ID: {task_id}")
            return task_id, task.done_event
        except BaseException as exc:
            if not accepted:
                # why: 历史写入、立即任务注册或入队失败时，必须释放提交阶段占用的去重标记。
                async with self._lock:
                    if unique_key:
                        self._active_unique_keys.discard(unique_key)
                    else:
                        self._pending_titles.discard(title)
                    self._immediate_tasks.pop(task_id, None)
                task.done_event.set()

                if history_created:
                    try:
                        await self._safe_finalize_task(
                            task_id, TaskStatus.FAILED, f"任务提交失败: {type(exc).__name__}"
                        )
                    except Exception as finalize_error:
                        self.logger.warning(f"标记提交失败任务 '{task_id}' 失败: {finalize_error}")
            raise

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
                async with self._db.transaction():
                    await self._db.task.update_task_progress_in_history(
                        task.task_id, status or TaskStatus.RUNNING, int(progress), description
                    )
            except Exception as e:
                self.logger.error(f"任务进度更新失败 (ID: {task.task_id}): {e}", exc_info=False)

            # 进度未完成时触发 TG 进度通知（TG 会 edit 已有消息，其他渠道跳过进度推送）
            # 加超时保护：通知推送不应阻塞任务执行（TG 代理不可达时可能卡住）
            if self._task_progress_callback is not None and progress < 100:
                try:
                    await asyncio.wait_for(
                        self._task_progress_callback(task, int(progress), description),
                        timeout=10  # 最多等10秒，超时则跳过本次通知
                    )
                except asyncio.TimeoutError:
                    self.logger.warning(f"任务进度通知超时 (ID: {task.task_id})，跳过本次推送")
                except Exception as e:
                    self.logger.debug(f"任务进度通知失败 (ID: {task.task_id}): {e}")

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

        # 如果在后备队列中也没找到，检查搜索队列
        if not found_and_removed:
            temp_list = []
            while not self._search_queue.empty():
                try:
                    task = self._search_queue.get_nowait()
                    if task.task_id == task_id:
                        found_and_removed = True
                        task_to_remove = task
                        task.done_event.set()
                        self.logger.info(f"已从搜索队列中取消待处理任务 '{task.title}' (ID: {task_id})。")
                    else:
                        temp_list.append(task)
                except asyncio.QueueEmpty:
                    break

            for task in temp_list:
                await self._search_queue.put(task)

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
        # 优先检查 _paused_tasks 字典（因速率限制而暂停的任务）
        async with self._lock:
            if task_id in self._paused_tasks:
                task, resume_time = self._paused_tasks[task_id]
                del self._paused_tasks[task_id]
                self.logger.info(f"用户中止了暂停任务 '{task.title}' (ID: {task_id})，已从暂停队列中移除")

                # 更新数据库状态为已取消
                try:
                    async with self._db.transaction():
                        await self._db.task.finalize_task_in_history(task_id, TaskStatus.CANCELLED, "用户取消")
                except Exception as e:
                    self.logger.warning(f"更新任务 '{task.title}' 状态失败: {e}")

                # 清理唯一键和标题
                if task.unique_key:
                    self._active_unique_keys.discard(task.unique_key)
                self._pending_titles.discard(task.title)

                # 触发完成事件
                task.done_event.set()
                return True

        # 检查下载队列的所有worker
        for worker_id, task in self._current_download_tasks.items():
            if task and task.task_id == task_id and task.running_coro_task:
                self.logger.info(f"正在中止下载队列 Worker {worker_id} 的任务 '{task.title}' (ID: {task_id})")
                task.pause_event.set()
                task.running_coro_task.cancel()
                return True

        # 检查搜索队列的所有worker
        for worker_id, task in self._current_search_tasks.items():
            if task and task.task_id == task_id and task.running_coro_task:
                self.logger.info(f"正在中止搜索队列 Worker {worker_id} 的任务 '{task.title}' (ID: {task_id})")
                task.pause_event.set()
                task.running_coro_task.cancel()
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

        # 检查 run_immediately 的立即执行任务
        imm_task = self._immediate_tasks.get(task_id)
        if imm_task and imm_task.running_coro_task:
            self.logger.info(f"正在中止立即执行任务 '{imm_task.title}' (ID: {task_id})")
            imm_task.pause_event.set()
            imm_task.running_coro_task.cancel()
            return True

        self.logger.warning(f"尝试中止任务 {task_id} 失败，因为它不是当前任务或未在运行。")
        return False

    async def pause_task(self, task_id: str) -> bool:
        """如果ID匹配，则暂停当前正在运行的任务。"""
        # 检查下载队列的所有worker
        for worker_id, task in self._current_download_tasks.items():
            if task and task.task_id == task_id:
                async with self._db.transaction():
                    task.pause_event.clear()
                    await self._db.task.update_task_progress_in_history(task.task_id, TaskStatus.PAUSED, None, "用户手动暂停，等待手动恢复")
                    self.logger.info(f"已暂停下载队列 Worker {worker_id} 的任务 '{task.title}' (ID: {task_id})。")
                    return True

        # 检查搜索队列的所有worker
        for worker_id, task in self._current_search_tasks.items():
            if task and task.task_id == task_id:
                async with self._db.transaction():
                    task.pause_event.clear()
                    await self._db.task.update_task_progress_in_history(task.task_id, TaskStatus.PAUSED, None, "用户手动暂停，等待手动恢复")
                    self.logger.info(f"已暂停搜索队列 Worker {worker_id} 的任务 '{task.title}' (ID: {task_id})。")
                    return True

        # 检查管理队列的当前任务
        if self._current_management_task and self._current_management_task.task_id == task_id:
            async with self._db.transaction():
                self._current_management_task.pause_event.clear()
                await self._db.task.update_task_progress_in_history(self._current_management_task.task_id, TaskStatus.PAUSED, None, "用户手动暂停，等待手动恢复")
                self.logger.info(f"已暂停管理队列任务 '{self._current_management_task.title}' (ID: {task_id})。")
                return True

        # 检查后备队列的当前任务
        if self._current_fallback_task and self._current_fallback_task.task_id == task_id:
            async with self._db.transaction():
                self._current_fallback_task.pause_event.clear()
                await self._db.task.update_task_progress_in_history(self._current_fallback_task.task_id, TaskStatus.PAUSED, None, "用户手动暂停，等待手动恢复")
                self.logger.info(f"已暂停后备队列任务 '{self._current_fallback_task.title}' (ID: {task_id})。")
                return True

        # 检查 run_immediately 的立即执行任务
        imm_task = self._immediate_tasks.get(task_id)
        if imm_task:
            async with self._db.transaction():
                imm_task.pause_event.clear()
                await self._db.task.update_task_progress_in_history(task_id, TaskStatus.PAUSED, None, "用户手动暂停，等待手动恢复")
                self.logger.info(f"已暂停立即执行任务 '{imm_task.title}' (ID: {task_id})。")
                return True

        self.logger.warning(f"尝试暂停任务 {task_id} 失败，因为它不是当前正在运行的任务。")
        return False

    async def resume_task(self, task_id: str) -> bool:
        """如果ID匹配，则恢复当前已暂停的任务。"""
        async with self._lock:
            if task_id in self._resuming_tasks:
                return False
            paused_entry = self._paused_tasks.get(task_id)

        if paused_entry:
            task, resume_time = paused_entry
            try:
                async with self._db.transaction():
                    await self._db.task.update_task_progress_in_history(
                        task_id, TaskStatus.PENDING, None, "用户请求恢复，已重新排队并等待流控检查",
                    )
                    if task.task_type:
                        await self._db.task.save_task_state_cache(task_id, json.dumps(task.task_parameters))
                task.pause_event.set()
                if task.queue_type == "download":
                    await self._download_queue.put(task)
                elif task.queue_type == "search":
                    await self._search_queue.put(task)
                elif task.queue_type == "management":
                    await self._management_queue.put(task)
                elif task.queue_type == "fallback":
                    await self._fallback_queue.put(task)
                async with self._lock:
                    if self._paused_tasks.get(task_id, (None, None))[0] is task:
                        del self._paused_tasks[task_id]
                self.logger.info(f"用户手动恢复任务 '{task.title}' (ID: {task_id})，原计划 {resume_time - time.time():.0f} 秒后自动恢复")
                return True
            except Exception:
                self.logger.exception(f"用户恢复任务 '{task.title}' (ID: {task_id}) 失败，保留暂停状态")
                return False

        # 检查下载队列的所有worker（正在执行但被暂停的任务）
        for worker_id, task in self._current_download_tasks.items():
            if task and task.task_id == task_id:
                async with self._db.transaction():
                    task.pause_event.set()
                    await self._db.task.update_task_status(task.task_id, TaskStatus.RUNNING)
                    self.logger.info(f"已恢复下载队列 Worker {worker_id} 的任务 '{task.title}' (ID: {task_id})。")
                    return True

        # 检查搜索队列的所有worker（正在执行但被暂停的任务）
        for worker_id, task in self._current_search_tasks.items():
            if task and task.task_id == task_id:
                async with self._db.transaction():
                    task.pause_event.set()
                    await self._db.task.update_task_status(task.task_id, TaskStatus.RUNNING)
                    self.logger.info(f"已恢复搜索队列 Worker {worker_id} 的任务 '{task.title}' (ID: {task_id})。")
                    return True

        # 检查管理队列的当前任务
        if self._current_management_task and self._current_management_task.task_id == task_id:
            async with self._db.transaction():
                self._current_management_task.pause_event.set()
                await self._db.task.update_task_status(self._current_management_task.task_id, TaskStatus.RUNNING)
                self.logger.info(f"已恢复管理队列任务 '{self._current_management_task.title}' (ID: {task_id})。")
                return True

        # 检查后备队列的当前任务
        if self._current_fallback_task and self._current_fallback_task.task_id == task_id:
            async with self._db.transaction():
                self._current_fallback_task.pause_event.set()
                await self._db.task.update_task_status(self._current_fallback_task.task_id, TaskStatus.RUNNING)
                self.logger.info(f"已恢复后备队列任务 '{self._current_fallback_task.title}' (ID: {task_id})。")
                return True

        # 检查 run_immediately 的立即执行任务
        imm_task = self._immediate_tasks.get(task_id)
        if imm_task:
            async with self._db.transaction():
                imm_task.pause_event.set()
                await self._db.task.update_task_status(task_id, TaskStatus.RUNNING)
                self.logger.info(f"已恢复立即执行任务 '{imm_task.title}' (ID: {task_id})。")
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
        async with self._db.transaction():
            task_info = await self._db.task.get_task_for_retry(task_id)

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

    async def _handle_interrupted_tasks(self) -> None:
        """沿用原任务记录恢复中断任务，保留手动暂停和流控等待状态。"""
        try:
            async with self._db.transaction():
                active_tasks = await self._db.task.get_all_running_task_states()
                pending_tasks = await self._db.task.get_pending_recoverable_tasks()
                # 仅清理恢复前就缺少参数的排队记录，不能误伤依靠缓存恢复的任务。
                unrecoverable_count = await self._db.task.mark_unrecoverable_pending_tasks_as_failed()

            # 恢复期间 worker 可能已开始执行，不能再全局清理运行中的记录。
            recovered_count = 0
            seen_task_ids = set()
            for task_info in active_tasks + pending_tasks:
                task_id = task_info["taskId"]
                if task_id in seen_task_ids:
                    continue
                seen_task_ids.add(task_id)
                if await self._try_recover_task(task_info):
                    recovered_count += 1

            self.logger.info(
                f"重启任务恢复完成: {recovered_count} 个已恢复，"
                f"{len(seen_task_ids) - recovered_count + unrecoverable_count} 个无法恢复"
            )
        except Exception as e:
            self.logger.error(f"处理中断任务时发生错误: {e}", exc_info=True)

    async def _try_recover_task(self, task_info: Dict) -> bool:
        """重建同一任务的执行逻辑，恢复队列或暂停状态，失败则明确写入终态。"""
        task_id = task_info["taskId"]
        task_type = task_info.get("taskType")
        task_title = task_info.get("title") or task_info.get("taskTitle") or "未知任务"
        unique_key = task_info.get("uniqueKey")
        queue_type = task_info.get("queueType") or "download"
        history_status = task_info.get("historyStatus")
        task = None
        reserved = False

        try:
            raw_parameters = task_info.get("taskParameters")
            # 空字典也是合法参数，只有缺失参数才不可恢复。
            if not task_type or raw_parameters is None:
                raise ValueError("缺少任务类型或恢复参数")
            task_parameters = json.loads(raw_parameters) if isinstance(raw_parameters, str) else raw_parameters
            if not isinstance(task_parameters, dict):
                raise ValueError("任务恢复参数必须为 JSON 对象")
            task_parameters = dict(task_parameters)
            # 流控元信息只参与调度，不能传入具体任务工厂。
            resume_at = task_parameters.pop("_rate_limit_resume_at", None)
            reason = task_parameters.pop("_rate_limit_reason", None) or "速率限制"
            remaining = 0.0
            if resume_at is not None:
                resume_at = float(resume_at)
                if not (-float("inf") < resume_at < float("inf")):
                    raise ValueError("流控恢复时间必须为有限时间戳")
                remaining = max(0.0, resume_at - time.time())

            coro_factory = await self._rebuild_coro_factory(task_type, task_parameters)
            if not coro_factory:
                raise ValueError(f"无法为任务类型 '{task_type}' 重建执行逻辑")

            if self._recovery_queue_resolver is not None:
                queue_type = self._recovery_queue_resolver(task_type, queue_type)
            queue_map = {
                "download": self._download_queue,
                "management": self._management_queue,
                "fallback": self._fallback_queue,
                "search": self._search_queue,
            }
            if queue_type not in queue_map:
                raise ValueError(f"无效的队列类型: {queue_type}")
            task = Task(
                task_id, task_title, coro_factory,
                scheduled_task_id=task_info.get("scheduledTaskId"),
                unique_key=unique_key, task_type=task_type,
                task_parameters=task_parameters, queue_type=queue_type,
            )
            async with self._lock:
                # 同 ID 重复恢复可直接跳过；不同 ID 的去重冲突必须明确失败。
                existing_tasks = [item[0] for item in self._paused_tasks.values()]
                existing_tasks.extend(self._immediate_tasks.values())
                existing_tasks.extend(self._current_download_tasks.values())
                existing_tasks.extend(self._current_search_tasks.values())
                existing_tasks.extend([self._current_management_task, self._current_fallback_task])
                for queue in queue_map.values():
                    existing_tasks.extend(queue._queue)
                if any(item and item.task_id == task_id for item in existing_tasks):
                    return True
                if unique_key:
                    if unique_key in self._active_unique_keys:
                        raise ValueError("同一资源已有活跃任务，无法重复恢复")
                    self._active_unique_keys.add(unique_key)
                else:
                    if task_title in self._pending_titles or any(
                        item and item.title == task_title for item in existing_tasks
                    ):
                        raise ValueError("同名任务已在队列中或正在运行，无法重复恢复")
                    self._pending_titles.add(task_title)
                reserved = True

            if queue_type != (task_info.get("queueType") or "download"):
                async with self._db.transaction():
                    await self._db.task.update_task_queue_type(task_id, queue_type)
            # 旧版流控没有期限字段，只能依据明确的流控描述重新排队校验。
            description = task_info.get("description") or ""
            legacy_rate_limit = any(word in description for word in ("速率受限", "速率限制", "流控"))
            if remaining > 0:
                await self.pause_task_for_rate_limit(task, remaining, reason=reason)
            elif history_status == TaskStatus.PAUSED and resume_at is None and not legacy_rate_limit:
                # 没有流控期限或明确流控描述的暂停视为用户暂停，不能擅自运行。
                task.pause_event.clear()
                async with self._lock:
                    self._paused_tasks[task_id] = (task, float("inf"))
            else:
                async with self._db.transaction():
                    await self._db.task.update_task_progress_in_history(
                        task_id, TaskStatus.PENDING, None, "服务重启，任务已恢复，等待执行..."
                    )
                    # 清除过期流控元信息，同时保留缓存中的最新任务参数。
                    await self._db.task.save_task_state_cache(
                        task_id, json.dumps(task_parameters, ensure_ascii=False)
                    )
                queue_map[queue_type].put_nowait(task)
            self.logger.info(f"已恢复任务 '{task_title}' (ID: {task_id}, 队列: {queue_type})")
            return True
        except Exception as e:
            if reserved:
                async with self._lock:
                    self._paused_tasks.pop(task_id, None)
                    if unique_key:
                        self._active_unique_keys.discard(unique_key)
                    else:
                        self._pending_titles.discard(task_title)
            if task is not None:
                task.done_event.set()
            self.logger.error(f"任务 '{task_title}' (ID: {task_id}) 恢复失败: {e}", exc_info=True)
            try:
                async with self._db.transaction():
                    await self._db.task.finalize_task_in_history(
                        task_id, TaskStatus.FAILED, f"服务重启，任务恢复失败: {e}"
                    )
            except Exception as finalize_error:
                self.logger.error(f"记录任务 {task_id} 恢复失败终态时出错: {finalize_error}", exc_info=True)
            return False

    async def _rebuild_coro_factory(self, task_type: str, task_parameters: Dict) -> Optional[Callable]:
        """经注入端口恢复工厂，队列服务不解析具体业务参数。"""
        if self._recovery_callback is None:
            return None
        return await self._recovery_callback(task_type, task_parameters)
