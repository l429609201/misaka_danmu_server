from abc import ABC, abstractmethod
import logging
from typing import Any, Dict, List, Optional, Set, Type

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker # type: ignore
from fastapi import Request
from httpx import HTTPStatusError

from .. import models
from ..config_manager import ConfigManager
from ..scraper_manager import ScraperManager

class BaseMetadataSource(ABC):
    """所有元数据源插件的抽象基类。"""

    # 每个子类必须定义自己的提供商名称
    provider_name: str

    def __init__(self, session_factory: async_sessionmaker[AsyncSession], config_manager: ConfigManager, scraper_manager: ScraperManager):
        self._session_factory = session_factory
        self.config_manager = config_manager
        self.scraper_manager = scraper_manager
        self.logger = logging.getLogger(self.__class__.__name__)
        self.client: Optional[httpx.AsyncClient] = None

    @abstractmethod
    async def search(self, keyword: str, user: models.User, mediaType: Optional[str] = None) -> List[models.MetadataDetailsResponse]:
        """根据关键词搜索媒体。"""
        raise NotImplementedError

    @abstractmethod
    async def get_details(self, item_id: str, user: models.User, mediaType: Optional[str] = None) -> Optional[models.MetadataDetailsResponse]:
        """获取指定ID的媒体详情。"""
        raise NotImplementedError

    @abstractmethod
    async def search_aliases(self, keyword: str, user: models.User) -> Set[str]:
        """根据关键词搜索别名。"""
        raise NotImplementedError

    @abstractmethod
    async def check_connectivity(self) -> str:
        """检查与源的连接性，并返回状态字符串。"""
        raise NotImplementedError
    
    @abstractmethod
    async def execute_action(self, action_name: str, payload: Dict[str, Any], user: models.User, request: Request) -> Any:
        """
        执行一个指定的操作。
        子类可以重写此方法来处理其特定的操作，例如OAuth流程。
        """
        raise NotImplementedError(f"操作 '{action_name}' 在 {self.provider_name} 中未实现。")

    async def get_comments_by_failover(self, title: str, season: int, episode_index: int, user: models.User) -> Optional[List[dict]]:
        """
        一个故障转移方法，用于从备用源查找并返回特定分集的弹幕。
        当主搜索源找不到分集时使用此方法。
        """
        return None # 默认实现不执行任何操作

    async def close(self):
        """关闭所有打开的资源，例如HTTP客户端。"""
        if self.client:
            await self.client.aclose()
