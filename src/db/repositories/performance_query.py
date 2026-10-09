"""
PerformanceQueryRepository - 性能监测查询层

职责：处理性能指标的记录和查询操作。
"""

import json
import logging
from collections import defaultdict
from typing import Optional, Dict, Any, List
from datetime import datetime, timedelta
from decimal import Decimal

from sqlalchemy import select, and_, desc, func, cast, delete, Float, Integer
from sqlalchemy.ext.asyncio import AsyncSession

from ..orm_models import SystemMetric, PerformanceAlert, TaskPerfEvent
from src.core.timezone import get_now

logger = logging.getLogger(__name__)


def _percentile(sorted_vals: List[float], p: float) -> float:
    """计算已排序列表的第 p 百分位数（线性插值），p 取 0~100。"""
    if not sorted_vals:
        return 0.0
    n = len(sorted_vals)
    if n == 1:
        return float(sorted_vals[0])
    idx = (p / 100.0) * (n - 1)
    lo = int(idx)
    hi = lo + 1
    if hi >= n:
        return float(sorted_vals[-1])
    frac = idx - lo
    return round(float(sorted_vals[lo]) * (1 - frac) + float(sorted_vals[hi]) * frac, 1)


class PerformanceQueryRepository:
    """性能监测查询仓储"""

    def __init__(self, session: AsyncSession):
        self._session = session

    async def record_metric(
        self,
        category: str,
        metric_name: str,
        value_int: Optional[int] = None,
        value_float: Optional[float] = None,
        value_text: Optional[str] = None,
        value_json: Optional[Dict[str, Any]] = None,
        subcategory: Optional[str] = None,
        display_name: Optional[str] = None,
        unit: Optional[str] = None,
        threshold_warning: Optional[float] = None,
        threshold_critical: Optional[float] = None,
        server_instance: Optional[str] = None,
        tags: Optional[Dict[str, Any]] = None,
        description: Optional[str] = None,
        context: Optional[str] = None,
        source: str = "auto_collect",
    ) -> SystemMetric:
        """
        记录一条性能指标

        Args:
            category: 指标大类 (database/task/cache/api/system/custom)
            metric_name: 指标名称（唯一标识）
            value_int: 整数值
            value_float: 浮点值
            value_text: 文本值
            value_json: JSON值（dict会被序列化）
            subcategory: 指标子类
            display_name: 显示名称
            unit: 单位
            threshold_warning: 警告阈值
            threshold_critical: 严重阈值
            server_instance: 服务器实例标识
            tags: 标签字典（会被序列化为JSON）
            description: 详细说明
            context: 上下文信息
            source: 数据来源

        Returns:
            创建的SystemMetric对象
        """
        # 自动判断状态
        status = "normal"
        if value_float is not None:
            if threshold_critical and value_float >= threshold_critical:
                status = "critical"
            elif threshold_warning and value_float >= threshold_warning:
                status = "warning"
        elif value_int is not None:
            if threshold_critical and value_int >= threshold_critical:
                status = "critical"
            elif threshold_warning and value_int >= threshold_warning:
                status = "warning"

        # 序列化JSON字段
        value_json_str = json.dumps(value_json, ensure_ascii=False) if value_json else None
        tags_str = json.dumps(tags, ensure_ascii=False) if tags else None

        metric = SystemMetric(
            category=category,
            subcategory=subcategory,
            metricName=metric_name,
            displayName=display_name,
            valueInt=value_int,
            valueFloat=Decimal(str(value_float)) if value_float is not None else None,
            valueText=value_text,
            valueJson=value_json_str,
            unit=unit,
            status=status,
            thresholdWarning=Decimal(str(threshold_warning)) if threshold_warning is not None else None,
            thresholdCritical=Decimal(str(threshold_critical)) if threshold_critical is not None else None,
            serverInstance=server_instance,
            tags=tags_str,
            source=source,
            description=description,
            context=context,
            collectedAt=get_now(),
            createdAt=get_now(),
        )

        self._session.add(metric)
        await self._session.flush()

        # 检查是否触发告警
        if status in ("warning", "critical"):
            await self._check_and_create_alert(metric)

        return metric

    async def record_metrics(self, metrics: List[Dict[str, Any]]) -> int:
        """记录探针采集结果，按实际成功创建的对象计数，不读取 Session.new。"""
        written = 0
        for payload in metrics:
            metric = await self.record_metric(**payload)
            if metric is not None:
                written += 1
        return written

    async def _check_and_create_alert(self, metric: SystemMetric):
        """检查指标是否需要创建告警"""
        # 这里可以扩展告警逻辑
        pass

    # ==================== 指标查询 ====================
    # why: 以下方法自 crud/performance.py 迁入，逻辑保持一致，
    #      仅去掉显式 session 参数（改用仓储实例持有的会话）。

    async def query_metrics(
        self,
        category: Optional[str] = None,
        subcategory: Optional[str] = None,
        metric_name: Optional[str] = None,
        status: Optional[str] = None,
        start_time: Optional[datetime] = None,
        end_time: Optional[datetime] = None,
        server_instance: Optional[str] = None,
        limit: int = 100,
        offset: int = 0,
    ) -> List[SystemMetric]:
        """按多条件查询性能指标，按采集时间倒序返回。

        Args:
            category: 指标大类过滤
            subcategory: 指标子类过滤
            metric_name: 指标名称过滤
            status: 状态过滤
            start_time: 采集时间下界（含）
            end_time: 采集时间上界（含）
            server_instance: 服务器实例过滤
            limit: 返回数量上限
            offset: 分页偏移量

        Returns:
            指标记录列表
        """
        stmt = select(SystemMetric)

        conditions = []
        if category:
            conditions.append(SystemMetric.category == category)
        if subcategory:
            conditions.append(SystemMetric.subcategory == subcategory)
        if metric_name:
            conditions.append(SystemMetric.metricName == metric_name)
        if status:
            conditions.append(SystemMetric.status == status)
        if start_time:
            conditions.append(SystemMetric.collectedAt >= start_time)
        if end_time:
            conditions.append(SystemMetric.collectedAt <= end_time)
        if server_instance:
            conditions.append(SystemMetric.serverInstance == server_instance)

        if conditions:
            stmt = stmt.where(and_(*conditions))

        stmt = stmt.order_by(desc(SystemMetric.collectedAt)).limit(limit).offset(offset)

        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def get_latest_metric(
        self,
        category: str,
        metric_name: str,
        server_instance: Optional[str] = None,
    ) -> Optional[SystemMetric]:
        """获取指定指标的最新一条记录。

        Args:
            category: 指标大类
            metric_name: 指标名称
            server_instance: 服务器实例过滤，为空则不限

        Returns:
            最新的指标记录，无记录时返回 None
        """
        stmt = select(SystemMetric).where(
            and_(
                SystemMetric.category == category,
                SystemMetric.metricName == metric_name,
            )
        )

        if server_instance:
            stmt = stmt.where(SystemMetric.serverInstance == server_instance)

        stmt = stmt.order_by(desc(SystemMetric.collectedAt)).limit(1)

        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def get_metric_aggregation(
        self,
        category: str,
        metric_name: str,
        start_time: datetime,
        end_time: datetime,
        server_instance: Optional[str] = None,
    ) -> Dict[str, Any]:
        """统计指定时间窗内某指标的聚合值。

        数值优先取 valueFloat，缺失时回退到 valueInt。

        Args:
            category: 指标大类
            metric_name: 指标名称
            start_time: 统计窗口起点（含）
            end_time: 统计窗口终点（含）
            server_instance: 服务器实例过滤，为空则不限

        Returns:
            含 count / avg / min / max / latest 的字典
        """
        conditions = [
            SystemMetric.category == category,
            SystemMetric.metricName == metric_name,
            SystemMetric.collectedAt >= start_time,
            SystemMetric.collectedAt <= end_time,
        ]

        if server_instance:
            conditions.append(SystemMetric.serverInstance == server_instance)

        value_expr = func.coalesce(SystemMetric.valueFloat, SystemMetric.valueInt)
        stmt = select(
            func.count(SystemMetric.id).label("count"),
            func.avg(value_expr).label("avg"),
            func.min(value_expr).label("min"),
            func.max(value_expr).label("max"),
        ).where(and_(*conditions))

        result = await self._session.execute(stmt)
        row = result.first()

        latest_stmt = (
            select(SystemMetric)
            .where(and_(*conditions))
            .order_by(desc(SystemMetric.collectedAt))
            .limit(1)
        )
        latest_result = await self._session.execute(latest_stmt)
        latest_metric = latest_result.scalar_one_or_none()

        latest_value = None
        if latest_metric:
            latest_value = (
                latest_metric.valueFloat
                if latest_metric.valueFloat is not None
                else latest_metric.valueInt
            )

        return {
            "count": row.count if row else 0,
            "avg": float(row.avg) if row and row.avg is not None else None,
            "min": float(row.min) if row and row.min is not None else None,
            "max": float(row.max) if row and row.max is not None else None,
            "latest": float(latest_value) if latest_value is not None else None,
        }

    async def query_alerts(
        self,
        is_resolved: Optional[bool] = None,
        alert_level: Optional[str] = None,
        metric_category: Optional[str] = None,
        limit: int = 100,
        offset: int = 0,
    ) -> List[PerformanceAlert]:
        """按多条件查询性能告警，未解决的排在前面。

        Args:
            is_resolved: None 表示不限，True/False 分别筛选已解决/未解决
            alert_level: 告警级别过滤
            metric_category: 指标分类过滤
            limit: 返回数量上限
            offset: 分页偏移量

        Returns:
            告警记录列表
        """
        stmt = select(PerformanceAlert)

        conditions = []
        if is_resolved is not None:
            conditions.append(PerformanceAlert.isResolved == (1 if is_resolved else 0))
        if alert_level:
            conditions.append(PerformanceAlert.alertLevel == alert_level)
        if metric_category:
            conditions.append(PerformanceAlert.metricCategory == metric_category)

        if conditions:
            stmt = stmt.where(and_(*conditions))

        stmt = (
            stmt.order_by(
                PerformanceAlert.isResolved.asc(),
                desc(PerformanceAlert.createdAt),
            )
            .limit(limit)
            .offset(offset)
        )

        result = await self._session.execute(stmt)
        return list(result.scalars().all())


    # ==================== 流程性能统计 ====================
    # why: 自 crud/task.py 迁入，百分位在 Python 层计算，
    #      避免 MySQL/PostgreSQL/SQLite 的方言差异。

    async def get_perf_stats(self, days: int = 7) -> List[Dict[str, Any]]:
        """查询各流程的性能汇总统计（含 P50/P95/P99 与步骤明细）。"""
        cutoff = get_now() - timedelta(days=days)

        # ── 步骤级聚合：avg/max + success 计数 ──
        step_stmt = (
            select(
                TaskPerfEvent.flowType,
                TaskPerfEvent.stepName,
                func.count().label("call_count"),
                func.avg(cast(TaskPerfEvent.durationMs, Float)).label("avg_ms"),
                func.max(cast(TaskPerfEvent.durationMs, Float)).label("max_ms"),
                func.sum(func.cast(TaskPerfEvent.success, Integer)).label("success_count"),
            )
            .where(TaskPerfEvent.createdAt >= cutoff)
            .group_by(TaskPerfEvent.flowType, TaskPerfEvent.stepName)
        )
        step_rows = (await self._session.execute(step_stmt)).mappings().all()

        # ── 步骤级原始耗时：用于计算百分位 ──
        raw_step_stmt = select(
            TaskPerfEvent.flowType,
            TaskPerfEvent.stepName,
            cast(TaskPerfEvent.durationMs, Float).label("duration_ms"),
        ).where(TaskPerfEvent.createdAt >= cutoff)
        raw_step_vals: Dict[Any, List[float]] = defaultdict(list)
        for row in await self._session.execute(raw_step_stmt):
            raw_step_vals[(row.flowType, row.stepName)].append(float(row.duration_ms or 0))
        for key in raw_step_vals:
            raw_step_vals[key].sort()

        # ── 流程级聚合（按 correlationId 去重计次）──
        flow_stmt = (
            select(
                TaskPerfEvent.flowType,
                func.count(func.distinct(TaskPerfEvent.correlationId)).label("total_runs"),
                func.avg(cast(TaskPerfEvent.totalDurationMs, Float)).label("avg_total_ms"),
            )
            .where(TaskPerfEvent.createdAt >= cutoff)
            .group_by(TaskPerfEvent.flowType)
        )
        flow_rows = {
            r["flowType"]: dict(r)
            for r in (await self._session.execute(flow_stmt)).mappings().all()
        }

        # ── 流程级原始总耗时：用于流程百分位 ──
        raw_flow_stmt = select(
            TaskPerfEvent.flowType,
            cast(TaskPerfEvent.totalDurationMs, Float).label("total_ms"),
        ).where(
            TaskPerfEvent.createdAt >= cutoff,
            TaskPerfEvent.totalDurationMs.isnot(None),
        )
        raw_flow_vals: Dict[Any, List[float]] = defaultdict(list)
        for row in await self._session.execute(raw_flow_stmt):
            raw_flow_vals[row.flowType].append(float(row.total_ms or 0))
        for key in raw_flow_vals:
            raw_flow_vals[key].sort()

        # ── 组装步骤结果（含百分位）──
        steps_by_flow: Dict[Any, List[Dict[str, Any]]] = defaultdict(list)
        for r in step_rows:
            flow = r["flowType"]
            step = r["stepName"]
            call_count = int(r["call_count"] or 0)
            success_count = int(r["success_count"] or 0)
            vals = raw_step_vals.get((flow, step), [])
            steps_by_flow[flow].append({
                "stepName": step,
                "avgMs": round(float(r["avg_ms"] or 0), 1),
                "maxMs": round(float(r["max_ms"] or 0), 1),
                "p50Ms": _percentile(vals, 50),
                "p95Ms": _percentile(vals, 95),
                "p99Ms": _percentile(vals, 99),
                "callCount": call_count,
                "successRate": round(success_count / call_count * 100, 1) if call_count else 100.0,
            })

        # ── 组装流程结果（含百分位）──
        stats: List[Dict[str, Any]] = []
        for flow_type_key, flow_data in flow_rows.items():
            flow_vals = raw_flow_vals.get(flow_type_key, [])
            stats.append({
                "flowType": flow_type_key,
                "totalRuns": int(flow_data.get("total_runs") or 0),
                "avgTotalMs": round(float(flow_data.get("avg_total_ms") or 0), 1),
                "p50TotalMs": _percentile(flow_vals, 50),
                "p95TotalMs": _percentile(flow_vals, 95),
                "p99TotalMs": _percentile(flow_vals, 99),
                "steps": steps_by_flow.get(flow_type_key, []),
            })

        stats.sort(key=lambda x: x["totalRuns"], reverse=True)
        return stats

    async def save_perf_events(
        self,
        flow_type: str,
        correlation_id: str,
        steps: List[Any],
        total_duration_ms: float
    ) -> None:
        """
        批量保存任务性能事件

        Args:
            flow_type: 流程类型（如「弹幕通用导入」）
            correlation_id: 关联ID（通常为task_id）
            steps: 步骤列表，每个步骤包含 step_name, duration_ms, success, details
            total_duration_ms: 整个流程的总耗时
        """
        for step in steps:
            event = TaskPerfEvent(
                flowType=flow_type,
                correlationId=correlation_id,
                stepName=step.step_name,
                durationMs=round(step.duration_ms, 2),
                success=step.success,
                details=step.details,
                totalDurationMs=round(total_duration_ms, 2),
                createdAt=get_now(),
            )
            self._session.add(event)

        await self._session.flush()

    async def delete_old_perf_events(self, retain_days: int = 90) -> int:
        """清理超过保留天数的性能事件，返回删除行数。"""
        cutoff = get_now() - timedelta(days=retain_days)
        result = await self._session.execute(
            delete(TaskPerfEvent).where(TaskPerfEvent.createdAt < cutoff)
        )
        await self._session.flush()
        return int(result.rowcount or 0)
