"""
指令系统基础模块
提供指令处理器基类和通用工具函数
"""
import time
import logging
from typing import Optional, Tuple, List, Any

from src.schemas.dandan import DandanSearchAnimeResponse, DandanSearchAnimeItem
from src.services.cache_service import get_cache_service
from src.workflows.image_public_url import get_custom_domain

logger = logging.getLogger(__name__)
COMMAND_HANDLERS: dict[str, "CommandHandler"] = {}


# ═══════════ 缓存读写薄壳（已收口至 CacheService） ═══════════
# why: 实现已迁移到 CacheService.get_with_prefix / set_with_prefix，
#      服务层内部自持驱动与数据库回退，不再需要显式 session。
#      此处保留 session 形参仅为兼容既有调用签名，函数体内不使用。


async def _get_db_cache(session: object, prefix: str, key: str) -> Optional[Any]:
    """从缓存读取数据（前缀式组合键）。

    Args:
        session: 保留形参以兼容既有调用点，实际不使用
        prefix: 缓存键前缀
        key: 业务键

    Returns:
        缓存值；未命中或缓存服务未初始化时返回 None
    """
    cache = get_cache_service()
    if cache is None:
        logger.debug(f"缓存服务未初始化，读取降级为 None: {prefix}{key}")
        return None
    return await cache.get_with_prefix(prefix, key)


async def _set_db_cache(session: object, prefix: str, key: str, value: Any, ttl: int) -> None:
    """写入缓存（前缀式组合键）。

    Args:
        session: 保留形参以兼容既有调用点，实际不使用
        prefix: 缓存键前缀
        key: 业务键
        value: 待缓存的值
        ttl: 过期时间（秒）
    """
    cache = get_cache_service()
    if cache is None:
        logger.debug(f"缓存服务未初始化，跳过写入: {prefix}{key}")
        return
    await cache.set_with_prefix(prefix, key, value, ttl)


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

    async def can_execute(self, token: str, session: object) -> Tuple[bool, int]:
        """
        检查是否可以执行指令（冷却检查）

        Args:
            token: 用户token
            session: 保留的兼容参数，命令不直接操作会话

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

    async def execute(self, token: str, args: List[str], session: object,
                     config_service, **kwargs):
        """
        执行指令，子类需要实现

        Args:
            token: 用户token
            args: 指令参数
            session: 保留的兼容参数，命令不直接操作会话
            config_service: 配置服务
            **kwargs: 其他依赖

        Returns:
            DandanSearchAnimeResponse
        """
        raise NotImplementedError

    async def record_execution(self, token: str, session: object):
        """
        记录执行时间

        Args:
            token: 用户token
            session: 保留的兼容参数，命令不直接操作会话
        """
        if self.cooldown_seconds > 0:
            cache_key = f"{token}_{self.name}"
            await _set_db_cache(session, "command_cooldown_", cache_key, time.time(), self.cooldown_seconds)

    async def get_image_url(self, config_service) -> str:
        """
        获取图片URL（logo 地址，域名允许 http/https，格式不合规时降级为相对路径）

        Args:
            config_service: 配置服务

        Returns:
            图片URL
        """
        custom_domain = await get_custom_domain(config_service)
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

