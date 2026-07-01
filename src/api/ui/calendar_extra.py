"""
日程同步增强 / 追更日历提醒 (14)
"""
import json
import logging
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from src.db import get_db_session, orm_models, ConfigManager
from src.core import get_now
from src.api.dependencies import get_config_manager

logger = logging.getLogger(__name__)
router = APIRouter()


@router.get("/calendar/upcoming", summary="即将播出的条目")
async def get_upcoming_shows(
    days: int = Query(7, ge=1, le=30),
    session: AsyncSession = Depends(get_db_session),
):
    """展示今日/明日/本周即将播出的追更条目"""
    now = get_now()
    current_weekday = now.isoweekday()  # 1=周一 ... 7=周日

    # 查询有airWeekday的本地条目
    q = select(orm_models.Anime).join(orm_models.AnimeMetadata).join(
        orm_models.AnimeSource
    ).where(
        orm_models.AnimeSource.incrementalRefreshEnabled == True,
        orm_models.AnimeMetadata.airWeekday != None,
    ).options(
        selectinload(orm_models.Anime.metadataRecord),
        selectinload(orm_models.Anime.sources),
    ).distinct()
    animes = (await session.execute(q)).scalars().all()

    result = []
    for anime in animes:
        meta = anime.metadataRecord
        if not meta or not meta.airWeekday:
            continue
        air_wd = meta.airWeekday
        diff = (air_wd - current_weekday) % 7
        if diff == 0:
            day_label = "today"
        elif diff == 1:
            day_label = "tomorrow"
        elif diff <= days:
            day_label = f"in_{diff}_days"
        else:
            continue

        # 检查最新一集弹幕是否已获取
        latest_ep = None
        for src in anime.sources:
            if src.incrementalRefreshEnabled:
                ep_q = select(orm_models.Episode).where(
                    orm_models.Episode.sourceId == src.id
                ).order_by(orm_models.Episode.episodeIndex.desc()).limit(1)
                ep = (await session.execute(ep_q)).scalar_one_or_none()
                if ep:
                    latest_ep = ep
                    break

        result.append({
            "animeId": anime.id,
            "title": anime.title,
            "season": anime.season,
            "airWeekday": air_wd,
            "airTime": meta.airTime,
            "dayLabel": day_label,
            "daysUntil": diff,
            "latestEpisode": latest_ep.episodeIndex if latest_ep else None,
            "latestHasDanmaku": (latest_ep.commentCount or 0) > 0 if latest_ep else False,
            "imageUrl": anime.imageUrl,
        })

    result.sort(key=lambda x: x["daysUntil"])
    return result


@router.get("/calendar/stale-episodes", summary="已播出但尚未刷新弹幕的分集")
async def get_stale_episodes(
    session: AsyncSession = Depends(get_db_session),
    config_manager: ConfigManager = Depends(get_config_manager),
):
    """标记已播出但弹幕数为0或很少的分集"""
    threshold = int(await config_manager.get("stale_episode_threshold", "5"))
    q = select(orm_models.Episode).join(orm_models.AnimeSource).where(
        orm_models.AnimeSource.incrementalRefreshEnabled == True,
        orm_models.Episode.commentCount <= threshold,
    ).options(
        selectinload(orm_models.Episode.source).selectinload(orm_models.AnimeSource.anime)
    ).limit(100)
    episodes = (await session.execute(q)).scalars().all()

    return [{
        "episodeId": ep.id,
        "title": ep.title,
        "episodeIndex": ep.episodeIndex,
        "commentCount": ep.commentCount,
        "animeTitle": ep.source.anime.title if ep.source and ep.source.anime else "",
        "animeId": ep.source.animeId if ep.source else None,
    } for ep in episodes]
