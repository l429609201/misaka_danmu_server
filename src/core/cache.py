"""
缓存抽象层

提供统一的缓存后端接口，支持 Memory / Redis / Database / Hybrid 四种后端。
默认 hybrid 模式：内存作为 L1 缓存 + 数据库作为 L2 持久化。

配置方式（config.yml）:
    cache:
      backend: "hybrid"          # memory / redis / database / hybrid
      redis_url: "redis://localhost:6379"
      memory_maxsize: 1024
      memory_default_ttl: 600

环境变量覆盖:
    DANMUAPI_CACHE__BACKEND=redis
    DANMUAPI_CACHE__REDIS_URL=redis://localhost:6379
"""

import json
import time
import logging
import asyncio
import hashlib
import functools
from abc import ABC, abstractmethod
from typing import Any, Optional, List, Callable, Union

logger = logging.getLogger(__name__)


def _match_wildcard(pattern: str, text: str) -> bool:
    """简单通配符匹配，仅支持 * 匹配任意字符"""
    if pattern == "*":
        return True
    import fnmatch
    return fnmatch.fnmatch(text, pattern)


# ==================== 抽象基类 ====================

class AsyncCacheBackend(ABC):
    """异步缓存后端抽象基类"""

    @abstractmethod
    async def get(self, key: str, region: str = "default") -> Optional[Any]:
        """获取缓存值，不存在或已过期返回 None"""

    @abstractmethod
    async def set(self, key: str, value: Any, ttl: int = 0, region: str = "default") -> None:
        """设置缓存值，ttl=0 表示不过期"""

    @abstractmethod
    async def delete(self, key: str, region: str = "default") -> bool:
        """删除缓存，返回是否成功"""

    @abstractmethod
    async def exists(self, key: str, region: str = "default") -> bool:
        """检查缓存是否存在"""

    @abstractmethod
    async def clear(self, region: Optional[str] = None) -> int:
        """清除缓存，指定 region 则只清该区域，否则全清。返回清除数量"""

    async def keys(self, pattern: str = "*", region: str = "default") -> List[str]:
        """
        按模式列出缓存键（不含 region 前缀）

        Args:
            pattern: 匹配模式，支持 * 通配符
            region: 缓存区域

        Returns:
            匹配的原始 key 列表（不含 region: 前缀）
        """
        return []

    async def set_many(self, entries: dict[str, tuple[Any, int]], region: str = "default") -> None:
        """默认批量接口适用于无网络往返的内存驱动，远端驱动覆盖实现。"""
        for key, (value, ttl) in entries.items():
            await self.set(key, value, ttl=ttl, region=region)


    async def get_by_prefix(self, prefix: str, region: str = "default") -> dict[str, Any]:
        """内存驱动默认实现；远端驱动使用批量查询覆盖此方法。"""
        keys = await self.keys(f"{prefix}*", region=region)
        values = {}
        for key in keys:
            value = await self.get(key, region=region)
            if value is not None:
                values[key] = value
        return values


    async def close(self) -> None:
        """关闭后端连接（子类按需覆盖）"""
        pass

    def _make_key(self, region: str, key: str) -> str:
        """生成带 region 前缀的完整 key"""
        return f"{region}:{key}"


# ==================== Memory 后端 ====================

