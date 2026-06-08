"""
系统健康度相关API
- 弹幕源健康度评分
- 首页系统健康总览
- 配置完整性评分
"""
import json
import logging
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.db import get_db_session, orm_models, ConfigManager
from src.core import get_now
from src.api.dependencies import get_scraper_manager, get_config_manager
from src.services import ScraperManager

logger = logging.getLogger(__name__)
router = APIRouter()


# ==================== Models ====================

class ScraperHealthItem(BaseModel):
    providerName: str
    displayName: str = ""
    isEnabled: bool = False
    totalSearches: int = 0
    successCount: int = 0
    failCount: int = 0
    timeoutCount: int = 0
    emptyCount: int = 0
    avgDurationMs: float = 0
    avgResultCount: float = 0
    healthScore: int = 100  # 0~100
    healthLevel: str = "excellent"  # excellent/good/unstable/bad
    lastSearchAt: Optional[str] = None
    lastError: Optional[str] = None


class SystemHealthSummary(BaseModel):
    scraperSummary: Dict[str, Any] = {}
    taskSummary: Dict[str, Any] = {}
    backupStatus: Dict[str, Any] = {}
    missingEpisodes: int = 0
    todayNewDanmaku: int = 0
    configScore: int = 0


class ConfigScoreResult(BaseModel):
    totalScore: int = 0
    maxScore: int = 0
    percentage: int = 0
    items: List[Dict[str, Any]] = []


# ==================== 弹幕源健康度 ====================

def _calc_health(stats: dict) -> tuple:
    """根据统计数据计算健康分和等级"""
    total = stats.get("totalSearches", 0)
    if total == 0:
        return 100, "excellent"
    success_rate = stats.get("successCount", 0) / total
    timeout_rate = stats.get("timeoutCount", 0) / total
    avg_dur = stats.get("avgDurationMs", 0)
    score = 100
    score -= max(0, int((1 - success_rate) * 60))
    score -= max(0, int(timeout_rate * 20))
    if avg_dur > 5000:
        score -= 15
    elif avg_dur > 3000:
        score -= 8
    elif avg_dur > 1500:
        score -= 3
    score = max(0, min(100, score))
    if score >= 80:
        level = "excellent"
    elif score >= 60:
        level = "good"
    elif score >= 40:
        level = "unstable"
    else:
        level = "bad"
    return score, level


@router.get("/system-health/scraper-stats", summary="获取弹幕源健康度统计")
async def get_scraper_health_stats(
    session: AsyncSession = Depends(get_db_session),
    scraper_manager: ScraperManager = Depends(get_scraper_manager),
):
    stmt = select(orm_models.Scraper).order_by(orm_models.Scraper.displayOrder)
    rows = (await session.execute(stmt)).scalars().all()

    result = []
    for row in rows:
        total = row.totalSearches or 0
        avg_dur = round(row.totalDurationMs / total, 1) if total > 0 else 0
        avg_res = round((row.totalResultCount or 0) / max(1, row.successCount or 1), 1)
        score, level = _calc_health({
            "totalSearches": total,
            "successCount": row.successCount or 0,
            "failCount": row.failCount or 0,
            "timeoutCount": row.timeoutCount or 0,
            "emptyCount": row.emptyCount or 0,
            "avgDurationMs": avg_dur,
        })
        scraper = scraper_manager.scrapers.get(row.providerName)
        display = getattr(scraper, 'display_name', '') or row.providerName if scraper else row.providerName
        result.append(ScraperHealthItem(
            providerName=row.providerName,
            displayName=display,
            isEnabled=row.isEnabled,
            totalSearches=total,
            successCount=row.successCount or 0,
            failCount=row.failCount or 0,
            timeoutCount=row.timeoutCount or 0,
            emptyCount=row.emptyCount or 0,
            avgDurationMs=avg_dur,
            avgResultCount=avg_res,
            healthScore=score,
            healthLevel=level,
            lastSearchAt=row.lastSearchAt.isoformat() if row.lastSearchAt else None,
            lastError=row.lastError,
        ))
    result.sort(key=lambda x: x.healthScore)
    return result


@router.post("/system-health/scraper-stats/reset", summary="重置弹幕源健康度统计")
async def reset_scraper_health_stats(
    session: AsyncSession = Depends(get_db_session),
):
    from sqlalchemy import update
    await session.execute(update(orm_models.Scraper).values(
        totalSearches=0, successCount=0, failCount=0, timeoutCount=0,
        emptyCount=0, totalDurationMs=0, totalResultCount=0,
        lastSearchAt=None, lastError=None,
    ))
    await session.commit()
    return {"message": "ok"}


