"""
任务运行时间线 / 任务画像 (19)
数据库 / 缓存容量趋势 (21)
"""
import json
import logging
import os
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from src.db import get_db_session, orm_models, ConfigManager
from src.core import get_now
from src.api.dependencies import get_config_manager

logger = logging.getLogger(__name__)
router = APIRouter()


# ==================== 任务画像 ====================

class TaskProfile(BaseModel):
    jobType: str = ""
    totalRuns: int = 0
    successCount: int = 0
    failCount: int = 0
    avgDurationSec: float = 0
    maxDurationSec: float = 0
    successRate: float = 0
    recentRuns: List[Dict[str, Any]] = []


@router.get("/task-profile/summary", summary="任务画像概览")
async def get_task_profiles(
    days: int = Query(7, ge=1, le=90),
    session: AsyncSession = Depends(get_db_session),
):
    since = get_now() - timedelta(days=days)
    q = select(
        orm_models.TaskHistory.title,
        orm_models.TaskHistory.status,
        orm_models.TaskHistory.createdAt,
        orm_models.TaskHistory.finishedAt,
    ).where(orm_models.TaskHistory.createdAt >= since).order_by(orm_models.TaskHistory.createdAt.desc())
    rows = (await session.execute(q)).all()

    profiles: Dict[str, Dict] = {}
    for title, status, created, finished in rows:
        key = title or "unknown"
        if key not in profiles:
            profiles[key] = {"jobType": key, "totalRuns": 0, "successCount": 0, "failCount": 0, "durations": [], "recentRuns": []}
        p = profiles[key]
        p["totalRuns"] += 1
        if status in ("completed", "success"):
            p["successCount"] += 1
        elif status in ("failed", "error"):
            p["failCount"] += 1
        dur = 0
        if created and finished:
            dur = max(0, (finished - created).total_seconds())
            p["durations"].append(dur)
        if len(p["recentRuns"]) < 10:
            p["recentRuns"].append({
                "status": status,
                "createdAt": created.isoformat() if created else "",
                "finishedAt": finished.isoformat() if finished else "",
                "durationSec": round(dur, 1),
            })

    result = []
    for p in profiles.values():
        durs = p.pop("durations", [])
        p["avgDurationSec"] = round(sum(durs) / len(durs), 1) if durs else 0
        p["maxDurationSec"] = round(max(durs), 1) if durs else 0
        p["successRate"] = round(p["successCount"] / p["totalRuns"] * 100, 1) if p["totalRuns"] > 0 else 0
        result.append(TaskProfile(**p))
    result.sort(key=lambda x: x.totalRuns, reverse=True)
    return result


@router.get("/task-profile/timeline", summary="单次任务时间线详情")
async def get_task_timeline(
    task_id: str = Query(...),
    session: AsyncSession = Depends(get_db_session),
):
    q = select(orm_models.TaskHistory).where(orm_models.TaskHistory.taskId == task_id)
    task = (await session.execute(q)).scalar_one_or_none()
    if not task:
        return {"error": "not found"}
    steps = []
    if task.description:
        try:
            desc_data = json.loads(task.description)
            if isinstance(desc_data, dict) and "steps" in desc_data:
                steps = desc_data["steps"]
        except (json.JSONDecodeError, TypeError):
            pass
    return {
        "taskId": task.taskId,
        "title": task.title,
        "status": task.status,
        "createdAt": task.createdAt.isoformat() if task.createdAt else "",
        "finishedAt": task.finishedAt.isoformat() if task.finishedAt else "",
        "durationSec": (task.finishedAt - task.createdAt).total_seconds() if task.finishedAt and task.createdAt else 0,
        "steps": steps,
    }


# ==================== 容量趋势 ====================

@router.get("/trends/capacity", summary="数据库/缓存容量趋势")
async def get_capacity_trends(
    config_manager: ConfigManager = Depends(get_config_manager),
):
    raw = await config_manager.get("capacity_trend_data", "[]")
    try:
        data = json.loads(raw) if isinstance(raw, str) else raw
    except (json.JSONDecodeError, TypeError):
        data = []
    return data


@router.get("/trends/current", summary="当前容量快照")
async def get_current_capacity(
    session: AsyncSession = Depends(get_db_session),
):
    counts = {}
    tables = [
        ("anime", orm_models.Anime),
        ("episode", orm_models.Episode),
        ("anime_sources", orm_models.AnimeSource),
        ("task_history", orm_models.TaskHistory),
        ("cache_data", orm_models.CacheData),
        ("media_items", orm_models.MediaItem),
    ]
    for name, model in tables:
        pk = list(model.__table__.primary_key.columns)[0]
        q = select(func.count(pk))
        counts[name] = (await session.execute(q)).scalar() or 0

    # 数据库文件大小
    db_size = 0
    db_path = os.path.join("config", "data.db")
    if os.path.exists(db_path):
        db_size = os.path.getsize(db_path)

    return {
        "tableCounts": counts,
        "dbSizeBytes": db_size,
        "timestamp": get_now().isoformat(),
    }
