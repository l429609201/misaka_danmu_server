"""
系统健康度相关API
- 弹幕源健康度评分
- 首页系统健康总览
- 配置完整性评分
"""
import glob
import json
import logging
import os
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from src.services.service_container import get_database_service
from src.services.config_service import ConfigService
from src.api.dependencies import get_config_service
from src.core import get_now
from src.api.dependencies import get_scraper_manager
from src.services.scraper_manager import ScraperManager

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
    scraper_manager: ScraperManager = Depends(get_scraper_manager),
) -> List[ScraperHealthItem]:
    """经服务层读取源统计，在事务结束前完成响应快照。"""
    db = get_database_service()
    async with db.transaction():
        rows = await db.scraper_crud.get_all()
        result = []
        for row in rows:
            total = row.totalSearches or 0
            avg_dur = round((row.totalDurationMs or 0) / total, 1) if total > 0 else 0
            avg_res = round((row.totalResultCount or 0) / max(1, row.successCount or 1), 1)
            score, level = _calc_health({
                "totalSearches": total,
                "successCount": row.successCount or 0,
                "timeoutCount": row.timeoutCount or 0,
                "avgDurationMs": avg_dur,
            })
            scraper = scraper_manager.scrapers.get(row.providerName)
            display = getattr(scraper, 'display_name', '') or row.providerName
            result.append(ScraperHealthItem(
                providerName=row.providerName, displayName=display,
                isEnabled=row.isEnabled, totalSearches=total,
                successCount=row.successCount or 0, failCount=row.failCount or 0,
                timeoutCount=row.timeoutCount or 0, emptyCount=row.emptyCount or 0,
                avgDurationMs=avg_dur, avgResultCount=avg_res,
                healthScore=score, healthLevel=level,
                lastSearchAt=row.lastSearchAt.isoformat() if row.lastSearchAt else None,
                lastError=row.lastError,
            ))
    result.sort(key=lambda x: x.healthScore)
    return result


@router.post("/system-health/scraper-stats/reset", summary="重置弹幕源健康度统计")
async def reset_scraper_health_stats() -> Dict[str, str]:
    """在独立短事务中重置健康统计，避免 API 直接提交会话。"""
    db = get_database_service()
    async with db.transaction():
        await db.scraper_crud.reset_health_stats()
    return {"message": "ok"}


# ==================== 系统健康总览 ====================

@router.get("/system-health/summary", summary="首页系统健康总览")
async def get_system_health_summary(
    config_service: ConfigService = Depends(get_config_service),
) -> SystemHealthSummary:
    """聚合健康状态，数据库短事务不跨越文件访问与配置服务调用。"""
    now = get_now()
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    db = get_database_service()
    async with db.transaction():
        scraper_rows = await db.scraper_crud.get_all()
        enabled_count = sum(1 for r in scraper_rows if r.isEnabled)
        unhealthy = 0
        for row in scraper_rows:
            total = row.totalSearches or 0
            avg_dur = round((row.totalDurationMs or 0) / total, 1) if total > 0 else 0
            score, _ = _calc_health({
                "totalSearches": total,
                "successCount": row.successCount or 0,
                "timeoutCount": row.timeoutCount or 0,
                "avgDurationMs": avg_dur,
            })
            if score < 60:
                unhealthy += 1
        scraper_summary = {"enabled": enabled_count, "total": len(scraper_rows), "unhealthy": unhealthy}
        task_summary = await db.health_query.get_task_counts_since(now - timedelta(hours=24))
        episode_counts = await db.health_query.get_episode_counts(today_start)
        config_counts = await db.health_query.get_config_counts()

    # 最近备份
    backup_status = {}
    try:
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

    # 沿用原接口口径：todayNewDanmaku 实际表示今日抓取的分集数。
    config_score = await _calc_config_score(config_service, config_counts)
    return SystemHealthSummary(
        scraperSummary=scraper_summary,
        taskSummary=task_summary,
        backupStatus=backup_status,
        todayNewDanmaku=episode_counts["today_new"],
        missingEpisodes=episode_counts["missing"],
        configScore=config_score["percentage"],
    )


# ==================== 配置完整性评分 ====================

async def _calc_config_score(config_service: ConfigService, counts: Dict[str, int]) -> dict:
    """根据仓储计数和配置服务计算评分，不持有数据库会话。"""
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
        val = await config_service.get(config_key, "")
        configured = bool(val and str(val).strip())
        score = weight if configured else 0
        total += score
        items.append({"key": key, "label": label, "configured": configured, "score": score, "maxScore": weight})

    # 保留原评分权重和返回字段，计数由查询仓储统一提供。
    for key, label, weight, tiered, detail_suffix in [
        ("media_server", "媒体服务器", 15, False, "个"),
        ("scrapers", "弹幕源", 15, True, "个启用"),
        ("notification", "通知渠道", 10, False, "个"),
        ("backup", "定期备份", 10, False, None),
        ("metadata", "元数据源", 15, True, "个启用"),
    ]:
        count = counts[key]
        score = (weight if count >= 2 else 8 if count == 1 else 0) if tiered else (weight if count > 0 else 0)
        max_score += weight
        total += score
        item = {"key": key, "label": label, "configured": count > 0, "score": score, "maxScore": weight}
        if detail_suffix is not None:
            item["detail"] = f"{count}{detail_suffix}"
        items.append(item)
    pct = int(total / max_score * 100) if max_score > 0 else 0
    return {"totalScore": total, "maxScore": max_score, "percentage": pct, "items": items}


@router.get("/system-health/config-score", response_model=ConfigScoreResult, summary="配置完整性评分")
async def get_config_score(
    config_service: ConfigService = Depends(get_config_service),
) -> dict:
    """先读取统计快照，再通过配置服务计算评分，避免嵌套事务。"""
    db = get_database_service()
    async with db.transaction():
        counts = await db.health_query.get_config_counts()
    return await _calc_config_score(config_service, counts)


# ==================== 番剧关注/优先级 ====================

@router.get("/system-health/anime-priority", summary="获取番剧优先级配置")
async def get_anime_priority(
    config_service: ConfigService = Depends(get_config_service),
):
    raw = await config_service.get("anime_priority_map", "{}")
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
    config_service: ConfigService = Depends(get_config_service),
) -> Dict[str, str]:
    """通过配置服务保存单项优先级，由服务管理事务和缓存失效。"""
    raw = await config_service.get("anime_priority_map", "{}")
    try:
        data = json.loads(raw) if isinstance(raw, str) else raw
    except (json.JSONDecodeError, TypeError):
        data = {}
    if body.priority == "normal":
        data.pop(str(body.animeId), None)
    else:
        data[str(body.animeId)] = body.priority
    await config_service.set("anime_priority_map", json.dumps(data))
    return {"message": "ok"}


@router.post("/system-health/anime-priority/batch", summary="批量设置番剧优先级")
async def batch_set_anime_priority(
    body: dict,
    config_service: ConfigService = Depends(get_config_service),
) -> Dict[str, Any]:
    """通过配置服务一次保存批量优先级，统一处理事务和缓存失效。"""
    anime_ids = body.get("animeIds", [])
    priority = body.get("priority", "normal")
    raw = await config_service.get("anime_priority_map", "{}")
    try:
        data = json.loads(raw) if isinstance(raw, str) else raw
    except (json.JSONDecodeError, TypeError):
        data = {}
    for aid in anime_ids:
        if priority == "normal":
            data.pop(str(aid), None)
        else:
            data[str(aid)] = priority
    await config_service.set("anime_priority_map", json.dumps(data))
    return {"message": "ok", "count": len(anime_ids)}
