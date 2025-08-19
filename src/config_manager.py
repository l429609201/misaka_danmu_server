import asyncio
import logging
from typing import Any, Dict, Optional, Tuple

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from . import crud


class ConfigManager:
    """
    一个用于集中管理、缓存和初始化数据库配置项的管理器。
    """

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]):
        self.session_factory = session_factory
        self._cache: Dict[str, Any] = {}
        self._lock = asyncio.Lock()
        self.logger = logging.getLogger(self.__class__.__name__)

    async def get(self, key: str, default: Optional[Any] = None) -> Any:
        """
        从缓存或数据库中获取一个配置项。
        如果缓存中存在，则直接返回。
        否则，从数据库中获取，存入缓存，然后返回。
        """
        if key in self._cache:
            return self._cache[key]

        async with self._lock:
            # 再次检查，防止在等待锁的过程中其他协程已经加载了配置
            if key in self._cache:
                return self._cache[key]

            async with self.session_factory() as session:
                value = await crud.get_config_value(session, key, default)
            self._cache[key] = value
            return value

    async def register_defaults(self, defaults: Dict[str, Tuple[Any, str]]):
        """
        注册默认配置项。
        此方法会检查数据库，如果配置项不存在，则使用提供的默认值和描述创建它。
        """
        async with self.session_factory() as session:
            await crud.initialize_configs(session, defaults)

    def invalidate(self, key: str):
        """从缓存中移除一个特定的键，以便下次获取时能从数据库重新加载。"""
        if key in self._cache:
            del self._cache[key]
            self.logger.info(f"配置缓存已失效: '{key}'")

    def clear_cache(self):
        """清空内存中的配置缓存，以便下次获取时能从数据库重新加载。"""
        self._cache.clear()
        self.logger.info("所有配置缓存已清空。")