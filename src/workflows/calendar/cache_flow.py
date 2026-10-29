"""日历缓存清理的跨缓存与数据库编排。"""

import logging
import asyncio
from typing import Any, Dict, List

from src.schemas.auth import User

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


async def get_all_calendars(metadata_manager: Any, user: User, force_refresh: bool = False) -> Dict[str, List[Dict[str, Any]]]:
    """从所有已启用的元数据源获取日历数据（三层架构）。

    架构说明：
        Layer 1: 内存/Redis 缓存（region=external_calendar，TTL 2h）
        Layer 2: 持久化表 external_calendar_item（24h 内有效）
        Layer 3: 调用 metadata source 的 get_calendar()，结果双写表+缓存

    :param force_refresh: 跳过 L1/L2 缓存，强制走外部源重新拉取（用于「同步日程」按钮）
    :return: { "bangumi": [...], "trakt": [...] } 仅包含实际有数据的源
    """
    CACHE_REGION = "external_calendar"
    CACHE_KEY = "weekly_all"
    CACHE_TTL = 2 * 60 * 60   # 2 小时
    TABLE_MAX_AGE_HOURS = 24  # 表数据 24h 内视为有效

    # ---- Layer 1: 内存/Redis 缓存 ----
    if not force_refresh:
        try:
            cached = await get_cache_service().get(CACHE_KEY, region=CACHE_REGION)
            if cached:
                metadata_manager.logger.debug("get_all_calendars: cache HIT (L1)")
                return cached
        except Exception as e:
            metadata_manager.logger.debug(f"L1 缓存读取失败（忽略）: {e}")

    # ---- Layer 2: 数据库表 ----
    if not force_refresh:
        try:
            db = get_database_service()
            async with db.transaction():
                grouped = await db.external_calendar.get_all_fresh(max_age_hours=TABLE_MAX_AGE_HOURS)
            if grouped:
                metadata_manager.logger.debug(f"get_all_calendars: table HIT (L2) providers={list(grouped.keys())}")
                # 回填 L1 缓存（不阻塞返回）
                try:
                    await get_cache_service().set(CACHE_KEY, grouped, ttl=CACHE_TTL, region=CACHE_REGION)
                except Exception as e:
                    metadata_manager.logger.debug(f"L1 缓存回填失败（忽略）: {e}")
                return grouped
        except Exception as e:
            metadata_manager.logger.warning(f"L2 表读取失败，回退到外部源: {e}")

    # ---- Layer 3: 调用外部 API（与原逻辑保持一致） ----
    results: Dict[str, List[Dict[str, Any]]] = {}

    async def _fetch(provider_name: str, source_instance):
        try:
            items = await source_instance.get_calendar(user)
            if items:
                return provider_name, items
        except Exception as e:
            metadata_manager.logger.warning(f"获取 {provider_name} 日历失败: {e}")
        return provider_name, []

    tasks = []
    for provider_name, setting in metadata_manager.source_settings.items():
        if not setting.get('isEnabled'):
            continue
        source = metadata_manager.sources.get(provider_name)
        if source and hasattr(source, 'get_calendar'):
            tasks.append(_fetch(provider_name, source))

    if tasks:
        fetched = await asyncio.gather(*tasks, return_exceptions=True)
        for item in fetched:
            if isinstance(item, Exception):
                continue
            name, items = item
            if items:
                results[name] = items

    # ---- 双写：持久化到表 + 写缓存 ----
    if results:
        # 写表（按 provider 分别 upsert）
        try:
            db = get_database_service()
            async with db.transaction():
                for provider_name, items in results.items():
                    await db.external_calendar.upsert_items(provider_name, items)
            metadata_manager.logger.info(f"get_all_calendars: 已持久化 {sum(len(v) for v in results.values())} 条到 external_calendar_item 表")
        except Exception as e:
            metadata_manager.logger.warning(f"L2 表写入失败（不影响返回）: {e}")

        # 同步「平台用户私人在追状态」（OAuth 账号下的 watching/wish/done 等）
        # 这个调用是可选的：未授权时各源会自动跳过返回 {}
        try:
            await sync_user_platform_status(metadata_manager, user)
            # 平台状态写入后，重新从表读取以保证返回的 results 含最新状态
            db = get_database_service()
            async with db.transaction():
                refreshed = await db.external_calendar.get_all_fresh(max_age_hours=TABLE_MAX_AGE_HOURS)
            if refreshed:
                results = refreshed
        except Exception as e:
            metadata_manager.logger.warning(f"同步平台用户状态失败（不影响返回）: {e}")

        # 写 L1 缓存
        try:
            await get_cache_service().set(CACHE_KEY, results, ttl=CACHE_TTL, region=CACHE_REGION)
        except Exception as e:
            metadata_manager.logger.debug(f"L1 缓存写入失败（忽略）: {e}")

    return results



async def sync_user_platform_status(metadata_manager: Any, user: User) -> Dict[str, int]:
    """同步「平台账号下我的在追/想看」状态到 external_calendar_item 表。

    遍历所有已启用的元数据源，如果该源实现了 get_user_watching_collection，
    则拉取用户在该平台的私人收藏状态，并 Upsert 到表中（仅更新 platformWatchStatus 等字段）。

    :return: { provider_name: updated_rows_count }
    """

    result: Dict[str, int] = {}
    for provider_name, setting in metadata_manager.source_settings.items():
        if not setting.get("isEnabled"):
            continue
        source = metadata_manager.sources.get(provider_name)
        if not source or not hasattr(source, "get_user_watching_collection"):
            continue
        try:
            statuses = await source.get_user_watching_collection(user)
            if not statuses:
                continue
            db = get_database_service()
            async with db.transaction():
                updated = await db.external_calendar.update_platform_status(provider_name, statuses)
            result[provider_name] = updated
            metadata_manager.logger.info(
                f"sync_user_platform_status: provider={provider_name} 拉取 {len(statuses)} 条，更新 {updated} 行"
            )
        except Exception as e:
            metadata_manager.logger.warning(f"sync_user_platform_status 调用 {provider_name} 失败: {e}")
    return result
