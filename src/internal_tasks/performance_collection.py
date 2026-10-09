"""性能采集内部轮询任务，生命周期由组合根管理。"""

import asyncio
import logging
from typing import Any, Optional

from src.services.performance_collector import PerformanceCollector
from src.workflows.system.performance_flow import collect_performance_metrics

logger = logging.getLogger(__name__)


class PerformanceCollectionTask:
    """保留秒级监控周期，服务只提供探针能力。"""

    def __init__(self, collector: PerformanceCollector, collect_interval: float = 60,
                 database_service: Any = None) -> None:
        self.collector = collector
        self.collect_interval = collect_interval
        self.database_service = database_service
        self.is_running = False
        self._task: Optional[asyncio.Task] = None

    async def start(self) -> None:
        """启动应用内部监控轮询。"""
        if self.is_running:
            return
        self.is_running = True
        self._task = asyncio.create_task(self._collect_loop())

    async def stop(self) -> None:
        """取消轮询并等待任务收尾。"""
        self.is_running = False
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

    async def _collect_loop(self) -> None:
        while self.is_running:
            try:
                written = await collect_performance_metrics(self.collector, self.database_service)
                logger.debug("性能采集完成：本轮写入 %s 条指标", written)
            except Exception:
                logger.error("性能指标采集失败", exc_info=True)
            await asyncio.sleep(self.collect_interval)
