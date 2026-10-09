"""
缓存服务层（Cache Service）

三层缓存架构的中间「服务层」，是业务层访问缓存的唯一入口。

设计目标（方案C，用户已拍板）：
1. 驱动层（src.core.cache）只保留单一存储驱动：Memory / Redis / Database，
   各驱动只管自己存储的增删改查，不做跨驱动回退。
2. 服务层 CacheService 负责：整合调度 + 回退降级 + 混合 L1/L2 协调，
   对外提供统一入口 get/set/delete/exists/clear/keys/get_or_set。
3. 业务层只用 get_cache_service()，不再散落「判空 + 数据库回退」模板。

混合（hybrid）模式的 L1（内存）+ L2（数据库）组合调度全部在本层实现：
    - get：先查内存 L1，miss 再查数据库 L2 并回填 L1；
    - set：L1 同步写入，L2 后台异步落库（不阻塞调用方响应路径）；
    - 重启后内存丢失，数据库仍可回填。

物理键规则（方案C决策4）：物理键 = f"{region}:{业务key}"，由底层驱动的
_make_key 统一拼接；本层只透传 (key, region)，不再自行拼前缀。

全局单例：get_cache_service() + init_cache_service()。
业务调用直接使用服务公开接口，不暴露底层驱动实例。
Tasks / Workflows 等非 HTTP 上下文同样通过全局单例获取服务；
API 层可另行提供 Depends 包装器（内部同样调用 get_cache_service()）。
"""

import asyncio
import json
import logging
from typing import Any, Awaitable, Callable, List, Optional

# C2 重构完成：DatabaseBackend 已迁移到 db.cache，core 只保留抽象层和 Memory/Redis 单一驱动
from src.core.cache import (
    AsyncCacheBackend,
    MemoryBackend,
    RedisBackend,
)
from src.core.config import CacheConfig
from src.db.cache import DatabaseBackend
from src.utils.dandan.serialization import convert_to_serializable, fix_bangumi_mapping

logger = logging.getLogger(__name__)