class MemoryBackend(AsyncCacheBackend):
    """
    基于进程内存的缓存后端
    使用 dict + 过期时间戳实现，轻量高效
    """

    def __init__(self, maxsize: int = 1024, default_ttl: int = 600):
        self._store: dict[str, tuple[Any, float]] = {}  # key -> (value, expire_timestamp)
        self._maxsize = maxsize
        self._default_ttl = default_ttl
        self._lock = asyncio.Lock()

    async def get(self, key: str, region: str = "default") -> Optional[Any]:
        full_key = self._make_key(region, key)
        entry = self._store.get(full_key)
        if entry is None:
            return None
        value, expire_at = entry
        if expire_at > 0 and time.time() > expire_at:
            del self._store[full_key]
            return None
        return value

    async def set(self, key: str, value: Any, ttl: int = 0, region: str = "default") -> None:
        full_key = self._make_key(region, key)
        expire_at = (time.time() + ttl) if ttl > 0 else 0
        async with self._lock:
            # 超出容量时淘汰最早的条目
            if full_key not in self._store and len(self._store) >= self._maxsize:
                self._evict()
            self._store[full_key] = (value, expire_at)

    async def delete(self, key: str, region: str = "default") -> bool:
        full_key = self._make_key(region, key)
        return self._store.pop(full_key, None) is not None

    async def exists(self, key: str, region: str = "default") -> bool:
        return await self.get(key, region) is not None

    async def clear(self, region: Optional[str] = None) -> int:
        if region is None:
            count = len(self._store)
            self._store.clear()
            return count
        prefix = f"{region}:"
        keys_to_delete = [k for k in self._store if k.startswith(prefix)]
        for k in keys_to_delete:
            del self._store[k]
        return len(keys_to_delete)

    async def keys(self, pattern: str = "*", region: str = "default") -> List[str]:
        prefix = f"{region}:"
        now = time.time()
        result = []
        for full_key, (_, expire_at) in list(self._store.items()):
            if not full_key.startswith(prefix):
                continue
            if 0 < expire_at <= now:
                continue
            raw_key = full_key[len(prefix):]
            if _match_wildcard(pattern, raw_key):
                result.append(raw_key)
        return result

    def _evict(self):
        """淘汰过期条目，如果没有过期的则淘汰最早插入的"""
        now = time.time()
        # 先清理过期的
        expired = [k for k, (_, exp) in self._store.items() if 0 < exp <= now]
        if expired:
            for k in expired:
                del self._store[k]
            return
        # 没有过期的，删最早的一个（FIFO）
        if self._store:
            oldest_key = next(iter(self._store))
            del self._store[oldest_key]


# ==================== Redis 后端 ====================

