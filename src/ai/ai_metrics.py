"""AI 调用监控和统计模块。"""

import asyncio
import logging
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Awaitable, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)


@dataclass
class AICallMetrics:
    """AI 调用指标。"""

    timestamp: datetime
    method: str
    success: bool
    duration_ms: int
    tokens_used: int
    model: str
    error: Optional[str] = None
    cache_hit: bool = False


class AIMetricsCollector:
    """只负责维护 AI 指标内存统计，并向上层发出指标事件。"""

    def __init__(
        self,
        max_history: int = 1000,
        on_record: Optional[Callable[[AICallMetrics], Awaitable[None]]] = None,
    ) -> None:
        """初始化指标收集器。"""
        self.metrics: List[AICallMetrics] = []
        self.max_history = max_history
        self._on_record = on_record
        self.logger = logging.getLogger(self.__class__.__name__)

    def record(self, metric: AICallMetrics) -> None:
        """记录指标并通知上层，不接触数据库或事务。"""
        self.metrics.append(metric)
        if len(self.metrics) > self.max_history:
            self.metrics = self.metrics[-self.max_history:]

        if self._on_record is not None:
            asyncio.create_task(self._notify(metric))

        if metric.success:
            self.logger.debug(
                f"AI调用成功: {metric.method} | 耗时: {metric.duration_ms}ms | "
                f"Tokens: {metric.tokens_used} | 缓存: {'命中' if metric.cache_hit else '未命中'}"
            )
        else:
            self.logger.warning(
                f"AI调用失败: {metric.method} | 耗时: {metric.duration_ms}ms | 错误: {metric.error}"
            )

    async def _notify(self, metric: AICallMetrics) -> None:
        """向业务服务发送指标事件，事件失败不影响 AI 主流程。"""
        try:
            if self._on_record is not None:
                await self._on_record(metric)
        except Exception:
            self.logger.exception("AI指标事件发送失败")

    def get_stats(self, hours: int = 24) -> Dict:
        """
        获取统计数据
        
        Args:
            hours: 统计最近多少小时的数据
        
        Returns:
            统计数据字典
        """
        cutoff = datetime.now() - timedelta(hours=hours)
        recent = [m for m in self.metrics if m.timestamp > cutoff]
        
        if not recent:
            return {
                "period_hours": hours,
                "total_calls": 0,
                "success_rate": 0.0,
                "total_tokens": 0,
                "avg_duration_ms": 0.0,
                "cache_hit_rate": 0.0,
                "by_method": {},
                "errors": []
            }
        
        # 基础统计
        total_calls = len(recent)
        success_count = sum(1 for m in recent if m.success)
        total_tokens = sum(m.tokens_used for m in recent)
        total_duration = sum(m.duration_ms for m in recent)
        cache_hits = sum(1 for m in recent if m.cache_hit)
        
        # 按方法分组统计
        by_method = self._group_by_method(recent)
        
        # 错误统计
        errors = [
            {
                "timestamp": m.timestamp.isoformat(),
                "method": m.method,
                "error": m.error
            }
            for m in recent if not m.success
        ]
        
        return {
            "period_hours": hours,
            "total_calls": total_calls,
            "success_rate": success_count / total_calls,
            "total_tokens": total_tokens,
            "avg_duration_ms": total_duration / total_calls,
            "cache_hit_rate": cache_hits / total_calls,
            "by_method": by_method,
            "errors": errors[-10:]  # 最近10个错误
        }
    
    def _group_by_method(self, metrics: List[AICallMetrics]) -> Dict:
        """按方法分组统计"""
        grouped = defaultdict(lambda: {
            "calls": 0,
            "success": 0,
            "tokens": 0,
            "duration_ms": 0,
            "cache_hits": 0
        })
        
        for m in metrics:
            g = grouped[m.method]
            g["calls"] += 1
            if m.success:
                g["success"] += 1
            g["tokens"] += m.tokens_used
            g["duration_ms"] += m.duration_ms
            if m.cache_hit:
                g["cache_hits"] += 1
        
        # 计算平均值和成功率
        result = {}
        for method, stats in grouped.items():
            result[method] = {
                "calls": stats["calls"],
                "success_rate": stats["success"] / stats["calls"],
                "total_tokens": stats["tokens"],
                "avg_duration_ms": stats["duration_ms"] / stats["calls"],
                "cache_hit_rate": stats["cache_hits"] / stats["calls"]
            }
        
        return result

