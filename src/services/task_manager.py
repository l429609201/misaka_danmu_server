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

# 恢复请求模型在模块顶部导入，不在恢复分支动态加载。
from src.schemas.control import ControlAutoImportRequest, EditImportRequest

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


def _parse_import_unique_key(key: str) -> Optional[Tuple[str, str, Optional[int]]]:
    """从导入类 unique_key 解析出 (provider, media_id, season)。

    支持的格式：
      - import-{provider}-{mediaId}-S{season}-ep{ep}
      - import-{provider}-{mediaId}-{8位hash}
      - ui-import-{provider}-{mediaId}-season-{s}-episode-{e}-{type}
      - url-import-{provider}-{mediaId}-{type}-season-{s} / url-import-{provider}-{mediaId}-{type}

    why：mediaId 可能含连字符，故不能简单 split。先剥离已知前缀，再从尾部
    剥离已知后缀标记（-S{n}-ep{m} / -season-{n}[-...] / 末尾8位hash），
    剩余部分首段为 provider、其余为 mediaId。解析失败返回 None（调用方静默跳过）。
    """
    if not key:
        return None
    # 剥离前缀
    prefix = None
    for p in ("ui-import-", "url-import-", "import-"):
        if key.startswith(p):
            prefix = p
            break
    if prefix is None:
        return None
    body = key[len(prefix):]

    season: Optional[int] = None
    # 剥离 -S{season}-ep{ep} 尾部（webhook 导入）
    m = re.search(r"-S(\d+)-ep\d*$", body)
    if m:
        season = int(m.group(1))
        body = body[:m.start()]
    else:
        # 剥离 -season-{s}[-episode-{e}][-{type}] 尾部（ui-import / url-import）
        m2 = re.search(r"-season-(\d+)(?:-.*)?$", body)
        if m2:
            season = int(m2.group(1))
            body = body[:m2.start()]
        else:
            # 剥离末尾 8 位 hash（编辑导入 import-{provider}-{mediaId}-{hash}）
            m3 = re.search(r"-[0-9a-f]{8}$", body)
            if m3:
                body = body[:m3.start()]
            else:
                # url-import-{provider}-{mediaId}-{type}：剥离末尾已知类型
                m4 = re.search(r"-(movie|tv_series|tv|other)$", body)
                if m4:
                    body = body[:m4.start()]

    parts = body.split("-", 1)
    if len(parts) != 2 or not parts[0] or not parts[1]:
        return None
    provider, media_id = parts[0], parts[1]
    return provider, media_id, season


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

        # 任务恢复所需的依赖，通过 set_recovery_dependencies 方法注入
        self._recovery_dependencies: Optional[Dict[str, Any]] = None
        # 具体任务实现由组合根注册，避免 Service 反向导入 Tasks。
        self._task_handlers: Dict[str, Callable[..., Coroutine]] = {}
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
                - metadata_manager: MetadataService 实例
                - ai_service: AIService 全局实例
                - title_recognition_manager: TitleRecognitionManager 实例
        """
        self._recovery_dependencies = dependencies
        self.logger.info("任务恢复依赖已设置")

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

    def set_notification_service(self, notification_service):
        """设置通知服务引用"""
        self._notification_service = notification_service
        self.logger.info("通知服务已注入 TaskManager")

    def _determine_event_type(self, task: Task, is_success: bool) -> Optional[str]:
        """根据任务的 unique_key 和 title 判断应触发的通知事件类型"""
        key = task.unique_key or ""
        title = task.title or ""
        suffix = "_success" if is_success else "_failed"

        # 删除任务不发通知（必须覆盖所有删除前缀，否则会掉到末尾兜底被误判为 import_success，
        # 导致出现"? 导入成功 / 删除成功"这种标题与内容矛盾的通知）
        if key.startswith((
            "delete-source-",        # 删除单个数据源
            "delete-bulk-sources-",  # 批量删除数据源
            "delete-anime-",         # 删除作品
            "delete-episode-",       # 删除单个分集
            "delete-bulk-episodes-", # 批量删除分集
            "modify-episodes-",      # 集数偏移（管理类操作，非导入，避免误判为"导入成功"）
        )):
            return None

        # 定时任务（有 scheduled_task_id）
        if task.scheduled_task_id:
            return "scheduled_task_complete" if is_success else "scheduled_task_failed"

        # Webhook 导入
        if key.startswith("webhook-search-"):
            return f"webhook_import{suffix}"

        # 数据源刷新（含定时"刷新最新集" refresh-latest- 前缀，否则会掉到末尾兜底被误判为导入）
        if (key.startswith("refresh-episode-") or key.startswith("full-refresh-")
                or key.startswith("bulk-refresh-") or key.startswith("refresh-latest-")):
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
                "后备匹配:": "match_fallback",
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
                async with self._db.transaction():
                    if key.startswith("refresh-episode-"):
                        # refresh-episode-{episodeId}：第三段是 episodeId
                        try:
                            episode_id = int(key.split("-")[2])
                        except (ValueError, IndexError):
                            episode_id = None
                        if episode_id is not None:
                            ep_info = await self._db.episode.get_episode_provider_info(episode_id)
                            if ep_info:
                                db_extra["episode"] = ep_info.get("episodeIndex")
                                # 通过 animeId 反查 Anime 标题/年份/季/海报
                                anime_row = await self._db.anime.get_by_id(ep_info.get("animeId"))
                                if anime_row:
                                    db_extra["anime_title"] = anime_row.title
                                    db_extra["season"] = anime_row.season
                                    db_extra["year"] = anime_row.year
                                    db_extra["image_url"] = (anime_row.imageUrl or "")
                                db_extra["source"] = ep_info.get("providerName", "")
                    elif key.startswith("refresh-latest-"):
                        # refresh-latest-{sourceId}-ep{n}：第三段是 sourceId，ep 后是集号（非 episodeId）。
                        # 按 sourceId 反查作品/源信息，集号从 -ep 后解析。
                        source_id = None
                        ep_index = None
                        try:
                            rest = key[len("refresh-latest-"):]
                            sid_part, _, ep_part = rest.partition("-ep")
                            source_id = int(sid_part)
                            if ep_part:
                                ep_index = int(ep_part)
                        except (ValueError, IndexError):
                            pass
                        if source_id is not None:
                            info = await self._db.source.get_anime_source_info(source_id)
                            if info:
                                db_extra["anime_title"] = info.get("title", "")
                                db_extra["season"] = info.get("season")
                                db_extra["year"] = info.get("year")
                                db_extra["source"] = info.get("providerName", "")
                                db_extra["image_url"] = info.get("imageUrl", "") or ""
                                if ep_index is not None:
                                    db_extra["episode"] = ep_index
                    elif key.startswith("full-refresh-") or key.startswith("bulk-refresh-"):
                        # full-refresh-{anime_id}-xxx：直接查 Anime
                        try:
                            anime_id = int(key.split("-")[2])
                        except (ValueError, IndexError):
                            anime_id = None
                        if anime_id is not None:
                            anime_row = await self._db.anime.get_by_id(anime_id)
                            if anime_row:
                                db_extra["anime_title"] = anime_row.title
                                db_extra["season"] = anime_row.season
                                db_extra["year"] = anime_row.year
                                db_extra["image_url"] = (anime_row.imageUrl or "")
            except Exception:
                pass  # 补查失败不影响通知发出

        # 3. 兜底：task_parameters 带 sourceId 的刷新类任务（如 TG 刷新 tg_refresh、指令刷新），
        #    其 unique_key 无 refresh 前缀，上面补不到，这里按 sourceId 反查作品/源信息。
        if not db_extra.get("anime_title"):
            source_id = (task.task_parameters or {}).get("sourceId")
            if source_id is not None:
                try:
                    async with self._db.transaction():
                        info = await self._db.source.get_anime_source_info(int(source_id))
                        if info:
                            db_extra["anime_title"] = info.get("title", "")
                            db_extra["season"] = info.get("season")
                            db_extra["year"] = info.get("year")
                            db_extra["source"] = info.get("providerName", "")
                            db_extra["tmdb_id"] = info.get("tmdbId", "") or ""
                            if not db_extra.get("image_url"):
                                db_extra["image_url"] = info.get("imageUrl", "") or ""
                except Exception:
                    pass  # 补查失败不影响通知发出

        # 4. 兜底：导入类任务（import-/ui-import-/url-import- 前缀）若 task_parameters
        #    未带 animeTitle（如 direct_import / URL导入 / 旧路径），从 unique_key 解析
        #    provider+mediaId 反查 DB 补齐作品名/季/来源。why：媒体库逐集导入等路径
        #    的微信通知只剩弹幕数，看不出是哪部作品。
        if not db_extra.get("anime_title") and not (task.task_parameters or {}).get("animeTitle"):
            key = task.unique_key or ""
            parsed = _parse_import_unique_key(key)
            if parsed:
                provider, media_id, season_hint = parsed
                try:
                    async with self._db.transaction():
                        anime_id = await self._db.source.get_anime_id_by_source_media_id(
                            provider, media_id, season=season_hint
                        )
                        # season 提示查不到时退化为不带 season 再查一次
                        if anime_id is None and season_hint is not None:
                            anime_id = await self._db.source.get_anime_id_by_source_media_id(
                                provider, media_id
                            )
                        if anime_id is not None:
                            anime_row = await self._db.anime.get_by_id(anime_id)
                            if anime_row:
                                db_extra["anime_title"] = anime_row.title
                                db_extra["season"] = anime_row.season
                                db_extra["year"] = anime_row.year
                                db_extra["media_type"] = anime_row.type
                                if not db_extra.get("image_url"):
                                    db_extra["image_url"] = (anime_row.imageUrl or "")
                            db_extra["source"] = provider
                except Exception:
                    pass  # 补查失败不影响通知发出

        # image_url 优先用任务参数里的，没有再用补查结果
        if not image_url:
            image_url = db_extra.get("image_url", "") or ""

        try:
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
        limiter = (self._recovery_dependencies or {}).get("rate_limiter")
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
        queue_type = getattr(task, "queue_type", "download")
        is_fallback = queue_type == "fallback"
        # fallback 任务按 title 前缀确定对应的订阅 key，与 _determine_event_type 保持一致
        _FALLBACK_PROGRESS_KEY_MAP = {
            "后备搜索:": "fallback_search_complete",
            "预下载弹幕:": "predownload_complete",
            "后备匹配:": "match_fallback_complete",
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
                async with self._db.transaction():
                    await self._db.task.update_task_progress_in_history(
                        task.task_id, status or TaskStatus.RUNNING, int(progress), description
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

            # 自动导入父任务只负责搜索和派发，兼容旧历史中的下载队列标记。
            if task_type == "auto_import" and queue_type == "download":
                queue_type = "search"
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
        # 恢复任务沿用启动时注册的共享 AI 服务。
        ai_service = deps.get("ai_service")
        title_recognition_manager = deps.get("title_recognition_manager")

        try:
            if task_type == "local_danmaku_import":
                # 本地导入只恢复持久化参数，无需抓取器或外部会话快照。
                return self.build_task_coro_factory(
                    "local_danmaku_import",
                    item_ids=task_parameters["item_ids"],
                    import_options=task_parameters.get("import_options", {}),
                )
            if task_type == "generic_import":
                # 恢复与首次派发共用注册处理器，服务层不反向导入任务包。
                return self.build_task_coro_factory(
                    "generic_import",
                    provider=task_parameters.get("provider"),
                    mediaId=task_parameters.get("mediaId"),
                    animeTitle=task_parameters.get("animeTitle"),
                    mediaType=task_parameters.get("mediaType"),
                    season=task_parameters.get("season"),
                    year=task_parameters.get("year"),
                    currentEpisodeIndex=task_parameters.get("currentEpisodeIndex"),
                    imageUrl=task_parameters.get("imageUrl"),
                    config_service=self.config_service,
                    metadata_manager=metadata_manager,
                    manager=scraper_manager,
                    task_manager=self,
                    rate_limiter=rate_limiter,
                    title_recognition_manager=title_recognition_manager,
                    doubanId=task_parameters.get("doubanId"),
                    tmdbId=task_parameters.get("tmdbId"),
                    imdbId=task_parameters.get("imdbId"),
                    tvdbId=task_parameters.get("tvdbId"),
                    bangumiId=task_parameters.get("bangumiId"),
                    # 恢复时保留多集选择，不能降级为全季导入。
                    selectedEpisodes=task_parameters.get("selectedEpisodes"),
                    mediaServerType=task_parameters.get("mediaServerType"),
                    mediaServerSeriesId=task_parameters.get("mediaServerSeriesId"),
                    mediaServerSeasonId=task_parameters.get("mediaServerSeasonId"),
                    mediaServerEpisodeId=task_parameters.get("mediaServerEpisodeId"),
                    fallbackCandidates=task_parameters.get("fallbackCandidates"),
                )

            elif task_type == "webhook_search":
                return self.build_task_coro_factory(
                    "webhook_search",
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
                    manager=scraper_manager,
                    task_manager=self,
                    metadata_manager=metadata_manager,
                    config_service=self.config_service,
                    ai_service=ai_service,
                    rate_limiter=rate_limiter,
                    title_recognition_manager=title_recognition_manager,
                    selectedEpisodes=task_parameters.get("selectedEpisodes"),
                    # 搜索任务恢复后仍需把媒体关联传给下游导入。
                    mediaServerType=task_parameters.get("mediaServerType"),
                    mediaServerSeriesId=task_parameters.get("mediaServerSeriesId"),
                    mediaServerSeasonId=task_parameters.get("mediaServerSeasonId"),
                    mediaServerEpisodeId=task_parameters.get("mediaServerEpisodeId"),
                )

            elif task_type == "full_refresh":
                source_id = task_parameters.get("sourceId")
                if not source_id:
                    return None
                # 恢复分支按名称解析处理器，避免分支间共享局部任务模块。
                return self.build_task_coro_factory(
                    "full_refresh", sourceId=source_id,
                    scraper_manager=scraper_manager, task_manager=self,
                    rate_limiter=rate_limiter, metadata_manager=metadata_manager,
                    config_service=self.config_service,
                )

            elif task_type == "incremental_refresh":
                source_id = task_parameters.get("sourceId")
                next_ep = task_parameters.get("nextEpisodeIndex")
                if not source_id or next_ep is None:
                    return None
                return self.build_task_coro_factory(
                    "incremental_refresh",
                    sourceId=source_id,
                    nextEpisodeIndex=next_ep,
                    manager=scraper_manager,
                    task_manager=self,
                    config_service=self.config_service,
                    rate_limiter=rate_limiter,
                    metadata_manager=metadata_manager,
                    title_recognition_manager=title_recognition_manager,
                    animeTitle=task_parameters.get("animeTitle", ""),
                )

            elif task_type == "auto_import":
                try:
                    payload = ControlAutoImportRequest(**task_parameters)
                except Exception:
                    self.logger.warning("auto_import 任务参数解析失败，无法重建")
                    return None
                return self.build_task_coro_factory(
                    "auto_import", payload=payload,
                    config_service=self.config_service,
                    scraper_manager=scraper_manager,
                    metadata_manager=metadata_manager, task_manager=self,
                    ai_service=ai_service,
                    rate_limiter=rate_limiter,
                    title_recognition_manager=title_recognition_manager,
                )

            elif task_type == "scan_and_import_target":
                provider = task_parameters.get("provider")
                external_id = task_parameters.get("externalId")
                if not provider or not external_id:
                    return None
                return self.build_task_coro_factory(
                    "scan_and_import_target",
                    scraper_manager=scraper_manager,
                    config_service=self.config_service,
                    provider=provider,
                    external_id=external_id,
                    title_recognition_manager=title_recognition_manager,
                    selected_episodes=task_parameters.get("selectedEpisodes"),
                )

            elif task_type in {"bangumiDataSync", "bangumiDataClear"}:
                if not task_parameters.get("manual"):
                    return None
                return self.build_task_coro_factory(task_type)

            elif task_type == "media_scan":
                server_id = task_parameters.get("serverId")
                if server_id is None:
                    return None
                return self.build_task_coro_factory(
                    "media_scan", server_id=server_id,
                    library_ids=task_parameters.get("libraryIds"),
                )

            elif task_type == "import_media_items":
                item_ids = task_parameters.get("itemIds")
                if not item_ids:
                    return None
                return self.build_task_coro_factory(
                    "import_media_items", item_ids=item_ids,
                    task_manager=self, scraper_manager=scraper_manager,
                    metadata_manager=metadata_manager,
                    config_service=self.config_service, ai_service=ai_service,
                    rate_limiter=rate_limiter,
                    title_recognition_manager=title_recognition_manager,
                )

            elif task_type == "import_all_unimported":
                # 未导入清单由任务实时重新计算，不依赖重启前的内存状态。
                server_id = task_parameters.get("serverId")
                if not server_id:
                    return None
                return self.build_task_coro_factory(
                    "import_all_unimported",
                    server_id=server_id,
                    media_type=task_parameters.get("mediaType"),
                    task_manager=self,
                    scraper_manager=scraper_manager,
                    metadata_manager=metadata_manager,
                    config_service=self.config_service,
                    ai_service=ai_service,
                    rate_limiter=rate_limiter,
                    title_recognition_manager=title_recognition_manager,
                )

            elif task_type == "manual_import":
                # XML/URL 手动导入（阶段4补齐：恢复完整性）
                source_id = task_parameters.get("sourceId")
                episode_index = task_parameters.get("episodeIndex")
                provider_name = task_parameters.get("providerName")

                if not all([source_id, episode_index, provider_name]):
                    self.logger.warning(f"manual_import 恢复失败：缺少必要参数 (sourceId/episodeIndex/providerName)")
                    return None

                # 从 source_id 反查 anime_id, title, content
                # why: 手动导入的 content（XML或URL）无法持久化到 task_parameters（可能很大），
                # 恢复时只能跳过该任务，提示用户重新提交
                self.logger.warning(
                    f"manual_import 任务无法自动恢复（content 未序列化），"
                    f"sourceId={source_id}, episodeIndex={episode_index}, providerName={provider_name}"
                )
                return None

            elif task_type == "edited_import":
                # 使用任务实际接收的编辑导入模型，避免恢复时导入不存在的名称。
                try:
                    request_data = EditImportRequest(**task_parameters)
                except Exception as e:
                    self.logger.error(f"edited_import 恢复失败：无法解析 task_parameters: {e}")
                    return None

                return self.build_task_coro_factory(
                    "edited_import",
                    request_data=request_data,
                    config_service=self.config_service,
                    manager=scraper_manager,
                    rate_limiter=rate_limiter,
                    title_recognition_manager=title_recognition_manager,
                )

            elif task_type == "download_comments":
                # 后备弹幕下载（阶段4补齐：无法恢复）
                # why: 该任务使用闭包，捕获外层运行时对象（scraper/rate_limiter/episodeId 等），
                # 这些对象无法序列化到 task_parameters，重启后无法恢复。
                # 用户可通过弹幕接口重新触发后备搜索。
                self.logger.warning(
                    f"download_comments 任务无法自动恢复（闭包依赖运行时对象），"
                    f"task_parameters={task_parameters}"
                )
                return None

            elif task_type == "match_fallback_download":
                # 匹配后备下载（B类·冷启动）——阶段5：闭包已抽取为独立可恢复任务
                # why: 参数全部可序列化存于 task_parameters，依赖（scraper/rate_limiter/config_service）
                # 在此从恢复依赖 + self.config_service 重新注入，彻底解决原闭包无法恢复的问题。
                episode_id = task_parameters.get("episodeId")
                if not episode_id:
                    self.logger.warning("match_fallback_download 恢复失败：缺少 episodeId")
                    return None
                return self.build_task_coro_factory(
                    "match_fallback_download",
                    episodeId=episode_id,
                    real_anime_id=task_parameters.get("real_anime_id"),
                    provider=task_parameters.get("provider"),
                    mediaId=task_parameters.get("mediaId"),
                    episode_number=task_parameters.get("episode_number"),
                    episode_title=task_parameters.get("episode_title"),
                    episode_url=task_parameters.get("episode_url"),
                    provider_episode_id=task_parameters.get("provider_episode_id"),
                    final_title=task_parameters.get("final_title"),
                    display_title=task_parameters.get("display_title"),
                    final_season=task_parameters.get("final_season"),
                    media_type=task_parameters.get("media_type"),
                    imageUrl=task_parameters.get("imageUrl"),
                    year=task_parameters.get("year"),
                    total_episodes=task_parameters.get("total_episodes"),
                    fallback_episode_cache_key=task_parameters.get("fallback_episode_cache_key"),
                    scraper_manager=scraper_manager,
                    rate_limiter=rate_limiter,
                    config_service=self.config_service,
                )

            else:
                self.logger.warning(f"未知的任务类型 '{task_type}'，无法重建协程工厂")
                return None

        except Exception as e:
            self.logger.error(f"重建任务类型 '{task_type}' 的协程工厂时发生错误: {e}", exc_info=True)
            return None