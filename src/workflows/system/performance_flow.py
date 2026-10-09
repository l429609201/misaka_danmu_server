"""系统性能采集流程：先探测，再以短事务持久化本轮结果。"""

from typing import Any

from src.services.performance_collector import PerformanceCollector
from src.services.service_container import get_database_service


async def collect_performance_metrics(collector: PerformanceCollector, database_service: Any = None) -> int:
    """采集并提交本轮指标，返回仓储实际记录数量。"""
    metrics = await collector.collect_metrics()
    if not metrics:
        return 0
    db = database_service or get_database_service()
    async with db.transaction():
        return await db.performance.record_metrics(metrics)
