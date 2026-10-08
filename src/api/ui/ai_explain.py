"""
AI 匹配可解释性增强 (10)
"""
import json
from datetime import timedelta
from typing import Any, Dict, List

from fastapi import APIRouter, Depends, Query

from src.api.dependencies import get_config_service
from src.core import get_now
from src.services.config_service import ConfigService
from src.services.service_container import get_database_service

router = APIRouter()


@router.get("/ai-explain/recent-matches", summary="最近AI匹配记录")
async def get_recent_ai_matches(
    limit: int = Query(20, ge=1, le=100),
) -> List[Dict[str, Any]]:
    """通过数据库服务读取最近调用记录，保持原有响应字段。"""
    db = get_database_service()
    async with db.transaction():
        return await db.ai_metrics.get_latest_records(limit)


@router.get("/ai-explain/stats", summary="AI匹配统计概览")
async def get_ai_match_stats(
    hours: int = Query(24, ge=1, le=720),
) -> Dict[str, Any]:
    """读取时间窗口内的聚合指标，并计算成功率和缓存命中率。"""
    since = get_now() - timedelta(hours=hours)
    db = get_database_service()
    async with db.transaction():
        stats = await db.ai_metrics.get_summary_since(since)
    # 比率和展示精度保留在接口层，仓储仅负责数据聚合。
    total = stats["totalCalls"]
    stats["successRate"] = round(stats["successCalls"] / total * 100, 1) if total else 0
    stats["cacheHitRate"] = round(stats["cacheHits"] / total * 100, 1) if total else 0
    stats["avgDurationMs"] = round(stats["avgDurationMs"], 1)
    stats["hours"] = hours
    return stats


@router.get("/ai-explain/low-confidence", summary="低置信度匹配记录")
async def get_low_confidence_matches(
    config_service: ConfigService = Depends(get_config_service),
) -> List[Dict[str, Any]]:
    """使用实际配置服务读取低置信度记录，避免废弃类型阻断路由导入。"""
    raw = await config_service.get("ai_low_confidence_matches", "[]")
    try:
        data = json.loads(raw) if isinstance(raw, str) else raw
    except (json.JSONDecodeError, TypeError):
        data = []
    return data[-50:]
