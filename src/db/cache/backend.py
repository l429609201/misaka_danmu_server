"""
数据库缓存驱动（Database Cache Backend）

从 src.core.cache 迁移到 src.db.cache 层，解决 core → db 的反向依赖。

驱动职责：单一存储的增删改查，仅包装 CacheRepository 的原子操作，
不做跨驱动的回退或混合逻辑（混合调度归服务层 CacheService）。

物理键规则：继承 AsyncCacheBackend._make_key，自动拼接 f"{region}:{key}"。

序列化约定：cache_data.cacheValue 存 JSON 文本，序列化/反序列化在本驱动内完成，
Repository 层只负责字符串的读写。
"""

import json
import logging
from datetime import timedelta
from typing import Any, Optional, List

from src.core.cache import AsyncCacheBackend
from src.core.timezone import get_now
from src.db.repositories.cache import CacheRepository

logger = logging.getLogger(__name__)


class DatabaseBackend(AsyncCacheBackend):
    """
    基于数据库的缓存后端。

    经 CacheRepository 复用数据库 cache_data 表。
    """

    def __init__(self, session_factory):
        """
        构造数据库缓存驱动。

        Args:
            session_factory: AsyncSession 工厂，用于创建数据库会话。
        """
        self._session_factory = session_factory

    async def get(self, key: str, region: str = "default") -> Optional[Any]:
        """获取缓存值，不存在、已过期或反序列化失败均返回 None。"""
        full_key = self._make_key(region, key)
        async with self._session_factory() as session:
            raw = await CacheRepository(session).get_value(full_key)
            # get_value 内部会清理过期项，需提交该删除
            await session.commit()
        if raw is None:
            return None
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            logger.warning(f"缓存值 JSON 解析失败: key={full_key}, value={raw[:100]}")
            return None

    async def set(self, key: str, value: Any, ttl: int = 0, region: str = "default") -> None:
        """设置缓存值，ttl=0 表示不过期（内部转换为 1 年）。"""
        full_key = self._make_key(region, key)
        if ttl <= 0:
            ttl = 86400 * 365  # 不过期则设为 1 年
        json_value = json.dumps(value, ensure_ascii=False)
        expires_at = get_now() + timedelta(seconds=ttl)
        async with self._session_factory() as session:
            await CacheRepository(session).upsert(full_key, json_value, expires_at)
            await session.commit()

    async def set_many(self, entries: dict[str, tuple[Any, int]], region: str = "default") -> None:
        """整批映射使用一个会话和一次提交，不再逐键创建事务。"""
        if not entries:
            return
        async with self._session_factory() as session:
            async with session.begin():
                await CacheRepository(session).set_json_many({
                    self._make_key(region, key): entry for key, entry in entries.items()
                })


    async def get_by_prefix(self, prefix: str, region: str = "default") -> dict[str, Any]:
        """一次 SQL 读取有效缓存并剥离物理区域前缀。"""
        async with self._session_factory() as session:
            values = await CacheRepository(session).get_json_by_prefix(self._make_key(region, prefix))
        region_prefix = f"{region}:"
        return {key[len(region_prefix):]: value for key, value in values.items()}


    async def delete(self, key: str, region: str = "default") -> bool:
        """删除缓存，返回是否成功删除（存在且已删除）。"""
        full_key = self._make_key(region, key)
        async with self._session_factory() as session:
            deleted = await CacheRepository(session).delete(full_key)
            await session.commit()
        return deleted

    async def exists(self, key: str, region: str = "default") -> bool:
        """检查缓存是否存在（未过期）。"""
        return (await self.get(key, region)) is not None

    async def clear(self, region: Optional[str] = None) -> int:
        """
        清除缓存，指定 region 只清该区域，否则全清。

        Returns:
            清除的键数量。
        """
        async with self._session_factory() as session:
            repo = CacheRepository(session)
            if region is None:
                count = await repo.clear_all()
                await session.commit()
                return count
            # 按 region 前缀清理
            keys = await repo.get_keys_by_pattern(f"{region}:*")
            for k in keys:
                await repo.delete(k)
            await session.commit()
            return len(keys)

    async def keys(self, pattern: str = "*", region: str = "default") -> List[str]:
        """
        列出匹配模式的缓存键（不含 region 前缀）。

        Args:
            pattern: glob 模式，如 "user_*"
            region: 缓存区域

        Returns:
            业务键列表（已剥离 region 前缀）
        """
        full_pattern = self._make_key(region, pattern)
        prefix = f"{region}:"
        async with self._session_factory() as session:
            full_keys = await CacheRepository(session).get_keys_by_pattern(full_pattern)
        # 剥离 region 前缀，返回业务键
        return [k.replace(prefix, "", 1) for k in full_keys if k.startswith(prefix)]
