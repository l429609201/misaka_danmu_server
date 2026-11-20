"""弹幕 XML 文件修复任务；通过 TaskManager.submit_task 提交后台执行。"""

import logging
from typing import Callable, List

from sqlalchemy.ext.asyncio import AsyncSession
from xml.etree import ElementTree

from src.services.service_container import get_database_service, get_file_storage_service
from src.utils.diagnostics.task_exceptions import TaskSuccess
from src.utils.parsing.xml_repair import repair_danmaku_xml

logger = logging.getLogger(__name__)


async def repair_xml_files_task(
    episode_ids: List[int],
    session: AsyncSession,
    progress_callback: Callable,
) -> None:
    """批量修复分集弹幕 XML 文件中的格式问题。

    仅对可自动修复的文件（非法控制字符、未转义 &）执行写回。
    结构损坏的文件跳过并记录日志，不抛出异常。
    """
    db = get_database_service()
    fs = get_file_storage_service()
    total = len(episode_ids)
    repaired_count = 0
    skipped_count = 0
    failed_count = 0
    missing_count = 0

    await progress_callback(0, f"开始检查 {total} 个分集...")

    for idx, episode_id in enumerate(episode_ids):
        # 获取分集文件路径
        async with db.transaction():
            episode = await db.episode.get_by_id(episode_id)

        if episode is None:
            logger.warning("XML修复：分集 %d 不存在，跳过", episode_id)
            missing_count += 1
            await progress_callback(
                int((idx + 1) / total * 100),
                f"[{idx + 1}/{total}] 分集 {episode_id} 不存在，跳过",
            )
            continue

        path = fs.resolve_fs_path(episode.danmakuFilePath) if episode.danmakuFilePath else None
        if not path or not fs.exists(path):
            logger.warning("XML修复：分集 %d 文件不存在（%s），跳过", episode_id, episode.danmakuFilePath)
            missing_count += 1
            await progress_callback(
                int((idx + 1) / total * 100),
                f"[{idx + 1}/{total}] {episode.episodeTitle} 文件不存在，跳过",
            )
            continue

        # 读取文件内容
        content = await fs.read_text(path)
        if not content:
            missing_count += 1
            await progress_callback(
                int((idx + 1) / total * 100),
                f"[{idx + 1}/{total}] {episode.episodeTitle} 文件为空，跳过",
            )
            continue

        # 先尝试正常解析
        try:
            ElementTree.fromstring(content)
            # XML 完好，无需修复
            skipped_count += 1
            await progress_callback(
                int((idx + 1) / total * 100),
                f"[{idx + 1}/{total}] {episode.episodeTitle} XML 正常，无需修复",
            )
            continue
        except ElementTree.ParseError:
            pass

        # 尝试修复
        repaired = repair_danmaku_xml(content, episode_id, file_path=str(path))
        if repaired is None:
            failed_count += 1
            await progress_callback(
                int((idx + 1) / total * 100),
                f"[{idx + 1}/{total}] {episode.episodeTitle} 结构性损坏，无法修复",
            )
            continue

        # 写回修复后的内容
        try:
            await fs.write_text(path, repaired)
            repaired_count += 1
            await progress_callback(
                int((idx + 1) / total * 100),
                f"[{idx + 1}/{total}] {episode.episodeTitle} 修复成功",
            )
        except Exception:
            logger.exception("XML修复：分集 %d 写回文件失败", episode_id)
            failed_count += 1
            await progress_callback(
                int((idx + 1) / total * 100),
                f"[{idx + 1}/{total}] {episode.episodeTitle} 修复后写入失败",
            )

    summary_parts = []
    if repaired_count:
        summary_parts.append(f"已修复 {repaired_count} 个")
    if skipped_count:
        summary_parts.append(f"已正常 {skipped_count} 个")
    if failed_count:
        summary_parts.append(f"无法修复 {failed_count} 个")
    if missing_count:
        summary_parts.append(f"文件缺失 {missing_count} 个")
    summary = "，".join(summary_parts) or "无分集处理"
    raise TaskSuccess(f"XML修复完成：{summary}")
