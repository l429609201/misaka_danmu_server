from abc import ABC, abstractmethod
from typing import Callable
import logging
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from ..task_manager import TaskManager
from ..scraper_manager import ScraperManager
from ..rate_limiter import RateLimiter
from ..metadata_manager import MetadataSourceManager

class BaseJob(ABC):
    """
    所有定时任务的抽象基类。
    """
    # 每个子类都必须覆盖这些类属性
    job_type: str # 任务的唯一标识符, e.g., "incremental_refresh"
    job_name: str # 任务的默认显示名称, e.g., "TMDB自动映射与更新"

    def __init__(self, session_factory: async_sessionmaker[AsyncSession], task_manager: TaskManager, scraper_manager: ScraperManager, rate_limiter: RateLimiter, metadata_manager: MetadataSourceManager):
        self._session_factory = session_factory
        self.task_manager = task_manager
        self.scraper_manager = scraper_manager
        self.rate_limiter = rate_limiter
        self.metadata_manager = metadata_manager
        self.logger = logging.getLogger(self.__class__.__name__)

    @abstractmethod
    async def run(self, session: AsyncSession, progress_callback: Callable):
        """
        执行任务的核心逻辑。
        progress_callback: 一个回调函数，用于报告进度 (progress: int, description: str)。
        """
        raise NotImplementedError