# ==================== 系统健康总览 ====================

@router.get("/system-health/summary", summary="首页系统健康总览")
async def get_system_health_summary(
    session: AsyncSession = Depends(get_db_session),
    config_manager: ConfigManager = Depends(get_config_manager),
):
    now = get_now()
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)

    # 弹幕源健康摘要
    scraper_q = select(orm_models.Scraper)
    scraper_rows = (await session.execute(scraper_q)).scalars().all()
    enabled_count = sum(1 for r in scraper_rows if r.isEnabled)
    unhealthy = 0
    for row in scraper_rows:
        total = row.totalSearches or 0
        avg_dur = round(row.totalDurationMs / total, 1) if total > 0 else 0
        score, _ = _calc_health({
            "totalSearches": total,
            "successCount": row.successCount or 0,
            "timeoutCount": row.timeoutCount or 0,
            "avgDurationMs": avg_dur,
        })
        if score < 60:
            unhealthy += 1
    scraper_summary = {"enabled": enabled_count, "total": len(scraper_rows), "unhealthy": unhealthy}

    # 任务摘要（最近24h）
    yesterday = now - timedelta(hours=24)
    task_q = select(
        orm_models.TaskHistory.status,
        func.count(orm_models.TaskHistory.taskId)
    ).where(orm_models.TaskHistory.createdAt >= yesterday).group_by(orm_models.TaskHistory.status)
    task_rows = (await session.execute(task_q)).all()
    task_summary = {row[0]: row[1] for row in task_rows}

    # 最近备份
    backup_status = {}
    try:
        import os, glob
        backup_dir = os.path.join("config", "backups")
        if os.path.exists(backup_dir):
            files = sorted(glob.glob(os.path.join(backup_dir, "*.gz")), key=os.path.getmtime, reverse=True)
            if files:
                latest = files[0]
                backup_status = {
                    "lastBackup": datetime.fromtimestamp(os.path.getmtime(latest)).isoformat(),
                    "totalBackups": len(files),
                    "latestSize": os.path.getsize(latest),
                }
    except Exception:
        pass

    # 今日新增弹幕
    danmaku_q = select(func.count(orm_models.Episode.id)).where(
        orm_models.Episode.fetchedAt >= today_start
    )
    today_new = (await session.execute(danmaku_q)).scalar() or 0

    # 缺失分集（弹幕数为0的分集）
    missing_q = select(func.count(orm_models.Episode.id)).where(
        orm_models.Episode.commentCount == 0
    )
    missing_count = (await session.execute(missing_q)).scalar() or 0

    # 配置完整性评分
    config_score = await _calc_config_score(config_manager, session)

    return SystemHealthSummary(
        scraperSummary=scraper_summary,
        taskSummary=task_summary,
        backupStatus=backup_status,
        todayNewDanmaku=today_new,
        missingEpisodes=missing_count,
        configScore=config_score["percentage"],
    )


# ==================== 配置完整性评分 ====================

