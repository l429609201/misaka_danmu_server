"""
数据库信息查询相关的业务编排层
"""
import logging
from typing import Dict, Any, Optional
from fastapi import Request

from src.core.config import settings
from src.core.cache import RedisBackend

logger = logging.getLogger(__name__)


async def workflow_get_database_info(request: Request) -> Dict[str, Any]:
    """
    获取当前数据库类型、连接信息、连接池状态以及 Redis 详细指标
    """
    db_cfg = settings.database
    cache_cfg = settings.cache

    # ---- 数据库连接池信息 ----
    pool_size = db_cfg.pool_size
    active_conn = 0
    idle_conn = 0
    overflow = 0
    try:
        engine = request.app.state.db_engine
        pool = engine.pool
        active_conn = pool.checkedout()
        idle_conn = pool.checkedin()
        overflow = pool.overflow()
        pool_size = pool.size()
    except Exception:
        pass

    # ---- Redis / 缓存信息 ----
    redis_connected = False
    redis_url = None
    redis_version = None
    redis_mem_used = None
    redis_mem_max = None
    redis_mem_used_bytes = None
    redis_mem_max_bytes = None
    redis_total_keys = None
    redis_clients = None
    redis_uptime = None

    try:
        # 从 app.state 获取缓存后端
        backend = getattr(request.app.state, 'cache_backend', None)
        if isinstance(backend, RedisBackend):
            redis_connected = True
            redis_url = backend._safe_url
            # 获取 Redis INFO
            try:
                client = await backend._get_client()
                info = await client.info()
                redis_version = info.get("redis_version")
                redis_mem_used = info.get("used_memory_human", "").strip()
                redis_mem_max = info.get("maxmemory_human", "").strip()
                redis_mem_used_bytes = info.get("used_memory")
                redis_mem_max_bytes = info.get("maxmemory")
                redis_clients = info.get("connected_clients")
                redis_uptime = info.get("uptime_in_seconds")
                # 统计所有 db 的 key 总数
                total_keys = 0
                for k, v in info.items():
                    if k.startswith("db") and isinstance(v, dict):
                        total_keys += v.get("keys", 0)
                redis_total_keys = total_keys
            except Exception:
                pass  # INFO 获取失败不影响基本信息
    except RuntimeError:
        pass  # 缓存后端未初始化

    return {
        "dbType": db_cfg.type.lower(),
        "dbHost": db_cfg.host,
        "dbPort": db_cfg.port,
        "dbName": db_cfg.name,
        "dbPoolType": db_cfg.pool_type,
        "dbPoolSize": pool_size,
        "dbActiveConnections": active_conn,
        "dbIdleConnections": idle_conn,
        "dbOverflow": overflow,
        "dbMaxOverflow": db_cfg.max_overflow,
        "dbPoolRecycle": db_cfg.pool_recycle,
        "cacheBackend": cache_cfg.backend,
        "redisUrl": redis_url,
        "redisConnected": redis_connected,
        "redisVersion": redis_version,
        "redisMemoryUsed": redis_mem_used,
        "redisMemoryMax": redis_mem_max,
        "redisMemoryUsedBytes": redis_mem_used_bytes,
        "redisMemoryMaxBytes": redis_mem_max_bytes,
        "redisTotalKeys": redis_total_keys,
        "redisConnectedClients": redis_clients,
        "redisUptimeSeconds": redis_uptime,
    }
