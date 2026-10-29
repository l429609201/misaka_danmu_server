"""
日程同步增强 / 追更日历提醒 (14)
"""
from typing import Any, Dict, List

from fastapi import APIRouter, Depends, Query

from src.api.dependencies import get_config_service
from src.core import get_now
from src.services.config_service import ConfigService
from src.services.service_container import get_database_service

router = APIRouter()


@router.get("/calendar/upcoming", summary="即将播出的条目")
async def get_upcoming_shows(
    days: int = Query(7, ge=1, le=30),
) -> List[Dict[str, Any]]:
    """展示今日、明日及指定天数内即将播出的追更条目。"""
    current_weekday = get_now().isoweekday()
    db = get_database_service()
    # 数据访问交给查询仓储，路由仅处理日期标签和响应排序。
    async with db.transaction():
        items = await db.anime.get_calendar_tracking_items()

    result = []
    for item in items:
        diff = (item["airWeekday"] - current_weekday) % 7
        if diff == 0:
            day_label = "today"
        elif diff == 1:
            day_label = "tomorrow"
        elif diff <= days:
            day_label = f"in_{diff}_days"
        else:
            continue
        result.append({**item, "dayLabel": day_label, "daysUntil": diff})

    result.sort(key=lambda item: item["daysUntil"])
    return result


@router.get("/calendar/stale-episodes", summary="已播出但尚未刷新弹幕的分集")
async def get_stale_episodes(
    config_service: ConfigService = Depends(get_config_service),
) -> List[Dict[str, Any]]:
    """按配置阈值查询追更源中弹幕较少的分集。"""
    threshold = int(await config_service.get("stale_episode_threshold", "5"))
    db = get_database_service()
    # 保留弹幕数量筛选口径，不额外引入尚无数据依据的播出时间判断。
    async with db.transaction():
        return await db.episode.get_calendar_stale_episodes(threshold)