class CacheService:
    """
    统一缓存服务层。

    根据后端类型持有对应的单一存储驱动，并在服务层内部完成：
    - 单驱动模式（memory/redis/database）：直接委托给主驱动；
    - 混合模式（hybrid）：Memory 作 L1、Database 作 L2，本层协调读写回填。

    对外方法的 key/region 语义与底层驱动完全一致，region 默认 "default"。
    """

    def __init__(
        self,
        *,
        backend_type: str,
        memory: Optional[MemoryBackend] = None,
        redis: Optional[RedisBackend] = None,
        database: Optional[DatabaseBackend] = None,
    ) -> None:
        """
        构建缓存服务。

        Args:
            backend_type: 后端类型 memory / redis / database / hybrid
            memory: 内存驱动（memory 模式的主驱动，hybrid 模式的 L1）
            redis: Redis 驱动（redis 模式的主驱动）
            database: 数据库驱动（database 模式的主驱动，hybrid 模式的 L2）
        """
        self._backend_type = backend_type
        self._memory = memory
        self._redis = redis
        self._database = database
        # get_or_set 并发保护锁
        self._lock = asyncio.Lock()

        # 确定单驱动模式下的主驱动；hybrid 模式主驱动为 None，走专用协调逻辑
        if backend_type == "memory":
            self._primary: Optional[AsyncCacheBackend] = memory
        elif backend_type == "redis":
            self._primary = redis
        elif backend_type == "database":
            self._primary = database
        elif backend_type == "hybrid":
            self._primary = None
        else:
            raise ValueError(f"不支持的缓存后端类型: {backend_type}")

        # 混合模式必须同时具备 L1/L2 驱动
        if backend_type == "hybrid" and (memory is None or database is None):
            raise ValueError("hybrid 模式需要同时提供 memory(L1) 与 database(L2) 驱动")
        if backend_type != "hybrid" and self._primary is None:
            raise ValueError(f"{backend_type} 模式缺少对应的驱动实例")

    @property
    def backend_type(self) -> str:
        """当前缓存后端类型"""
        return self._backend_type

    @property
    def is_hybrid(self) -> bool:
        """是否为混合 L1/L2 模式"""
        return self._backend_type == "hybrid"

    async def get(self, key: str, region: str = "default") -> Optional[Any]:
        """获取缓存值，不存在或已过期返回 None。混合模式下 L1 miss 会查 L2 并回填 L1。"""
        if not self.is_hybrid:
            return await self._primary.get(key, region=region)

        # 混合模式：L1 内存优先
        value = await self._memory.get(key, region=region)
        if value is not None:
            return value
        # L2 数据库回填
        value = await self._database.get(key, region=region)
        if value is not None:
            # 回填 L1（原始 TTL 未知，沿用内存默认 TTL，与旧 HybridBackend 行为一致）
            await self._memory.set(
                key, value, ttl=self._memory._default_ttl, region=region
            )
        return value

    async def set(self, key: str, value: Any, ttl: int = 0, region: str = "default") -> None:
        """设置缓存值，ttl=0 表示不过期。混合模式下 L1 同步写、L2 后台异步落库。"""
        if not self.is_hybrid:
            await self._primary.set(key, value, ttl=ttl, region=region)
            return

        # 混合模式：L1 内存同步写入，调用方 await 后立即可命中
        await self._memory.set(key, value, ttl=ttl, region=region)

        # L2 数据库：后台 Task 异步落库，不阻塞响应路径。
        # 写入延迟或失败不影响本次 L1 命中；重启后 L2 仍可回填。
        async def _write_db() -> None:
            try:
                await self._database.set(key, value, ttl=ttl, region=region)
            except Exception as e:
                logger.warning(f"[CacheService] 后台写数据库缓存失败 key={key!r} region={region!r}: {e}")

        asyncio.create_task(_write_db())

    async def set_many(self, entries: dict[str, tuple[Any, int]], region: str = "default") -> None:
        """批量写入缓存；混合模式先持久化整批，再更新内存，避免逐集后台任务。"""
        if not entries:
            return
        if not self.is_hybrid:
            await self._primary.set_many(entries, region=region)
            return
        await self._database.set_many(entries, region=region)
        await self._memory.set_many(entries, region=region)


    async def get_by_prefix(self, prefix: str, region: str = "default") -> dict[str, Any]:
        """批量读取指定前缀，混合模式以内存覆盖尚未落库的最新值。"""
        if not self.is_hybrid:
            return await self._primary.get_by_prefix(prefix, region=region)
        values = await self._database.get_by_prefix(prefix, region=region)
        values.update(await self._memory.get_by_prefix(prefix, region=region))
        return values


    async def delete(self, key: str, region: str = "default") -> bool:
        """删除缓存，返回是否有任一层成功删除。"""
        if not self.is_hybrid:
            return await self._primary.delete(key, region=region)

        mem_ok = await self._memory.delete(key, region=region)
        db_ok = await self._database.delete(key, region=region)
        return mem_ok or db_ok

    async def exists(self, key: str, region: str = "default") -> bool:
        """检查缓存是否存在。"""
        if not self.is_hybrid:
            return await self._primary.exists(key, region=region)
        if await self._memory.exists(key, region=region):
            return True
        return await self._database.exists(key, region=region)

    async def clear(self, region: Optional[str] = None) -> int:
        """清除缓存，指定 region 只清该区域，否则全清。返回清除数量。"""
        if not self.is_hybrid:
            return await self._primary.clear(region=region)
        mem_count = await self._memory.clear(region=region)
        db_count = await self._database.clear(region=region)
        return mem_count + db_count

    async def keys(self, pattern: str = "*", region: str = "default") -> List[str]:
        """按模式列出缓存键（不含 region 前缀）。混合模式以数据库为权威来源。"""
        if not self.is_hybrid:
            return await self._primary.keys(pattern, region=region)
        # 数据库是持久化权威来源，内存仅为其子集
        return await self._database.keys(pattern, region=region)

    # ==================== 前缀式读写（承接原 crud.fallback.get/set_db_cache） ====================
    # why: 后备流程与命令流程的缓存键是 f"{prefix}{key}" 组合形式，且读取端需要
    #      兼容历史数据（JSON 字符串双重序列化、bangumi_mapping 补丁）。
    #      这类「键拼装 + 历史兼容」属于编排职责，收口在服务层；
    #      纯存储读写仍由底层驱动 / CacheRepository 负责。
    #
    # 与旧 crud 版本的差异：不再需要显式传入 session。
    #   旧实现：backend 判空 → backend.set；失败 → crud_cache.set_cache(session, ...)
    #   新实现：backend 判空 → self.set；数据库回退已内置于 DatabaseBackend
    #           （database / hybrid 模式下 L2 即数据库缓存表），语义等价。

    async def get_with_prefix(self, prefix: str, key: str) -> Optional[Any]:
        """按 f"{prefix}{key}" 组合键读取缓存，并兼容历史数据格式。

        兼容处理：
        1. 命中值为非空 JSON 字符串时尝试反序列化（历史版本存的是字符串）；
        2. 对结果套用 fix_bangumi_mapping 修复双重序列化的 bangumi_mapping。

        Args:
            prefix: 键前缀，可为空字符串
            key: 业务键

        Returns:
            缓存值；未命中、已过期或服务不可用时返回 None
        """
        cache_key = f"{prefix}{key}"
        try:
            result = await self.get(cache_key, region="default")
        except Exception as e:
            logger.warning(f"[CacheService] 前缀式读取失败 key={cache_key!r}: {e}")
            return None

        if result is None:
            return None

        # 历史数据兼容：值可能是 JSON 字符串
        if isinstance(result, str):
            if not result.strip():
                return None
            try:
                result = json.loads(result)
            except (json.JSONDecodeError, TypeError):
                return result  # 非 JSON 的纯字符串按原值返回

        return fix_bangumi_mapping(result)

    async def set_with_prefix(self, prefix: str, key: str, value: Any, ttl: int) -> None:
        """按 f"{prefix}{key}" 组合键写入缓存，写入失败不外抛异常。

        非基础类型（如 Pydantic 模型）会先转为可 JSON 序列化的结构，
        与旧 set_db_cache 的行为保持一致。

        Args:
            prefix: 键前缀，可为空字符串
            key: 业务键
            value: 待缓存的值
            ttl: 过期时间（秒），0 表示不过期
        """

        cache_key = f"{prefix}{key}"
        if not isinstance(value, (str, int, float, bool, type(None))):
            value = convert_to_serializable(value)

        try:
            await self.set(cache_key, value, ttl=ttl, region="default")
        except Exception as e:
            # 写缓存失败仅影响后续命中率，不影响业务正确性，故吞掉异常
            logger.warning(f"[CacheService] 前缀式写入失败 key={cache_key!r}: {e}")

    async def get_or_set(
        self,
        key: str,
        factory: Callable[[], Awaitable[Any]],
        ttl: int = 0,
        region: str = "default",
    ) -> Any:
        """
        获取缓存；不存在则调用 factory 生成、写入并返回。

        使用锁 + 双重检查防止并发场景下 factory 被重复执行。

        Args:
            key: 业务键
            factory: 无参异步工厂函数，缓存 miss 时调用生成值
            ttl: 过期时间（秒），0 表示不过期
            region: 缓存区域
        """
        cached = await self.get(key, region=region)
        if cached is not None:
            return cached

        async with self._lock:
            # 双重检查：等锁期间可能已被其它协程写入
            cached = await self.get(key, region=region)
            if cached is not None:
                return cached

            value = await factory()
            await self.set(key, value, ttl=ttl, region=region)
            return value

    async def close(self) -> None:
        """关闭底层驱动连接（主要针对 Redis）。"""
        for driver in (self._memory, self._redis, self._database):
            if driver is None:
                continue
            try:
                await driver.close()
            except Exception as e:
                logger.warning(f"[CacheService] 关闭驱动 {type(driver).__name__} 失败: {e}")