async def _calc_config_score(config_manager: ConfigManager, session: AsyncSession) -> dict:
    items = []
    total = 0
    max_score = 0

    checks = [
        ("proxy", "proxyUrl", "代理配置", 10),
        ("ai", "aiMatcherEnabled", "AI匹配", 10),
        ("webhook", "webhookApiKey", "Webhook", 10),
        ("danmaku_path", "danmakuBasePath", "弹幕输出路径", 15),
    ]
    for key, config_key, label, weight in checks:
        max_score += weight
        val = await config_manager.get(config_key, "")
        configured = bool(val and str(val).strip())
        score = weight if configured else 0
        total += score
        items.append({"key": key, "label": label, "configured": configured, "score": score, "maxScore": weight})

    # 媒体服务器
    max_score += 15
    ms_q = select(func.count(orm_models.MediaServer.id)).where(orm_models.MediaServer.isEnabled == True)
    ms_count = (await session.execute(ms_q)).scalar() or 0
    ms_score = 15 if ms_count > 0 else 0
    total += ms_score
    items.append({"key": "media_server", "label": "媒体服务器", "configured": ms_count > 0, "score": ms_score, "maxScore": 15, "detail": f"{ms_count}个"})

    # 弹幕源
    max_score += 15
    sc_q = select(func.count(orm_models.Scraper.providerName)).where(orm_models.Scraper.isEnabled == True)
    enabled_scrapers = (await session.execute(sc_q)).scalar() or 0
    sc_score = 15 if enabled_scrapers >= 2 else (8 if enabled_scrapers >= 1 else 0)
    total += sc_score
    items.append({"key": "scrapers", "label": "弹幕源", "configured": enabled_scrapers > 0, "score": sc_score, "maxScore": 15, "detail": f"{enabled_scrapers}个启用"})

    # 通知渠道
    max_score += 10
    nc_q = select(func.count(orm_models.NotificationChannel.id)).where(orm_models.NotificationChannel.isEnabled == True)
    nc_count = (await session.execute(nc_q)).scalar() or 0
    nc_score = 10 if nc_count > 0 else 0
    total += nc_score
    items.append({"key": "notification", "label": "通知渠道", "configured": nc_count > 0, "score": nc_score, "maxScore": 10, "detail": f"{nc_count}个"})

    # 备份任务
    max_score += 10
    bk_q = select(func.count(orm_models.ScheduledTask.taskId)).where(
        orm_models.ScheduledTask.jobType == "databaseBackup",
        orm_models.ScheduledTask.isEnabled == True,
    )
    bk_count = (await session.execute(bk_q)).scalar() or 0
    bk_score = 10 if bk_count > 0 else 0
    total += bk_score
    items.append({"key": "backup", "label": "定期备份", "configured": bk_count > 0, "score": bk_score, "maxScore": 10})

    # 元数据源
    max_score += 15
    md_q = select(func.count(orm_models.MetadataSource.providerName)).where(orm_models.MetadataSource.isEnabled == True)
    md_count = (await session.execute(md_q)).scalar() or 0
    md_score = 15 if md_count >= 2 else (8 if md_count >= 1 else 0)
    total += md_score
    items.append({"key": "metadata", "label": "元数据源", "configured": md_count > 0, "score": md_score, "maxScore": 15, "detail": f"{md_count}个启用"})

    pct = int(total / max_score * 100) if max_score > 0 else 0
    return {"totalScore": total, "maxScore": max_score, "percentage": pct, "items": items}


@router.get("/system-health/config-score", response_model=ConfigScoreResult, summary="配置完整性评分")
async def get_config_score(
    session: AsyncSession = Depends(get_db_session),
    config_manager: ConfigManager = Depends(get_config_manager),
):
    return await _calc_config_score(config_manager, session)


# ==================== 番剧关注/优先级 ====================

@router.get("/system-health/anime-priority", summary="获取番剧优先级配置")
async def get_anime_priority(
    config_manager: ConfigManager = Depends(get_config_manager),
):
    raw = await config_manager.get("anime_priority_map", "{}")
    try:
        data = json.loads(raw) if isinstance(raw, str) else raw
    except (json.JSONDecodeError, TypeError):
        data = {}
    return data


class AnimePriorityUpdate(BaseModel):
    animeId: int
    priority: str  # "high" / "normal" / "ignore"


@router.post("/system-health/anime-priority", summary="设置番剧优先级")
async def set_anime_priority(
    body: AnimePriorityUpdate,
    config_manager: ConfigManager = Depends(get_config_manager),
):
    raw = await config_manager.get("anime_priority_map", "{}")
    try:
        data = json.loads(raw) if isinstance(raw, str) else raw
    except (json.JSONDecodeError, TypeError):
        data = {}
    if body.priority == "normal":
        data.pop(str(body.animeId), None)
    else:
        data[str(body.animeId)] = body.priority
    await config_manager.setValue("anime_priority_map", json.dumps(data))
    return {"message": "ok"}


@router.post("/system-health/anime-priority/batch", summary="批量设置番剧优先级")
async def batch_set_anime_priority(
    body: dict,
    config_manager: ConfigManager = Depends(get_config_manager),
):
    anime_ids = body.get("animeIds", [])
    priority = body.get("priority", "normal")
    raw = await config_manager.get("anime_priority_map", "{}")
    try:
        data = json.loads(raw) if isinstance(raw, str) else raw
    except (json.JSONDecodeError, TypeError):
        data = {}
    for aid in anime_ids:
        if priority == "normal":
            data.pop(str(aid), None)
        else:
            data[str(aid)] = priority
    await config_manager.setValue("anime_priority_map", json.dumps(data))
    return {"message": "ok", "count": len(anime_ids)}