class RedisBackend(AsyncCacheBackend):
    """
    基于 Redis 的缓存后端
    使用 redis.asyncio，JSON 优先序列化，pickle 兜底
    """

    def __init__(self, redis_url: str, max_memory: str = "256mb",
                 socket_timeout: int = 30, socket_connect_timeout: int = 5):
        # 兼容 Valkey: 自动将 valkey:// / valkeys:// 转换为 redis:// / rediss://
        from urllib.parse import urlparse
        parsed = urlparse(redis_url)
        if parsed.scheme == "valkey":
            redis_url = redis_url.replace("valkey://", "redis://", 1)
            logger.info("检测到 Valkey URL，已自动转换为 redis:// 协议")
        elif parsed.scheme == "valkeys":
            redis_url = redis_url.replace("valkeys://", "rediss://", 1)
            logger.info("检测到 Valkey TLS URL，已自动转换为 rediss:// 协议")

        self._redis_url = redis_url
        self._max_memory = max_memory
        self._socket_timeout = socket_timeout
        self._socket_connect_timeout = socket_connect_timeout
        self._client = None
        self._lock = asyncio.Lock()

        # 日志只打印 host:port，隐藏密码
        try:
            self._safe_url = f"{parsed.hostname or 'localhost'}:{parsed.port or 6379}/{parsed.path.strip('/') or '0'}"
        except Exception:
            self._safe_url = "redis://***"

    async def _get_client(self):
        """懒初始化 Redis 连接"""
        if self._client is None:
            async with self._lock:
                if self._client is None:
                    try:
                        import redis.asyncio as aioredis
                    except ImportError:
                        raise ImportError(
                            "使用 Redis 缓存后端需要安装 redis 包: pip install redis"
                        )
                    self._client = aioredis.from_url(
                        self._redis_url,
                        socket_timeout=self._socket_timeout,
                        socket_connect_timeout=self._socket_connect_timeout,
                        decode_responses=False,
                        health_check_interval=60,
                    )
                    # 设置 Redis 内存策略
                    try:
                        await self._client.config_set("maxmemory", self._max_memory)
                        await self._client.config_set("maxmemory-policy", "allkeys-lru")
                    except Exception as e:
                        logger.warning(f"设置 Redis 内存策略失败（可能无权限）: {e}")
        return self._client

    def _serialize(self, value: Any) -> bytes:
        """序列化：JSON 优先，pickle 兜底"""
        try:
            data = json.dumps(value, ensure_ascii=False)
            return b"J" + data.encode("utf-8")
        except (TypeError, ValueError):
            import pickle
            return b"P" + pickle.dumps(value)

    def _deserialize(self, raw: bytes) -> Any:
        """反序列化"""
        if raw is None:
            return None
        marker, payload = raw[:1], raw[1:]
        if marker == b"J":
            return json.loads(payload.decode("utf-8"))
        elif marker == b"P":
            return pickle.loads(payload)
        # 兼容无标记的旧数据
        try:
            return json.loads(raw.decode("utf-8"))
        except Exception:
            return None

    async def get(self, key: str, region: str = "default") -> Optional[Any]:
        client = await self._get_client()
        full_key = self._make_key(region, key)
        raw = await client.get(full_key)
        if raw is None:
            return None
        return self._deserialize(raw)

    async def set(self, key: str, value: Any, ttl: int = 0, region: str = "default") -> None:
        client = await self._get_client()
        full_key = self._make_key(region, key)
        data = self._serialize(value)
        if ttl > 0:
            await client.setex(full_key, ttl, data)
        else:
            await client.set(full_key, data)

    async def set_many(self, entries: dict[str, tuple[Any, int]], region: str = "default") -> None:
        """通过一次 pipeline 写入整批映射，保留每条记录的 TTL。"""
        if not entries:
            return
        client = await self._get_client()
        # 先序列化，失败时不发送半批数据。
        values = [(self._make_key(region, key), self._serialize(value), ttl)
                  for key, (value, ttl) in entries.items()]
        async with client.pipeline(transaction=True) as pipe:
            for key, value, ttl in values:
                pipe.set(key, value, ex=ttl if ttl > 0 else None)
            await pipe.execute()


    async def get_by_prefix(self, prefix: str, region: str = "default") -> dict[str, Any]:
        """扫描一次键空间，再分块 MGET，避免逐键网络请求。"""
        keys = await self.keys(f"{prefix}*", region=region)
        client = await self._get_client()
        values = {}
        for offset in range(0, len(keys), 200):
            batch = keys[offset:offset + 200]
            raw_values = await client.mget([self._make_key(region, key) for key in batch])
            for key, raw in zip(batch, raw_values):
                if raw is not None:
                    values[key] = self._deserialize(raw)
        return values


    async def delete(self, key: str, region: str = "default") -> bool:
        client = await self._get_client()
        full_key = self._make_key(region, key)
        return (await client.delete(full_key)) > 0

    async def exists(self, key: str, region: str = "default") -> bool:
        client = await self._get_client()
        full_key = self._make_key(region, key)
        return (await client.exists(full_key)) > 0

    async def clear(self, region: Optional[str] = None) -> int:
        client = await self._get_client()
        if region is None:
            # 先获取当前 key 数量，再 flushdb，以便返回准确的清除条数
            try:
                count = await client.dbsize()
            except Exception:
                count = -1
            await client.flushdb()
            return count
        pattern = f"{region}:*"
        count = 0
        async for key in client.scan_iter(match=pattern, count=100):
            await client.delete(key)
            count += 1
        return count

    async def keys(self, pattern: str = "*", region: str = "default") -> List[str]:
        client = await self._get_client()
        full_pattern = self._make_key(region, pattern)
        prefix = f"{region}:"
        result = []
        async for raw_key in client.scan_iter(match=full_pattern, count=100):
            key_str = raw_key.decode("utf-8") if isinstance(raw_key, bytes) else raw_key
            if key_str.startswith(prefix):
                result.append(key_str[len(prefix):])
            else:
                result.append(key_str)
        return result

    async def close(self) -> None:
        if self._client:
            await self._client.aclose()
            self._client = None
            logger.info("Redis 缓存后端已关闭")


