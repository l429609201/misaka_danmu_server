import logging
import asyncio
import re
import time
from abc import ABC, abstractmethod
from typing import Any, Callable, Dict, List, Optional, Type, Tuple, TYPE_CHECKING
from typing import Union
from functools import wraps
import httpx
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src.db import crud
from src.db import models
from src.core.cache import get_cache_backend

from src.utils import TransportManager

if TYPE_CHECKING:
    from src.db import ConfigManager

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
        # 格式: 标题末尾的阿拉伯数字, e.g., 刀剑神域2, 模范出租车3
        # 匹配非数字字符后跟1-2位数字结尾，排除年份(4位数字)
        (re.compile(r"[^\d](\d{1,2})\s*$"), lambda m: int(m.group(1)) if 1 <= int(m.group(1)) <= 20 else None),
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


def track_performance(func):
    """
    装饰器: 跟踪异步方法的执行时间,不影响并发性能。
    记录到 INFO 级别,方便查看性能统计。
    使用任务ID作为键存储耗时，确保并发安全，供 scraper_manager 读取。
    """
    @wraps(func)
    async def wrapper(self, *args, **kwargs):
        start_time = time.perf_counter()
        task_id = id(asyncio.current_task())  # 获取当前任务ID，确保并发安全
        try:
            result = await func(self, *args, **kwargs)
            elapsed = time.perf_counter() - start_time
            elapsed_ms = elapsed * 1000
            # 使用任务ID作为键存储耗时，确保并发安全
            if not hasattr(self, '_task_timings'):
                self._task_timings = {}
            self._task_timings[task_id] = elapsed_ms
            # 记录到 INFO 级别,显示搜索源名称和耗时
            self.logger.info(f"[{self.provider_name}] {func.__name__} 耗时: {elapsed:.3f}s")
            return result
        except Exception as e:
            elapsed = time.perf_counter() - start_time
            elapsed_ms = elapsed * 1000
            # 即使失败也存储耗时
            if not hasattr(self, '_task_timings'):
                self._task_timings = {}
            self._task_timings[task_id] = elapsed_ms
            self.logger.warning(f"[{self.provider_name}] {func.__name__} 失败耗时: {elapsed:.3f}s")
            raise
    return wrapper


# 通用分集过滤规则（硬编码），用于前端"填充通用规则"按钮
COMMON_EPISODE_BLACKLIST_REGEX = r'^(.*?)((.+?版)|(特(别|典))|((导|演)员|嘉宾|角色)访谈|福利|彩蛋|花絮|预告|特辑|专访|访谈|幕后|周边|资讯|看点|速看|回顾|盘点|合集|PV|MV|CM|OST|ED|OP|BD|特典|SP|NCOP|NCED|MENU|Web-DL|rip|x264|x265|aac|flac)(.*?)$'


