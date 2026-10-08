"""异步操作的取消收尾工具，不依赖业务层或数据库。"""

import asyncio
from typing import Awaitable, TypeVar

import anyio


T = TypeVar("T")


async def finish_before_cancel(operation: Awaitable[T]) -> T:
    """等待已开始操作完成后再传播取消，兼容 ASGI 与直接任务取消。"""
    task = asyncio.ensure_future(operation)
    cancellation = None
    # ASGI 的取消是 level cancellation，单用 asyncio.shield 仍会重复取消等待者。
    with anyio.CancelScope(shield=True):
        while not task.done():
            try:
                await asyncio.shield(task)
            except asyncio.CancelledError as exc:
                cancellation = exc
            except BaseException:
                break
        try:
            result = task.result()
        except BaseException as exc:
            if cancellation is not None:
                raise cancellation from exc
            raise
        if cancellation is not None:
            raise cancellation
        return result
