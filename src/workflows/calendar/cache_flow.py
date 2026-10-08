"""日历缓存清理的跨缓存与数据库编排。"""

import logging

from src.services.cache_service import get_cache_service
from src.services.service_container import get_database_service

logger = logging.getLogger(__name__)


async def clear_calendar_cache_flow() -> int:
    """清理外部日历缓存及纯缓存条目，保留订阅意向。"""
    cache = get_cache_service()
    deleted = 0
    for pattern in ("trakt_calendar_*", "bangumi_calendar_*"):
        keys = await cache.keys(pattern=pattern, region="metadata")
        for key in keys:
            if await cache.delete(key, region="metadata"):
                deleted += 1
    try:
        cleared = await cache.clear(region="external_calendar")
        if isinstance(cleared, int):
            deleted += cleared
    except Exception as exc:
        logger.debug("清除 external_calendar 缓存失败（忽略）: %s", exc)

    try:
        db = get_database_service()
        async with db.transaction():
            deleted += await db.external_calendar.clear_calendar_cache_items()
    except Exception as exc:
        logger.warning("清除 external_calendar_item 纯缓存条目失败: %s", exc)
    return deleted
