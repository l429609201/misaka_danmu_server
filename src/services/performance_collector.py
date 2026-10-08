"""
性能监测自动采集服务

定期采集系统性能指标：
- 数据库连接池状态
- 任务队列状态
- 缓存命中率
- 系统资源使用率
"""

import asyncio
import logging
import socket
import psutil
from typing import Optional
from datetime import datetime

from src.services.service_container import get_database_service

logger = logging.getLogger(__name__)


class PerformanceCollector:
    """性能指标采集器"""
    
    def __init__(self, db_engine, task_manager=None):
        """
        初始化性能采集器

        Args:
            db_engine: 数据库引擎
            task_manager: 任务管理器（可选）
        """
        self.db_engine = db_engine
        self.task_manager = task_manager
        # C3.3 迁移：删除 cache_manager 参数，缓存指标采集已在 C2 废弃（HybridBackend 删除后无统一接口）
        self._db = get_database_service()

        # 采集配置
        self.collect_interval = 60  # 采集间隔（秒）
        self.is_running = False
        self._task = None

        # 实例标识
        # 主机标识复用模块级 socket，初始化不再延迟导入。
        self.server_instance = socket.gethostname()
    
    async def start(self):
        """启动自动采集"""
        if self.is_running:
            logger.warning("性能采集器已在运行")
            return
        
        self.is_running = True
        self._task = asyncio.create_task(self._collect_loop())
        logger.info(f"性能采集器已启动，采集间隔: {self.collect_interval}秒")
    
    async def stop(self):
        """停止自动采集"""
        if not self.is_running:
            return
        
        self.is_running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        logger.info("性能采集器已停止")
    
    async def _collect_loop(self):
        """采集循环"""
        # 采集器启动后先等一小段，避开应用启动瞬间的资源峰值，让首轮数据更稳定
        loop_count = 0
        while self.is_running:
            try:
                written = await self.collect_all_metrics()
                loop_count += 1
                # 每轮成功都打 DEBUG：避免日志过于冗余，仅在需要时查看
                logger.debug(
                    f"性能采集完成（第 {loop_count} 轮）：本轮写入 {written} 条指标，"
                    f"下次采集在 {self.collect_interval} 秒后"
                )
            except Exception as e:
                logger.error(f"性能指标采集失败: {e}", exc_info=True)

            # 等待下一次采集
            await asyncio.sleep(self.collect_interval)

    async def collect_all_metrics(self) -> int:
        """采集所有指标，返回本轮成功写入的指标条数。"""
        async with self._db.transaction():
            # 1. 数据库连接池状态
            await self._collect_db_pool_metrics()

            # 2. 任务队列状态
            if self.task_manager:
                await self._collect_task_queue_metrics()

            # 3. 系统资源（C3.3：缓存指标采集已删除，CacheManager 废弃后无统一接口）
            await self._collect_system_metrics()

            # 统计本轮新增的指标条数（commit 前 session.new 里都是待插入的 SystemMetric）
            written = sum(
                1 for obj in self._db._session.new
                if obj.__class__.__name__ == "SystemMetric"
            )
            return written
    
    async def _collect_db_pool_metrics(self):
        """采集数据库连接池指标"""
        try:
            pool = self.db_engine.pool

            # 连接池大小
            pool_size = pool.size()
            # 已签出连接数
            checked_out = pool.checkedout()
            # 已签入（空闲）连接数
            checked_in = pool.checkedin()
            # 溢出连接数
            overflow = pool.overflow()

            # 连接池使用率（四舍五入到4位小数，匹配数据库DECIMAL(20,4)精度）
            usage_rate = round((checked_out / pool_size * 100), 4) if pool_size > 0 else 0.0

            # 记录连接池大小
            await self._db.performance.record_metric(
                category="database",
                subcategory="pool",
                metric_name="db_pool_size",
                display_name="数据库连接池大小",
                value_int=pool_size,
                unit="count",
                server_instance=self.server_instance,
            )

            # 记录已使用连接数
            await self._db.performance.record_metric(
                category="database",
                subcategory="pool",
                metric_name="db_pool_checked_out",
                display_name="已使用连接数",
                value_int=checked_out,
                unit="count",
                threshold_warning=round(pool_size * 0.8, 4),
                threshold_critical=round(pool_size * 0.95, 4),
                server_instance=self.server_instance,
            )

            # 记录空闲连接数
            await self._db.performance.record_metric(
                category="database",
                subcategory="pool",
                metric_name="db_pool_checked_in",
                display_name="空闲连接数",
                value_int=checked_in,
                unit="count",
                server_instance=self.server_instance,
            )

            # 记录溢出连接数
            await self._db.performance.record_metric(
                category="database",
                subcategory="pool",
                metric_name="db_pool_overflow",
                display_name="溢出连接数",
                value_int=overflow,
                unit="count",
                threshold_warning=5.0,
                threshold_critical=10.0,
                server_instance=self.server_instance,
            )

            # 记录使用率
            await self._db.performance.record_metric(
                category="database",
                subcategory="pool",
                metric_name="db_pool_usage_rate",
                display_name="连接池使用率",
                value_float=usage_rate,
                unit="percent",
                threshold_warning=80.0,
                threshold_critical=95.0,
                server_instance=self.server_instance,
            )

        except Exception as e:
            logger.error(f"采集数据库连接池指标失败: {e}")

    async def _collect_task_queue_metrics(self):
        """采集任务队列指标"""
        try:
            # 下载队列状态
            download_queue_size = self.task_manager._download_queue.qsize() if hasattr(self.task_manager, '_download_queue') else 0
            download_running = len(self.task_manager._current_download_tasks) if hasattr(self.task_manager, '_current_download_tasks') else 0

            # 搜索队列状态
            search_queue_size = self.task_manager._search_queue.qsize() if hasattr(self.task_manager, '_search_queue') else 0
            search_running = len(self.task_manager._current_search_tasks) if hasattr(self.task_manager, '_current_search_tasks') else 0

            # 管理队列状态
            management_queue_size = self.task_manager._management_queue.qsize() if hasattr(self.task_manager, '_management_queue') else 0
            # 修正：TaskManager 使用 _current_management_task（任务对象）而不是 _management_task_running（布尔值）
            management_running = 1 if (hasattr(self.task_manager, '_current_management_task') and self.task_manager._current_management_task is not None) else 0

            # 最大并发数
            max_concurrent = self.task_manager._max_concurrent_tasks if hasattr(self.task_manager, '_max_concurrent_tasks') else 10
            max_search_concurrent = self.task_manager._max_search_workers if hasattr(self.task_manager, '_max_search_workers') else 3

            # 记录下载队列排队数
            await self._db.performance.record_metric(
                category="task",
                subcategory="queue",
                metric_name="download_queue_pending",
                display_name="下载队列排队任务数",
                value_int=download_queue_size,
                unit="count",
                threshold_warning=20.0,
                threshold_critical=50.0,
                description=f"当前有 {download_queue_size} 个任务在下载队列中等待",
                server_instance=self.server_instance,
            )

            # 记录下载队列运行数
            await self._db.performance.record_metric(
                category="task",
                subcategory="queue",
                metric_name="download_queue_running",
                display_name="下载队列运行任务数",
                value_int=download_running,
                unit="count",
                description=f"当前有 {download_running}/{max_concurrent} 个 worker 在运行",
                server_instance=self.server_instance,
            )

            # 记录管理队列排队数
            await self._db.performance.record_metric(
                category="task",
                subcategory="queue",
                metric_name="management_queue_pending",
                display_name="管理队列排队任务数",
                value_int=management_queue_size,
                unit="count",
                threshold_warning=10.0,
                threshold_critical=20.0,
                description=f"当前有 {management_queue_size} 个管理任务在等待",
                server_instance=self.server_instance,
            )

            # 记录管理队列运行数
            await self._db.performance.record_metric(
                category="task",
                subcategory="queue",
                metric_name="management_queue_running",
                display_name="管理队列运行任务数",
                value_int=management_running,
                unit="count",
                description=f"管理队列{'正在' if management_running else '未'}运行任务",
                server_instance=self.server_instance,
            )

            # 记录搜索队列排队数
            await self._db.performance.record_metric(
                category="task",
                subcategory="queue",
                metric_name="search_queue_pending",
                display_name="搜索队列排队任务数",
                value_int=search_queue_size,
                unit="count",
                threshold_warning=20.0,
                threshold_critical=50.0,
                description=f"当前有 {search_queue_size} 个任务在搜索队列中等待",
                server_instance=self.server_instance,
            )

            # 记录搜索队列运行数
            await self._db.performance.record_metric(
                category="task",
                subcategory="queue",
                metric_name="search_queue_running",
                display_name="搜索队列运行任务数",
                value_int=search_running,
                unit="count",
                description=f"当前有 {search_running}/{max_search_concurrent} 个搜索 worker 在运行",
                server_instance=self.server_instance,
            )

            # 队列利用率（四舍五入到4位小数，匹配数据库DECIMAL(20,4)精度）
            utilization = round((download_running / max_concurrent * 100), 4) if max_concurrent > 0 else 0.0
            await self._db.performance.record_metric(
                category="task",
                subcategory="queue",
                metric_name="download_queue_utilization",
                display_name="下载队列利用率",
                value_float=utilization,
                unit="percent",
                threshold_warning=90.0,
                threshold_critical=100.0,
                server_instance=self.server_instance,
            )

        except Exception as e:
            logger.error(f"采集任务队列指标失败: {e}")

    async def _collect_cache_metrics(self):
        """采集缓存指标"""
        try:
            # 尝试获取缓存后端信息
            # C4 注意：避免循环导入，cache_service 改为延迟导入（在函数内部 import）
            from src.services.cache_service import get_cache_service

            cache_backend = get_cache_service()

            if not cache_backend:
                return

            backend_type = cache_backend.__class__.__name__

            # 记录缓存后端类型
            await self._db.performance.record_metric(
                category="cache",
                subcategory="backend",
                metric_name="cache_backend_type",
                display_name="缓存后端类型",
                value_text=backend_type,
                description=f"当前使用的缓存后端: {backend_type}",
                server_instance=self.server_instance,
            )

            # 如果是 Redis 后端，获取详细信息
            if hasattr(cache_backend, '_client') and backend_type == 'RedisBackend':
                try:
                    client = await cache_backend._get_client()
                    info = await client.info()

                    # Redis 内存使用（四舍五入到4位小数）
                    memory_used = info.get('used_memory', 0)
                    memory_used_mb = round(memory_used / (1024 * 1024), 4)

                    await self._db.performance.record_metric(
                        category="cache",
                        subcategory="redis",
                        metric_name="redis_memory_used",
                        display_name="Redis 内存使用量",
                        value_float=memory_used_mb,
                        unit="MB",
                        server_instance=self.server_instance,
                    )

                    # Redis 连接数
                    connected_clients = info.get('connected_clients', 0)
                    await self._db.performance.record_metric(
                        category="cache",
                        subcategory="redis",
                        metric_name="redis_connected_clients",
                        display_name="Redis 连接客户端数",
                        value_int=connected_clients,
                        unit="count",
                        threshold_warning=50.0,
                        threshold_critical=100.0,
                        server_instance=self.server_instance,
                    )

                    # Redis 键总数
                    total_keys = sum(info.get(f'db{i}', {}).get('keys', 0) for i in range(16))
                    await self._db.performance.record_metric(
                        category="cache",
                        subcategory="redis",
                        metric_name="redis_total_keys",
                        display_name="Redis 键总数",
                        value_int=total_keys,
                        unit="count",
                        server_instance=self.server_instance,
                    )

                except Exception as e:
                    logger.error(f"获取 Redis 信息失败: {e}")

        except Exception as e:
            logger.error(f"采集缓存指标失败: {e}")

    async def _collect_system_metrics(self):
        """采集系统资源指标"""
        try:
            # CPU 使用率
            cpu_percent = round(psutil.cpu_percent(interval=0.1), 4)
            await self._db.performance.record_metric(
                category="system",
                subcategory="cpu",
                metric_name="cpu_usage",
                display_name="CPU 使用率",
                value_float=cpu_percent,
                unit="percent",
                threshold_warning=70.0,
                threshold_critical=90.0,
                server_instance=self.server_instance,
            )

            # 内存使用率
            memory = psutil.virtual_memory()
            await self._db.performance.record_metric(
                category="system",
                subcategory="memory",
                metric_name="memory_usage",
                display_name="内存使用率",
                value_float=round(memory.percent, 4),
                unit="percent",
                threshold_warning=80.0,
                threshold_critical=95.0,
                description=f"已用: {memory.used / (1024**3):.2f}GB / 总计: {memory.total / (1024**3):.2f}GB",
                server_instance=self.server_instance,
            )

            # 磁盘使用率
            disk = psutil.disk_usage('/')
            await self._db.performance.record_metric(
                category="system",
                subcategory="disk",
                metric_name="disk_usage",
                display_name="磁盘使用率",
                value_float=round(disk.percent, 4),
                unit="percent",
                threshold_warning=80.0,
                threshold_critical=90.0,
                description=f"已用: {disk.used / (1024**3):.2f}GB / 总计: {disk.total / (1024**3):.2f}GB",
                server_instance=self.server_instance,
            )

        except Exception as e:
            logger.error(f"采集系统资源指标失败: {e}")


# 全局采集器实例
_global_collector: Optional[PerformanceCollector] = None


def get_performance_collector() -> Optional[PerformanceCollector]:
    """获取全局性能采集器实例"""
    return _global_collector


async def init_performance_collector(db_engine, task_manager=None, auto_start: bool = True):
    """
    初始化性能采集器

    Args:
        db_engine: 数据库引擎
        task_manager: 任务管理器（可选）
        auto_start: 是否自动启动采集
    """
    global _global_collector

    if _global_collector:
        logger.warning("性能采集器已存在，先停止旧实例")
        await _global_collector.stop()

    # C3.3 迁移：删除 cache_manager 参数，PerformanceCollector 已不再采集缓存指标
    _global_collector = PerformanceCollector(
        db_engine=db_engine,
        task_manager=task_manager,
    )

    if auto_start:
        await _global_collector.start()

    logger.info("性能采集器初始化完成")
    return _global_collector


async def shutdown_performance_collector():
    """关闭性能采集器"""
    global _global_collector

    if _global_collector:
        await _global_collector.stop()
        _global_collector = None
        logger.info("性能采集器已关闭")


