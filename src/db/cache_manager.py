"""
缓存管理器模块

提供统一的缓存管理接口，底层委托给 src.core.cache 的缓存后端。
保持原有 API 签名不变，确保现有调用方无需修改。

此模块位于 db 层，保持向后兼容。
"""

import logging
import asyncio
from typing import Any, Optional, List
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker


class CacheManager:
    """
    统一的缓存管理器

    底层使用 src.core.cache 的缓存后端（Memory/Redis/Database/Hybrid），
    对外保持原有接口不变。

    使用示例:
        cache_manager = CacheManager(session_factory)

        # 设置缓存
        await cache_manager.set("user_data", "user_123", {"name": "Alice"}, ttl_seconds=3600)

        # 获取缓存
        data = await cache_manager.get("user_data", "user_123")

        # 删除缓存
        await cache_manager.delete("user_data", "user_123")

        # 清除某个前缀的所有缓存
        await cache_manager.clear_by_prefix("user_data")
    """

    def __init__(self, session_factory: async_sessionmaker[AsyncSession], backend=None):
        """
        初始化缓存管理器

        Args:
            session_factory: SQLAlchemy异步会话工厂（保留用于向后兼容）
            backend: AsyncCacheBackend 实例，如果不提供则使用全局后端
        """
        self.session_factory = session_factory
        self._backend = backend
        self._lock = asyncio.Lock()
        self.logger = logging.getLogger(self.__class__.__name__)

    def _get_backend(self):
        """获取缓存后端，优先使用实例级别的，否则用全局的"""
        if self._backend is not None:
            return self._backend
        try:
            from src.core.cache import get_cache_backend
            return get_cache_backend()
        except RuntimeError:
            # 全局后端未初始化，回退到数据库直接操作
            return None

    async def get(self, prefix: str, key: str, session: Optional[AsyncSession] = None) -> Optional[Any]:
        """获取缓存数据"""
        backend = self._get_backend()
        if backend is not None:
            return await backend.get(key, region=prefix)

        # 回退：直接走数据库
        from . import crud
        cache_key = f"{prefix}{key}"
        if session:
            return await crud.get_cache(session, cache_key)
        async with self.session_factory() as new_session:
            return await crud.get_cache(new_session, cache_key)

    async def set(
        self,
        prefix: str,
        key: str,
        value: Any,
        ttl_seconds: int,
        session: Optional[AsyncSession] = None
    ) -> None:
        """设置缓存数据"""
        backend = self._get_backend()
        if backend is not None:
            await backend.set(key, value, ttl=ttl_seconds, region=prefix)
            return

        # 回退：直接走数据库
        from . import crud
        cache_key = f"{prefix}{key}"
        if session:
            await crud.set_cache(session, cache_key, value, ttl_seconds)
        else:
            async with self.session_factory() as new_session:
                await crud.set_cache(new_session, cache_key, value, ttl_seconds)
                await new_session.commit()

    async def delete(self, prefix: str, key: str, session: Optional[AsyncSession] = None) -> bool:
        """删除指定的缓存"""
        backend = self._get_backend()
        if backend is not None:
            return await backend.delete(key, region=prefix)

        # 回退
        from . import crud
        cache_key = f"{prefix}{key}"
        try:
            if session:
                await crud.delete_cache(session, cache_key)
            else:
                async with self.session_factory() as new_session:
                    await crud.delete_cache(new_session, cache_key)
                    await new_session.commit()
            return True
        except Exception as e:
            self.logger.error(f"删除缓存失败: {cache_key}, 错误: {e}")
            return False

    async def exists(self, prefix: str, key: str, session: Optional[AsyncSession] = None) -> bool:
        """检查缓存是否存在"""
        data = await self.get(prefix, key, session)
        return data is not None

    async def get_keys_by_pattern(
        self,
        pattern: str,
        session: Optional[AsyncSession] = None
    ) -> List[str]:
        """根据模式匹配获取缓存键列表"""
        # 这个方法只有数据库后端支持，直接走 CRUD
        from . import crud
        if session:
            return await crud.get_cache_keys_by_pattern(session, pattern)
        async with self.session_factory() as new_session:
            return await crud.get_cache_keys_by_pattern(new_session, pattern)

    async def clear_by_prefix(self, prefix: str, session: Optional[AsyncSession] = None) -> int:
        """清除指定前缀的所有缓存"""
        backend = self._get_backend()
        if backend is not None:
            return await backend.clear(region=prefix)

        # 回退
        from . import crud
        pattern = f"{prefix}*"
        keys = await self.get_keys_by_pattern(pattern, session)
        deleted_count = 0
        for cache_key in keys:
            try:
                if session:
                    await crud.delete_cache(session, cache_key)
                else:
                    async with self.session_factory() as new_session:
                        await crud.delete_cache(new_session, cache_key)
                        await new_session.commit()
                deleted_count += 1
            except Exception as e:
                self.logger.error(f"删除缓存失败: {cache_key}, 错误: {e}")
        self.logger.info(f"清除前缀 '{prefix}' 的缓存,共删除 {deleted_count} 条")
        return deleted_count

    async def clear_all(self, session: Optional[AsyncSession] = None) -> int:
        """清除所有缓存"""
        backend = self._get_backend()
        if backend is not None:
            count = await backend.clear(region=None)
            self.logger.info(f"清除所有缓存,共删除 {count} 条")
            return count

        # 回退
        from . import crud
        if session:
            deleted_count = await crud.clear_all_cache(session)
        else:
            async with self.session_factory() as new_session:
                deleted_count = await crud.clear_all_cache(new_session)
                await new_session.commit()
        self.logger.info(f"清除所有缓存,共删除 {deleted_count} 条")
        return deleted_count

    async def get_or_set(
        self,
        prefix: str,
        key: str,
        factory_func,
        ttl_seconds: int,
        session: Optional[AsyncSession] = None
    ) -> Any:
        """获取缓存,如果不存在则通过工厂函数创建并缓存"""
        # 先尝试获取
        cached_data = await self.get(prefix, key, session)
        if cached_data is not None:
            return cached_data

        # 使用锁防止并发创建
        async with self._lock:
            # 双重检查
            cached_data = await self.get(prefix, key, session)
            if cached_data is not None:
                return cached_data

            # 调用工厂函数生成数据
            new_data = await factory_func()

            # 缓存数据
            await self.set(prefix, key, new_data, ttl_seconds, session)

            return new_data

