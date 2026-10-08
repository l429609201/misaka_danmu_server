"""
性能统计 API — 任务流程各阶段耗时汇总

GET /api/ui/perf/stats?days=7
"""

import logging
from typing import List, Optional, Any, Dict
from datetime import timedelta

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel

from src.schemas.auth import User
from src.utils.auth import security
from src.services.service_container import get_database_service
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
    current_user: User = Depends(security.get_current_user),
) -> List[PerfFlowStat]:
    """通过性能查询仓储获取流程统计，在短事务结束后构造响应。"""
    db = get_database_service()
    async with db.transaction():
        raw = await db.performance.get_perf_stats(days=days)
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
    current_user: User = Depends(security.get_current_user),
) -> Dict[str, Any]:
    """读取最新指标及告警，在事务内转换响应以免访问脱离会话的对象。"""
    categories = ["system", "database", "task", "cache"]
    grouped: Dict[str, List[Dict[str, Any]]] = {c: [] for c in categories}
    db = get_database_service()
    async with db.transaction():
        for cat in categories:
            rows = await db.performance.query_metrics(category=cat, limit=200)
            seen = set()
            latest: List[Dict[str, Any]] = []
            # 查询结果倒序排列，保留每项首次出现的最新记录。
            for m in rows:
                if m.metricName in seen:
                    continue
                seen.add(m.metricName)
                latest.append(_fmt_metric(m))
            grouped[cat] = latest

        alerts = await db.performance.query_alerts(is_resolved=False, limit=50)
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
    current_user: User = Depends(security.get_current_user),
) -> Dict[str, Any]:
    """通过仓储读取指标历史，在事务内生成按时间正序的响应快照。"""
    start_time = get_now() - timedelta(hours=hours)
    db = get_database_service()
    async with db.transaction():
        rows = await db.performance.query_metrics(
            category=category, metric_name=metric, start_time=start_time, limit=1000,
        )
        # 仓储倒序返回，反转为绘图使用的正序。
        points: List[Dict[str, Any]] = []
        for m in reversed(rows):
            value = float(m.valueFloat) if m.valueFloat is not None else m.valueInt
            points.append({
                "collectedAt": m.collectedAt.isoformat() if m.collectedAt else None,
                "value": value,
            })
    return {"category": category, "metric": metric, "hours": hours, "points": points}
