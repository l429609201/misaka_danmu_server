"""任务上下文管理

为任务系统提供上下文变量，用于在任务执行期间传递 session_factory。
这个模块零依赖，避免循环导入。
"""
from contextvars import ContextVar
from typing import Optional, Callable, Any

# ContextVar：task_manager 在启动每个任务前注入 session_factory。
# why：用 ContextVar 而非全局变量，保证多任务并发时各自使用各自的 factory，互不干扰。
# flush 优先读取此变量开独立 session，与外层 task session 生命周期完全解耦，
# 避免 SQLAlchemy AsyncSession.__aexit__ 遇异常(TaskSuccess等)自动 rollback 把
# 已 commit 的 perf 数据也一起回滚。
_task_session_factory_var: ContextVar = ContextVar("_task_session_factory", default=None)


def set_task_session_factory(factory: Optional[Callable[[], Any]]) -> None:
    """在启动任务前由 task_manager 调用，将 session_factory 注入当前 asyncio Task 上下文。"""
    _task_session_factory_var.set(factory)


def get_task_session_factory() -> Optional[Callable[[], Any]]:
    """获取当前任务的 session_factory"""
    return _task_session_factory_var.get()
