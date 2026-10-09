"""日历日程同步的统一业务编排。"""

import logging
from typing import Any, Dict, Optional

from src.services.service_container import get_database_service
from src.workflows.calendar.weekly_flow import sync_scraper_calendars

from src.workflows.calendar.cache_flow import get_all_calendars

logger = logging.getLogger(__name__)


async def sync_calendar_schedule(
    user: Any,
    metadata_service: Any,
    scraper_manager: Optional[Any] = None,
    force_refresh: bool = False,
) -> Dict[str, Any]:
    """绑定追更作品的外部标识、同步播出日程与可选弹幕源时间表。"""
    db = get_database_service()
    async with db.transaction():
        calendar_sources = await db.source.get_calendar_sources()
    # 日程保存在作品元数据上；同一作品的多个追更源只同步一次。
    sources = list({source["animeId"]: source for source in calendar_sources}.values())

    bound_count = 0
    updated_count = 0
    details = []
    enabled = [
        name for name in ("bangumi", "trakt")
        if name in metadata_service.sources
        and metadata_service.source_settings.get(name, {}).get("isEnabled")
    ]
    for name in enabled:
        id_field = "bangumiId" if name == "bangumi" else "traktId"
        # 搜索是外部 I/O；只把匹配结果的写入包进独立短事务。
        for source in sources:
            if source.get(id_field) or not (source.get("animeTitle") or "").strip():
                continue
            try:
                matches = await metadata_service.search(name, source["animeTitle"].strip(), user)
                if not matches:
                    continue
                matched_id = str(matches[0].id)
                async with db.transaction():
                    await db.source.update_metadata_ids(source["animeId"], **{id_field: matched_id})
                source[id_field] = matched_id
                bound_count += 1
            except Exception as exc:
                logger.warning("日程绑定失败 %s / %s: %s", source.get("animeTitle"), name, exc)
    if bound_count:
        details.append(f"自动绑定 {bound_count} 部")

    try:
        calendars = dict(await get_all_calendars(metadata_service, user, force_refresh=force_refresh) or {})
    except Exception as exc:
        logger.error("获取外部日历失败: %s", exc)
        calendars = {}

    if not calendars.get("bangumi"):
        try:
            offline = await metadata_service.get_offline_air_schedule()
            if offline:
                calendars["bangumi"] = [
                    {"bangumiId": str(bgm_id), "airWeekday": info["airWeekday"],
                     "airTime": info.get("airTime")}
                    for bgm_id, info in offline.items()
                ]
                logger.info("Bangumi 在线无日程，离线兜底提取 %s 部", len(offline))
        except Exception as exc:
            logger.warning("Bangumi 离线日程兜底失败: %s", exc)

    for name in ("bangumi", "trakt"):
        items = calendars.get(name) or []
        id_field = "bangumiId" if name == "bangumi" else "traktId"
        schedules = {
            str(item[id_field]): item
            for item in items
            if item.get(id_field) and item.get("airWeekday") in range(1, 8)
        }
        changed = 0
        # 两种来源可能命中同一作品：按来源顺序更新内存快照以避免重复计数。
        for source in sources:
            item = schedules.get(str(source.get(id_field)))
            if item is None:
                continue
            weekday = item["airWeekday"]
            air_time = item.get("airTime") or source.get("airTime")
            if weekday == source.get("airWeekday") and air_time == source.get("airTime"):
                continue
            async with db.transaction():
                await db.source.update_air_schedule(source["animeId"], weekday, air_time)
            source["airWeekday"] = weekday
            source["airTime"] = air_time
            changed += 1
        if changed:
            updated_count += changed
            details.append(f"{name}: 日程更新 {changed} 部")

    if scraper_manager is not None:
        providers = [
            name for name, scraper in scraper_manager.scrapers.items()
            if scraper_manager.scraper_settings.get(name, {}).get("isEnabled", True)
            and getattr(scraper, "supports_subscription", False)
        ]
        if providers:
            synced = await sync_scraper_calendars(scraper_manager, providers)
            if synced:
                details.append(f"番剧时间表: 刷新 {synced} 部")

    if not details:
        details.append("无变更")
    return {
        "message": f"同步完成（{'、'.join(details)}）",
        "updatedCount": updated_count,
        "boundCount": bound_count,
        "details": details,
    }
