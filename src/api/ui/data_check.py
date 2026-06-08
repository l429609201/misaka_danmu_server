"""
媒体库数据体检 / 修复助手 (13)
媒体服务器映射修复工具 (18)
"""
import logging
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import func, select, and_, or_
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from src.db import crud, get_db_session, orm_models

logger = logging.getLogger(__name__)
router = APIRouter()


class DataCheckResult(BaseModel):
    category: str = ""
    severity: str = "info"  # info/warning/error
    count: int = 0
    items: List[Dict[str, Any]] = []
    suggestion: str = ""


@router.get("/data-check/scan", summary="媒体库数据体检扫描")
async def scan_data_issues(
    session: AsyncSession = Depends(get_db_session),
    limit: int = Query(50, ge=1, le=200),
):
    results = []

    # 1. 缺元数据ID的条目
    q1 = select(orm_models.Anime).outerjoin(orm_models.AnimeMetadata).where(
        or_(
            orm_models.AnimeMetadata.id == None,
            and_(
                orm_models.AnimeMetadata.tmdbId == None,
                orm_models.AnimeMetadata.bangumiId == None,
            )
        )
    ).limit(limit)
    missing_meta = (await session.execute(q1)).scalars().all()
    if missing_meta:
        results.append(DataCheckResult(
            category="missing_metadata",
            severity="warning",
            count=len(missing_meta),
            items=[{"id": a.id, "title": a.title, "season": a.season} for a in missing_meta],
            suggestion="这些条目缺少元数据ID(tmdbId/bangumiId)，建议在弹幕库中手动补充或重新匹配",
        ))

    # 2. 零弹幕分集
    q2 = select(orm_models.Episode).where(
        orm_models.Episode.commentCount == 0
    ).options(selectinload(orm_models.Episode.source)).limit(limit)
    zero_eps = (await session.execute(q2)).scalars().all()
    if zero_eps:
        results.append(DataCheckResult(
            category="zero_danmaku",
            severity="warning",
            count=len(zero_eps),
            items=[{"id": e.id, "title": e.title, "episodeIndex": e.episodeIndex, "sourceId": e.sourceId} for e in zero_eps[:limit]],
            suggestion="这些分集弹幕数为0，可能是抓取失败或来源无弹幕，建议尝试刷新",
        ))

    # 3. 孤立分集（source被删但episode还在 — 通常不会出现因为cascade，但防御性检查）
    q3_count = select(func.count(orm_models.Episode.id)).where(
        ~orm_models.Episode.sourceId.in_(select(orm_models.AnimeSource.id))
    )
    orphan_count = (await session.execute(q3_count)).scalar() or 0
    if orphan_count > 0:
        results.append(DataCheckResult(
            category="orphan_episodes",
            severity="error",
            count=orphan_count,
            suggestion="存在孤立分集（弹幕源已删除但分集记录仍在），建议清理",
        ))

    # 4. 重复条目检测（同标题+同季度）
    q4 = select(
        orm_models.Anime.title,
        orm_models.Anime.season,
        func.count(orm_models.Anime.id).label("cnt")
    ).group_by(orm_models.Anime.title, orm_models.Anime.season).having(func.count(orm_models.Anime.id) > 1)
    dup_rows = (await session.execute(q4)).all()
    if dup_rows:
        results.append(DataCheckResult(
            category="duplicate_anime",
            severity="warning",
            count=len(dup_rows),
            items=[{"title": r[0], "season": r[1], "count": r[2]} for r in dup_rows[:limit]],
            suggestion="这些条目存在重复（同标题+同季度），建议合并或删除多余条目",
        ))

    # 5. 媒体服务器映射检查
    q5 = select(orm_models.AnimeMetadata).where(
        orm_models.AnimeMetadata.mediaServerType != None,
        orm_models.AnimeMetadata.mediaServerSeriesId == None,
    ).limit(limit)
    bad_mapping = (await session.execute(q5)).scalars().all()
    if bad_mapping:
        results.append(DataCheckResult(
            category="broken_mapping",
            severity="warning",
            count=len(bad_mapping),
            items=[{"animeId": m.animeId, "serverType": m.mediaServerType} for m in bad_mapping],
            suggestion="这些条目有媒体服务器类型但缺少SeriesId，可能映射不完整",
        ))

    return results


@router.post("/data-check/fix-orphans", summary="清理孤立分集")
async def fix_orphan_episodes(session: AsyncSession = Depends(get_db_session)):
    q = select(orm_models.Episode).where(
        ~orm_models.Episode.sourceId.in_(select(orm_models.AnimeSource.id))
    )
    orphans = (await session.execute(q)).scalars().all()
    count = len(orphans)
    for ep in orphans:
        await session.delete(ep)
    await session.commit()
    return {"message": "ok", "deleted": count}


@router.post("/data-check/clear-mapping", summary="清除无效媒体服务器映射")
async def clear_broken_mappings(session: AsyncSession = Depends(get_db_session)):
    q = select(orm_models.AnimeMetadata).where(
        orm_models.AnimeMetadata.mediaServerType != None,
        orm_models.AnimeMetadata.mediaServerSeriesId == None,
    )
    broken = (await session.execute(q)).scalars().all()
    count = len(broken)
    for m in broken:
        m.mediaServerType = None
        m.mediaServerSeriesId = None
        m.mediaServerSeasonId = None
    await session.commit()
    return {"message": "ok", "fixed": count}
