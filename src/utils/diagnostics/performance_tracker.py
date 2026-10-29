"""
异步方法耗时跟踪装饰器

用于搜索源（BaseScraper 子类）的方法耗时统计。
耗时结果按 asyncio 任务 ID 存入实例的 _task_timings 字典，
供 scraper_manager 在并发搜索结束后读取各源的实际耗时。
"""

import asyncio
import logging
import time
from functools import wraps
from typing import Any, Callable

logger = logging.getLogger(__name__)


def track_performance(func: Callable) -> Callable:
    """
    装饰器：跟踪异步方法的执行时间，不影响并发性能。

    以当前 asyncio 任务 ID 作为键存储耗时，因此并发调用同一方法时
    各自的耗时互不覆盖，可被 scraper_manager 按任务准确读取。

    被装饰方法所属实例需具备 logger 与 provider_name 属性
    （BaseScraper 子类均满足）。

    Args:
        func: 待装饰的异步方法

    Returns:
        包装后的异步方法；无论成功或抛错都会记录耗时
    """

    @wraps(func)
    async def wrapper(self, *args, **kwargs) -> Any:
        start_time = time.perf_counter()
        # 以任务 ID 为键，确保并发安全
        task_id = id(asyncio.current_task())

        def _record(elapsed_ms: float) -> None:
            """将耗时写入实例的任务计时表"""
            if not hasattr(self, '_task_timings'):
                self._task_timings = {}
            self._task_timings[task_id] = elapsed_ms

        try:
            result = await func(self, *args, **kwargs)
            elapsed = time.perf_counter() - start_time
            _record(elapsed * 1000)
            self.logger.info(
                f"[{self.provider_name}] {func.__name__} 耗时: {elapsed:.3f}s"
            )
            return result
        except Exception:
            elapsed = time.perf_counter() - start_time
            # 失败同样记录耗时，便于定位超时来源
            _record(elapsed * 1000)
            self.logger.warning(
                f"[{self.provider_name}] {func.__name__} 失败耗时: {elapsed:.3f}s"
            )
            raise

    return wrapper
