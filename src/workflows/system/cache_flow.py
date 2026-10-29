"""
缓存管理相关的业务编排层
"""
import logging

from fastapi import HTTPException, Request

from src.services.cache_service import get_cache_service
from src.services.database_service import DatabaseService
from src.schemas.auth import User

logger = logging.getLogger(__name__)


async def workflow_clear_all_caches(
    request: Request,
    db: DatabaseService,
    current_user: User
) -> dict:
    """清除统一缓存服务及数据库残留缓存，返回实际删除数量。

    backend_cache 为服务清理数量（hybrid 模式包含 L1/L2），
    database_cache 为随后清理的数据库残留数量，避免重复统计。
    """
    # 搜索写入全局 CacheService，旧 app.state.cache_backend 已不再初始化。
    cache_service = get_cache_service()
    if cache_service is None:
        raise HTTPException(status_code=503, detail="缓存服务尚未初始化，无法清除所有缓存")

    backend_type = cache_service.backend_type
    # 不吞掉清理异常，避免后端清理失败仍向用户报告全部成功。
    backend_count = await cache_service.clear()

    # memory/redis 模式下搜索失败回退仍可能写入数据库，需继续清理残留。
    # database/hybrid 已由服务清理数据库，此处只计剩余记录，不重复计数。
    async with db.transaction():
        deleted_count = await db.cache.clear_all()

    logger.info(
        f"用户 '{current_user.username}' 清除了所有缓存: "
        f"后端({backend_type}) {backend_count} 条, 数据库残留 {deleted_count} 条。"
    )

    return {
        "message": f"成功清除缓存: 后端({backend_type}) {backend_count} 条, 数据库残留 {deleted_count} 条。",
        "backend_cache": backend_count,
        "database_cache": deleted_count
    }
