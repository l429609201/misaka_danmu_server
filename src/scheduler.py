import asyncio
import importlib
import inspect
import logging
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Type
from uuid import uuid4

import aiomysql
from apscheduler.events import EVENT_JOB_EXECUTED, EVENT_JOB_ERROR, JobExecutionEvent
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from . import crud
from .jobs.base import BaseJob
from .task_manager import TaskManager
from .scraper_manager import ScraperManager

logger = logging.getLogger(__name__)

def cron_is_valid(cron: str, min_hours: int) -> bool:
    """
    一个简单的CRON表达式验证器，用于检查轮询间隔是否满足最小小时数。
    注意：这是一个非常简化的检查，只处理常见的 '*/X' 小时格式。
    """
    try:
        parts = cron.split()
        if len(parts) != 5:
            # 不支持带秒或年的格式，但允许它通过，因为这可能是高级用法
            return True

        hour_part = parts[1]
        
        # Case 1: '*/X' - every X hours
        if hour_part.startswith('*/'):
            interval = int(hour_part[2:])
            if interval < min_hours:
                return False
        elif hour_part == '*': # every hour
            return False
    except (ValueError, IndexError): return False
    return True

# --- Scheduler Manager ---

class SchedulerManager:
    def __init__(self, pool: aiomysql.Pool, task_manager: TaskManager, scraper_manager: ScraperManager):
        self.pool = pool
        self.task_manager = task_manager
        self.scraper_manager = scraper_manager
        self.scheduler = AsyncIOScheduler(timezone="Asia/Shanghai")
        self._job_classes: Dict[str, Type[BaseJob]] = {}

    def _load_jobs(self):
        """
        动态发现并加载 'jobs' 目录下的所有任务类。
        """
        jobs_dir = Path(__file__).parent / "jobs"
        for file in jobs_dir.glob("*.py"):
            if file.name.startswith("_") or file.name == "base.py":
                continue

            module_name = f".jobs.{file.stem}"
            try:
                module = importlib.import_module(module_name, package="src")
                for name, obj in inspect.getmembers(module, inspect.isclass):
                    if issubclass(obj, BaseJob) and obj is not BaseJob:
                        if obj.job_type in self._job_classes:
                            logger.warning(f"发现重复的定时任务类型 '{obj.job_type}'。将被覆盖。")
                        self._job_classes[obj.job_type] = obj
                        logger.info(f"定时任务 '{obj.job_name}' (类型: {obj.job_type}) 已加载。")
            except Exception as e:
                logger.error(f"从 {file.name} 加载定时任务失败: {e}")

    def get_available_jobs(self) -> List[Dict[str, str]]:
        """获取所有已加载的可用任务类型及其名称。"""
        return [{"type": job.job_type, "name": job.job_name} for job in self._job_classes.values()]

    def _create_job_runner(self, job_type: str) -> Callable:
        """创建一个包装器，用于在 TaskManager 中运行任务，并等待其完成。"""
        job_class = self._job_classes[job_type]
        
        async def runner():
            job_instance = job_class(self.pool, self.task_manager, self.scraper_manager)
            task_coro_factory = lambda callback: job_instance.run(callback)
            task_id, done_event = await self.task_manager.submit_task(task_coro_factory, job_instance.job_name)
            # The apscheduler job now waits for the actual task to complete.
            await done_event.wait()
            logger.info(f"定时任务的运行器已确认任务 '{job_instance.job_name}' (ID: {task_id}) 执行完毕。")
        
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
            # 并且能确保手动触发的任务也能正确记录运行时间。
            last_run_time = event.scheduled_run_time
            await crud.update_scheduled_task_run_times(self.pool, job.id, last_run_time, job.next_run_time)
            logger.info(f"已更新定时任务 '{job.name}' (ID: {job.id}) 的运行时间。")

    async def start(self):
        self._load_jobs()
        # 修正：使用同步的包装器作为监听器
        self.scheduler.add_listener(self._event_handler_wrapper, EVENT_JOB_EXECUTED | EVENT_JOB_ERROR)
        self.scheduler.start()
        await self.load_jobs_from_db()
        logger.info("定时任务调度器已启动。")

    async def stop(self):
        self.scheduler.shutdown()

    async def load_jobs_from_db(self):
        tasks = await crud.get_scheduled_tasks(self.pool)
        for task in tasks:
            if task['job_type'] in self._job_classes:
                try:
                    runner = self._create_job_runner(task['job_type'])
                    job = self.scheduler.add_job(runner, CronTrigger.from_crontab(task['cron_expression']), id=task['id'], name=task['name'], replace_existing=True)
                    if not task['is_enabled']: self.scheduler.pause_job(task['id'])
                    # When loading, the job object is new and has no last_run_time. We only need to update the next_run_time.
                    await crud.update_scheduled_task_run_times(self.pool, job.id, task['last_run_at'], job.next_run_time)
                except Exception as e:
                    logger.error(f"加载定时任务 '{task['name']}' (ID: {task['id']}) 失败: {e}")

    async def get_all_tasks(self) -> List[Dict[str, Any]]:
        """从数据库获取所有定时任务的列表。"""
        return await crud.get_scheduled_tasks(self.pool)

    async def add_task(self, name: str, job_type: str, cron: str, is_enabled: bool) -> Dict[str, Any]:
        if job_type not in self._job_classes:
            raise ValueError(f"未知的任务类型: {job_type}")
        # 确保增量更新任务的轮询间隔不低于3小时
        if job_type == "incremental_refresh" and not cron_is_valid(cron, 3):
            raise ValueError("定时增量更新任务的轮询间隔不得低于3小时。请使用如 '0 */3 * * *' (每3小时) 或更长的间隔。")
        
        # 新增：确保“定时追更”任务只能创建一个
        if job_type == "incremental_refresh":
            exists = await crud.check_scheduled_task_exists_by_type(self.pool, "incremental_refresh")
            if exists:
                raise ValueError("“定时追更”任务已存在，无法重复创建。")

        task_id = str(uuid4())
        await crud.create_scheduled_task(self.pool, task_id, name, job_type, cron, is_enabled)
        runner = self._create_job_runner(job_type)
        job = self.scheduler.add_job(runner, CronTrigger.from_crontab(cron), id=task_id, name=name)
        if not is_enabled: job.pause()        
        await crud.update_scheduled_task_run_times(self.pool, task_id, None, job.next_run_time)
        return await crud.get_scheduled_task(self.pool, task_id)

    async def update_task(self, task_id: str, name: str, cron: str, is_enabled: bool) -> Optional[Dict[str, Any]]:
        job = self.scheduler.get_job(task_id)
        if not job: return None

        task_info = await crud.get_scheduled_task(self.pool, task_id)
        if not task_info: return None
        job_type = task_info['job_type']

        # 确保增量更新任务的轮询间隔不低于3小时
        if job_type == "incremental_refresh" and not cron_is_valid(cron, 3):
            raise ValueError("定时增量更新任务的轮询间隔不得低于3小时。请使用如 '0 */3 * * *' (每3小时) 或更长的间隔。")
        await crud.update_scheduled_task(self.pool, task_id, name, cron, is_enabled)
        job.modify(name=name)
        job.reschedule(trigger=CronTrigger.from_crontab(cron))
        if is_enabled: job.resume()
        else: job.pause()
        await crud.update_scheduled_task_run_times(self.pool, task_id, job.last_run_time, job.next_run_time)
        return await crud.get_scheduled_task(self.pool, task_id)

    async def delete_task(self, task_id: str):
        if self.scheduler.get_job(task_id): self.scheduler.remove_job(task_id)
        await crud.delete_scheduled_task(self.pool, task_id)

    async def run_task_now(self, task_id: str):
        if job := self.scheduler.get_job(task_id):
            job.modify(next_run_time=datetime.now(self.scheduler.timezone))
        else:
            raise ValueError("找不到指定的任务ID")