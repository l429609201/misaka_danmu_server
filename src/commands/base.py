"""
指令系统基础模块
提供指令处理器基类和通用工具函数
"""
import time
import logging
from typing import Optional, Tuple, List, Any, TYPE_CHECKING
from sqlalchemy.ext.asyncio import AsyncSession

from src.db import crud
from src.core.cache import get_cache_backend

if TYPE_CHECKING:
    from src.api.dandan import DandanSearchAnimeResponse, DandanSearchAnimeItem

logger = logging.getLogger(__name__)


async def _get_db_cache(session: AsyncSession, prefix: str, key: str) -> Optional[Any]:
    """
    从缓存中获取数据（优先走缓存后端，回退到数据库）

    Args:
        session: 数据库会话
        prefix: 缓存键前缀
        key: 缓存键

    Returns:
        缓存值或None
    """
    cache_key = f"{prefix}{key}"
    backend = get_cache_backend()
    if backend is not None:
        try:
            return await backend.get(cache_key, region="default")
        except Exception as e:
            logger.warning(f"缓存后端读取失败，回退到数据库: {cache_key}, 错误: {e}")

    cache_entry = await crud.get_cache(session, cache_key)
    if cache_entry:
        if hasattr(cache_entry, 'value'):
            return cache_entry.value
        else:
            return cache_entry
    return None


async def _set_db_cache(session: AsyncSession, prefix: str, key: str, value: Any, ttl: int):
    """
    设置缓存（优先走缓存后端，回退到数据库）

    Args:
        session: 数据库会话
        prefix: 缓存键前缀
        key: 缓存键
        value: 缓存值
        ttl: 过期时间（秒）
    """
    cache_key = f"{prefix}{key}"
    backend = get_cache_backend()
    if backend is not None:
        try:
            await backend.set(cache_key, value, ttl=ttl, region="default")
            return
        except Exception as e:
            logger.warning(f"缓存后端写入失败，回退到数据库: {cache_key}, 错误: {e}")

    await crud.set_cache(session, cache_key, value, ttl)


def parse_command(search_term: str) -> Optional[Tuple[str, List[str]]]:
    """
    解析指令

    Args:
        search_term: 搜索词

    Returns:
        (指令名称, 参数列表) 或 None（不是指令）

    Special:
        如果只输入 @，返回 ("HELP", []) 以展示帮助
    """
    if not search_term.startswith('@'):
        return None

    parts = search_term[1:].strip().split()

    # 如果只输入 @，视为帮助指令
    if not parts:
        return ("HELP", [])

    command_name = parts[0].upper()
    args = parts[1:] if len(parts) > 1 else []

    return (command_name, args)


class CommandHandler:
    """指令处理器基类"""

    def __init__(self, name: str, description: str, cooldown_seconds: int = 0,
                 usage: Optional[str] = None, examples: Optional[List[str]] = None):
        """
        初始化指令处理器

        Args:
            name: 指令名称
            description: 指令描述
            cooldown_seconds: 冷却时间（秒），0表示无冷却
            usage: 使用说明（可选）
            examples: 使用示例列表（可选）
        """
        self.name = name
        self.description = description
        self.cooldown_seconds = cooldown_seconds
        self.usage = usage or f"@{name}"
        self.examples = examples or []

    async def can_execute(self, token: str, session: AsyncSession) -> Tuple[bool, int]:
        """
        检查是否可以执行指令（冷却检查）

        Args:
            token: 用户token
            session: 数据库会话

        Returns:
            (是否可执行, 剩余冷却秒数)
        """
        if self.cooldown_seconds <= 0:
            return True, 0

        cache_key = f"{token}_{self.name}"
        last_exec_time = await _get_db_cache(session, "command_cooldown_", cache_key)

        if last_exec_time is None:
            return True, 0

        elapsed = time.time() - last_exec_time
        remaining = max(0, self.cooldown_seconds - int(elapsed))

        return remaining == 0, remaining

    async def execute(self, token: str, args: List[str], session: AsyncSession,
                     config_manager, **kwargs):
        """
        执行指令，子类需要实现

        Args:
            token: 用户token
            args: 指令参数
            session: 数据库会话
            config_manager: 配置管理器
            **kwargs: 其他依赖

        Returns:
            DandanSearchAnimeResponse
        """
        raise NotImplementedError

    async def record_execution(self, token: str, session: AsyncSession):
        """
        记录执行时间

        Args:
            token: 用户token
            session: 数据库会话
        """
        if self.cooldown_seconds > 0:
            cache_key = f"{token}_{self.name}"
            await _set_db_cache(session, "command_cooldown_", cache_key, time.time(), self.cooldown_seconds)

    async def get_image_url(self, config_manager) -> str:
        """
        获取图片URL

        Args:
            config_manager: 配置管理器

        Returns:
            图片URL
        """
        custom_domain = await config_manager.get("customApiDomain", "")
        return f"{custom_domain}/static/logo.png" if custom_domain else "/static/logo.png"

    def build_response_item(self, anime_id: int, title: str, description: str,
                           image_url: str, **kwargs) -> "DandanSearchAnimeItem":
        """
        构建响应项

        Args:
            anime_id: 动画ID
            title: 标题
            description: 描述
            image_url: 图片URL
            **kwargs: 其他参数（episodeCount, rating等）

        Returns:
            DandanSearchAnimeItem
        """
        from src.api.dandan import DandanSearchAnimeItem

        return DandanSearchAnimeItem(
            animeId=anime_id,
            bangumiId=str(anime_id),
            animeTitle=title,
            type=kwargs.get("type", "other"),
            typeDescription=description,
            imageUrl=image_url,
            startDate=kwargs.get("startDate", "2025-01-01T00:00:00+08:00"),
            year=kwargs.get("year", 2025),
            episodeCount=kwargs.get("episodeCount", 0),
            rating=kwargs.get("rating", 0.0),
            isFavorited=kwargs.get("isFavorited", False)
        )

    def build_response(self, items: List["DandanSearchAnimeItem"]) -> "DandanSearchAnimeResponse":
        """
        构建响应

        Args:
            items: 响应项列表

        Returns:
            DandanSearchAnimeResponse
        """
        from src.api.dandan import DandanSearchAnimeResponse

        return DandanSearchAnimeResponse(animes=items)

    def success_response(self, title: str, description: str, image_url: str,
                        **kwargs) -> "DandanSearchAnimeResponse":
        """
        构建成功响应

        Args:
            title: 标题
            description: 描述
            image_url: 图片URL
            **kwargs: 其他参数

        Returns:
            DandanSearchAnimeResponse
        """
        item = self.build_response_item(
            anime_id=999999998,
            title=f"✓ {title}",
            description=description,
            image_url=image_url,
            type="other",
            typeDescription="指令执行成功",
            **kwargs
        )
        return self.build_response([item])

    def error_response(self, title: str, description: str, image_url: str,
                      **kwargs) -> "DandanSearchAnimeResponse":
        """
        构建错误响应

        Args:
            title: 标题
            description: 描述
            image_url: 图片URL
            **kwargs: 其他参数

        Returns:
            DandanSearchAnimeResponse
        """
        item = self.build_response_item(
            anime_id=999999998,
            title=f"✗ {title}",
            description=description,
            image_url=image_url,
            type="other",
            typeDescription="指令执行失败",
            **kwargs
        )
        return self.build_response([item])