class BaseScraper(ABC):
    """
    所有搜索源的抽象基类。
    定义了搜索媒体、获取分集和获取弹幕的通用接口。

    注意：分集过滤规则现在完全从 config 表读取，不再使用硬编码的默认值。
    - 特定源分集黑名单：{provider_name}_episode_blacklist_regex
    如果 config 表中键不存在，启动时会通过 register_defaults 创建并填充默认值。
    如果键存在但值为空，则不进行过滤。
    """

    def __init__(self, session_factory: async_sessionmaker[AsyncSession], config_manager: "ConfigManager", transport_manager: TransportManager):
        self._session_factory = session_factory
        self.config_manager = config_manager
        self.transport_manager = transport_manager
        self.logger = logging.getLogger(self.__class__.__name__)
        # 用于跟踪当前客户端实例所使用的代理配置
        self._current_proxy_config: Optional[str] = None
        # 缓存 scraper_manager 引用,用于访问预加载的 scraper 设置
        self._scraper_manager_ref: Optional[Any] = None

    async def _get_proxy_for_provider(self) -> Optional[str]:
        """
        获取当前 provider 的代理配置。
        优先使用预加载的缓存,避免重复数据库查询。

        支持三种代理模式：
        - none: 不使用代理
        - http_socks: HTTP/SOCKS 代理
        - accelerate: 加速代理（URL 重写模式，不返回代理 URL）
        """
        # 获取代理模式
        proxy_mode = await self.config_manager.get("proxyMode", "none")

        # 兼容旧配置：如果 proxyMode 为 none 但 proxyEnabled 为 true，则使用 http_socks 模式
        if proxy_mode == "none":
            proxy_enabled_globally = (await self.config_manager.get("proxyEnabled", "false")).lower() == 'true'
            if proxy_enabled_globally:
                proxy_mode = "http_socks"

        # 如果代理模式为 none 或 accelerate，则不返回 HTTP 代理 URL
        # accelerate 模式通过 URL 重写实现，不需要设置 httpx 的 proxy 参数
        if proxy_mode != "http_socks":
            return None

        proxy_url = await self.config_manager.get("proxyUrl", "")
        if not proxy_url:
            return None

        # 获取当前 provider 的代理设置
        provider_setting = None
        if self._scraper_manager_ref and hasattr(self._scraper_manager_ref, '_cached_scraper_settings'):
            # 使用预加载的缓存（快速路径）
            provider_setting = self._scraper_manager_ref._cached_scraper_settings.get(self.provider_name)
        else:
            # 降级到数据库查询（仅在缓存未初始化时，如测试环境）
            async with self._session_factory() as session:
                scraper_settings = await crud.get_all_scraper_settings(session)
            provider_setting = next((s for s in scraper_settings if s['providerName'] == self.provider_name), None)

        use_proxy_for_this_provider = provider_setting.get('useProxy', False) if provider_setting else False

        return proxy_url if use_proxy_for_this_provider else None

    async def _should_use_accelerate_proxy(self) -> bool:
        """检查是否应该使用加速代理模式"""
        proxy_mode = await self.config_manager.get("proxyMode", "none")
        return proxy_mode == "accelerate"

    async def _get_accelerate_proxy_url(self) -> str:
        """获取加速代理地址"""
        return await self.config_manager.get("accelerateProxyUrl", "")

    def _transform_url_for_accelerate(self, original_url: str, proxy_base: str) -> str:
        """
        转换 URL 为加速代理格式

        原始: https://api.example.com/path
        转换: https://proxy.vercel.app/https/api.example.com/path
        """
        if not proxy_base:
            return original_url

        proxy_base = proxy_base.rstrip('/')
        protocol = "https" if original_url.startswith("https://") else "http"
        target = original_url.replace(f"{protocol}://", "")

        return f"{proxy_base}/{protocol}/{target}"

    async def _transform_url_if_needed(self, url: str) -> str:
        """
        根据代理模式转换 URL

        - none/http_socks: 返回原始 URL
        - accelerate: 返回加速代理格式的 URL（如果当前 provider 启用了代理）
        """
        if not await self._should_use_accelerate_proxy():
            return url

        # 检查当前 provider 是否启用了代理
        provider_setting = None
        if self._scraper_manager_ref and hasattr(self._scraper_manager_ref, '_cached_scraper_settings'):
            provider_setting = self._scraper_manager_ref._cached_scraper_settings.get(self.provider_name)
        else:
            async with self._session_factory() as session:
                scraper_settings = await crud.get_all_scraper_settings(session)
            provider_setting = next((s for s in scraper_settings if s['providerName'] == self.provider_name), None)

        use_proxy_for_this_provider = provider_setting.get('useProxy', False) if provider_setting else False

        if not use_proxy_for_this_provider:
            return url

        proxy_base = await self._get_accelerate_proxy_url()
        if proxy_base:
            return self._transform_url_for_accelerate(url, proxy_base)

        return url
    
    async def _log_proxy_usage(self, proxy_url: Optional[str]):
        if proxy_url:
            self.logger.debug(f"通过代理 '{proxy_url}' 发起请求...")

    async def _create_client(self, **kwargs) -> httpx.AsyncClient: # type: ignore
        """
        创建 httpx.AsyncClient，并根据配置应用代理。
        子类可以传递额外的 httpx.AsyncClient 参数。
        """
        proxy_to_use = await self._get_proxy_for_provider()
        await self._log_proxy_usage(proxy_to_use)
        self._current_proxy_config = proxy_to_use

        client_kwargs = {"proxy": proxy_to_use, "timeout": 20.0, "follow_redirects": True, **kwargs}
        return httpx.AsyncClient(**client_kwargs)

    async def _get_from_cache(self, key: str) -> Optional[Any]:
        """
        从缓存中获取数据。
        优先使用预取的缓存（批量查询优化），否则单独查询数据库。
        """
        # 【优化】优先使用预取的缓存
        if hasattr(self, '_prefetched_cache'):
            if key in self._prefetched_cache:
                cached_value = self._prefetched_cache[key]
                if cached_value is not None:
                    self.logger.debug(f"{self.provider_name}: 使用预取缓存 (命中) - {key}")
                    return cached_value
                else:
                    # 批量查询已执行，但缓存不存在
                    self.logger.debug(f"{self.provider_name}: 使用预取缓存 (未命中) - {key}")
                    return None
        
        # 降级到单独数据库查询（仅在批量查询未执行时）
        self.logger.debug(f"{self.provider_name}: 缓存未预取，进行单独查询 - {key}")
        async with self._session_factory() as session:
            try:
                _backend = get_cache_backend()
                if _backend is not None:
                    try:
                        result = await _backend.get(key, region="default")
                        if result is not None:
                            return result
                    except Exception:
                        pass
                return await crud.get_cache(session, key)
            finally:
                await session.close()

    async def _set_to_cache(self, key: str, value: Any, config_key: str, default_ttl: int):
        """将数据存入数据库缓存，TTL从配置中读取。"""
        ttl_str = await self.config_manager.get(config_key, str(default_ttl))
        ttl = int(ttl_str)
        if ttl > 0:
            async with self._session_factory() as session:
                try:
                    _backend = get_cache_backend()
                    if _backend is not None:
                        try:
                            await _backend.set(key, value, ttl=ttl, region="default")
                        except Exception:
                            await crud.set_cache(session, key, value, ttl, provider=self.provider_name)
                    else:
                        await crud.set_cache(session, key, value, ttl, provider=self.provider_name)
                    await session.commit()
                finally:
                    await session.close()

    # 每个子类都必须覆盖这个类属性
    provider_name: str

    # (可选) 子类可以覆盖此字典来声明其可配置的字段。
    # 格式: { "config_key": ("UI显示的标签", "字段类型", "UI上的提示信息") }
    # 支持的字段类型: "string", "boolean", "password"
    configurable_fields: Dict[str, Tuple[str, str, str]] = {}

    # (新增) 子类应覆盖此列表，声明它们可以处理的域名
    handled_domains: List[str] = []

    # (新增) 子类可以覆盖此属性，以提供一个默认的 Referer
    referer: Optional[str] = None

    # (新增) 子类可以覆盖此属性，以表明其是否支持日志记录
    is_loggable: bool = True

    rate_limit_quota: Optional[int] = None # 新增：特定源的配额

    # 点赞火焰阈值：l >= 此值显示 🔥，否则显示 ❤️（各源可在内部覆盖）
    likes_fire_threshold: int = 1000

    def build_media_url(self, media_id: str) -> Optional[str]:
        """
        构造平台播放页面URL。
        子类可以覆盖此方法以提供特定平台的URL构造逻辑。

        Args:
            media_id: 媒体ID

        Returns:
            平台播放页面URL，如果无法构造则返回None
        """
        return None
    
    async def _should_log_responses(self) -> bool:
        """动态检查是否应记录原始响应，确保配置实时生效。"""
        if not self.is_loggable:
            return False

        # 修正：使用特定于提供商的配置键，例如 'scraper_tencent_log_responses'
        config_key = f"scraper_{self.provider_name}_log_responses"
        is_enabled_str = await self.config_manager.get(config_key, "false")
        # 健壮性检查：同时处理布尔值和字符串 "true"，以防配置值类型不确定。
        if isinstance(is_enabled_str, bool):
            return is_enabled_str
        return str(is_enabled_str).lower() == 'true'

    async def get_episode_blacklist_pattern(self) -> Optional[re.Pattern]:
        """
        获取用于过滤分集标题的正则表达式对象。
        只使用特定于提供商的黑名单，不再有全局黑名单。

        注意：此方法不使用硬编码的默认值作为兜底。
        - 如果 config 表中键不存在，启动时会通过 register_defaults 创建并填充默认值
        - 如果键存在但值为空，则不进行过滤
        """
        # 获取特定于提供商的黑名单
        provider_key = f"{self.provider_name}_episode_blacklist_regex"
        # 不提供默认值，如果数据库中没有则返回空字符串
        provider_pattern_str = await self.config_manager.get(provider_key, "")

        if not provider_pattern_str or not provider_pattern_str.strip():
            return None

        try:
            return re.compile(provider_pattern_str, re.IGNORECASE)
        except re.error as e:
            self.logger.error(f"编译分集黑名单正则表达式失败: '{provider_pattern_str}'. 错误: {e}")
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
        返回的字典列表应与 crud.save_danmaku_for_episode 的期望格式兼容。
        """
        raise NotImplementedError

    def format_episode_id_for_comments(self, provider_episode_id: Any) -> str:
        """
        (新增) 将 get_comments 所需的 episode_id 格式化为字符串。
        大多数源直接返回字符串，但Bilibili和MGTV需要特殊处理。
        """
        return str(provider_episode_id)

    async def _filter_junk_episodes(self, episodes: List["models.ProviderEpisodeInfo"]) -> List["models.ProviderEpisodeInfo"]:
        """
        过滤掉垃圾分集（预告、花絮等）

        注意：此方法现在从 config 表读取过滤规则，不再使用硬编码的正则表达式。
        如果 config 表中没有配置过滤规则，则不进行过滤。
        """
        if not episodes:
            return episodes

        # 从 config 表获取过滤规则，不使用硬编码兜底
        blacklist_pattern = await self.get_episode_blacklist_pattern()

        # 如果没有配置过滤规则，直接返回所有分集
        if not blacklist_pattern:
            self.logger.info(f"{self.provider_name}: 分集过滤结果 (无过滤规则): 共 {len(episodes)} 集")
            return episodes

        filtered_episodes = []
        filtered_out_episodes = []

        for episode in episodes:
            # 使用从 config 表获取的正则表达式进行过滤
            match = blacklist_pattern.search(episode.title)
            if match:
                junk_type = match.group(0)
                filtered_out_episodes.append((episode, junk_type))
            else:
                filtered_episodes.append(episode)

        # 打印分集过滤摘要
        summary_parts = [f"{self.provider_name}: 分集过滤结果:"]

        # 打印过滤掉的分集（这些比较重要，逐条列出）
        if filtered_out_episodes:
            summary_parts.append(f"  已过滤 {len(filtered_out_episodes)} 集:")
            for episode, junk_type in filtered_out_episodes:
                summary_parts.append(f"    ✗ {episode.title} ({junk_type})")

        # 保留的分集只显示数量
        if filtered_episodes:
            summary_parts.append(f"  保留 {len(filtered_episodes)} 集")

        if not filtered_episodes and not filtered_out_episodes:
            summary_parts.append(f"  无分集数据")

        self.logger.info("\n".join(summary_parts))

        return filtered_episodes

    @abstractmethod
    async def close(self):
        """关闭所有打开的资源，例如HTTP客户端。"""
        pass
