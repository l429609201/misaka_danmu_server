"""
CacheRepository - 缓存数据访问层

从 src.db.crud.fallback 和 src.db.crud.cache 迁移而来的所有缓存相关函数
"""

import logging
import json
from typing import Optional, List, Any, Dict
from datetime import datetime, timedelta
from sqlalchemy import select, delete as sa_delete
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.dialects.mysql import insert as mysql_insert
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from ..orm_models import CacheData
from .base import BaseRepository
from src.core.timezone import get_now

logger = logging.getLogger(__name__)

# ═══════════ 缓存键常量 ═══════════
EPISODE_MAPPING_CACHE_PREFIX = "episode_mapping_"
FALLBACK_SEARCH_CACHE_PREFIX = "fallback_search_"
USER_LAST_BANGUMI_CHOICE_PREFIX = "user_last_bangumi_"

# 缓存TTL
FALLBACK_SEARCH_CACHE_TTL = 3600
USER_LAST_BANGUMI_CHOICE_TTL = 86400
EPISODE_MAPPING_TTL = 10800

# 缓存 region
_CACHE_REGION = "default"


class CacheRepository(BaseRepository[CacheData]):
    """缓存 Repository"""

    async def set_json_many(self, entries: Dict[str, tuple[Any, int]]) -> None:
        """分块批量写入 JSON 缓存，每项携带独立 TTL，事务由调用方管理。"""
        if not entries:
            return
        now = get_now()
        table = CacheData.__table__
        dialect = self._session.get_bind().dialect.name
        rows = [
            {"cache_key": key, "cache_value": json.dumps(value, ensure_ascii=False),
             "expires_at": now + timedelta(seconds=ttl if ttl > 0 else 86400 * 365)}
            for key, (value, ttl) in entries.items()
        ]
        # 限制单条语句的参数数量，避免长剧集超过数据库绑定参数上限。
        for offset in range(0, len(rows), 200):
            batch = rows[offset:offset + 200]
            if dialect in ("mysql", "mariadb"):
                stmt = mysql_insert(table).values(batch)
                stmt = stmt.on_duplicate_key_update(
                    cache_value=stmt.inserted.cache_value, expires_at=stmt.inserted.expires_at
                )
            elif dialect in ("postgresql", "sqlite"):
                insert_factory = pg_insert if dialect == "postgresql" else sqlite_insert
                stmt = insert_factory(table).values(batch)
                stmt = stmt.on_conflict_do_update(
                    index_elements=[table.c.cache_key],
                    set_={"cache_value": stmt.excluded.cache_value,
                          "expires_at": stmt.excluded.expires_at},
                )
            else:
                raise ValueError(f"不支持批量缓存写入的数据库: {dialect}")
            await self._session.execute(stmt)
        await self._session.flush()


    async def get_by_id(self, cache_key: str) -> Optional[CacheData]:
        """根据 key 获取缓存"""
        return await self._session.get(CacheData, cache_key)

    async def get_value(self, cache_key: str) -> Optional[str]:
        """获取缓存值"""
        cache = await self.get_by_id(cache_key)
        if not cache:
            return None

        # 检查是否过期
        if cache.expiresAt and cache.expiresAt < get_now():
            await self.delete(cache_key)
            return None

        return cache.cacheValue

    async def get_all(self, **filters) -> List[CacheData]:
        """获取所有缓存"""
        stmt = select(CacheData)
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def create(self, cache_key: str, cache_value: str, expires_at: datetime = None) -> CacheData:
        """创建缓存"""
        cache = CacheData(
            cacheKey=cache_key,
            cacheValue=cache_value,
            expiresAt=expires_at,
        )
        self._session.add(cache)
        await self._session.flush()
        return cache

    async def update(self, cache_key: str, cache_value: str = None, expires_at: datetime = None) -> Optional[CacheData]:
        """更新缓存"""
        cache = await self.get_by_id(cache_key)
        if not cache:
            return None

        if cache_value is not None:
            cache.cacheValue = cache_value
        if expires_at is not None:
            cache.expiresAt = expires_at

        await self._session.flush()
        return cache

    async def upsert(self, cache_key: str, cache_value: str, expires_at: datetime = None) -> CacheData:
        """插入或更新缓存"""
        cache = await self.get_by_id(cache_key)
        if cache:
            cache.cacheValue = cache_value
            cache.expiresAt = expires_at
        else:
            cache = CacheData(
                cacheKey=cache_key,
                cacheValue=cache_value,
                expiresAt=expires_at,
            )
            self._session.add(cache)

        await self._session.flush()
        return cache

    async def delete(self, cache_key: str) -> bool:
        """删除缓存"""
        cache = await self.get_by_id(cache_key)
        if not cache:
            return False

        await self._session.delete(cache)
        await self._session.flush()
        return True

    async def delete_expired(self) -> int:
        """删除所有过期缓存"""
        stmt = select(CacheData).where(CacheData.expiresAt < get_now())
        result = await self._session.execute(stmt)
        expired = result.scalars().all()

        count = 0
        for cache in expired:
            await self._session.delete(cache)
            count += 1

        await self._session.flush()
        return count

    async def get_keys_by_prefix(self, prefix: str) -> List[str]:
        """获取指定前缀的所有缓存键（从 crud.cache 迁移）"""
        stmt = select(CacheData.cacheKey).where(CacheData.cacheKey.like(f"{prefix}%"))
        result = await self._session.execute(stmt)
        return [row[0] for row in result.all()]

    async def get_keys_by_pattern(self, pattern: str) -> List[str]:
        """
        按 glob 模式获取未过期的缓存键（替代 crud.get_cache_keys_by_pattern）。

        why：数据库缓存驱动需要按 region 前缀批量清理，前缀匹配不足以表达
        "region:user_*" 这类模式，因此保留通配符查询能力。

        Args:
            pattern: glob 模式，'*' 会被转换为 SQL 的 '%'

        Returns:
            匹配且未过期的完整缓存键列表
        """
        sql_pattern = pattern.replace("*", "%")
        stmt = select(CacheData.cacheKey).where(
            CacheData.cacheKey.like(sql_pattern),
            CacheData.expiresAt > get_now(),
        )
        result = await self._session.execute(stmt)
        return [row[0] for row in result.all()]

    # ═══════════ JSON 值读写（crud.get_cache / set_cache 的等价替代） ═══════════

    async def get_json(self, cache_key: str) -> Optional[Any]:
        """读取并反序列化 JSON 缓存值。

        why：原 crud.get_cache 内建 json.loads 与过期判定，调用方（搜索分页缓存、
        后备搜索等）直接消费 dict/list。若改用裸 get_value 会把 JSON 字符串
        透给调用方，导致类型错乱，故在 Repository 内保留同等语义。

        Args:
            cache_key: 完整缓存键

        Returns:
            反序列化后的对象；未命中、已过期或解析失败时返回 None
        """
        cached_value = await self.get_value(cache_key)
        if cached_value is None:
            return None
        try:
            return json.loads(cached_value)
        except (json.JSONDecodeError, TypeError):
            logger.warning(f"解析 JSON 缓存失败: {cache_key}")
            return None

    async def get_json_by_prefix(self, prefix: str) -> Dict[str, Any]:
        """一次查询读取前缀下未过期的 JSON 缓存，避免逐键查询。"""
        stmt = select(CacheData.cacheKey, CacheData.cacheValue).where(
            CacheData.cacheKey.startswith(prefix, autoescape=True),
            (CacheData.expiresAt.is_(None)) | (CacheData.expiresAt >= get_now()),
        )
        result = await self._session.execute(stmt)
        values: Dict[str, Any] = {}
        for key, value in result.all():
            try:
                values[key] = json.loads(value)
            except (json.JSONDecodeError, TypeError):
                # 单条损坏缓存不应阻断其他搜索会话的源切换。
                logger.warning("解析 JSON 缓存失败: %s", key)
        return values


    async def set_json(
        self,
        cache_key: str,
        value: Any,
        ttl_seconds: int
    ) -> None:
        """序列化并写入 JSON 缓存值，按相对 TTL 计算过期时间。

        why：原 crud.set_cache 接收 ttl_seconds（相对秒数），而 upsert 接收
        expires_at（绝对时间）。此方法承接 TTL 换算，避免每个调用点重复
        `get_now() + timedelta(...)`。

        Args:
            cache_key: 完整缓存键
            value: 任意可 JSON 序列化的对象
            ttl_seconds: 相对存活秒数
        """
        json_value = json.dumps(value, ensure_ascii=False)
        expires_at = get_now() + timedelta(seconds=ttl_seconds)
        await self.upsert(cache_key, json_value, expires_at)

    async def clear_all(self) -> int:
        """
        清空全部缓存（替代 crud.clear_all_cache）。

        Returns:
            被删除的记录数
        """
        result = await self._session.execute(sa_delete(CacheData))
        await self._session.flush()
        return int(result.rowcount or 0)

    # ═══════════ Episode 映射缓存 ═══════════

    async def store_episode_mapping(
        self,
        episode_id: int,
        anime_id: int,
        source_id: int,
        episode_number: int
    ) -> None:
        """存储 episodeId → 源坐标 的映射（从 crud.fallback 迁移）"""
        cache_key = f"{EPISODE_MAPPING_CACHE_PREFIX}{episode_id}"
        mapping_data = {
            "animeId": anime_id,
            "sourceId": source_id,
            "episodeNumber": episode_number,
        }

        # 复用统一的 JSON 写入，避免重复序列化与 TTL 换算
        await self.set_json(cache_key, mapping_data, EPISODE_MAPPING_TTL)
        logger.debug(f"已缓存 episode 映射: {cache_key} → {mapping_data}")

    async def update_episode_mapping(
        self,
        episode_id: int,
        anime_id: int,
        source_id: int,
        episode_number: int
    ) -> None:
        """更新 episodeId 映射（从 crud.fallback 迁移）"""
        await self.store_episode_mapping(episode_id, anime_id, source_id, episode_number)

    async def get_episode_mapping(self, episode_id: int) -> Optional[Dict[str, Any]]:
        """获取 episodeId 映射（从 crud.fallback 迁移）"""
        cache_key = f"{EPISODE_MAPPING_CACHE_PREFIX}{episode_id}"
        cached_value = await self.get_value(cache_key)

        if not cached_value:
            return None

        try:
            return json.loads(cached_value)
        except (json.JSONDecodeError, TypeError):
            logger.warning(f"解析 episode 映射缓存失败: {cache_key}")
            return None

    # ═══════════ 后备搜索缓存 ═══════════

    async def cache_fallback_search(
        self,
        search_key: str,
        result_data: Any,
        ttl_seconds: int = FALLBACK_SEARCH_CACHE_TTL
    ) -> None:
        """缓存后备搜索结果（从 crud.fallback 迁移）"""
        cache_key = f"{FALLBACK_SEARCH_CACHE_PREFIX}{search_key}"
        # 复用统一的 JSON 写入，避免重复序列化与 TTL 换算
        await self.set_json(cache_key, result_data, ttl_seconds)
        logger.debug(f"已缓存后备搜索: {cache_key}")

    async def get_fallback_search_cache(self, search_key: str) -> Optional[Any]:
        """获取后备搜索缓存（从 crud.fallback 迁移）"""
        cache_key = f"{FALLBACK_SEARCH_CACHE_PREFIX}{search_key}"
        cached_value = await self.get_value(cache_key)

        if not cached_value:
            return None

        try:
            return json.loads(cached_value)
        except (json.JSONDecodeError, TypeError):
            logger.warning(f"解析后备搜索缓存失败: {cache_key}")
            return None

    # ═══════════ 用户选择缓存 ═══════════

    async def store_user_bangumi_choice(
        self,
        user_id: str,
        bangumi_id: int,
        anime_id: int
    ) -> None:
        """存储用户最近的 Bangumi 选择（从 crud.fallback 迁移）"""
        cache_key = f"{USER_LAST_BANGUMI_CHOICE_PREFIX}{user_id}_{bangumi_id}"
        expires_at = get_now() + timedelta(seconds=USER_LAST_BANGUMI_CHOICE_TTL)
        await self.upsert(cache_key, str(anime_id), expires_at)
        logger.debug(f"已缓存用户选择: {cache_key} → anime_id={anime_id}")

    async def get_user_bangumi_choice(
        self,
        user_id: str,
        bangumi_id: int
    ) -> Optional[int]:
        """获取用户最近的 Bangumi 选择（从 crud.fallback 迁移）"""
        cache_key = f"{USER_LAST_BANGUMI_CHOICE_PREFIX}{user_id}_{bangumi_id}"
        cached_value = await self.get_value(cache_key)

        if not cached_value:
            return None

        try:
            return int(cached_value)
        except (ValueError, TypeError):
            logger.warning(f"解析用户选择缓存失败: {cache_key}")
            return None
