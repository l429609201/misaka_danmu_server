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

Session 隔离策略
----------------
任务型流程（task_manager 调度）：session 生命周期由
``async with session_factory() as session:`` 管理。SQLAlchemy 的
AsyncSession.__aexit__ 遇到任何异常（包括 TaskSuccess）时会先
rollback 再 close，即使 flush 已经 commit，context var 方式能
绕开这个问题——flush 用独立 session 写入并立即 commit，与外层
task session 的生命周期完全解耦。

task_manager 在启动每个任务前调用
``set_task_session_factory(factory)``，flush 自动读取并开独立
session，任务函数签名无需任何改动。

请求型流程（FastAPI Depends）：session 只 close 不 rollback，
flush(session, session_factory=...) 显式传入 factory 的做法
（comments.py 后备路径）同样正确，两者并存。
"""

import time
import logging
import functools
from contextlib import asynccontextmanager
from contextvars import ContextVar
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

# ContextVar：task_manager 在启动每个任务前注入 session_factory。
# why：用 ContextVar 而非全局变量，保证多任务并发时各自使用各自的 factory，互不干扰。
# flush 优先读取此变量开独立 session，与外层 task session 生命周期完全解耦，
# 避免 SQLAlchemy AsyncSession.__aexit__ 遇异常(TaskSuccess等)自动 rollback 把
# 已 commit 的 perf 数据也一起回滚。
_task_session_factory_var: ContextVar = ContextVar("_task_session_factory", default=None)


def set_task_session_factory(factory) -> None:
    """在启动任务前由 task_manager 调用，将 session_factory 注入当前 asyncio Task 上下文。"""
    _task_session_factory_var.set(factory)


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

    async def flush(self, session, session_factory=None) -> None:
        """批量写入 task_perf_events 表。

        - 若无步骤记录则跳过
        - 写入失败只打 warning，不抛异常

        独立 session 优先级（高→低）：
          1. 参数 session_factory（显式传入，用于请求型路由后备路径）
          2. ContextVar _task_session_factory_var（task_manager 启动任务前注入，
             覆盖所有任务型流程，无需修改任务函数签名）
          3. 直接复用传入的 session（兜底，用于无上述注入的极少数场景）

        why：任务型流程的 session 生命周期由
        ``async with session_factory() as session:`` 管理，SQLAlchemy
        AsyncSession.__aexit__ 遇到任何异常（包括 TaskSuccess）时会先
        rollback 再 close——即便 flush 内已 commit，rollback 仍可能
        把同一连接上后续操作的 pending 状态清空（驱动层行为因数据库而异）。
        用独立 session 写入并立即 commit，彻底与外层 task session 解耦。
        """
        if not self._steps:
            return

        # 确定实际使用的 factory：显式参数 > ContextVar > 无
        effective_factory = session_factory or _task_session_factory_var.get()

        if effective_factory is not None:
            # 独立 session：与外层 task/request session 生命周期完全隔离
            try:
                async with effective_factory() as independent_session:
                    await save_perf_events(
                        session=independent_session,
                        flow_type=self.flow_type,
                        correlation_id=self.correlation_id,
                        steps=self._steps,
                        total_duration_ms=self.total_duration_ms,
                    )
                    await independent_session.commit()
            except Exception as exc:
                logger.warning(f"[性能统计] 写入 task_perf_events 失败（不影响主流程）: {exc}", exc_info=True)
            return

        # 兜底：直接复用传入 session（适用于无 factory 注入的场景）
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
