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

from src.services.service_container import get_database_service
from src.core import get_now
from src.services.config_service import ConfigService, get_config_service

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
) -> List[TaskProfile]:
    """从仓储读取标量快照，事务外计算任务画像。"""
    since = get_now() - timedelta(days=days)
    db = get_database_service()
    async with db.transaction():
        rows = await db.health_query.get_task_profile_rows(since)

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
async def get_task_timeline(task_id: str = Query(...)) -> Dict[str, Any]:
    """从仓储读取任务快照，不在接口层构造 SQL 或持有 ORM 对象。"""
    db = get_database_service()
    async with db.transaction():
        task = await db.health_query.get_task_timeline_record(task_id)
    if not task:
        return {"error": "not found"}
    steps = []
    if task["description"]:
        try:
            desc_data = json.loads(task["description"])
            if isinstance(desc_data, dict) and "steps" in desc_data:
                steps = desc_data["steps"]
        except (json.JSONDecodeError, TypeError):
            pass
    created, finished = task["createdAt"], task["finishedAt"]
    return {
        "taskId": task["taskId"],
        "title": task["title"],
        "status": task["status"],
        "createdAt": created.isoformat() if created else "",
        "finishedAt": finished.isoformat() if finished else "",
        "durationSec": (finished - created).total_seconds() if finished and created else 0,
        "steps": steps,
    }


# ==================== 容量趋势 ====================

@router.get("/trends/capacity", summary="数据库/缓存容量趋势")
async def get_capacity_trends(
    config_service: ConfigService = Depends(get_config_service),
) -> List[Dict[str, Any]]:
    """通过配置服务读取容量趋势数据。"""
    raw = await config_service.get("capacity_trend_data", "[]")
    try:
        data = json.loads(raw) if isinstance(raw, str) else raw
    except (json.JSONDecodeError, TypeError):
        data = []
    return data


@router.get("/trends/current", summary="当前容量快照")
async def get_current_capacity() -> Dict[str, Any]:
    """通过健康查询仓储统计容量，文件访问放在数据库事务之外。"""
    db = get_database_service()
    async with db.transaction():
        counts = await db.health_query.get_capacity_counts()

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
