"""分集编号任务入口，仅适配任务进度与完成信号。"""
from typing import Callable, List

from sqlalchemy.ext.asyncio import AsyncSession

from src.utils.diagnostics.task_exceptions import TaskSuccess
from src.workflows.episode_reorder import renumber_episodes


async def reorder_episodes_task(
    sourceId: int, session: AsyncSession, progress_callback: Callable,
) -> None:
    """重整指定源的分集编号，保留任务工厂注入参数。"""
    # 事务及文件补偿由 Workflow 自持，不操作任务工厂注入的会话。
    message = await renumber_episodes(progress_callback, source_id=sourceId)
    raise TaskSuccess(message)


async def offset_episodes_task(
    episode_ids: List[int], offset: int, session: AsyncSession,
    progress_callback: Callable,
) -> None:
    """偏移选中分集的编号，将编排结果转换为任务完成信号。"""
    message = await renumber_episodes(
        progress_callback, episode_ids=episode_ids, offset=offset,
    )
    raise TaskSuccess(message)
