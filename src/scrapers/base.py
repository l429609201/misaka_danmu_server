import logging
import time
import re
from abc import ABC, abstractmethod
from typing import Any, Callable, Dict, List, Optional, Type

import aiomysql
from .. import crud
from .. import models


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

    def __init__(self, pool: aiomysql.Pool):
        self.pool = pool
        self.logger = logging.getLogger(self.__class__.__name__)
        self.ttl_config: Dict[str, int] = {} # 用于缓存从数据库读取的TTL值

    async def _get_ttl(self, key: str, default: int) -> int:
        """获取并缓存TTL配置值。"""
        if key not in self.ttl_config:
            ttl_str = await crud.get_config_value(self.pool, key, str(default))
            self.ttl_config[key] = int(ttl_str)
        return self.ttl_config[key]

    async def _get_from_cache(self, key: str) -> Optional[Any]:
        """从数据库缓存中获取数据。"""
        return await crud.get_cache(self.pool, key)

    async def _set_to_cache(self, key: str, value: Any, config_key: str, default_ttl: int):
        """将数据存入数据库缓存，TTL从配置中读取。"""
        ttl = await self._get_ttl(config_key, default_ttl)
        if ttl > 0:
            await crud.set_cache(self.pool, key, value, ttl, provider=self.provider_name)

    # 每个子类都必须覆盖这个类属性
    provider_name: str

    # (可选) 子类可以覆盖此字典来声明其可配置的字段
    configurable_fields: Dict[str, str] = {}

    # (新增) 子类可以覆盖此属性，以表明其是否支持日志记录
    is_loggable: bool = True

    async def _should_log_responses(self) -> bool:
        """检查数据库以确定是否应为此爬虫记录原始响应。"""
        if not self.is_loggable:
            return False
        
        config_key = f"scraper_{self.provider_name}_log_responses"
        # 如果未设置，则默认为 'false'
        is_enabled_str = await crud.get_config_value(self.pool, config_key, "false")
        return is_enabled_str.lower() == 'true'

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

    @abstractmethod
    async def close(self):
        """
        关闭所有打开的资源，例如HTTP客户端。
        """
        raise NotImplementedError