"""通用导入收尾：追更状态、媒体关联与结果文案。"""
import logging
from typing import Dict, List, Optional, Tuple

from src.services.service_container import get_database_service
from src.utils.diagnostics.task_exceptions import TaskFailed
from src.utils.parsing.filename_parser import format_episode_ranges

logger = logging.getLogger(__name__)


async def finish_generic_import(
    result: Tuple[int, List[int], List[int], int, Dict[int, str]],
    is_incremental_refresh: bool, incremental_refresh_source_id: Optional[int],
    media_server_episode_id: Optional[str], episode_index: Optional[int],
    source_id: int, image_download_failed: bool,
) -> str:
    """提交收尾状态，全部失败时抛出业务失败，否则返回完成文案。"""
    total, successful, skipped, failed_count, failed_details = result
    refresh_failed = not successful and not skipped and failed_count > 0
    db = get_database_service()
    if is_incremental_refresh and incremental_refresh_source_id:
        disabled = False
        async with db.transaction():
            if refresh_failed:
                max_failures = int(await db.config.get_value("incrementalRefreshMaxFailures", "10"))
                failures = await db.source.increment_refresh_failures(incremental_refresh_source_id)
                if failures is not None and failures >= max_failures:
                    await db.source.update(incremental_refresh_source_id, incrementalRefreshEnabled=False)
                    disabled = True
            else:
                await db.source.update(incremental_refresh_source_id, incrementalRefreshFailures=0)
        if disabled:
            logger.warning("源 ID %s 追更失败次数达到%s次，已禁用追更", incremental_refresh_source_id, max_failures)
        elif not refresh_failed:
            logger.info("源 ID %s 追更成功，已重置失败计数", incremental_refresh_source_id)
    if refresh_failed:
        details = "\n".join(f"第{index}集: {error}" for index, error in sorted(failed_details.items()))
        raise TaskFailed("导入完成，但所有分集弹幕获取失败。\n失败详情:\n" + details)

    if media_server_episode_id and episode_index is not None and source_id:
        try:
            async with db.transaction():
                episode = await db.episode.get_by_source_and_index(source_id, episode_index)
                if episode:
                    await db.episode.update_media_server_id(episode.id, media_server_episode_id)
        except Exception as exc:
            logger.warning("写入媒体服务 Episode ID 失败: %s", exc)
    parts = []
    if successful:
        ranges = format_episode_ranges(successful, separator=", ")
        suffix = f"共获取 {total} 条弹幕" if total > 0 else "暂无弹幕数据"
        parts.append(f"导入集: < {ranges} >，{suffix}")
    if skipped:
        ranges = format_episode_ranges(skipped, separator=", ")
        parts.append(f"跳过集: < {ranges} > (已有弹幕)")
    message = "导入完成，" + "；".join(parts) + "。"
    if failed_count > 0:
        message += f" {failed_count} 个分集因网络或解析错误获取失败。"
    if image_download_failed:
        message += " (警告：海报图片下载失败)"
    return message
