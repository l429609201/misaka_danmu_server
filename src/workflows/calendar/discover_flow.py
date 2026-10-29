"""当季 Bangumi 日历与本地作品标识聚合。"""

from typing import Any

from src.schemas.auth import User
from src.services.metadata_service import MetadataService
from src.services.service_container import get_database_service


async def discover_current_season_flow(user: User, metadata_service: MetadataService) -> dict[str, Any]:
    """通过元数据源查询当季番剧，在短事务中补充本地收录状态。"""
    calendar = await metadata_service.get_bangumi_calendar_items(user)
    db = get_database_service()
    async with db.transaction():
        local_ids = await db.anime.get_local_bangumi_ids()

    weekly: dict[int, list[dict[str, Any]]] = {}
    local_count = 0
    for item in calendar:
        weekday = item.get("airWeekday") or 0
        bangumi_id = str(item.get("bangumiId") or "")
        is_local = bangumi_id in local_ids
        local_count += is_local
        weekly.setdefault(weekday, []).append({
            "bangumiId": bangumi_id,
            "title": item.get("animeTitle") or "",
            "titleJp": item.get("titleJp") or "",
            "airDate": item.get("airDate"),
            "airWeekday": weekday,
            "imageUrl": item.get("imageUrl"),
            "rating": item.get("rating"),
            "rank": item.get("rank"),
            "isLocal": is_local,
        })
    return {"weekly": weekly, "stats": {"total": len(calendar), "localCount": local_count}}
