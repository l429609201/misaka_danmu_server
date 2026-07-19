"""
Cache相关的CRUD操作
"""

import json
import logging
from typing import Optional, Dict, Any, List
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, distinct, case, or_, and_, update, delete
from sqlalchemy.orm import selectinload
from sqlalchemy.dialects.mysql import insert as mysql_insert
from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from datetime import datetime, timedelta

from ..orm_models import CacheData
from .. import models, orm_models
from src.core.timezone import get_now

logger = logging.getLogger(__name__)


async def get_cache(session: AsyncSession, key: str) -> Optional[Any]:
    # 先查询缓存记录（包括已过期的）以便调试
    debug_stmt = select(CacheData.cacheValue, CacheData.expiresAt).where(CacheData.cacheKey == key)
    debug_result = await session.execute(debug_stmt)
    debug_row = debug_result.first()

    if debug_row:
        value, expires_at = debug_row
        current_time = get_now()
        is_expired = expires_at <= current_time
        logger.debug(
            f"缓存查询: key={key}, "
            f"expires_at={expires_at}, "
            f"current_time={current_time}, "
            f"is_expired={is_expired}"
        )

        if is_expired:
            logger.debug(f"缓存已过期: key={key}")
            return None

        try:
            return json.loads(value)
        except json.JSONDecodeError:
            logger.warning(f"缓存值JSON解析失败: key={key}, value={value[:100] if value else None}")
            return None
    else:
        logger.debug(f"缓存不存在: key={key}")
        return None


async def set_cache(session: AsyncSession, key: str, value: Any, ttl_seconds: int, provider: Optional[str] = None):
    json_value = json.dumps(value, ensure_ascii=False)
    expires_at = get_now() + timedelta(seconds=ttl_seconds)

    dialect = session.bind.dialect.name
    values_to_insert = {"cacheProvider": provider, "cacheKey": key, "cacheValue": json_value, "expiresAt": expires_at}

    if dialect == 'mysql':
        stmt = mysql_insert(CacheData).values(values_to_insert)
        stmt = stmt.on_duplicate_key_update(
            cache_provider=stmt.inserted.cache_provider,
            cache_value=stmt.inserted.cache_value,
            expires_at=stmt.inserted.expires_at
        )
    elif dialect == 'postgresql':
        stmt = postgresql_insert(CacheData).values(values_to_insert)
        # 修正：使用 on_conflict_do_update 并通过 index_elements 指定主键列，以提高兼容性
        stmt = stmt.on_conflict_do_update(
            index_elements=['cache_key'],
            set_={"cache_provider": stmt.excluded.cache_provider, "cache_value": stmt.excluded.cache_value, "expires_at": stmt.excluded.expires_at}
        )
    else:
        raise NotImplementedError(f"缓存设置功能尚未为数据库类型 '{dialect}' 实现。")

    await session.execute(stmt)
    await session.commit()


async def clear_expired_cache(session: AsyncSession):
    await session.execute(delete(CacheData).where(CacheData.expiresAt <= get_now()))
    await session.commit()


async def clear_all_cache(session: AsyncSession) -> int:
    result = await session.execute(delete(CacheData))
    await session.commit()
    return result.rowcount


async def delete_cache(session: AsyncSession, key: str) -> bool:
    result = await session.execute(delete(CacheData).where(CacheData.cacheKey == key))
    await session.commit()
    return result.rowcount > 0


async def get_cache_keys_by_pattern(session: AsyncSession, pattern: str) -> List[str]:
    """根据模式获取缓存键列表"""
    # 将通配符*转换为SQL的%
    sql_pattern = pattern.replace('*', '%')
    stmt = select(CacheData.cacheKey).where(
        CacheData.cacheKey.like(sql_pattern),
        CacheData.expiresAt > func.now()
    )
    result = await session.execute(stmt)
    return [row[0] for row in result.fetchall()]




async def count_cache_keys_by_pattern(session: AsyncSession, pattern: str) -> int:
    """统计未过期且匹配模式的缓存键，不加载键列表。"""
    sql_pattern = pattern.replace('*', '%')
    stmt = select(func.count()).select_from(CacheData).where(
        CacheData.cacheKey.like(sql_pattern),
        CacheData.expiresAt > func.now(),
    )
    return int((await session.execute(stmt)).scalar_one())


async def list_cache_keys_by_pattern(
    session: AsyncSession, pattern: str, offset: int, limit: int
) -> List[str]:
    """在数据库层排序并分页返回未过期缓存键。"""
    sql_pattern = pattern.replace('*', '%')
    stmt = (
        select(CacheData.cacheKey)
        .where(CacheData.cacheKey.like(sql_pattern), CacheData.expiresAt > func.now())
        .order_by(CacheData.cacheKey)
        .offset(offset)
        .limit(limit)
    )
    return list((await session.execute(stmt)).scalars().all())