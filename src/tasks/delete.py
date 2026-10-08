"""删除任务入口；事务与提交后清理由删除编排统一负责。"""
import asyncio
import logging
from typing import Callable, List

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.exc import OperationalError

from src.utils.diagnostics.task_exceptions import TaskSuccess
from src.workflows.danmaku_delete import delete_episode, delete_library_entry

logger = logging.getLogger(__name__)

# 保留任务工厂的 session 形参契约；文件删除仅由编排层提交后检查引用并执行。


async def delete_anime_task(animeId: int, session: AsyncSession, progress_callback: Callable, delete_files: bool = True) -> None:
    """委托删除编排，保留作品锁超时重试，不再提前删除物理文件。"""
    for attempt in range(3):
        await progress_callback(0, f"开始删除 (尝试 {attempt + 1}/3)...")
        try:
            deleted, _ = await delete_library_entry(animeId, is_source=False, delete_files=delete_files)
        except OperationalError as exc:
            # 数据库服务已结束本次事务，重试不能复用失败会话。
            if "Lock wait timeout exceeded" not in str(exc) or attempt == 2:
                raise
            await asyncio.sleep(2 ** (attempt + 1))
            continue
        if not deleted:
            raise TaskSuccess("作品未找到，无需删除。")
        raise TaskSuccess("删除成功。" if delete_files else "删除成功（保留了弹幕文件）。")


async def delete_source_task(sourceId: int, session: AsyncSession, progress_callback: Callable, delete_files: bool = True) -> None:
    """委托编排层同事务删除源与空作品，提交后才清理零引用文件。"""
    await progress_callback(0, "开始删除...")
    deleted, orphan_deleted = await delete_library_entry(
        sourceId, is_source=True, delete_files=delete_files,
    )
    if not deleted:
        raise TaskSuccess("数据源未找到，无需删除。")
    orphan_msg = "，并清理了空的作品条目" if orphan_deleted else ""
    suffix = "" if delete_files else "（保留了弹幕文件）"
    raise TaskSuccess(f"删除成功{suffix}{orphan_msg}。")


async def delete_episode_task(episodeId: int, session: AsyncSession, progress_callback: Callable, delete_files: bool = True) -> None:
    """单集删除委托编排层；保留任务注入签名，不借用外部会话。"""
    await progress_callback(0, "开始删除...")
    deleted = await delete_episode(episodeId, delete_files)
    if not deleted:
        raise TaskSuccess("分集未找到，无需删除。")
    raise TaskSuccess("删除成功。" if delete_files else "删除成功（保留了弹幕文件）。")


async def delete_bulk_episodes_task(episodeIds: List[int], session: AsyncSession, progress_callback: Callable, delete_files: bool = True) -> None:
    """逐集独立提交，已提交项目立即完成零引用清理，不受后续失败影响。"""
    total = len(episodeIds)
    await progress_callback(5, f"准备删除 {total} 个分集...")
    deleted_count = 0
    for i, episode_id in enumerate(episodeIds):
        progress = 5 + int(((i + 1) / total) * 90)
        await progress_callback(progress, f"正在删除分集 {i + 1}/{total} (ID: {episode_id}) 的数据...")
        if await delete_episode(episode_id, delete_files):
            deleted_count += 1
            # 沿用逐项释放数据库资源的节奏，睡眠不占用文件变更锁。
            await asyncio.sleep(0.1)
    suffix = "" if delete_files else "（保留了弹幕文件）"
    raise TaskSuccess(f"批量删除完成，共处理 {total} 个，成功删除 {deleted_count} 个。{suffix}")


async def delete_bulk_sources_task(sourceIds: List[int], session: AsyncSession, progress_callback: Callable, delete_files: bool = True) -> None:
    """逐源独立提交并收尾，保留单项失败后继续处理后续源的行为。"""
    total = len(sourceIds)
    deleted_count = 0
    orphan_anime_count = 0
    failed_count = 0
    for i, source_id in enumerate(sourceIds):
        await progress_callback(int(i / total * 90), f"正在删除源 {i + 1}/{total} (ID: {source_id})...")
        try:
            deleted, orphan_deleted = await delete_library_entry(
                source_id, is_source=True, delete_files=delete_files,
            )
            deleted_count += int(deleted)
            orphan_anime_count += int(orphan_deleted)
        except Exception:
            # 编排层已结束该项事务，后续源使用新事务，不复用失败会话。
            failed_count += 1
            logger.exception("批量删除源任务中，删除源 %s 失败", source_id)
    suffix = "" if delete_files else "（保留了弹幕文件）"
    orphan_msg = f"，清理了 {orphan_anime_count} 个空作品条目" if orphan_anime_count else ""
    failure_msg = f"，失败 {failed_count} 个" if failed_count else ""
    raise TaskSuccess(f"批量删除完成，共处理 {total} 个，成功删除 {deleted_count} 个{orphan_msg}{failure_msg}。{suffix}")

