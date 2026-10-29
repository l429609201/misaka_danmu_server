"""性能持久化服务：组合纯计时状态并独立提交统计记录。"""

import functools
import logging
import time
from typing import Any, Callable, Optional

from src.services.service_container import get_database_service
from src.utils.diagnostics.task_context import get_task_session_factory
from src.utils.diagnostics.task_exceptions import TaskSuccess
from src.utils.diagnostics.task_profiler import TaskProfiler as TimingProfiler

logger = logging.getLogger(__name__)


class PerformanceService:
    """在独立事务中保存计时结果，不复用业务事务。"""

    def __init__(self, database_service: Any = None) -> None:
        self._database_service = database_service

    async def save_profile(self, profiler: TimingProfiler, session_factory: Any = None) -> None:
        """按显式工厂、任务上下文工厂或服务自持事务保存记录。"""
        if not profiler.steps:
            return
        try:
            db = self._database_service or get_database_service()
            factory = session_factory or get_task_session_factory()
            payload = dict(flow_type=profiler.flow_type, correlation_id=profiler.correlation_id,
                           steps=list(profiler.steps), total_duration_ms=profiler.total_duration_ms)
            if factory is not None:
                async with factory() as independent_session:
                    async with independent_session.begin():
                        async with db.transaction(session=independent_session):
                            await db.performance.save_perf_events(**payload)
            else:
                async with db.transaction():
                    await db.performance.save_perf_events(**payload)
        except Exception as exc:
            # 统计失败不回滚主流程，也不吞掉主流程已经抛出的异常。
            logger.warning("[性能统计] 写入 task_perf_events 失败（不影响主流程）: %s", exc, exc_info=True)


class TaskProfiler(TimingProfiler):
    """带持久化能力的计时服务，保持现有任务 flush 调用契约。"""

    def __init__(self, flow_type: str, correlation_id: Optional[str] = None,
                 performance_service: Optional[PerformanceService] = None) -> None:
        super().__init__(flow_type, correlation_id)
        self._performance_service = performance_service or PerformanceService()

    async def flush(self, session: Any = None, session_factory: Any = None) -> None:
        """保存统计；兼容 session 参数但不复用调用方业务会话。"""
        await self._performance_service.save_profile(self, session_factory)


def profile_flow(flow_type: str) -> Callable:
    """对任务整体计时并保存，正常业务完成异常不标记为失败。"""
    def decorator(func: Callable) -> Callable:
        @functools.wraps(func)
        async def wrapper(*args: Any, **kwargs: Any) -> Any:
            profiler = TaskProfiler(flow_type)
            start = time.perf_counter()
            success = True
            details = None
            try:
                return await func(*args, **kwargs)
            except TaskSuccess:
                raise
            except Exception as exc:
                success = False
                details = str(exc)[:500]
                raise
            finally:
                profiler.record_step("整体执行", (time.perf_counter() - start) * 1000, success, details)
                await profiler.flush()
        return wrapper
    return decorator
