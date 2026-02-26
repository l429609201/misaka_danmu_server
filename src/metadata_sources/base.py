from abc import ABC, abstractmethod
import logging
from typing import Any, Dict, List, Optional, Set, Type,Tuple

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker # type: ignore
from fastapi import Request
from httpx import HTTPStatusError

from src.db import models, ConfigManager, CacheManager
from src.services import ScraperManager

class BaseMetadataSource(ABC):
    """所有元数据源插件的抽象基类。"""

    # 每个子类必须定义自己的提供商名称
    provider_name: str
    # 新增：声明可配置字段 { "db_key": ("UI标签", "类型", "提示") }
    configurable_fields: Dict[str, Tuple[str, str, str]] = {}
    # 新增：是否支持获取分集URL (用于补充源功能)
    supports_episode_urls: bool = False

    def __init__(self, session_factory: async_sessionmaker[AsyncSession], config_manager: ConfigManager, scraper_manager: ScraperManager, cache_manager: CacheManager):
        self._session_factory = session_factory
        self.config_manager = config_manager
        self.scraper_manager = scraper_manager
        self.cache_manager = cache_manager
        self.logger = logging.getLogger(self.__class__.__name__)

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
    async def check_connectivity(self) -> Dict[str, str]:
        """检查源的配置状态，并返回状态字典。

        返回格式: {"code": "ok|unconfigured|warning|error|disabled", "message": "..."}
        """
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

    async def get_episode_urls(self, metadata_id: str, target_provider: Optional[str] = None) -> List[tuple]:
        """
        获取分集URL列表 (补充源功能)。

        Args:
            metadata_id: 元数据源中的条目ID
            target_provider: 目标平台 (tencent/iqiyi/youku/bilibili/mgtv等), 如果为None则返回所有平台

        Returns:
            List[Tuple[int, str]]: (集数, 播放URL) 的列表
        """
        return [] # 默认实现返回空列表

    async def close(self):
        """关闭所有打开的资源，例如HTTP客户端。"""
        pass
