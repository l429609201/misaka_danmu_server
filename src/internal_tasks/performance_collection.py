"""性能采集内部轮询任务，生命周期由组合根管理。"""

import asyncio
import logging
from typing import Any, Optional

from src.services.performance_collector import PerformanceCollector
from src.workflows.system.performance_flow import collect_performance_metrics, prune_performance_metrics

logger = logging.getLogger(__name__)

# 指标保留与清理节奏：采集无清理会让 system_metrics 无限增长
# 保留 1 天 + 5 分钟采样：表稳定在约 15×288≈4300 行，降低业务库写入与索引维护开销
_METRIC_RETAIN_DAYS = 1
_PRUNE_INTERVAL_SECONDS = 3600.0
# 启动后延迟首轮清理：避开启动高峰，防止升级后立即补删历史数据压垮数据库
_PRUNE_INITIAL_DELAY_SECONDS = 300.0
_COLLECT_INTERVAL_SECONDS = 300.0


class PerformanceCollectionTask:
    """保留秒级监控周期，服务只提供探针能力。"""

    def __init__(self, collector: PerformanceCollector, collect_interval: float = _COLLECT_INTERVAL_SECONDS,
                 database_service: Any = None) -> None:
        self.collector = collector
        self.collect_interval = collect_interval
        self.database_service = database_service
        self.is_running = False
        self._task: Optional[asyncio.Task] = None
        # 清理独立成后台循环，分批删除不阻塞采集
        self._prune_task: Optional[asyncio.Task] = None

    async def start(self) -> None:
        """启动应用内部监控轮询。"""
        if self.is_running:
            return
        self.is_running = True
        self._task = asyncio.create_task(self._collect_loop())
        self._prune_task = asyncio.create_task(self._prune_loop())

    async def stop(self) -> None:
        """取消轮询并等待任务收尾。"""
        self.is_running = False
        for task in (self._task, self._prune_task):
            if task is None:
                continue
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        self._task = None
        self._prune_task = None

    async def _prune_loop(self) -> None:
        """独立清理循环：延迟首轮，之后每小时分批清理过期指标。"""
        await asyncio.sleep(_PRUNE_INITIAL_DELAY_SECONDS)
        while self.is_running:
            try:
                deleted = await prune_performance_metrics(_METRIC_RETAIN_DAYS, self.database_service)
                if deleted:
                    logger.info("已清理 %s 条超过 %s 天的系统指标", deleted, _METRIC_RETAIN_DAYS)
            except Exception:
                logger.error("系统指标清理失败", exc_info=True)
            await asyncio.sleep(_PRUNE_INTERVAL_SECONDS)

    async def _collect_loop(self) -> None:
        while self.is_running:
            try:
                written = await collect_performance_metrics(self.collector, self.database_service)
                logger.debug("性能采集完成：本轮写入 %s 条指标", written)
            except Exception:
                logger.error("性能指标采集失败", exc_info=True)
            await asyncio.sleep(self.collect_interval)
