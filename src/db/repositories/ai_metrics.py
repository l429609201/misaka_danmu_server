"""
AIMetricsRepository - AI调用统计数据访问层
"""

import logging
from datetime import datetime, timedelta
from typing import Any, Dict, Optional, List

from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..orm_models import AIMetricsLog
from .base import BaseRepository

logger = logging.getLogger(__name__)


class AIMetricsRepository(BaseRepository[AIMetricsLog]):
    """AI调用统计 Repository"""

    async def get_latest_records(self, limit: int) -> List[Dict[str, Any]]:
        """读取指定数量的最新调用记录，返回可在事务外使用的普通数据。"""
        result = await self._session.execute(
            select(AIMetricsLog).order_by(AIMetricsLog.timestamp.desc()).limit(limit)
        )
        return [{
            "id": row.id, "method": row.method, "success": row.success,
            "durationMs": row.durationMs, "tokensUsed": row.tokensUsed,
            "model": row.model, "error": row.error, "cacheHit": row.cacheHit,
            "timestamp": row.timestamp.isoformat() if row.timestamp else "",
        } for row in result.scalars().all()]

    async def get_summary_since(self, since: datetime) -> Dict[str, Any]:
        """聚合指定时间以来的调用数量、成功数、缓存命中和资源消耗。"""
        # 单次聚合替代五次查询，空窗口下所有指标保持为零。
        result = await self._session.execute(
            select(
                func.count(AIMetricsLog.id),
                func.count(case((AIMetricsLog.success.is_(True), 1))),
                func.coalesce(func.sum(AIMetricsLog.tokensUsed), 0),
                func.count(case((AIMetricsLog.cacheHit.is_(True), 1))),
                func.coalesce(func.avg(AIMetricsLog.durationMs), 0),
            ).where(AIMetricsLog.timestamp >= since)
        )
        total, success, tokens, hits, duration = result.one()
        return {
            "totalCalls": total, "successCalls": success,
            "totalTokens": tokens, "cacheHits": hits, "avgDurationMs": float(duration),
        }

    async def get_stats(self, hours: int = 24) -> Dict[str, Any]:
        """读取时间窗口内的调用统计，保持与内存统计一致的响应结构。"""
        # 与指标采集器的时间口径保持一致，并在数据库聚合以免载入全部历史。
        cutoff = datetime.now() - timedelta(hours=hours)
        totals = await self.get_summary_since(cutoff)
        total = totals["totalCalls"]
        grouped = await self._session.execute(
            select(
                AIMetricsLog.method,
                func.count(AIMetricsLog.id),
                func.count(case((AIMetricsLog.success.is_(True), 1))),
                func.coalesce(func.sum(AIMetricsLog.tokensUsed), 0),
                func.coalesce(func.avg(AIMetricsLog.durationMs), 0),
                func.count(case((AIMetricsLog.cacheHit.is_(True), 1))),
            ).where(AIMetricsLog.timestamp >= cutoff).group_by(AIMetricsLog.method)
        )
        by_method = {
            method: {
                "calls": calls,
                "success_rate": success / calls,
                "total_tokens": tokens,
                "avg_duration_ms": float(duration),
                "cache_hit_rate": hits / calls,
            }
            for method, calls, success, tokens, duration, hits in grouped.all()
        }
        errors = await self._session.execute(
            select(AIMetricsLog.timestamp, AIMetricsLog.method, AIMetricsLog.error)
            .where(AIMetricsLog.timestamp >= cutoff, AIMetricsLog.success.is_(False))
            .order_by(AIMetricsLog.timestamp.desc(), AIMetricsLog.id.desc()).limit(10)
        )
        return {
            "period_hours": hours,
            "total_calls": total,
            "success_rate": totals["successCalls"] / total if total else 0.0,
            "total_tokens": totals["totalTokens"],
            "avg_duration_ms": totals["avgDurationMs"],
            "cache_hit_rate": totals["cacheHits"] / total if total else 0.0,
            "by_method": by_method,
            "errors": [
                {"timestamp": timestamp.isoformat(), "method": method, "error": error}
                for timestamp, method, error in reversed(errors.all())
            ],
        }

    async def get_summary(self) -> Dict[str, Any]:
        """读取历史调用总计及首末调用时间，空库返回零值与空时间。"""
        result = await self._session.execute(
            select(
                func.count(AIMetricsLog.id),
                func.coalesce(func.sum(AIMetricsLog.tokensUsed), 0),
                func.min(AIMetricsLog.timestamp),
                func.max(AIMetricsLog.timestamp),
            )
        )
        calls, tokens, first_call, last_call = result.one()
        return {
            "total_calls_all_time": calls,
            "total_tokens_all_time": tokens,
            "first_call": first_call.isoformat() if first_call else None,
            "last_call": last_call.isoformat() if last_call else None,
        }


    async def get_by_id(self, log_id: int) -> Optional[AIMetricsLog]:
        """根据 ID 获取日志"""
        return await self._session.get(AIMetricsLog, log_id)

    async def get_all(self, **filters) -> List[AIMetricsLog]:
        """获取所有日志"""
        stmt = select(AIMetricsLog).order_by(AIMetricsLog.timestamp.desc())
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def get_recent(self, hours: int = 24) -> List[AIMetricsLog]:
        """获取最近的日志"""
        cutoff = datetime.now() - timedelta(hours=hours)
        stmt = (
            select(AIMetricsLog)
            .where(AIMetricsLog.timestamp >= cutoff)
            .order_by(AIMetricsLog.timestamp.desc())
        )
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def create(
        self,
        timestamp: datetime,
        method: str,
        success: bool,
        duration_ms: int,
        tokens_used: int,
        model: str,
        error: str = None,
        cache_hit: bool = False
    ) -> AIMetricsLog:
        """创建AI调用日志"""
        log = AIMetricsLog(
            timestamp=timestamp,
            method=method,
            success=success,
            durationMs=duration_ms,
            tokensUsed=tokens_used,
            model=model,
            error=error,
            cacheHit=cache_hit
        )
        self._session.add(log)
        await self._session.flush()
        return log

    async def update(self, log_id: int, **kwargs) -> Optional[AIMetricsLog]:
        """更新日志"""
        log = await self.get_by_id(log_id)
        if not log:
            return None

        for key, value in kwargs.items():
            if hasattr(log, key):
                setattr(log, key, value)

        await self._session.flush()
        return log

    async def delete(self, log_id: int) -> bool:
        """删除日志"""
        log = await self.get_by_id(log_id)
        if not log:
            return False

        await self._session.delete(log)
        await self._session.flush()
        return True

    async def delete_old_logs(self, before_date: datetime) -> int:
        """删除指定日期之前的日志"""
        stmt = select(AIMetricsLog).where(AIMetricsLog.timestamp < before_date)
        result = await self._session.execute(stmt)
        logs = result.scalars().all()

        count = 0
        for log in logs:
            await self._session.delete(log)
            count += 1

        await self._session.flush()
        return count
