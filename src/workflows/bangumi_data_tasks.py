"""Bangumi 离线索引同步、清理任务的统一编排入口。"""

from typing import Any, Callable

from src.services.service_container import get_metadata_service
from src.services.task_manager import TaskFailed, TaskManager, TaskSuccess


async def execute_bangumi_data_sync(session: Any, progress_callback: Callable) -> None:
    """后台任务经 MetadataService 同步索引，失败交由任务系统记录。"""
    await progress_callback(10, "正在从 CDN 拉取 bangumi-data...")
    result = await get_metadata_service().sync_bangumi_data(progress_callback=progress_callback)
    if not result.get("success"):
        raise TaskFailed(f"bangumi-data 同步失败：{result.get('message')}")
    raise TaskSuccess(f"bangumi-data 同步完成，共 {result.get('count')} 条。")


async def execute_bangumi_data_clear(session: Any, progress_callback: Callable) -> None:
    """后台任务经 MetadataService 清理索引。"""
    await progress_callback(10, "正在清除 bangumi-data 离线索引...")
    result = await get_metadata_service().clear_bangumi_data()
    if not result.get("success"):
        raise TaskFailed(f"bangumi-data 清除失败：{result.get('message')}")
    raise TaskSuccess(f"bangumi-data 离线索引已清除，共 {result.get('count')} 条。")


async def submit_bangumi_data_task(task_manager: TaskManager, operation: str) -> str:
    """提交可恢复的手动同步或清理任务，并返回任务 ID。"""
    if operation == "sync":
        task_type = "bangumiDataSync"
        title = "bangumi-data 离线索引同步（手动）"
        unique_key = "bangumi-data-sync-manual"
    elif operation == "clear":
        task_type = "bangumiDataClear"
        title = "bangumi-data 离线索引清除"
        unique_key = "bangumi-data-clear-manual"
    else:
        raise ValueError(f"未知离线数据任务: {operation}")
    task_id, _ = await task_manager.submit_task(
        task_manager.build_task_coro_factory(task_type), title,
        unique_key=unique_key, task_type=task_type, queue_type="management",
        task_parameters={"manual": True},
    )
    return task_id