# ==================== 驱动组合构建 ====================

def _build_service(backend_type: str, session_factory, cache_config) -> CacheService:
    """
    根据后端类型构建 CacheService（含所需驱动实例）。

    单一存储驱动直接复用 src.core.cache 的驱动类；混合模式由 CacheService
    自行组合 Memory + Database。
    """
    if backend_type == "memory":
        memory = MemoryBackend(
            maxsize=cache_config.memory_maxsize,
            default_ttl=cache_config.memory_default_ttl,
        )
        logger.info(f"缓存服务: Memory (maxsize={cache_config.memory_maxsize})")
        return CacheService(backend_type="memory", memory=memory)

    if backend_type == "redis":
        if not cache_config.redis_url:
            raise ValueError("Redis 缓存后端需要配置 redis_url")
        redis = RedisBackend(
            redis_url=cache_config.redis_url,
            max_memory=cache_config.redis_max_memory,
            socket_timeout=cache_config.redis_socket_timeout,
            socket_connect_timeout=cache_config.redis_socket_connect_timeout,
        )
        return CacheService(backend_type="redis", redis=redis)

    if backend_type == "database":
        if session_factory is None:
            raise ValueError("Database 缓存后端需要 session_factory")
        database = DatabaseBackend(session_factory)
        logger.info("缓存服务: Database")
        return CacheService(backend_type="database", database=database)

    if backend_type == "hybrid":
        if session_factory is None:
            raise ValueError("Hybrid 缓存后端需要 session_factory")
        memory = MemoryBackend(
            maxsize=cache_config.memory_maxsize,
            default_ttl=cache_config.memory_default_ttl,
        )
        database = DatabaseBackend(session_factory)
        logger.info(
            f"缓存服务: Hybrid (Memory L1 + Database L2, maxsize={cache_config.memory_maxsize})"
        )
        return CacheService(backend_type="hybrid", memory=memory, database=database)

    raise ValueError(f"不支持的缓存后端类型: {backend_type}")


