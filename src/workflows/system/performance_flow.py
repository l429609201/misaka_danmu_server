"""系统性能采集流程：先探测，再以短事务持久化本轮结果。"""

import asyncio
from datetime import timedelta
from typing import Any

from src.core.timezone import get_now
from src.services.performance_collector import PerformanceCollector
from src.services.service_container import get_database_service

# 分批清理参数：每批一个短事务，批间让出 IO，避免单个长事务拖垮数据库
_PRUNE_BATCH_SIZE = 5000
_PRUNE_BATCH_PAUSE_SECONDS = 0.5


async def collect_performance_metrics(collector: PerformanceCollector, database_service: Any = None) -> int:
    """采集并提交本轮指标，返回仓储实际记录数量。"""
    metrics = await collector.collect_metrics()
    if not metrics:
        return 0
    db = database_service or get_database_service()
    async with db.transaction():
        return await db.performance.record_metrics(metrics)


async def prune_performance_metrics(retain_days: int = 1, database_service: Any = None) -> int:
    """分批清理过期系统指标，返回删除总行数；每批由本流程声明独立事务。"""
    db = database_service or get_database_service()
    # cutoff 固定在开始时刻，避免循环期间新写入的行被反复纳入
    cutoff = get_now() - timedelta(days=retain_days)
    total = 0
    while True:
        async with db.transaction():
            deleted = await db.performance.delete_old_metrics(cutoff, _PRUNE_BATCH_SIZE)
        total += deleted
        if deleted < _PRUNE_BATCH_SIZE:
            return total
        await asyncio.sleep(_PRUNE_BATCH_PAUSE_SECONDS)
