"""
本地弹幕 - 管理 Workflow
处理本地弹幕项的更新、删除等管理操作
"""
import logging
from pathlib import Path
from typing import Any, Dict, List

from src.services.database_service import DatabaseService
from src.services.file_storage_service import get_file_storage_service, wait_for_settlement
from src.workflows.danmaku_paths import get_referenced_paths

logger = logging.getLogger(__name__)


async def _cleanup_local_file(db: DatabaseService, path: Path) -> bool:
    """调用方持锁至收尾结束；仅清理本地项与分集均未引用的文件。"""
    fs = get_file_storage_service()
    try:
        async with db.transaction():
            references = await get_referenced_paths()
        if any(fs.same_file_path(path, other) for other in references):
            logger.info("本地弹幕文件仍有引用，保留：%s", path)
            return False
        # 本地源目录属于用户，不随弹幕项删除清理父目录。
        return await fs.delete_file(path, cleanup_empty_dirs=False)
    except Exception:
        logger.exception("已提交，引用核查或文件清理失败，保留文件：%s", path)
        return False


async def update_local_item_flow(
    db: DatabaseService,
    item_id: int,
    update_data: Dict[str, Any]
) -> Dict[str, Any]:
    """更新本地项，路径引用变化与文件清理使用同一变更锁。"""
    async with get_file_storage_service().danmaku_mutation():
        async with db.transaction():
            item = await db.local_danmaku.get_by_id(item_id)
            if not item:
                raise ValueError(f"本地弹幕项不存在: {item_id}")
            await db.local_danmaku.update(item_id, **update_data)
    logger.info("更新了本地弹幕项 %s: %s", item_id, update_data)
    return {"message": "更新成功", "itemId": item_id}


async def delete_local_item_flow(
    db: DatabaseService,
    item_id: int,
    delete_file: bool = False
) -> Dict[str, Any]:
    """独立提交本地项删除，确认提交后持锁等待零引用文件清理。"""
    fs = get_file_storage_service()
    outcome = db.TransactionOutcome()
    file_path = None
    file_deleted = False
    async with fs.danmaku_mutation():
        try:
            async with db.transaction(outcome=outcome):
                item = await db.local_danmaku.get_by_id(item_id)
                if item is None:
                    raise ValueError(f"本地弹幕项不存在: {item_id}")
                if delete_file and item.filePath:
                    file_path = fs.resolve_fs_path(item.filePath)
                    if file_path is None:
                        logger.warning("本地项 %s 路径无法解析，保留文件", item_id)
                await db.local_danmaku.delete(item_id)
        finally:
            # 提交失败或结果未知都不删文件；取消也必须等待收尾再释放锁。
            if outcome.status == "committed" and file_path is not None:
                file_deleted = await wait_for_settlement(_cleanup_local_file(db, file_path))
    logger.info("删除了本地弹幕项 %s，实际删除文件：%s", item_id, file_deleted)
    return {"message": "删除成功", "itemId": item_id, "fileDeleted": file_deleted}


async def batch_delete_local_items_flow(
    db: DatabaseService,
    item_ids: List[int],
    delete_files: bool = False
) -> Dict[str, Any]:
    """逐项独立提交本地弹幕项，并在每项提交后完成共享引用清理。

    每个单项删除都自持变更锁和事务；批量过程中即使后续项目失败，已提交项目
    仍已完成文件收尾。取消信号不会被异常统计吞掉，当前项目会先等待底层 I/O 收尾。
    """
    success_count = 0
    failed_count = 0
    failed_items: List[Dict[str, Any]] = []

    for item_id in item_ids:
        try:
            await delete_local_item_flow(db, item_id, delete_files)
            success_count += 1
        except Exception as exc:
            failed_count += 1
            failed_items.append({"itemId": item_id, "error": str(exc)})
            logger.exception("批量删除本地弹幕项 %s 失败", item_id)

    logger.info("批量删除本地弹幕项：成功 %s，失败 %s", success_count, failed_count)
    return {
        "message": f"批量删除完成: 成功 {success_count}, 失败 {failed_count}",
        "success": success_count,
        "failed": failed_count,
        "failedItems": failed_items,
    }
