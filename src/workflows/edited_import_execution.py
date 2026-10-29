"""编辑导入执行流程：协调准备、下载与业务结果。"""
from typing import Any, Callable

from src.utils.diagnostics.task_exceptions import TaskFailed, TaskSuccess
from src.utils.parsing.filename_parser import format_episode_ranges
from src.workflows.edited_import import prepare_edited_import
from src.workflows.episode_import import import_episodes_iteratively


async def execute_edited_import(
    request_data: Any, progress_callback: Callable, config_service: Any,
    manager: Any, rate_limiter: Any, title_recognition_manager: Any,
) -> str:
    """执行编辑导入并返回完成文案；流控由任务入口转换为暂停。"""
    if not request_data.episodes:
        raise TaskSuccess("没有提供任何分集，任务结束。")
    scraper = manager.get_scraper(request_data.provider)
    episodes, anime_id, source_id, first_comments = await prepare_edited_import(
        request_data, scraper, rate_limiter,
        title_recognition_manager, progress_callback,
    )
    # 前端已应用偏移，不再次向逐集编排传入识别词管理器。
    total, successful, _, failed_count, failed_details = await import_episodes_iteratively(
        scraper=scraper, rate_limiter=rate_limiter,
        progress_callback=progress_callback, episodes=episodes,
        anime_id=anime_id, source_id=source_id,
        first_episode_comments=first_comments, config_service=config_service,
    )
    details = "\n".join(
        f"第{index}集: {error}" for index, error in sorted(failed_details.items())
    )
    if total == 0:
        message = "编辑导入完成，但未找到任何新弹幕。"
        if details:
            message += "\n失败详情:\n" + details
        raise TaskFailed(message)
    episode_range = format_episode_ranges(successful, separator=", ")
    message = f"编辑导入完成，导入集: < {episode_range} >，共获取 {total} 条弹幕。"
    if failed_count > 0:
        message += f"\n失败 {failed_count} 集:\n" + details
    return message
