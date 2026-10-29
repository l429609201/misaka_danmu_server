"""本地弹幕导入任务入口。"""
import logging
from typing import Any, Callable, Dict, List

from sqlalchemy.ext.asyncio import AsyncSession

from src.utils.diagnostics.task_exceptions import TaskFailed, TaskSuccess
from src.workflows.local_danmaku.import_execution import import_local_item

logger = logging.getLogger(__name__)


async def import_local_danmaku_task(
    session: AsyncSession, progress_callback: Callable,
    item_ids: List[int], import_options: Dict[str, Any],
) -> None:
    """管理逐项导入进度与失败统计，业务由独立 Workflow 完成。"""
    succeeded = failed = 0
    for position, item_id in enumerate(item_ids, 1):
        await progress_callback(int(position / len(item_ids) * 90), f"正在导入 {position}/{len(item_ids)}...")
        try:
            await import_local_item(item_id, import_options)
            succeeded += 1
        except Exception as exc:
            failed += 1
            logger.error("导入本地项失败 (ID: %s): %s", item_id, exc, exc_info=True)
    message = f"导入完成: 成功 {succeeded} 个, 失败 {failed} 个"
    if not succeeded and failed:
        raise TaskFailed(message)
    await progress_callback(100, "导入完成")
    raise TaskSuccess(message)
