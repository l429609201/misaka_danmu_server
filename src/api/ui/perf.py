"""
性能统计 API — 任务流程各阶段耗时汇总

GET /api/ui/perf/stats?days=7
"""

import logging
from typing import List

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from src import security
from src.db import crud, get_db_session, models

logger = logging.getLogger(__name__)

router = APIRouter()


class PerfStepStat(BaseModel):
    stepName: str
    avgMs: float
    maxMs: float
    callCount: int
    successRate: float  # 0.0 ~ 100.0


class PerfFlowStat(BaseModel):
    flowType: str
    totalRuns: int
    avgTotalMs: float
    steps: List[PerfStepStat]


@router.get(
    "/perf/stats",
    response_model=List[PerfFlowStat],
    summary="获取各任务流程性能汇总统计",
)
async def get_perf_stats(
    days: int = Query(7, ge=1, le=90, description="统计天数，1/7/30，最大90天"),
    current_user: models.User = Depends(security.get_current_user),
    session: AsyncSession = Depends(get_db_session),
):
    """
    返回各任务流程的步骤级性能汇总：
    - flowType: 流程类型（弹幕导入/全量刷新/后备匹配 等）
    - totalRuns: 该时段内执行次数
    - avgTotalMs: 平均总耗时（毫秒）
    - steps: 各步骤的 avgMs/maxMs/callCount/successRate
    """
    raw = await crud.get_perf_stats(session, days=days)
    return [
        PerfFlowStat(
            flowType=item["flowType"],
            totalRuns=item["totalRuns"],
            avgTotalMs=item["avgTotalMs"],
            steps=[
                PerfStepStat(
                    stepName=s["stepName"],
                    avgMs=s["avgMs"],
                    maxMs=s["maxMs"],
                    callCount=s["callCount"],
                    successRate=s["successRate"],
                )
                for s in item.get("steps", [])
            ],
        )
        for item in raw
    ]