# ==================== @cached 装饰器 ====================
# C4 注意：此装饰器依赖已废弃的 _global_backend，实际项目中已无使用。
# 保留仅为兼容性，建议业务代码改用 CacheService。

_global_backend: Optional[AsyncCacheBackend] = None  # 仅供 @cached 装饰器过渡使用

def cached(
    region: str = "default",
    ttl: int = 300,
    key_prefix: str = "",
    skip_none: bool = True,
    backend: Optional[AsyncCacheBackend] = None,
):
    """
    函数级缓存装饰器

    用法:
        @cached(region="comments", ttl=300)
        async def get_comments(episode_id: int):
            ...

        @cached(region="metadata", ttl=21600, key_prefix="tmdb")
        async def search_metadata(title: str, year: int):
            ...

    Args:
        region: 缓存区域名称，用于隔离不同功能的缓存
        ttl: 缓存过期时间（秒），默认 5 分钟
        key_prefix: 额外的 key 前缀
        skip_none: 如果函数返回 None 则不缓存（默认 True）
        backend: 指定缓存后端，默认使用全局后端
    """
    def decorator(func: Callable):
        @functools.wraps(func)
        async def wrapper(*args, **kwargs):
            # 获取后端
            cache_backend = backend or _global_backend
            if cache_backend is None:
                # 缓存未初始化，直接执行函数
                return await func(*args, **kwargs)

            # 自动生成缓存 key
            cache_key = _generate_cache_key(func, key_prefix, *args, **kwargs)

            # 尝试从缓存获取
            try:
                cached_value = await cache_backend.get(cache_key, region=region)
                if cached_value is not None:
                    return cached_value
            except Exception as e:
                logger.warning(f"缓存读取失败 [{region}:{cache_key}]: {e}")

            # 缓存未命中，执行函数
            result = await func(*args, **kwargs)

            # 写入缓存
            if result is not None or not skip_none:
                try:
                    await cache_backend.set(cache_key, result, ttl=ttl, region=region)
                except Exception as e:
                    logger.warning(f"缓存写入失败 [{region}:{cache_key}]: {e}")

            return result
        return wrapper
    return decorator


def _generate_cache_key(func: Callable, prefix: str, *args, **kwargs) -> str:
    """
    根据函数名和参数自动生成缓存 key
    使用 MD5 哈希确保 key 长度可控
    """
    parts = [func.__module__, func.__qualname__]
    if prefix:
        parts.insert(0, prefix)

    # 序列化参数
    arg_parts = []
    for arg in args:
        # 跳过 self/cls 和不可序列化的对象（如 session）
        if hasattr(arg, '__dict__') and not isinstance(arg, (str, int, float, bool, list, dict, tuple)):
            continue
        arg_parts.append(str(arg))
    for k, v in sorted(kwargs.items()):
        if hasattr(v, '__dict__') and not isinstance(v, (str, int, float, bool, list, dict, tuple)):
            continue
        arg_parts.append(f"{k}={v}")

    raw_key = ":".join(parts) + ":" + ",".join(arg_parts)

    # 如果 key 太长，用 MD5 缩短
    if len(raw_key) > 200:
        key_hash = hashlib.md5(raw_key.encode()).hexdigest()
        func_name = func.__qualname__.split(".")[-1]
        return f"{prefix}:{func_name}:{key_hash}" if prefix else f"{func_name}:{key_hash}"

    return raw_key
