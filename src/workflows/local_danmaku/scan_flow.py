"""
本地弹幕 - 扫描 Workflow
处理文件解析与本地弹幕记录的事务编排
"""
import logging
from typing import Any, Dict

from src.services.file_storage_service import get_file_storage_service
from src.services.database_service import DatabaseService
from src.utils.storage.local_danmaku_scanner import LocalDanmakuScanner

logger = logging.getLogger(__name__)


async def scan_local_danmaku_flow(
    scan_path: str,
    db: DatabaseService,
) -> Dict[str, Any]:
    """扫描目录，先解析文件，再在单一事务中替换本地弹幕索引。"""
    # 同一变更锁覆盖解析至引用提交，避免扫描期间文件被合作删除入口移除。
    fs = get_file_storage_service()
    async with fs.danmaku_mutation():
        scanner = LocalDanmakuScanner()
        result = await scanner.scan_directory(scan_path)
        records = result.pop("records")
        async with db.transaction():
            # 扫描只替换索引，不删除用户源文件；提交失败保留原索引。
            await db.local_danmaku.delete_all()
            for record in records:
                await db.local_danmaku.create(**record)

    logger.info("扫描了本地目录: %s，结果: %s", scan_path, result)
    return {
        "message": f"扫描完成: 找到 {result['total']} 个文件, 成功 {result['success']} 个",
        "result": result,
    }
