"""
性能监测指标的CRUD操作
"""

import json
import logging
from typing import Optional, List, Dict, Any
from datetime import datetime, timedelta
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, desc, and_, or_
from decimal import Decimal

from src.db.orm_models import SystemMetric, PerformanceAlert
from src.core.timezone import get_now

logger = logging.getLogger(__name__)


async def record_metric(
    session: AsyncSession,
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
        session: 数据库会话
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
    
    session.add(metric)
    await session.flush()
    
    # 如果状态异常，检查是否需要创建告警
    if status in ("warning", "critical"):
        await _check_and_create_alert(session, metric)
    
    return metric


async def _check_and_create_alert(session: AsyncSession, metric: SystemMetric):
    """检查并创建告警（内部函数）"""
    # 检查是否已有未解决的同类告警
    stmt = select(PerformanceAlert).where(
        and_(
            PerformanceAlert.metricCategory == metric.category,
            PerformanceAlert.metricName == metric.metricName,
            PerformanceAlert.isResolved == 0,
        )
    )
    result = await session.execute(stmt)
    existing_alert = result.scalar_one_or_none()
    
    if existing_alert:
        # 已有未解决告警，不重复创建
        return
    
    # 创建新告警
    alert_message = f"{metric.displayName or metric.metricName} 超过{metric.status}阈值"
    if metric.description:
        alert_message += f": {metric.description}"
    
    current_value = metric.valueFloat if metric.valueFloat is not None else metric.valueInt
    threshold_value = metric.thresholdCritical if metric.status == "critical" else metric.thresholdWarning
    
    alert = PerformanceAlert(
        metricCategory=metric.category,
        metricName=metric.metricName,
        alertLevel=metric.status,
        alertMessage=alert_message,
        currentValue=current_value,
        thresholdValue=threshold_value,
        createdAt=get_now(),
    )
    session.add(alert)


async def query_metrics(
    session: AsyncSession,
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
    """
    查询性能指标

    Args:
        session: 数据库会话
        category: 指标大类过滤
        subcategory: 指标子类过滤
        metric_name: 指标名称过滤
        status: 状态过滤
        start_time: 开始时间
        end_time: 结束时间
        server_instance: 服务器实例过滤
        limit: 返回数量限制
        offset: 偏移量

    Returns:
        指标列表
    """
    stmt = select(SystemMetric)

    # 构建过滤条件
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

    # 排序和分页
    stmt = stmt.order_by(desc(SystemMetric.collectedAt)).limit(limit).offset(offset)

    result = await session.execute(stmt)
    return list(result.scalars().all())


async def get_latest_metric(
    session: AsyncSession,
    category: str,
    metric_name: str,
    server_instance: Optional[str] = None,
) -> Optional[SystemMetric]:
    """获取指定指标的最新记录"""
    stmt = select(SystemMetric).where(
        and_(
            SystemMetric.category == category,
            SystemMetric.metricName == metric_name,
        )
    )

    if server_instance:
        stmt = stmt.where(SystemMetric.serverInstance == server_instance)

    stmt = stmt.order_by(desc(SystemMetric.collectedAt)).limit(1)

    result = await session.execute(stmt)
    return result.scalar_one_or_none()


async def get_metric_aggregation(
    session: AsyncSession,
    category: str,
    metric_name: str,
    start_time: datetime,
    end_time: datetime,
    server_instance: Optional[str] = None,
) -> Dict[str, Any]:
    """
    获取指标的聚合统计

    Returns:
        {
            "count": 总记录数,
            "avg": 平均值,
            "min": 最小值,
            "max": 最大值,
            "latest": 最新值
        }
    """
    conditions = [
        SystemMetric.category == category,
        SystemMetric.metricName == metric_name,
        SystemMetric.collectedAt >= start_time,
        SystemMetric.collectedAt <= end_time,
    ]

    if server_instance:
        conditions.append(SystemMetric.serverInstance == server_instance)

    # 聚合查询（优先使用 valueFloat，其次 valueInt）
    stmt = select(
        func.count(SystemMetric.id).label("count"),
        func.avg(func.coalesce(SystemMetric.valueFloat, SystemMetric.valueInt)).label("avg"),
        func.min(func.coalesce(SystemMetric.valueFloat, SystemMetric.valueInt)).label("min"),
        func.max(func.coalesce(SystemMetric.valueFloat, SystemMetric.valueInt)).label("max"),
    ).where(and_(*conditions))

    result = await session.execute(stmt)
    row = result.first()

    # 获取最新值
    latest_stmt = select(SystemMetric).where(and_(*conditions)).order_by(desc(SystemMetric.collectedAt)).limit(1)
    latest_result = await session.execute(latest_stmt)
    latest_metric = latest_result.scalar_one_or_none()

    latest_value = None
    if latest_metric:
        latest_value = latest_metric.valueFloat if latest_metric.valueFloat is not None else latest_metric.valueInt

    return {
        "count": row.count if row else 0,
        "avg": float(row.avg) if row and row.avg is not None else None,
        "min": float(row.min) if row and row.min is not None else None,
        "max": float(row.max) if row and row.max is not None else None,
        "latest": float(latest_value) if latest_value is not None else None,
    }


async def cleanup_old_metrics(
    session: AsyncSession,
    retention_days: int = 30,
) -> int:
    """
    清理过期的指标记录

    Args:
        session: 数据库会话
        retention_days: 保留天数

    Returns:
        删除的记录数
    """
    cutoff_time = get_now() - timedelta(days=retention_days)

    stmt = select(func.count(SystemMetric.id)).where(SystemMetric.createdAt < cutoff_time)
    result = await session.execute(stmt)
    count = result.scalar() or 0

    if count > 0:
        from sqlalchemy import delete
        delete_stmt = delete(SystemMetric).where(SystemMetric.createdAt < cutoff_time)
        await session.execute(delete_stmt)
        logger.info(f"清理了 {count} 条超过 {retention_days} 天的性能指标记录")

    return count
