import logging
import time
import asyncio
import re
from abc import ABC, abstractmethod
from typing import Any, Callable, Dict, List, Optional, Type
from typing import Union
import httpx
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from .. import crud
from .. import models
from ..config_manager import ConfigManager


def _roman_to_int(s: str) -> int:
    """将罗马数字字符串转换为整数。"""
    roman_map = {'I': 1, 'V': 5, 'X': 10, 'L': 50, 'C': 100, 'D': 500, 'M': 1000}
    s = s.upper()
    result = 0
    i = 0
    while i < len(s):
        # 处理减法规则 (e.g., IV, IX)
        if i + 1 < len(s) and roman_map[s[i]] < roman_map[s[i+1]]:
            result += roman_map[s[i+1]] - roman_map[s[i]]
            i += 2
        else:
            result += roman_map[s[i]]
            i += 1
    return result

def get_season_from_title(title: str) -> int:
    """从标题中解析季度信息，返回季度数。"""
    if not title:
        return 1

    # A map for Chinese numerals, including formal and simple.
    chinese_num_map = {
        '一': 1, '二': 2, '三': 3, '四': 4, '五': 5, '六': 6, '七': 7, '八': 8, '九': 9, '十': 10,
        '壹': 1, '贰': 2, '叁': 3, '肆': 4, '伍': 5, '陆': 6, '柒': 7, '捌': 8, '玖': 9, '拾': 10
    }

    # 模式的顺序很重要
    patterns = [
        # 格式: S01, Season 1
        (re.compile(r"(?:S|Season)\s*(\d+)", re.I), lambda m: int(m.group(1))),
        # 格式: 第 X 季/部/幕 (支持中文和阿拉伯数字)
        (re.compile(r"第\s*([一二三四五六七八九十壹贰叁肆伍陆柒捌玖拾\d])\s*[季部幕]", re.I),
         lambda m: chinese_num_map.get(m.group(1)) if not m.group(1).isdigit() else int(m.group(1))),
        # 格式: X之章 (支持简繁中文数字)
        (re.compile(r"([一二三四五六七八九十壹贰叁肆伍陆柒捌玖拾])\s*之\s*章", re.I),
         lambda m: chinese_num_map.get(m.group(1))),
        # 格式: Unicode 罗马数字, e.g., Ⅲ
        (re.compile(r"\s+([Ⅰ-Ⅻ])(?=\s|$)", re.I), 
         lambda m: {'Ⅰ': 1, 'Ⅱ': 2, 'Ⅲ': 3, 'Ⅳ': 4, 'Ⅴ': 5, 'Ⅵ': 6, 'Ⅶ': 7, 'Ⅷ': 8, 'Ⅸ': 9, 'Ⅹ': 10, 'Ⅺ': 11, 'Ⅻ': 12}.get(m.group(1).upper())),
        # 格式: ASCII 罗马数字, e.g., III
        (re.compile(r"\s+([IVXLCDM]+)\b", re.I), lambda m: _roman_to_int(m.group(1))),
    ]

    for pattern, handler in patterns:
        match = pattern.search(title)
        if match:
            try:
                season = handler(match)
                if season is not None: return season
            except (ValueError, KeyError, IndexError):
                continue
    return 1 # Default to season 1

