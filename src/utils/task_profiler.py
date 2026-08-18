"""任务性能计时器模块

为各类任务流程提供统一的步骤级计时能力。

两种使用方式：
1. 修饰器（适合 Job.run() 整函数计时）：
       @profile_flow(FLOW_BANGUMI_DATA_SYNC)
       async def run(self, session, progress_callback): ...

2. 上下文管理器（适合函数内部步骤级计时）：
       profiler = TaskProfiler("弹幕通用导入", task_id)
       async with profiler.step("获取分集列表"): ...
       async with profiler.step("下载弹幕"): ...
       await profiler.flush(session)

设计原则：flush 失败只打 warning，绝不影响主流程。
"""

import time
import logging
import functools
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import List, Optional
from uuid import uuid4

# why: crud.task 顶部只依赖 orm_models/core/sqlalchemy，不引用 utils，无循环导入风险。
# 延迟导入（仅 flush 时用）仍会改为顶级导入放此处，但 crud.task 本身要 import task_profiler 吗？
# 答：不会，save_perf_events 不依赖 task_profiler，只有 task_profiler.flush 单向调用 crud.task，
# 因此顶部直接导入 save_perf_events 是安全的。
from src.db.crud.task import save_perf_events  # noqa: E402 — 模块初始化时 crud 包已就绪
# why: 从零依赖的 task_exceptions 导入，避免 task_manager → src.db → utils → task_manager 循环
from src.utils.task_exceptions import TaskSuccess, TaskFailed

logger = logging.getLogger(__name__)


@dataclass
class _PerfStep:
    """单个步骤的性能记录（内部使用）"""
    step_name: str
    duration_ms: float
    success: bool
    details: Optional[str] = None


class TaskProfiler:
    """任务性能计时器

    用法::
        profiler = TaskProfiler("弹幕通用导入", task_id)
        async with profiler.step("存在性检查"):
            ...  # 原有代码，异常自动标记 success=False
        async with profiler.step("下载弹幕"):
            ...
        await profiler.flush(session)  # 任务末尾（建议在 finally 块中调用）
    """

    def __init__(self, flow_type: str, correlation_id: Optional[str] = None):
        """
        Args:
            flow_type: 流程类型，如「弹幕通用导入」「全量刷新」，用于前端分组聚合
            correlation_id: 关联 ID，通常为 task_id。不传则自动生成 UUID。
        """
        self.flow_type = flow_type
        self.correlation_id = correlation_id or str(uuid4())
        self._steps: List[_PerfStep] = []
        self._total_start: float = time.perf_counter()

    @asynccontextmanager
    async def step(self, step_name: str):
        """异步上下文管理器：计时单个步骤。

        - 步骤正常结束：success=True
        - 步骤内抛出异常：success=False，details=异常信息，异常继续往上抛
        """
        start = time.perf_counter()
        try:
            yield
            duration = (time.perf_counter() - start) * 1000
            self._steps.append(_PerfStep(step_name, duration, True))
        except Exception as exc:
            duration = (time.perf_counter() - start) * 1000
            # 截断异常信息避免 DB 字段超长
            details = str(exc)[:500] if exc else None
            self._steps.append(_PerfStep(step_name, duration, False, details))
            raise  # 异常继续往上抛，不干扰原有流程

    def record_step(self, step_name: str, duration_ms: float, success: bool = True, details: Optional[str] = None):
        """同步版本：手动记录一个已完成步骤（用于已有计时逻辑的迁移）"""
        self._steps.append(_PerfStep(step_name, duration_ms, success, details))

    @property
    def total_duration_ms(self) -> float:
        """从 profiler 创建到现在的总耗时（毫秒）"""
        return (time.perf_counter() - self._total_start) * 1000

    async def flush(self, session) -> None:
        """批量写入 task_perf_events 表。

        - 若无步骤记录则跳过
        - 写入失败只打 warning，不抛异常
        - why：使用独立 commit，避免调用方 session 未 commit 时数据随 close 回滚
          （搜索/请求型路由的 FastAPI session 依赖项只 close 不 commit）
        """
        if not self._steps:
            return
        try:
            await save_perf_events(
                session=session,
                flow_type=self.flow_type,
                correlation_id=self.correlation_id,
                steps=self._steps,
                total_duration_ms=self.total_duration_ms,
            )
            await session.commit()
        except Exception as exc:
            logger.warning(f"[性能统计] 写入 task_perf_events 失败（不影响主流程）: {exc}", exc_info=True)
            try:
                await session.rollback()
            except Exception:
                pass


def profile_flow(flow_type: str):
    """修饰器：对整个 Job.run() 方法计时，记录为单步骤流程。

    适用于 BaseJob 子类的 run() 方法，自动从参数中提取 session（第2个位置参数）。

    用法::
        class BangumiDataSyncJob(BaseJob):
            @profile_flow(FLOW_BANGUMI_DATA_SYNC)
            async def run(self, session, progress_callback):
                ...
    """
    def decorator(func):
        @functools.wraps(func)
        async def wrapper(*args, **kwargs):
            # args[0]=self, args[1]=session（BaseJob.run 约定位置）
            session = args[1] if len(args) > 1 else kwargs.get("session")
            profiler = TaskProfiler(flow_type)
            step_name = "整体执行"
            start = time.perf_counter()
            success = True
            details = None
            try:
                result = await func(*args, **kwargs)
                return result
            except TaskSuccess:
                # why：TaskSuccess 是业务正常完成，不应标记 success=False
                raise
            except TaskFailed as exc:
                # why：TaskFailed 是可预期业务失败，success=False 但不算崩溃
                success = False
                details = str(exc)[:500]
                raise
            except Exception as exc:
                # 未预期异常
                success = False
                details = str(exc)[:500]
                raise
            finally:
                duration = (time.perf_counter() - start) * 1000
                profiler.record_step(step_name, duration, success, details)
                if session is not None:
                    await profiler.flush(session)
        return wrapper
    return decorator


# ───────────────────────── 流程类型常量 ─────────────────────────

# 任务型
FLOW_GENERIC_IMPORT = "弹幕通用导入"
FLOW_FULL_REFRESH = "全量刷新"
FLOW_SINGLE_REFRESH = "单集刷新"
FLOW_BULK_REFRESH = "批量刷新"
FLOW_AUTO_IMPORT = "全自动导入"
FLOW_WEBHOOK_IMPORT = "Webhook导入"
FLOW_INCREMENTAL_REFRESH = "定时追更"
FLOW_BANGUMI_DATA_SYNC = "BGM数据同步"
FLOW_SUBSCRIPTION_SCAN = "订阅扫描"
FLOW_WATCHLIST_SYNC = "收藏列表同步"
FLOW_FILL_MISSING_EPISODES = "分集补全"
FLOW_TMDB_AUTO_SCRAPE = "TMDB自动刮削"
FLOW_DANMAKU_CLEANUP = "弹幕定时清理"
FLOW_DATABASE_BACKUP = "数据库备份"
FLOW_REFRESH_LATEST_EPISODE = "刷新最新集"
FLOW_DATABASE_MAINTENANCE = "数据库维护"
FLOW_WEBHOOK_PROCESSOR = "Webhook处理器"

# 请求型（dandan API）
FLOW_FALLBACK_MATCH = "后备匹配下载"
FLOW_FALLBACK_SEARCH = "后备搜索下载"
FLOW_HOME_SEARCH = "主页检索"
