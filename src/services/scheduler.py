import asyncio
import inspect
import logging
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional, Sequence, Type
from uuid import uuid4

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from apscheduler.events import EVENT_JOB_EXECUTED, EVENT_JOB_ERROR, JobExecutionEvent
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from src.services.ai_service import AIService
from src.core import get_app_timezone
# 任务基类与所有具体作业由生命周期组装层注入。
from src.rate_limiter import RateLimiter
from .database_service import DatabaseService
from .task_manager import TaskManager
from .scraper_manager import ScraperManager
from .metadata_service import MetadataService

logger = logging.getLogger(__name__)

# --- Scheduler Manager ---

class SchedulerManager:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession], task_manager: TaskManager, scraper_manager: ScraperManager, rate_limiter: RateLimiter, metadata_manager: MetadataService, config_service, ai_service: AIService, title_recognition_manager=None, *, job_base_class: Type[Any], job_classes: Sequence[Type[Any]], database_service: DatabaseService) -> None:
        self._session_factory = session_factory
        self._db = database_service
        self.task_manager = task_manager
        self.scraper_manager = scraper_manager
        self.rate_limiter = rate_limiter
        self.metadata_manager = metadata_manager
        self.config_service = config_service
        self.ai_service = ai_service
        self.title_recognition_manager = title_recognition_manager
        self.scheduler = AsyncIOScheduler(timezone=str(get_app_timezone()))
        self._job_classes: Dict[str, Type[Any]] = {}
        for job_class in job_classes:
            if not isinstance(job_class, type) or not issubclass(job_class, job_base_class) or job_class is job_base_class:
                raise TypeError(f"无效的定时任务类: {job_class!r}")
            if not job_class.job_type or job_class.job_type in self._job_classes:
                raise ValueError(f"重复或为空的定时任务类型: {job_class.job_type!r}")
            self._job_classes[job_class.job_type] = job_class

    def _log_registered_jobs(self) -> None:
        """记录组装层显式注入的可调度作业。"""
        _P = "  - "
        log_lines = [f"已注册 {len(self._job_classes)} 个定时任务类型"]
        for job_type in sorted(self._job_classes):
            log_lines.append(f"{_P}{job_type}")
        logger.info("\n".join(log_lines))

    def get_available_jobs(self) -> List[Dict[str, str]]:
        """获取所有已加载的可用任务类型及其名称、描述。"""
        return [
            {
                "jobType": job.job_type,
                "name": job.job_name,
                "name_en": getattr(job, 'job_name_en', ''),
                "name_tw": getattr(job, 'job_name_tw', ''),
                "description": getattr(job, 'description', ''),
                "description_en": getattr(job, 'description_en', ''),
                "description_tw": getattr(job, 'description_tw', ''),
                "isSystemTask": getattr(job, 'is_system_task', False),
                "configSchema": getattr(job, 'config_schema', [])
            }
            for job in self._job_classes.values()
        ]

    def _create_job_runner(self, job_type: str, scheduled_task_id: str) -> Callable:
        """创建一个包装器，用于在 TaskManager 中运行任务，并等待其完成。"""
        job_class = self._job_classes[job_type]

        async def runner():
            # 从数据库读取任务实例级配置（taskConfig JSON）
            task_config = {}
            async with self._db.transaction():
                task_info = await self._db.scheduled_task.get_scheduled_task(scheduled_task_id)
                if task_info:
                    task_config = task_info.get('taskConfig', {})
            logger.info(f"定时任务 '{scheduled_task_id}' (类型: {job_type}) 读取到 taskConfig: {task_config}")

            # 修正：智能地将依赖项传递给任务的构造函数
            # 这使得像 TmdbAutoMapJob 这样的任务可以选择不接收 rate_limiter
            init_params = inspect.signature(job_class.__init__).parameters

            dependencies = {
                "session_factory": self._session_factory,
                "task_manager": self.task_manager,
                "scraper_manager": self.scraper_manager,
                "rate_limiter": self.rate_limiter,
                "metadata_manager": self.metadata_manager,
                "config_service": self.config_service,
                "ai_service": self.ai_service,
                "title_recognition_manager": self.title_recognition_manager,
            }

            args_to_pass = {name: dep for name, dep in dependencies.items() if name in init_params}
            job_instance = job_class(**args_to_pass)

            # 检查 run 方法是否接受 task_config 参数
            run_params = inspect.signature(job_instance.run).parameters
            def make_task_coro(cfg):
                if 'task_config' in run_params:
                    return lambda session, callback: job_instance.run(session, callback, task_config=cfg)
                else:
                    return lambda session, callback: job_instance.run(session, callback)

            task_coro_factory = make_task_coro(task_config)
            task_id, _ = await self.task_manager.submit_task(
                task_coro_factory,
                job_instance.job_name,
                scheduled_task_id=scheduled_task_id,
                queue_type="management",  # 定时任务使用管理队列
                run_immediately=True  # 立即执行，不排队等待
            )
            # 提交到 TaskManager 后立即返回，由 TaskManager 异步执行
            # 不等待 done_event，避免阻塞 APScheduler / 事件循环 / 前端请求
            logger.info(f"定时任务 '{job_instance.job_name}' (ID: {task_id}) 已提交到 TaskManager，将异步执行。")

        return runner

    def _event_handler_wrapper(self, event: JobExecutionEvent):
        """
        一个同步的包装器，用于调度异步的事件处理器。
        这是为了解决 'coroutine was never awaited' 的 RuntimeWarning，
        确保我们的异步逻辑能被正确执行。
        """
        # 将真正的异步处理函数作为一个新任务在事件循环中运行
        asyncio.create_task(self._handle_job_event(event))

    async def _handle_job_event(self, event: JobExecutionEvent):
        job = self.scheduler.get_job(event.job_id)
        if job:
            # 修正：使用 event.scheduled_run_time 作为 last_run_at 时间。
            # 这比 job.last_run_time 更可靠，因为它直接来自刚刚发生的事件，
            # 并且能确保手动触发的任务也能正确记录运行时间。并将其转换为 naive datetime。
            last_run_time = event.scheduled_run_time.replace(tzinfo=None) if event.scheduled_run_time else None
            next_run_time = job.next_run_time.replace(tzinfo=None) if job.next_run_time else None
            async with self._db.transaction():
                await self._db.scheduled_task.update_scheduled_task_run_times(job.id, last_run_time, next_run_time)
            logger.info(f"已更新定时任务 '{job.name}' (ID: {job.id}) 的运行时间。")

    async def start(self):
        self._log_registered_jobs()
        # 修正：使用同步的包装器作为监听器
        self.scheduler.add_listener(self._event_handler_wrapper, EVENT_JOB_EXECUTED | EVENT_JOB_ERROR)
        self.scheduler.start()
        await self.load_jobs_from_db()
        logger.info("定时任务调度器已启动。")

    async def stop(self):
        self.scheduler.shutdown()

    async def load_jobs_from_db(self):
        async with self._db.transaction():
            tasks = await self._db.scheduled_task.get_scheduled_tasks()
            for task in tasks:
                if task['jobType'] in self._job_classes:
                    try:
                        runner = self._create_job_runner(task['jobType'], task['taskId'])
                        job = self.scheduler.add_job(runner, CronTrigger.from_crontab(task['cronExpression']), id=task['taskId'], name=task['name'], replace_existing=True)
                        if not task['isEnabled']: self.scheduler.pause_job(task['taskId'])
                        next_run_time = job.next_run_time.replace(tzinfo=None) if job.next_run_time else None
                        await self._db.scheduled_task.update_scheduled_task_run_times(job.id, task['lastRunAt'], next_run_time)
                    except Exception as e:
                        logger.error(f"加载定时任务 '{task['name']}' (ID: {task['taskId']}) 失败: {e}")

    async def get_all_tasks(self) -> List[Dict[str, Any]]:
        """从数据库获取所有定时任务的列表。"""
        async with self._db.transaction():
            return await self._db.scheduled_task.get_scheduled_tasks()

    async def add_task(self, name: str, job_type: str, cron: str, is_enabled: bool, task_config: dict = None) -> Dict[str, Any]:
        if job_type not in self._job_classes:
            raise ValueError(f"未知的任务类型: {job_type}")
        
        async with self._db.transaction():
            task_id = str(uuid4())
            await self._db.scheduled_task.create_scheduled_task(task_id, name, job_type, cron, is_enabled, task_config)
            runner = self._create_job_runner(job_type, task_id)
            job = self.scheduler.add_job(runner, CronTrigger.from_crontab(cron), id=task_id, name=name)
            if not is_enabled: job.pause()
            next_run_time = job.next_run_time.replace(tzinfo=None) if job.next_run_time else None
            await self._db.scheduled_task.update_scheduled_task_run_times(task_id, None, next_run_time)
            return await self._db.scheduled_task.get_scheduled_task(task_id)

    async def update_task(self, task_id: str, name: str, cron: str, is_enabled: bool, task_config: dict = None) -> Optional[Dict[str, Any]]:
        async with self._db.transaction():
            task_info = await self._db.scheduled_task.get_scheduled_task(task_id)
            if not task_info: return None

            # 获取APScheduler中的job对象
            job = self.scheduler.get_job(task_id)
            if job:
                job.modify(name=name)
                job.reschedule(trigger=CronTrigger.from_crontab(cron))
                if is_enabled:
                    job.resume()
                else:
                    job.pause()
                next_run_time = job.next_run_time.replace(tzinfo=None) if job.next_run_time else None
            else:
                next_run_time = None

            await self._db.scheduled_task.update_scheduled_task(task_id, name, cron, is_enabled, task_config)
            await self._db.scheduled_task.update_scheduled_task_run_times(task_id, task_info['lastRunAt'], next_run_time)
            return await self._db.scheduled_task.get_scheduled_task(task_id)

    async def delete_task(self, task_id: str) -> bool:
        async with self._db.transaction():
            task_info = await self._db.scheduled_task.get_scheduled_task(task_id)
            if not task_info: return False

            if self.scheduler.get_job(task_id): self.scheduler.remove_job(task_id)
            await self._db.scheduled_task.delete_scheduled_task(task_id)
            return True

    async def run_task_now(self, task_id: str):
        """立即运行指定的定时任务"""
        async with self._db.transaction():
            task_info = await self._db.scheduled_task.get_scheduled_task(task_id)
            if not task_info:
                raise ValueError(f"找不到ID为 '{task_id}' 的定时任务")

        job = self.scheduler.get_job(task_id)
        if not job:
            raise ValueError(f"调度器中找不到ID为 '{task_id}' 的任务")

        # 修正：直接使用数据库中的 jobType，而不是从 job.func.keywords 获取
        runner = self._create_job_runner(task_info['jobType'], task_id)
        await runner()

        # 手动运行后更新运行时间
        last_run_time = datetime.now()
        next_run_time = job.next_run_time.replace(tzinfo=None) if job.next_run_time else None
        async with self._db.transaction():
            await self._db.scheduled_task.update_scheduled_task_run_times(task_id, last_run_time, next_run_time)
        logger.info(f"手动运行定时任务 '{task_info['name']}' (ID: {task_id}) 后已更新运行时间。")

    async def run_task_now_by_type(self, job_type: str):
        """根据任务类型查找任务并立即运行它。"""
        async with self._db.transaction():
            task_id = await self._db.scheduled_task.get_scheduled_task_id_by_type(job_type)

        if not task_id:
            raise ValueError(f"找不到类型为 '{job_type}' 的定时任务")

        await self.run_task_now(task_id)



