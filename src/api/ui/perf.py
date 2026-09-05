"""
性能统计 API — 任务流程各阶段耗时汇总

GET /api/ui/perf/stats?days=7
"""

import logging
from typing import List, Optional, Any, Dict
from datetime import timedelta

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from src import security
from src.db import crud, get_db_session, models
from src.db.crud import performance as perf_crud
from src.core.timezone import get_now

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


# ============ 系统资源监控（system_metrics 表）============
# 数据来源：PerformanceCollector 每 60 秒采集的系统资源指标，
# 与上面的任务流程统计（task_perf_events）是两套独立数据。


def _fmt_metric(m) -> Dict[str, Any]:
    """把 SystemMetric ORM 对象转成前端友好的 dict（数值统一取 float/int）。"""
    value = None
    if m.valueFloat is not None:
        value = float(m.valueFloat)
    elif m.valueInt is not None:
        value = m.valueInt
    return {
        "category": m.category,
        "subcategory": m.subcategory,
        "metricName": m.metricName,
        "displayName": m.displayName or m.metricName,
        "value": value,
        "valueText": m.valueText,
        "unit": m.unit,
        "status": m.status,
        "thresholdWarning": float(m.thresholdWarning) if m.thresholdWarning is not None else None,
        "thresholdCritical": float(m.thresholdCritical) if m.thresholdCritical is not None else None,
        "description": m.description,
        "collectedAt": m.collectedAt.isoformat() if m.collectedAt else None,
    }


@router.get("/perf/system-metrics", summary="获取系统资源最新指标（分组）")
async def get_system_metrics(
    current_user: models.User = Depends(security.get_current_user),
    session: AsyncSession = Depends(get_db_session),
):
    """
    返回各类系统资源指标的最新值，按 category 分组：
    - system: CPU / 内存 / 磁盘使用率
    - database: 连接池大小 / 已用 / 空闲 / 使用率
    - task: 下载/管理队列排队与运行数、利用率
    - cache: Redis 内存 / 连接数 / 键数
    另附未解决告警列表 alerts。

    实现：取每个 metricName 的最新一条（按 collectedAt 倒序去重）。
    """
    # 一次取足够多的近端记录，再在内存里按 metricName 去重取最新，
    # 避免对每个指标名单独发查询（指标名不固定，随采集内容变化）。
    categories = ["system", "database", "task", "cache"]
    grouped: Dict[str, List[Dict[str, Any]]] = {c: [] for c in categories}

    for cat in categories:
        rows = await perf_crud.query_metrics(session, category=cat, limit=200)
        seen = set()
        latest: List[Dict[str, Any]] = []
        # query_metrics 已按 collectedAt 倒序，首次遇到的即最新
        for m in rows:
            if m.metricName in seen:
                continue
            seen.add(m.metricName)
            latest.append(_fmt_metric(m))
        grouped[cat] = latest

    alerts = await perf_crud.query_alerts(session, is_resolved=False, limit=50)
    alert_list = [
        {
            "id": a.id,
            "level": a.alertLevel,
            "category": a.metricCategory,
            "metricName": a.metricName,
            "message": a.alertMessage,
            "currentValue": float(a.currentValue) if a.currentValue is not None else None,
            "thresholdValue": float(a.thresholdValue) if a.thresholdValue is not None else None,
            "createdAt": a.createdAt.isoformat() if a.createdAt else None,
        }
        for a in alerts
    ]

    return {"groups": grouped, "alerts": alert_list}


class MetricHistoryPoint(BaseModel):
    collectedAt: str
    value: Optional[float] = None


@router.get("/perf/system-metrics/history", summary="获取单个系统指标的历史趋势")
async def get_system_metric_history(
    category: str = Query(..., description="指标大类，如 system/database/task/cache"),
    metric: str = Query(..., description="指标名，如 cpu_usage / db_pool_usage_rate"),
    hours: int = Query(24, ge=1, le=168, description="回溯小时数，1~168（最多7天）"),
    current_user: models.User = Depends(security.get_current_user),
    session: AsyncSession = Depends(get_db_session),
):
    """
    返回指定指标在最近 N 小时内的时间序列（按时间正序），供前端画趋势折线。
    """
    start_time = get_now() - timedelta(hours=hours)
    rows = await perf_crud.query_metrics(
        session,
        category=category,
        metric_name=metric,
        start_time=start_time,
        limit=1000,
    )
    # query_metrics 按 collectedAt 倒序返回，这里反转成正序便于画图
    points: List[Dict[str, Any]] = []
    for m in reversed(rows):
        value = float(m.valueFloat) if m.valueFloat is not None else m.valueInt
        points.append({
            "collectedAt": m.collectedAt.isoformat() if m.collectedAt else None,
            "value": value,
        })
    return {"category": category, "metric": metric, "hours": hours, "points": points}
