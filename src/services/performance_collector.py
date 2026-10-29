"""性能探针服务：只采集基础设施状态，不调度任务或访问数据库仓储。"""

import asyncio
import logging
import socket
from typing import Any

import psutil

logger = logging.getLogger(__name__)


class PerformanceCollector:
    """采集连接池、任务队列和系统资源指标。"""

    def __init__(self, db_engine: Any, task_manager: Any = None) -> None:
        self.db_engine = db_engine
        self.task_manager = task_manager
        self.server_instance = socket.gethostname()

    def _metric(self, category: str, subcategory: str, metric_name: str,
                display_name: str, **values: Any) -> dict[str, Any]:
        return dict(category=category, subcategory=subcategory, metric_name=metric_name,
                    display_name=display_name, server_instance=self.server_instance, **values)

    async def collect_metrics(self) -> list[dict[str, Any]]:
        """采集各探针结果，单类探针失败不影响其他类别。"""
        metrics = []
        for probe in (self.collect_db_pool_metrics, self.collect_task_queue_metrics):
            try:
                metrics.extend(probe())
            except Exception:
                logger.warning("性能探针采集失败: %s", probe.__name__, exc_info=True)
        try:
            # 系统探针包含阻塞采样，在线程执行，不能搬到纯工具层。
            metrics.extend(await asyncio.to_thread(self.collect_system_metrics))
        except Exception:
            logger.warning("系统性能探针采集失败", exc_info=True)
        return metrics

    def collect_db_pool_metrics(self) -> list[dict[str, Any]]:
        """读取数据库引擎连接池状态，不取得业务会话。"""
        pool = self.db_engine.pool
        size = pool.size()
        checked_out = pool.checkedout()
        metric = self._metric
        return [
            metric("database", "pool", "db_pool_size", "数据库连接池大小", value_int=size, unit="count"),
            metric("database", "pool", "db_pool_checked_out", "已使用连接数", value_int=checked_out,
                   unit="count", threshold_warning=round(size * 0.8, 4), threshold_critical=round(size * 0.95, 4)),
            metric("database", "pool", "db_pool_checked_in", "空闲连接数", value_int=pool.checkedin(), unit="count"),
            metric("database", "pool", "db_pool_overflow", "溢出连接数", value_int=pool.overflow(),
                   unit="count", threshold_warning=5.0, threshold_critical=10.0),
            metric("database", "pool", "db_pool_usage_rate", "连接池使用率",
                   value_float=round(checked_out / size * 100, 4) if size > 0 else 0.0,
                   unit="percent", threshold_warning=80.0, threshold_critical=95.0),
        ]

    def collect_task_queue_metrics(self) -> list[dict[str, Any]]:
        """读取已有任务管理器队列及并发状态。"""
        manager = self.task_manager
        if manager is None:
            return []
        download_queue = getattr(manager, "_download_queue", None)
        search_queue = getattr(manager, "_search_queue", None)
        management_queue = getattr(manager, "_management_queue", None)
        download_pending = download_queue.qsize() if download_queue is not None else 0
        search_pending = search_queue.qsize() if search_queue is not None else 0
        management_pending = management_queue.qsize() if management_queue is not None else 0
        download_running = len(getattr(manager, "_current_download_tasks", ()))
        search_running = len(getattr(manager, "_current_search_tasks", ()))
        management_running = int(getattr(manager, "_current_management_task", None) is not None)
        max_concurrent = getattr(manager, "_max_concurrent_tasks", 10)
        max_search = getattr(manager, "_max_search_workers", 3)
        metric = self._metric
        return [
            metric("task", "queue", "download_queue_pending", "下载队列排队任务数", value_int=download_pending,
                   unit="count", threshold_warning=20.0, threshold_critical=50.0,
                   description=f"当前有 {download_pending} 个任务在下载队列中等待"),
            metric("task", "queue", "download_queue_running", "下载队列运行任务数", value_int=download_running,
                   unit="count", description=f"当前有 {download_running}/{max_concurrent} 个 worker 在运行"),
            metric("task", "queue", "management_queue_pending", "管理队列排队任务数", value_int=management_pending,
                   unit="count", threshold_warning=10.0, threshold_critical=20.0,
                   description=f"当前有 {management_pending} 个管理任务在等待"),
            metric("task", "queue", "management_queue_running", "管理队列运行任务数", value_int=management_running,
                   unit="count", description=f"管理队列{'正在' if management_running else '未'}运行任务"),
            metric("task", "queue", "search_queue_pending", "搜索队列排队任务数", value_int=search_pending,
                   unit="count", threshold_warning=20.0, threshold_critical=50.0,
                   description=f"当前有 {search_pending} 个任务在搜索队列中等待"),
            metric("task", "queue", "search_queue_running", "搜索队列运行任务数", value_int=search_running,
                   unit="count", description=f"当前有 {search_running}/{max_search} 个搜索 worker 在运行"),
            metric("task", "queue", "download_queue_utilization", "下载队列利用率",
                   value_float=round(download_running / max_concurrent * 100, 4) if max_concurrent > 0 else 0.0,
                   unit="percent", threshold_warning=90.0, threshold_critical=100.0),
        ]

    def collect_system_metrics(self) -> list[dict[str, Any]]:
        """采样真实主机资源，作为基础设施能力保留在服务层。"""
        cpu_percent = round(psutil.cpu_percent(interval=0.1), 4)
        memory = psutil.virtual_memory()
        disk = psutil.disk_usage("/")
        metric = self._metric
        return [
            metric("system", "cpu", "cpu_usage", "CPU 使用率", value_float=cpu_percent,
                   unit="percent", threshold_warning=70.0, threshold_critical=90.0),
            metric("system", "memory", "memory_usage", "内存使用率", value_float=round(memory.percent, 4),
                   unit="percent", threshold_warning=80.0, threshold_critical=95.0,
                   description=f"已用: {memory.used / (1024**3):.2f}GB / 总计: {memory.total / (1024**3):.2f}GB"),
            metric("system", "disk", "disk_usage", "磁盘使用率", value_float=round(disk.percent, 4),
                   unit="percent", threshold_warning=80.0, threshold_critical=90.0,
                   description=f"已用: {disk.used / (1024**3):.2f}GB / 总计: {disk.total / (1024**3):.2f}GB"),
        ]
