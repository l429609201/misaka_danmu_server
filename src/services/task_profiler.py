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
任务统计使用独立会话和事务，与外层业务事务解耦。
仅退出 ``async with session_factory() as session:`` 不会自动提交，
需要显式事务上下文；外层 rollback 不会撤销独立事务已提交的数据。

task_manager 在启动任务前调用 ``set_task_session_factory(factory)``，
flush 优先使用显式工厂或上下文工厂，并通过 session.begin() 提交。
若没有工厂，使用 DatabaseService.transaction() 创建并提交独立事务。

flush(session, session_factory=...) 的签名保留兼容，但不复用传入的
业务 session，也不因统计写入失败而回滚该业务 session。
"""

import time
import logging
import functools
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any, List, Optional
from uuid import uuid4

from src.services.service_container import get_database_service
from src.utils.diagnostics.task_context import get_task_session_factory
from src.utils.diagnostics.task_exceptions import TaskSuccess, TaskFailed

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

    async def flush(self, session: Optional[Any] = None, session_factory=None) -> None:
        """批量写入 task_perf_events 表。

        - 若无步骤记录则跳过
        - 写入失败只打 warning，不抛异常

        独立 session 优先级（高→低）：
          1. 参数 session_factory（显式传入，用于请求型路由后备路径）
          2. ContextVar _task_session_factory_var（task_manager 启动任务前注入，
             覆盖所有任务型流程，无需修改任务函数签名）
          3. DatabaseService 自持事务（自动创建独立会话并提交）

        session 参数仅保留调用兼容，不复用业务会话，避免统计记录随
        业务失败回滚，或统计失败时回滚调用方的业务数据。
        """
        if not self._steps:
            return

        # 独立工厂缺失时由服务新建事务，不复用或回滚调用方的业务会话。
        try:
            effective_factory = session_factory or get_task_session_factory()
            db = get_database_service()
            if effective_factory is not None:
                async with effective_factory() as independent_session:
                    # 借用模式不负责提交，独立会话的事务由 begin 管理。
                    async with independent_session.begin():
                        async with db.transaction(session=independent_session):
                            await db.performance.save_perf_events(
                                flow_type=self.flow_type,
                                correlation_id=self.correlation_id,
                                steps=self._steps,
                                total_duration_ms=self.total_duration_ms,
                            )
            else:
                # 自持事务始终使用新会话，并负责提交、回滚和关闭。
                async with db.transaction():
                    await db.performance.save_perf_events(
                        flow_type=self.flow_type,
                        correlation_id=self.correlation_id,
                        steps=self._steps,
                        total_duration_ms=self.total_duration_ms,
                    )
        except Exception as exc:
            logger.warning(f"[性能统计] 写入 task_perf_events 失败（不影响主流程）: {exc}", exc_info=True)


def profile_flow(flow_type: str):
    """修饰器：对整个 Job.run() 方法计时，记录为单步骤流程。"""
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
                # TaskSuccess 是业务正常完成，不应标记 success=False
                raise
            except TaskFailed as exc:
                # TaskFailed 是可预期业务失败，success=False 但不算崩溃
                success = False
                details = str(exc)[:500]
                raise
            except Exception as exc:
                success = False
                details = str(exc)[:500]
                raise
            finally:
                duration = (time.perf_counter() - start) * 1000
                profiler.record_step(step_name, duration, success, details)
                # 即使函数没有 session 参数，也由 flush 自行创建独立事务。
                await profiler.flush(session)
        return wrapper
    return decorator


# 任务型流程常量
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

# 请求型流程常量
FLOW_FALLBACK_MATCH = "后备匹配下载"
FLOW_FALLBACK_SEARCH = "后备搜索下载"
FLOW_HOME_SEARCH = "主页检索"
