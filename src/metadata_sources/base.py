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
    # 新增：是否为搜索补充源（当弹幕源搜索无结果时，可为对应平台提供兜底数据）
    is_search_supplement_source: bool = False
    # 补充源平台映射：内部平台名 → 弹幕源 provider name
    # 子类覆盖此字典即可声明支持哪些平台的补充
    # 例如: {"qq": "tencent", "bilibili1": "bilibili", "qiyi": "iqiyi"}
    PLATFORM_TO_PROVIDER: Dict[str, str] = {}

    def __init__(self, session_factory: async_sessionmaker[AsyncSession], config_manager: ConfigManager, scraper_manager: ScraperManager, cache_manager: CacheManager):
        self._session_factory = session_factory
        self.config_manager = config_manager
        self.scraper_manager = scraper_manager
        self.cache_manager = cache_manager
        self.logger = logging.getLogger(self.__class__.__name__)

    @classmethod
    def _build_provider_to_platforms(cls) -> Dict[str, List[str]]:
        """反转 PLATFORM_TO_PROVIDER 映射：弹幕源 provider → [内部平台名列表]

        例如 {"qq": "tencent", "bilibili1": "bilibili"}
        → {"tencent": ["qq"], "bilibili": ["bilibili1"]}

        子类通常不需要覆盖此方法，直接使用即可。
        """
        result: Dict[str, List[str]] = {}
        for platform_key, provider in cls.PLATFORM_TO_PROVIDER.items():
            result.setdefault(provider, []).append(platform_key)
        return result

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

    async def supplement_search(
        self,
        keyword: str,
        empty_providers: Set[str],
        user: models.User
    ) -> List[models.ProviderSearchInfo]:
        """当弹幕源搜索无结果时，为对应平台提供兜底搜索结果。（模板方法）

        基类统一处理：
        1. 过滤 empty_providers，只保留有 PLATFORM_TO_PROVIDER 映射的
        2. 调用子类 _match_supplement_items() 获取原始匹配结果
        3. 按 mediaId 去重
        4. 自动填充 supplementSource

        子类需设 is_search_supplement_source = True 并实现 _match_supplement_items()。

        Args:
            keyword: 搜索关键词
            empty_providers: 返回0结果的弹幕源名称集合（如 {"youku", "iqiyi"}）
            user: 系统用户

        Returns:
            以对应弹幕源 provider 名义生成的 ProviderSearchInfo 列表
        """
        if not self.PLATFORM_TO_PROVIDER:
            return []

        # 1. 过滤：只对有映射关系的空结果源进行补全
        provider_platforms_map = self._build_provider_to_platforms()
        providers_to_supplement = {p for p in empty_providers if p in provider_platforms_map}
        if not providers_to_supplement:
            return []

        # 2. 调用子类实现获取原始匹配结果
        raw_items = await self._match_supplement_items(keyword, providers_to_supplement, provider_platforms_map, user)
        if not raw_items:
            return []

        # 3. 去重 + 自动填充 supplementSource
        seen_media_ids: Set[str] = set()
        final_items: List[models.ProviderSearchInfo] = []
        for item in raw_items:
            if item.mediaId not in seen_media_ids:
                # 确保 supplementSource 统一设置
                item.supplementSource = self.provider_name
                final_items.append(item)
                seen_media_ids.add(item.mediaId)

        if final_items:
            supplemented_providers = {item.provider for item in final_items}
            self.logger.debug(f"{self.provider_name}补充搜索: 为 {supplemented_providers} 补全了 {len(final_items)} 个结果")

        return final_items

    async def _match_supplement_items(
        self,
        keyword: str,
        providers_to_supplement: Set[str],
        provider_platforms_map: Dict[str, List[str]],
        user: models.User
    ) -> List[models.ProviderSearchInfo]:
        """子类实现：根据关键词搜索并匹配可补充的条目。

        基类已完成 provider 过滤和去重，子类只需关注：
        1. 如何搜索获取候选结果
        2. 如何判断候选结果支持哪些 provider
        3. 构建 ProviderSearchInfo 返回（provider 使用弹幕源标准名称）

        Args:
            keyword: 搜索关键词
            providers_to_supplement: 需要补充的弹幕源名称集合（已过滤，只含有映射的）
            provider_platforms_map: 反转映射 {弹幕源名 → [内部平台名列表]}
            user: 系统用户

        Returns:
            匹配到的 ProviderSearchInfo 列表（无需去重，基类会处理）
        """
        return []

    async def close(self):
        """关闭所有打开的资源，例如HTTP客户端。"""
        pass
