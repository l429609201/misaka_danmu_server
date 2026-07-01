"""
AI 匹配可解释性增强 (10)
"""
import json
import logging
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.db import get_db_session, orm_models, ConfigManager
from src.api.dependencies import get_config_manager

logger = logging.getLogger(__name__)
router = APIRouter()


@router.get("/ai-explain/recent-matches", summary="最近AI匹配记录")
async def get_recent_ai_matches(
    limit: int = Query(20, ge=1, le=100),
    session: AsyncSession = Depends(get_db_session),
):
    """查询最近的AI匹配调用记录，展示选择理由和token消耗"""
    q = select(orm_models.AIMetricsLog).order_by(
        orm_models.AIMetricsLog.timestamp.desc()
    ).limit(limit)
    rows = (await session.execute(q)).scalars().all()
    return [{
        "id": r.id,
        "method": r.method,
        "success": r.success,
        "durationMs": r.durationMs,
        "tokensUsed": r.tokensUsed,
        "model": r.model,
        "error": r.error,
        "cacheHit": r.cacheHit,
        "timestamp": r.timestamp.isoformat() if r.timestamp else "",
    } for r in rows]


@router.get("/ai-explain/stats", summary="AI匹配统计概览")
async def get_ai_match_stats(
    hours: int = Query(24, ge=1, le=720),
    session: AsyncSession = Depends(get_db_session),
):
    from datetime import timedelta
    from src.core import get_now
    since = get_now() - timedelta(hours=hours)

    total_q = select(func.count(orm_models.AIMetricsLog.id)).where(
        orm_models.AIMetricsLog.timestamp >= since
    )
    total = (await session.execute(total_q)).scalar() or 0

    success_q = select(func.count(orm_models.AIMetricsLog.id)).where(
        orm_models.AIMetricsLog.timestamp >= since,
        orm_models.AIMetricsLog.success == True,
    )
    success = (await session.execute(success_q)).scalar() or 0

    tokens_q = select(func.sum(orm_models.AIMetricsLog.tokensUsed)).where(
        orm_models.AIMetricsLog.timestamp >= since
    )
    total_tokens = (await session.execute(tokens_q)).scalar() or 0

    cache_q = select(func.count(orm_models.AIMetricsLog.id)).where(
        orm_models.AIMetricsLog.timestamp >= since,
        orm_models.AIMetricsLog.cacheHit == True,
    )
    cache_hits = (await session.execute(cache_q)).scalar() or 0

    avg_dur_q = select(func.avg(orm_models.AIMetricsLog.durationMs)).where(
        orm_models.AIMetricsLog.timestamp >= since
    )
    avg_dur = (await session.execute(avg_dur_q)).scalar() or 0

    return {
        "totalCalls": total,
        "successCalls": success,
        "successRate": round(success / total * 100, 1) if total > 0 else 0,
        "totalTokens": total_tokens,
        "cacheHits": cache_hits,
        "cacheHitRate": round(cache_hits / total * 100, 1) if total > 0 else 0,
        "avgDurationMs": round(float(avg_dur), 1),
        "hours": hours,
    }


@router.get("/ai-explain/low-confidence", summary="低置信度匹配记录")
async def get_low_confidence_matches(
    config_manager: ConfigManager = Depends(get_config_manager),
):
    """获取标记为低置信度的AI匹配记录"""
    raw = await config_manager.get("ai_low_confidence_matches", "[]")
    try:
        data = json.loads(raw) if isinstance(raw, str) else raw
    except (json.JSONDecodeError, TypeError):
        data = []
    return data[-50:]
