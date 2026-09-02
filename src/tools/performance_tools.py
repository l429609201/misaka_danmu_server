"""
LLM 内部性能监测工具

提供给 LLM 在运行时直接调用的性能监测能力（非 HTTP API）

使用方式：
    from src.tools.performance_tools import PerformanceTools
    
    perf_tools = PerformanceTools()
    
    # 查询性能指标
    metrics = await perf_tools.get_latest_metrics("database")
    
    # 查询告警
    alerts = await perf_tools.get_active_alerts()
"""

import logging
from typing import List, Dict, Any, Optional
from datetime import datetime, timedelta

from src.db.crud import performance as perf_crud
from src.db import get_db_session_factory

logger = logging.getLogger(__name__)


class PerformanceTools:
    """
    LLM 性能监测内部工具

    设计为 LLM 运行时可直接调用的工具类，而非 HTTP API
    """

    def __init__(self):
        """初始化性能监测工具"""
        self.session_factory = get_db_session_factory()
    
    # ===== 性能指标查询 =====
    
    async def get_latest_metrics(
        self,
        category: Optional[str] = None,
        limit: int = 20
    ) -> List[Dict[str, Any]]:
        """
        获取最新的性能指标
        
        参数:
            category: 指标分类（database/task/cache/api/system/custom）
            limit: 返回数量（默认 20）
        
        返回:
            指标列表，每项包含：
            - metric_name: 指标名称
            - value_int/value_float/value_text: 指标值
            - unit: 单位
            - status: 状态（normal/warning/critical）
            - collected_at: 采集时间
        
        示例:
            # 获取所有数据库相关指标
            metrics = await perf_tools.get_latest_metrics("database")
            for m in metrics:
                print(f"{m['metric_name']}: {m['value_float']} {m['unit']} [{m['status']}]")
        """
        try:
            async with self.session_factory() as session:
                metrics = await perf_crud.get_metrics(
                    session,
                    category=category,
                    limit=limit
                )
                return [self._format_metric(m) for m in metrics]
        except Exception as e:
            logger.error(f"获取性能指标失败: {e}", exc_info=True)
            return []
    
    async def get_metric_value(
        self,
        category: str,
        metric_name: str
    ) -> Optional[Dict[str, Any]]:
        """
        获取指定指标的最新值
        
        参数:
            category: 指标分类
            metric_name: 指标名称
        
        返回:
            指标信息，包含值、单位、状态等
        
        示例:
            metric = await perf_tools.get_metric_value("database", "db_pool_size")
            if metric:
                print(f"连接池大小: {metric['value_int']}")
        """
        try:
            async with self.session_factory() as session:
                metric = await perf_crud.get_latest_metric(
                    session,
                    category=category,
                    metric_name=metric_name
                )
                return self._format_metric(metric) if metric else None
        except Exception as e:
            logger.error(f"获取指标值失败: {e}", exc_info=True)
            return None
    
    async def get_metric_history(
        self,
        category: str,
        metric_name: str,
        hours: int = 1
    ) -> List[Dict[str, Any]]:
        """
        获取指标的历史数据
        
        参数:
            category: 指标分类
            metric_name: 指标名称
            hours: 时间范围（小时，默认 1）
        
        返回:
            指标历史列表
        
        示例:
            history = await perf_tools.get_metric_history("database", "db_pool_usage_rate", hours=24)
            for h in history:
                print(f"{h['collected_at']}: {h['value_float']}%")
        """
        try:
            start_time = datetime.now() - timedelta(hours=hours)
            
            async with self.session_factory() as session:
                metrics = await perf_crud.get_metrics(
                    session,
                    category=category,
                    metric_name=metric_name,
                    start_time=start_time,
                    limit=1000
                )
                return [self._format_metric(m) for m in metrics]
        except Exception as e:
            logger.error(f"获取指标历史失败: {e}", exc_info=True)
            return []
    
    async def get_metric_aggregation(
        self,
        category: str,
        metric_name: str,
        hours: int = 1
    ) -> Optional[Dict[str, Any]]:
        """
        获取指标的聚合统计
        
        参数:
            category: 指标分类
            metric_name: 指标名称
            hours: 时间范围（小时）
        
        返回:
            {
                "avg": 平均值,
                "min": 最小值,
                "max": 最大值,
                "latest": 最新值,
                "sample_count": 样本数
            }
        
        示例:
            agg = await perf_tools.get_metric_aggregation("database", "db_pool_usage_rate", hours=24)
            print(f"连接池使用率 - 平均: {agg['avg']}%, 最高: {agg['max']}%")
        """
        try:
            start_time = datetime.now() - timedelta(hours=hours)
            
            async with self.session_factory() as session:
                agg = await perf_crud.get_metric_aggregation(
                    session,
                    category=category,
                    metric_name=metric_name,
                    start_time=start_time
                )
                return agg
        except Exception as e:
            logger.error(f"获取指标聚合统计失败: {e}", exc_info=True)
            return None
    
    # ===== 告警查询 =====
    
    async def get_active_alerts(self, limit: int = 50) -> List[Dict[str, Any]]:
        """
        获取未解决的告警列表
        
        参数:
            limit: 返回数量（默认 50）
        
        返回:
            告警列表，每项包含：
            - alert_level: 告警级别（warning/critical）
            - metric_category: 指标分类
            - metric_name: 指标名称
            - alert_message: 告警消息
            - current_value: 当前值
            - threshold_value: 阈值
            - created_at: 创建时间
        
        示例:
            alerts = await perf_tools.get_active_alerts()
            for alert in alerts:
                print(f"[{alert['alert_level']}] {alert['alert_message']}")
        """
        try:
            async with self.session_factory() as session:
                alerts = await perf_crud.get_alerts(
                    session,
                    is_resolved=False,
                    limit=limit
                )
                return [self._format_alert(a) for a in alerts]
        except Exception as e:
            logger.error(f"获取告警列表失败: {e}", exc_info=True)
            return []
    
    async def get_critical_alerts(self) -> List[Dict[str, Any]]:
        """
        获取严重级别的未解决告警
        
        返回:
            严重告警列表
        
        示例:
            critical = await perf_tools.get_critical_alerts()
            if critical:
                print(f"⚠️ 发现 {len(critical)} 个严重告警！")
        """
        try:
            async with self.session_factory() as session:
                alerts = await perf_crud.get_alerts(
                    session,
                    is_resolved=False,
                    alert_level="critical",
                    limit=100
                )
                return [self._format_alert(a) for a in alerts]
        except Exception as e:
            logger.error(f"获取严重告警失败: {e}", exc_info=True)
            return []
    
    # ===== 快捷查询方法 =====
    
    async def get_database_health(self) -> Dict[str, Any]:
        """
        获取数据库健康状态概览
        
        返回:
            {
                "pool_size": 连接池大小,
                "pool_usage_rate": 使用率（%）,
                "active_connections": 活跃连接数,
                "status": "healthy/warning/critical"
            }
        """
        metrics = await self.get_latest_metrics("database", limit=10)
        
        health = {
            "pool_size": None,
            "pool_usage_rate": None,
            "active_connections": None,
            "status": "unknown"
        }
        
        for m in metrics:
            if m['metric_name'] == 'db_pool_size':
                health['pool_size'] = m['value_int']
            elif m['metric_name'] == 'db_pool_usage_rate':
                health['pool_usage_rate'] = m['value_float']
            elif m['metric_name'] == 'db_active_connections':
                health['active_connections'] = m['value_int']
        
        # 判断健康状态
        if health['pool_usage_rate'] is not None:
            if health['pool_usage_rate'] > 90:
                health['status'] = 'critical'
            elif health['pool_usage_rate'] > 70:
                health['status'] = 'warning'
            else:
                health['status'] = 'healthy'
        
        return health
    
    async def get_task_queue_status(self) -> Dict[str, Any]:
        """
        获取任务队列状态
        
        返回:
            {
                "pending_tasks": 排队任务数,
                "running_tasks": 运行中任务数,
                "queue_utilization": 队列利用率（%）,
                "status": "healthy/warning/critical"
            }
        """
        metrics = await self.get_latest_metrics("task", limit=10)
        
        status = {
            "pending_tasks": None,
            "running_tasks": None,
            "queue_utilization": None,
            "status": "unknown"
        }
        
        for m in metrics:
            if m['metric_name'] == 'task_queue_pending':
                status['pending_tasks'] = m['value_int']
            elif m['metric_name'] == 'task_queue_running':
                status['running_tasks'] = m['value_int']
            elif m['metric_name'] == 'task_queue_utilization':
                status['queue_utilization'] = m['value_float']
        
        # 判断状态
        if status['queue_utilization'] is not None:
            if status['queue_utilization'] > 90:
                status['status'] = 'critical'
            elif status['queue_utilization'] > 70:
                status['status'] = 'warning'
            else:
                status['status'] = 'healthy'
        
        return status
    
    async def get_system_resources(self) -> Dict[str, Any]:
        """
        获取系统资源使用情况
        
        返回:
            {
                "cpu_usage": CPU 使用率（%）,
                "memory_usage": 内存使用率（%）,
                "disk_usage": 磁盘使用率（%）,
                "status": "healthy/warning/critical"
            }
        """
        metrics = await self.get_latest_metrics("system", limit=10)
        
        resources = {
            "cpu_usage": None,
            "memory_usage": None,
            "disk_usage": None,
            "status": "unknown"
        }
        
        for m in metrics:
            if m['metric_name'] == 'system_cpu_usage':
                resources['cpu_usage'] = m['value_float']
            elif m['metric_name'] == 'system_memory_usage':
                resources['memory_usage'] = m['value_float']
            elif m['metric_name'] == 'system_disk_usage':
                resources['disk_usage'] = m['value_float']
        
        # 判断状态（以最高使用率为准）
        max_usage = max(
            resources['cpu_usage'] or 0,
            resources['memory_usage'] or 0,
            resources['disk_usage'] or 0
        )
        
        if max_usage > 90:
            resources['status'] = 'critical'
        elif max_usage > 75:
            resources['status'] = 'warning'
        else:
            resources['status'] = 'healthy'
        
        return resources
    
    # ===== 辅助方法 =====
    
    def _format_metric(self, metric: Dict[str, Any]) -> Dict[str, Any]:
        """格式化指标数据"""
        return {
            "metric_name": metric.get("metricName"),
            "category": metric.get("category"),
            "subcategory": metric.get("subcategory"),
            "value_int": metric.get("valueInt"),
            "value_float": metric.get("valueFloat"),
            "value_text": metric.get("valueText"),
            "unit": metric.get("unit"),
            "status": metric.get("status"),
            "collected_at": metric.get("collectedAt"),
            "description": metric.get("description")
        }
    
    def _format_alert(self, alert: Dict[str, Any]) -> Dict[str, Any]:
        """格式化告警数据"""
        return {
            "alert_level": alert.get("alertLevel"),
            "metric_category": alert.get("metricCategory"),
            "metric_name": alert.get("metricName"),
            "alert_message": alert.get("alertMessage"),
            "current_value": alert.get("currentValue"),
            "threshold_value": alert.get("thresholdValue"),
            "created_at": alert.get("createdAt"),
            "is_resolved": alert.get("isResolved")
        }


# ===== 全局实例（单例模式）=====
_perf_tools_instance: Optional[PerformanceTools] = None


def get_performance_tools() -> PerformanceTools:
    """获取性能监测工具实例（单例）"""
    global _perf_tools_instance
    if _perf_tools_instance is None:
        _perf_tools_instance = PerformanceTools()
    return _perf_tools_instance
