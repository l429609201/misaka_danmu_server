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
        # run_immediately=True 的任务不经过队列 worker，单独注册以支持暂停/终止
        self._immediate_tasks: Dict[str, Task] = {}  # {task_id: Task}
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
        # 关闭标志位：优雅关闭时设为 True，区分「程序关闭」和「用户主动取消」
        self._is_shutting_down: bool = False

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

        # 后备下载/搜索任务 —— 按 title 前缀细分，与 _get_progress_callback 的
        # _FALLBACK_PROGRESS_KEY_MAP 保持一致，确保进度和完成通知使用相同的订阅 key
        if getattr(task, "queue_type", "") == "fallback":
            _FALLBACK_EVENT_MAP = {
                "后备搜索:": "fallback_search",
                "预下载弹幕:": "predownload",
                "匹配后备:": "match_fallback",
            }
            prefix = next(
                (v for k, v in _FALLBACK_EVENT_MAP.items() if title.startswith(k)),
                "download_fallback",  # 兜底
            )
            return f"{prefix}{suffix}"

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
            # 即使不需要发通知，也要清理进度消息缓存
            self._notification_service.cleanup_task_progress(task.task_id)
            return

        # 1. 优先从 task_parameters 取 imageUrl（import/auto_import 任务已有）
        image_url: str = (task.task_parameters or {}).get("imageUrl", "") or ""

        # 2. 刷新类任务 task_parameters 通常缺标题/集数/年份/海报，从数据库补查。
        #    db_extra 收集补查到的字段，稍后仅用于填补 payload 中为空的项（不覆盖已有值）。
        db_extra: Dict[str, Any] = {}
        if task.unique_key:
            key = task.unique_key
            try:
                async with self._session_factory() as session:
                    if key.startswith("refresh-episode-") or key.startswith("refresh-latest-"):
                        # 这两类 key 第三段是 episodeId（refresh-episode-{episodeId} /
                        # refresh-latest-{source_id}-ep{n} 取 source 走另一分支，这里仅处理 episodeId 形态）
                        # refresh-episode-{episodeId}
                        try:
                            episode_id = int(key.split("-")[2].split("ep")[-1]) if key.startswith("refresh-latest-") else int(key.split("-")[2])
                        except (ValueError, IndexError):
                            episode_id = None
                        if episode_id is not None:
                            ep_info = await crud.get_episode_provider_info(session, episode_id)
                            if ep_info:
                                db_extra["episode"] = ep_info.get("episodeIndex")
                                # 通过 animeId 反查 Anime 标题/年份/季/海报
                                from src.db.orm_models import Anime as AnimeORM
                                anime_row = await session.get(AnimeORM, ep_info.get("animeId"))
                                if anime_row:
                                    db_extra["anime_title"] = anime_row.title
                                    db_extra["season"] = anime_row.season
                                    db_extra["year"] = anime_row.year
                                    db_extra["image_url"] = (anime_row.imageUrl or "")
                                db_extra["source"] = ep_info.get("providerName", "")
                    elif key.startswith("full-refresh-") or key.startswith("bulk-refresh-"):
                        # full-refresh-{anime_id}-xxx：直接查 Anime
                        from src.db.orm_models import Anime as AnimeORM
                        try:
                            anime_id = int(key.split("-")[2])
                        except (ValueError, IndexError):
                            anime_id = None
                        if anime_id is not None:
                            anime_row = await session.get(AnimeORM, anime_id)
                            if anime_row:
                                db_extra["anime_title"] = anime_row.title
                                db_extra["season"] = anime_row.season
                                db_extra["year"] = anime_row.year
                                db_extra["image_url"] = (anime_row.imageUrl or "")
            except Exception:
                pass  # 补查失败不影响通知发出

        # image_url 优先用任务参数里的，没有再用补查结果
        if not image_url:
            image_url = db_extra.get("image_url", "") or ""

        try:
            import datetime
            params = task.task_parameters or {}
            # 提取任务参数中的上下文字段，供通知格式化使用
            extra = {
                "search_term": params.get("searchTerm", ""),
                "search_type": str(params.get("searchType", "")).replace("AutoImportSearchType.", "").lower(),
                "season": params.get("season"),
                "episode": params.get("episode"),
                "anime_title": params.get("animeTitle", "") or params.get("anime_title", ""),
                "episode_count": params.get("episodeCount"),
                "webhook_source": params.get("webhookSource", ""),
                "provider": params.get("provider", "") or params.get("providerName", ""),
                # source 字段：新消息类导入模板读取 source 展示"来源/弹幕源"，映射自 provider
                "source": params.get("provider", "") or params.get("providerName", ""),
                "media_id": params.get("mediaId", "") or params.get("media_id", ""),
                "tmdb_id": params.get("tmdbId", ""),
                "media_type": params.get("type", "") or params.get("mediaType", ""),
                "year": params.get("year"),
                "finished_at": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            }
            # 用数据库补查结果填补 extra 中为空的字段（不覆盖任务参数已有的值）。
            # 这样刷新类任务也能在通知里显示标题/集数/年份/季/海报。
            for _k, _v in db_extra.items():
                if _v in (None, "") :
                    continue
                if extra.get(_k) in (None, "", 0):
                    extra[_k] = _v
            await self._notification_service.emit_event(event_type, {
                "task_title": task.title,
                "message": message,
                "task_id": task.task_id,
                "unique_key": task.unique_key or "",
                "image_url": image_url,
                "task_parameters": params,
                **extra,
            })
        except Exception as e:
            self.logger.error(f"发射通知事件 {event_type} 失败: {e}")

    async def _safe_finalize_task(self, task_id: str, status, message: str):
        """安全地写入任务最终状态。

        DB 暂时不可用时只记录 WARNING，不向上传播异常，
        避免在 except/finally 清理路径中引发二次异常。
        """
        try:
            async with self._session_factory() as session:
                await crud.finalize_task_in_history(session, task_id, status, message)
        except Exception as e:
            self.logger.warning(
                f"⚠️ 任务 {task_id} 最终状态（{status}）未能写入DB"
                f"（DB 可能暂时不可用，重启后将由中断恢复机制处理）: {e}"
            )

    async def _safe_update_task_status(self, task_id: str, status, progress, message: str):
        """安全地更新任务进度状态。

        DB 暂时不可用时只记录 WARNING，不向上传播异常。
        """
        try:
            async with self._session_factory() as session:
                await crud.update_task_progress_in_history(session, task_id, status, progress, message)
        except Exception as e:
            self.logger.warning(
                f"⚠️ 任务 {task_id} 进度状态（{status}）未能写入DB"
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
            for t in (self._current_download_task, self._current_management_task, self._current_fallback_task):
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
        # 延迟导入避免循环依赖（rate_limiter → src.services → task_manager）
        from src.rate_limiter import RateLimitExceededError
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

            await self._safe_update_task_status(
                task.task_id, TaskStatus.PAUSED,
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
            await self._safe_finalize_task(task.task_id, TaskStatus.COMPLETED, final_message)
            self.logger.info(f"任务 '{task.title}' (ID: {task.task_id}) 已成功完成，消息: {final_message}")
            await self._emit_task_event(task, True, final_message)
        except asyncio.CancelledError:
            if self._is_shutting_down:
                # 程序优雅关闭导致的取消，不修改数据库状态
                # 保留「运行中」状态，重启后 _handle_interrupted_tasks 会自动恢复该任务
                self.logger.info(f"程序关闭，任务 '{task.title}' (ID: {task.task_id}) 将在重启后自动恢复")
                task.done_event.set()
                return
            else:
                # 用户主动取消（abort_current_task 触发）
                self.logger.info(f"任务 '{task.title}' (ID: {task.task_id}) 已被用户取消。")
                await self._safe_finalize_task(task.task_id, TaskStatus.FAILED, "任务已被用户取消")
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
            await self._safe_update_task_status(
                task.task_id, TaskStatus.PAUSED, None,
                f"速率受限，将在 {e.retry_after_seconds:.0f} 秒后自动重试..."
            )
            await self.pause_task_for_rate_limit(task, e.retry_after_seconds)
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
                if task.unique_key:
                    self._active_unique_keys.discard(task.unique_key)
                # Also remove from pending_titles again just in case of race conditions.
                self._pending_titles.discard(task.title)
            task.done_event.set()

    async def stop(self):
        """停止任务管理器。"""
        # 标记正在关闭，使运行中任务因 CancelledError 中断时不覆盖数据库状态
        # 这样重启后 _handle_interrupted_tasks 能找到这些任务并恢复
        self._is_shutting_down = True

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
        queue_type = getattr(task, "queue_type", "download")
        is_fallback = queue_type == "fallback"
        # fallback 任务按 title 前缀确定对应的订阅 key，与 _determine_event_type 保持一致
        _FALLBACK_PROGRESS_KEY_MAP = {
            "后备搜索:": "fallback_search_complete",
            "预下载弹幕:": "predownload_complete",
            "匹配后备:": "match_fallback_complete",
        }
        if is_fallback:
            title = task.title or ""
            progress_check_key = next(
                (v for k, v in _FALLBACK_PROGRESS_KEY_MAP.items() if title.startswith(k)),
                "fallback_search_complete",  # 兜底
            )
        else:
            progress_check_key = "task_progress"

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

            # 进度未完成时触发 TG 进度通知（TG 会 edit 已有消息，其他渠道跳过进度推送）
            # 加超时保护：通知推送不应阻塞任务执行（TG 代理不可达时可能卡住）
            if self._notification_service and progress < 100:
                try:
                    await asyncio.wait_for(
                        self._notification_service.emit_task_progress(
                            task_id=task.task_id,
                            task_title=task.title,
                            progress=int(progress),
                            description=description,
                            check_event_key=progress_check_key,
                        ),
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

        # 检查 run_immediately 的立即执行任务
        imm_task = self._immediate_tasks.get(task_id)
        if imm_task:
            async with self._session_factory() as session:
                imm_task.pause_event.clear()
                await crud.update_task_status(session, task_id, TaskStatus.PAUSED)
                self.logger.info(f"已暂停立即执行任务 '{imm_task.title}' (ID: {task_id})。")
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

        # 检查 run_immediately 的立即执行任务
        imm_task = self._immediate_tasks.get(task_id)
        if imm_task:
            async with self._session_factory() as session:
                imm_task.pause_event.set()
                await crud.update_task_status(session, task_id, TaskStatus.RUNNING)
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
        """处理服务重启时中断的任务（运行中、已暂停、排队中）"""
        try:
            async with self._session_factory() as session:
                # 1. 处理运行中 + 已暂停的任务（这些任务有 TaskStateCache 记录）
                #    两种状态在重启后都需要重新提交到队列执行
                active_tasks = await crud.get_all_running_task_states(session)

                if active_tasks:
                    running_count = sum(1 for t in active_tasks if t.get("historyStatus") == "运行中")
                    paused_count = sum(1 for t in active_tasks if t.get("historyStatus") == "已暂停")
                    self.logger.info(
                        f"发现 {len(active_tasks)} 个中断任务（运行中: {running_count} 个, 已暂停: {paused_count} 个），正在尝试恢复..."
                    )
                    active_recovered = 0
                    for task_info in active_tasks:
                        if await self._try_recover_task(task_info):
                            active_recovered += 1

                    self.logger.info(f"中断任务处理完成: {active_recovered} 个已恢复重新入队，{len(active_tasks) - active_recovered} 个无法恢复")

                # 始终清理所有残留的"运行中/已暂停"状态的任务（包括无 TaskStateCache 的定时任务）
                # 必须在 active_tasks 条件块外执行，否则没有 TaskStateCache 记录的
                # run_immediately 定时任务会永远卡在"运行中"状态
                interrupted_count = await crud.mark_interrupted_tasks_as_failed(session)
                if interrupted_count > 0:
                    self.logger.info(f"已将 {interrupted_count} 个中断的运行中/已暂停任务标记为失败")

                # 2. 处理排队中的任务（这些任务直接从 TaskHistory 表恢复，无需 TaskStateCache）
                pending_tasks = await crud.get_pending_recoverable_tasks(session)

                if pending_tasks:
                    self.logger.info(f"发现 {len(pending_tasks)} 个排队中的任务，正在尝试恢复...")
                    recovered_count = 0
                    failed_count = 0

                    for task_info in pending_tasks:
                        if await self._try_recover_task(task_info):
                            recovered_count += 1
                            # 将原记录标记为"已取消"，防止下次重启再次查到并重复恢复（翻倍）
                            # 新任务已用新 ID 加入队列，原记录不再需要
                            await crud.update_task_status(session, task_info["taskId"], "已取消")
                        else:
                            failed_count += 1
                            # 将无法恢复的任务标记为失败
                            await crud.update_task_status(session, task_info["taskId"], "失败")

                    self.logger.info(f"排队中的任务处理完成: {recovered_count} 个已恢复, {failed_count} 个无法恢复")

                # 3. 清理剩余的排队中任务（无 taskType 的任务，无法恢复，标记为失败）
                unrecoverable_count = await crud.mark_unrecoverable_pending_tasks_as_failed(session)
                if unrecoverable_count > 0:
                    self.logger.info(f"已将 {unrecoverable_count} 个无法恢复的排队中任务标记为失败（缺少任务类型信息）")

                if not active_tasks and not pending_tasks and not unrecoverable_count:
                    self.logger.info("没有发现需要处理的中断任务")

        except Exception as e:
            self.logger.error(f"处理中断任务时发生错误: {e}", exc_info=True)

    async def _try_recover_task(self, task_info: Dict) -> bool:
        """尝试恢复单个中断任务（运行中、已暂停或排队中）

        Args:
            task_info: 任务信息字典，包含 taskId、taskType、taskParameters 等

        Returns:
            bool: 恢复成功返回True，失败返回False
        """
        task_id = task_info["taskId"]
        task_type = task_info.get("taskType")
        task_title = task_info.get("taskTitle", "未知任务")
        unique_key = task_info.get("uniqueKey")
        queue_type = task_info.get("queueType", "download")
        history_status = task_info.get("historyStatus", "未知")

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

            # 统一尝试重建协程工厂并重新提交（运行中、已暂停、排队中均使用相同恢复逻辑）
            coro_factory = await self._rebuild_coro_factory(task_type, task_parameters)
            if not coro_factory:
                self.logger.warning(f"无法为任务类型 '{task_type}' 重建协程工厂，任务 '{task_title}' (ID: {task_id}) [{history_status}] 恢复失败")
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
            self.logger.info(f"成功恢复任务 '{task_title}' [{history_status}]，新任务ID: {new_task_id}")
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
                from src import tasks

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
                from src.tasks import webhook as webhook_tasks

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
                from src import tasks as all_tasks
                source_id = task_parameters.get("sourceId")
                if not source_id:
                    return None
                return lambda session, callback: all_tasks.full_refresh_task(
                    source_id, session, scraper_manager, self, rate_limiter,
                    callback, metadata_manager, self.config_manager,
                )

            elif task_type == "incremental_refresh":
                from src import tasks as all_tasks
                source_id = task_parameters.get("sourceId")
                next_ep = task_parameters.get("nextEpisodeIndex")
                if not source_id or next_ep is None:
                    return None
                anime_title = task_parameters.get("animeTitle", "")
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
                    animeTitle=anime_title,
                )

            elif task_type == "auto_import":
                from src import tasks as all_tasks
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