from abc import ABC, abstractmethod
import logging
from typing import Any, Dict, Optional
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from fastapi import Request
from pydantic import BaseModel

from ..task_manager import TaskManager
from ..rate_limiter import RateLimiter
from ..scraper_manager import ScraperManager
from ..metadata_manager import MetadataSourceManager

class WebhookPayload(BaseModel):
    """定义 Webhook 负载的通用结构。"""
    event_type: str
    media_title: str
    season_number: Optional[int] = None
    episode_number: Optional[int] = None

class BaseWebhook(ABC):
    """所有 Webhook 处理器的抽象基类。"""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession], task_manager: TaskManager, scraper_manager: ScraperManager, rate_limiter: RateLimiter, metadata_manager: MetadataSourceManager):
        self._session_factory = session_factory
        self.task_manager = task_manager
        self.scraper_manager = scraper_manager
        self.rate_limiter = rate_limiter
        self.metadata_manager = metadata_manager
        self.logger = logging.getLogger(self.__class__.__name__)

    @abstractmethod
    async def handle(self, request: Request):
        """处理传入的 Webhook 负载。"""
        raise NotImplementedError