# ==================== 全局单例 ====================

_global_cache_service: Optional[CacheService] = None


def get_cache_service() -> Optional[CacheService]:
    """
    获取全局缓存服务实例。

    契约：正常启动流程调用 init_cache_service() 后必为非 None。
    未初始化时返回 None（例如在 init 之前被误调用），调用方可据此判空降级。
    """
    return _global_cache_service


async def init_cache_service(session_factory=None, cache_config=None) -> CacheService:
    """
    初始化全局缓存服务（应用启动时调用一次）。

    与 init_cache_backend() 并存于 C1 过渡阶段，二者各自持有独立的驱动实例。

    降级策略（与 init_cache_backend 保持一致）：
    - 构造失败（如 redis 模式缺 redis_url）自动降级到 Hybrid（有 session_factory）
      或 Memory（无 session_factory），确保实例一定被赋值；
    - Redis 模式启动时做连接健康检查，ping 失败同样降级。
    """
    global _global_cache_service
    if cache_config is None:
        cache_config = CacheConfig()

    try:
        service = _build_service(cache_config.backend, session_factory, cache_config)
    except Exception as e:
        logger.warning(
            f"缓存服务构造失败（backend={cache_config.backend}）：{e}；自动降级到 Hybrid/Memory 模式"
        )
        if session_factory is not None:
            service = _build_service("hybrid", session_factory, cache_config)
        else:
            service = _build_service("memory", session_factory, cache_config)

    # Redis 后端健康检查（复用驱动的懒连接 + ping）
    if cache_config.backend == "redis" and service.backend_type == "redis" and service._redis is not None:
        try:
            client = await service._redis._get_client()
            await client.ping()
            logger.info(
                f"缓存服务: Redis ({service._redis._safe_url})\n"
                f"  - 连接成功\n"
                f"  - 健康检查通过"
            )
        except Exception as e:
            logger.warning(f"Redis 连接失败 ({service._redis._safe_url}): {e}")
            logger.warning("自动降级到 Hybrid 模式（Memory L1 + Database L2）")
            try:
                await service.close()
            except Exception:
                pass
            if session_factory is not None:
                service = _build_service("hybrid", session_factory, cache_config)
            else:
                service = _build_service("memory", session_factory, cache_config)

    _global_cache_service = service
    return _global_cache_service


async def close_cache_service() -> None:
    """关闭全局缓存服务（应用关闭时调用）。"""
    global _global_cache_service
    if _global_cache_service is not None:
        await _global_cache_service.close()
        _global_cache_service = None
        logger.info("全局缓存服务已关闭")