class BaseScraper(ABC):
    """
    所有搜索源的抽象基类。
    定义了搜索媒体、获取分集和获取弹幕的通用接口。
    """
    def __init__(self, session_factory: async_sessionmaker[AsyncSession], config_manager: ConfigManager):
        self._session_factory = session_factory
        self.config_manager = config_manager
        self.logger = logging.getLogger(self.__class__.__name__)
        self.client: Optional[httpx.AsyncClient] = None

    async def _create_client(self, **kwargs) -> httpx.AsyncClient:
        """
        创建 httpx.AsyncClient，并根据配置应用代理。
        子类可以传递额外的 httpx.AsyncClient 参数。
        """
        proxy_url_task = self.config_manager.get("proxy_url", "")
        proxy_enabled_globally_task = self.config_manager.get("proxy_enabled", "false")

        async with self._session_factory() as session:
            scraper_settings_task = crud.get_all_scraper_settings(session)
            proxy_url, proxy_enabled_str, scraper_settings = await asyncio.gather(
                proxy_url_task, proxy_enabled_globally_task, scraper_settings_task
            )
        proxy_enabled_globally = proxy_enabled_str.lower() == 'true'

        provider_setting = next((s for s in scraper_settings if s['providerName'] == self.provider_name), None)
        use_proxy_for_this_provider = provider_setting.get('use_proxy', False) if provider_setting else False

        proxy_to_use = proxy_url if proxy_enabled_globally and use_proxy_for_this_provider and proxy_url else None

        client_kwargs = {"proxy": proxy_to_use, "timeout": 20.0, "follow_redirects": True, **kwargs}
        return httpx.AsyncClient(**client_kwargs)

    async def _get_from_cache(self, key: str) -> Optional[Any]:
        """从数据库缓存中获取数据。"""
        async with self._session_factory() as session:
            return await crud.get_cache(session, key)

    async def _set_to_cache(self, key: str, value: Any, config_key: str, default_ttl: int):
        """将数据存入数据库缓存，TTL从配置中读取。"""
        ttl_str = await self.config_manager.get(config_key, str(default_ttl))
        ttl = int(ttl_str)
        if ttl > 0:
            async with self._session_factory() as session:
                await crud.set_cache(session, key, value, ttl, provider=self.provider_name)

    # 每个子类都必须覆盖这个类属性
    provider_name: str

    # (可选) 子类可以覆盖此字典来声明其可配置的字段
    configurable_fields: Dict[str, str] = {}

    # (新增) 子类应覆盖此列表，声明它们可以处理的域名
    handled_domains: List[str] = []

    # (新增) 子类可以覆盖此属性，以提供一个默认的 Referer
    referer: Optional[str] = None

    # (新增) 子类可以覆盖此属性，以表明其是否支持日志记录
    is_loggable: bool = True

    rate_limit_quota: Optional[int] = None # 新增：特定源的配额
    
    async def _should_log_responses(self) -> bool:
        """动态检查是否应记录原始响应，确保配置实时生效。"""
        if not self.is_loggable:
            return False
        
        is_enabled_str = await self.config_manager.get("logRawResponses", "false")
        return is_enabled_str.lower() == 'true'

    async def get_episode_blacklist_pattern(self) -> Optional[re.Pattern]:
        """
        获取并编译此源的自定义分集黑名单正则表达式。
        结果会被缓存以提高性能。
        """
        # 移除实例级别的缓存，直接依赖 ConfigManager 的缓存机制。
        # ConfigManager 的缓存会在配置更新时被正确地失效。
        key = f"{self.provider_name}_episode_blacklist_regex"
        regex_str = await self.config_manager.get(key, "")
        if regex_str:
            try:
                # 每次调用都重新编译，因为 ConfigManager 会缓存 regex_str，
                # 这里的开销很小。
                return re.compile(regex_str, re.IGNORECASE)
            except re.error as e:
                self.logger.error(f"无效的黑名单正则表达式: '{regex_str}' - {e}")
        return None

    async def execute_action(self, action_name: str, payload: Dict[str, Any]) -> Any:
        """
        执行一个指定的操作。
        子类应重写此方法来处理其声明的操作。
        :param action_name: 要执行的操作的名称。
        :param payload: 包含操作所需参数的字典。
        """
        raise NotImplementedError(f"操作 '{action_name}' 在 {self.provider_name} 中未实现。")

    @abstractmethod
    async def search(self, keyword: str, episode_info: Optional[Dict[str, Any]] = None) -> List[models.ProviderSearchInfo]:
        """
        根据关键词搜索媒体。
        episode_info: 可选字典，包含 'season' 和 'episode'。
        """
        raise NotImplementedError

    @abstractmethod
    async def get_info_from_url(self, url: str) -> Optional[models.ProviderSearchInfo]:
        """
        (新增) 从一个作品的URL中提取信息，并返回一个 ProviderSearchInfo 对象。
        这用于支持从URL直接导入整个作品。
        """
        raise NotImplementedError

    @abstractmethod
    async def get_id_from_url(self, url: str) -> Optional[Union[str, Dict[str, str]]]:
        """
        (新增) 统一的从URL解析ID的接口。
        子类应重写此方法以支持从URL直接导入。
        """
        raise NotImplementedError

    @abstractmethod
    async def get_episodes(self, media_id: str, target_episode_index: Optional[int] = None, db_media_type: Optional[str] = None) -> List[models.ProviderEpisodeInfo]:
        """
        获取给定媒体ID的所有分集。
        如果提供了 target_episode_index，则可以优化为只获取到该分集为止。
        db_media_type: 从数据库中读取的媒体类型 ('movie', 'tv_series')，可用于指导刮削策略。
        """
        raise NotImplementedError

    @abstractmethod
    async def get_comments(self, episode_id: str, progress_callback: Optional[Callable] = None) -> List[dict]:
        """
        获取给定分集ID的所有弹幕。
        返回的字典列表应与 crud.bulk_insert_comments 的期望格式兼容。
        """
        raise NotImplementedError

    async def get_id_from_url(self, url: str) -> Optional[Union[str, Dict[str, str]]]:
        """
        (新增) 统一的从URL解析ID的接口。
        子类应重写此方法以支持从URL直接导入。
        """
        return None

    def format_episode_id_for_comments(self, provider_episode_id: Any) -> str:
        """
        (新增) 将 get_comments 所需的 episode_id 格式化为字符串。
        大多数源直接返回字符串，但Bilibili和MGTV需要特殊处理。
        """
        return str(provider_episode_id)

    @abstractmethod
    async def close(self):
        """
        关闭所有打开的资源，例如HTTP客户端。
        """
        raise NotImplementedError