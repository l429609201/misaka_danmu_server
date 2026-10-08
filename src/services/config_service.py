"""
配置管理服务模块

提供全局单例的配置管理服务，支持：
- 数据库配置读写
- 内存缓存加速
- 默认值注册
- 缓存失效管理

架构设计：
- 单例模式：通过 get_config_service() 全局访问
- 无循环依赖：独立于 db 层，通过 session_factory 注入
- 接口统一：get/set/invalidate/clear_cache/register_defaults

使用示例：
    from src.services.config_service import get_config_service

    config = get_config_service()
    value = await config.get("my_key", default="default_value")
    await config.set("my_key", "new_value")
"""

import asyncio
import logging
from typing import Any, Dict, Optional, Tuple
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

# ✅ 添加 DatabaseService 导入
from src.services.database_service import DatabaseService

logger = logging.getLogger(__name__)


class ConfigService:
    """
    配置管理服务（单例）

    提供全局配置的读写和缓存管理。
    设计上与 CacheService 保持一致的架构风格。
    """

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]):
        """
        初始化配置服务

        Args:
            session_factory: 数据库会话工厂
        """
        self._session_factory = session_factory
        self._db = DatabaseService(session_factory)  # ✅ 新架构：使用 DatabaseService
        self._cache: Dict[str, Any] = {}
        self._lock = asyncio.Lock()
        logger.info("ConfigService 初始化完成")

    async def get(self, key: str, default: Optional[Any] = None) -> Any:
        """
        获取配置项（带缓存）

        Args:
            key: 配置键
            default: 默认值（如果配置不存在）

        Returns:
            配置值
        """
        # 快速路径：缓存命中
        if key in self._cache:
            return self._cache[key]

        # 双重检查锁
        async with self._lock:
            if key in self._cache:
                return self._cache[key]

            # 从数据库读取
            async with self._db.transaction():
                value = await self._db.config.get_config_value(key, default)

            # 写入缓存
            self._cache[key] = value
            return value

    async def get_search_cache_ttl(self) -> int:
        """读取搜索结果缓存寿命，兼容历史非法配置并保证至少三小时。"""
        # 原始搜索与主页结果共用同一策略，避免各入口硬编码造成配置失效。
        minimum_ttl = 10800
        try:
            value = await self.get("searchTtlSeconds", minimum_ttl)
            return max(int(str(value).strip()), minimum_ttl)
        except (RuntimeError, TypeError, ValueError) as exc:
            logger.warning("读取搜索缓存 TTL 失败，使用 %d 秒: %s", minimum_ttl, exc)
            return minimum_ttl


    async def set(self, key: str, value: str) -> None:
        """
        设置配置项（写穿缓存）

        Args:
            key: 配置键
            value: 配置值
        """
        async with self._db.transaction():
            await self._db.config.upsert(key, value)

        # 失效缓存
        self.invalidate(key)
        logger.debug(f"配置已更新: {key}")

    async def register_defaults(self, defaults: Dict[str, Tuple[Any, str]]) -> None:
        """
        注册默认配置项

        检查数据库，如果配置项不存在，则使用提供的默认值和描述创建它。

        Args:
            defaults: 默认配置字典 {key: (value, description)}
        """
        async with self._db.transaction():
            await self._db.config.initialize_configs(defaults)
        logger.info(f"已注册 {len(defaults)} 个默认配置项")

    def invalidate(self, key: str) -> None:
        """
        失效指定配置的缓存

        Args:
            key: 配置键
        """
        if key in self._cache:
            del self._cache[key]
            logger.debug(f"配置缓存已失效: {key}")

    def clear_cache(self) -> None:
        """清空所有配置缓存"""
        self._cache.clear()
        logger.info("所有配置缓存已清空")

    def get_cache_size(self) -> int:
        """获取当前缓存的配置数量"""
        return len(self._cache)


# ==================== 全局单例访问 ====================

_config_service: Optional[ConfigService] = None


def init_config_service(session_factory: async_sessionmaker[AsyncSession]) -> ConfigService:
    """
    初始化全局 ConfigService 单例

    Args:
        session_factory: 数据库会话工厂

    Returns:
        ConfigService 实例
    """
    global _config_service
    if _config_service is not None:
        logger.warning("ConfigService 已经初始化，跳过重复初始化")
        return _config_service

    _config_service = ConfigService(session_factory)
    logger.info("✓ ConfigService 全局单例已初始化")
    return _config_service


def get_config_service() -> ConfigService:
    """
    获取全局 ConfigService 单例

    Returns:
        ConfigService 实例

    Raises:
        RuntimeError: 如果 ConfigService 尚未初始化
    """
    if _config_service is None:
        raise RuntimeError(
            "ConfigService not initialized. "
            "Call init_config_service(session_factory) first."
        )
    return _config_service
