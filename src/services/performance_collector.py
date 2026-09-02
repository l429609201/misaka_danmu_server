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
import psutil
from typing import Optional
from datetime import datetime

from src.db.crud import performance as perf_crud
from src.db.database import get_session_factory

logger = logging.getLogger(__name__)


class PerformanceCollector:
    """性能指标采集器"""
    
    def __init__(self, db_engine, task_manager=None, cache_manager=None):
        self.db_engine = db_engine
        self.task_manager = task_manager
        self.cache_manager = cache_manager
        self.session_factory = get_session_factory()  # 不需要传递参数，使用全局 session factory
        
        # 采集配置
        self.collect_interval = 60  # 采集间隔（秒）
        self.is_running = False
        self._task = None
        
        # 实例标识
        import socket
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
        while self.is_running:
            try:
                await self.collect_all_metrics()
            except Exception as e:
                logger.error(f"性能指标采集失败: {e}", exc_info=True)
            
            # 等待下一次采集
            await asyncio.sleep(self.collect_interval)
    
    async def collect_all_metrics(self):
        """采集所有指标"""
        async with self.session_factory() as session:
            # 1. 数据库连接池状态
            await self._collect_db_pool_metrics(session)
            
            # 2. 任务队列状态
            if self.task_manager:
                await self._collect_task_queue_metrics(session)
            
            # 3. 缓存状态
            if self.cache_manager:
                await self._collect_cache_metrics(session)
            
            # 4. 系统资源
            await self._collect_system_metrics(session)
            
            await session.commit()
    
    async def _collect_db_pool_metrics(self, session):
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
            
            # 连接池使用率
            usage_rate = (checked_out / pool_size * 100) if pool_size > 0 else 0
            
            # 记录连接池大小
            await perf_crud.record_metric(
                session=session,
                category="database",
                subcategory="pool",
                metric_name="db_pool_size",
                display_name="数据库连接池大小",
                value_int=pool_size,
                unit="count",
                server_instance=self.server_instance,
            )
            
            # 记录已使用连接数
            await perf_crud.record_metric(
                session=session,
                category="database",
                subcategory="pool",
                metric_name="db_pool_checked_out",
                display_name="已使用连接数",
                value_int=checked_out,
                unit="count",
                threshold_warning=float(pool_size * 0.8),
                threshold_critical=float(pool_size * 0.95),
                server_instance=self.server_instance,
            )
            
            # 记录空闲连接数
            await perf_crud.record_metric(
                session=session,
                category="database",
                subcategory="pool",
                metric_name="db_pool_checked_in",
                display_name="空闲连接数",
                value_int=checked_in,
                unit="count",
                server_instance=self.server_instance,
            )
            
            # 记录溢出连接数
            await perf_crud.record_metric(
                session=session,
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
            await perf_crud.record_metric(
                session=session,
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

    async def _collect_task_queue_metrics(self, session):
        """采集任务队列指标"""
        try:
            # 下载队列状态
            download_queue_size = self.task_manager._download_queue.qsize() if hasattr(self.task_manager, '_download_queue') else 0
            download_running = len(self.task_manager._current_download_tasks) if hasattr(self.task_manager, '_current_download_tasks') else 0

            # 管理队列状态
            management_queue_size = self.task_manager._management_queue.qsize() if hasattr(self.task_manager, '_management_queue') else 0
            management_running = 1 if self.task_manager._management_task_running else 0

            # 最大并发数
            max_concurrent = self.task_manager._max_concurrent_tasks if hasattr(self.task_manager, '_max_concurrent_tasks') else 10

            # 记录下载队列排队数
            await perf_crud.record_metric(
                session=session,
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
            await perf_crud.record_metric(
                session=session,
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
            await perf_crud.record_metric(
                session=session,
                category="task",
                subcategory="queue",
                metric_name="management_queue_pending",
                display_name="管理队列排队任务数",
                value_int=management_queue_size,
                unit="count",
                threshold_warning=10.0,
                threshold_critical=20.0,
                server_instance=self.server_instance,
            )

            # 队列利用率
            utilization = (download_running / max_concurrent * 100) if max_concurrent > 0 else 0
            await perf_crud.record_metric(
                session=session,
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

    async def _collect_cache_metrics(self, session):
        """采集缓存指标"""
        try:
            # 尝试获取缓存后端信息
            from src.core.cache import get_cache_backend
            cache_backend = get_cache_backend()

            if not cache_backend:
                return

            backend_type = cache_backend.__class__.__name__

            # 记录缓存后端类型
            await perf_crud.record_metric(
                session=session,
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

                    # Redis 内存使用
                    memory_used = info.get('used_memory', 0)
                    memory_used_mb = memory_used / (1024 * 1024)

                    await perf_crud.record_metric(
                        session=session,
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
                    await perf_crud.record_metric(
                        session=session,
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
                    await perf_crud.record_metric(
                        session=session,
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

    async def _collect_system_metrics(self, session):
        """采集系统资源指标"""
        try:
            # CPU 使用率
            cpu_percent = psutil.cpu_percent(interval=0.1)
            await perf_crud.record_metric(
                session=session,
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
            await perf_crud.record_metric(
                session=session,
                category="system",
                subcategory="memory",
                metric_name="memory_usage",
                display_name="内存使用率",
                value_float=memory.percent,
                unit="percent",
                threshold_warning=80.0,
                threshold_critical=95.0,
                description=f"已用: {memory.used / (1024**3):.2f}GB / 总计: {memory.total / (1024**3):.2f}GB",
                server_instance=self.server_instance,
            )

            # 磁盘使用率
            disk = psutil.disk_usage('/')
            await perf_crud.record_metric(
                session=session,
                category="system",
                subcategory="disk",
                metric_name="disk_usage",
                display_name="磁盘使用率",
                value_float=disk.percent,
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


async def init_performance_collector(db_engine, task_manager=None, cache_manager=None, auto_start: bool = True):
    """
    初始化性能采集器

    Args:
        db_engine: 数据库引擎
        task_manager: 任务管理器
        cache_manager: 缓存管理器
        auto_start: 是否自动启动采集
    """
    global _global_collector

    if _global_collector:
        logger.warning("性能采集器已存在，先停止旧实例")
        await _global_collector.stop()

    _global_collector = PerformanceCollector(
        db_engine=db_engine,
        task_manager=task_manager,
        cache_manager=cache_manager,
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


