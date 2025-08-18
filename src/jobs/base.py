from abc import ABC, abstractmethod
from typing import Callable
import aiomysql
import logging

from ..task_manager import TaskManager
from ..scraper_manager import ScraperManager

class BaseJob(ABC):
    """
    所有定时任务的抽象基类。
    """
    # 每个子类都必须覆盖这些类属性
    job_type: str # 任务的唯一标识符, e.g., "incremental_refresh"
    job_name: str # 任务的默认显示名称, e.g., "TMDB自动映射与更新"

    def __init__(self, pool: aiomysql.Pool, task_manager: TaskManager, scraper_manager: ScraperManager):
        self.pool = pool
        self.task_manager = task_manager
        self.scraper_manager = scraper_manager
        self.logger = logging.getLogger(self.__class__.__name__)

    @abstractmethod
    async def run(self, progress_callback: Callable):
        """
        执行任务的核心逻辑。
        progress_callback: 一个回调函数，用于报告进度 (progress: int, description: str)。
        """
        raise NotImplementedError