"""弹幕路径引用编排：调用方必须持有文件变更锁，写入查询复用当前事务。"""

import logging
from pathlib import Path
from typing import Optional

from src.services.file_storage_service import get_file_storage_service
from src.services.service_container import get_database_service

logger = logging.getLogger(__name__)


async def get_referenced_paths(exclude_episode_id: Optional[int] = None) -> list[Path]:
    """在调用方事务内获取引用路径，解析不明时拒绝冒险修改文件。"""
    db = get_database_service()
    fs = get_file_storage_service()
    paths = []
    for episode_id, stored_path in await db.episode.get_danmaku_path_references():
        if episode_id == exclude_episode_id:
            continue
        path = fs.resolve_fs_path(stored_path)
        if path is None:
            raise ValueError(f"分集 {episode_id} 路径无法解析，无法安全判断共享引用")
        paths.append(fs.canonical_path(path))
    for stored_path in await db.local_danmaku.get_file_path_references():
        path = fs.resolve_fs_path(stored_path)
        if path is None:
            raise ValueError("本地弹幕路径无法解析，无法安全判断共享引用")
        paths.append(fs.canonical_path(path))
    return paths


async def select_write_path(path: Path, episode_id: int) -> Path:
    """持锁选择当前分集的写入目标，共享目标写时分离，缺失文件引用同样保护。"""
    fs = get_file_storage_service()
    references = await get_referenced_paths(episode_id)
    path = fs.canonical_path(path)
    if not any(fs.same_file_path(path, other) for other in references):
        return path
    # 既检查物理占用，也检查数据库中的缺失文件引用，避免抢占其他分集路径。
    index = 0
    while True:
        suffix = f"_{index}" if index else ""
        candidate = path.with_name(f"{path.stem}_episode_{episode_id}{suffix}{path.suffix}")
        if not fs.path_occupied(candidate) and not any(
            fs.same_file_path(candidate, other) for other in references
        ):
            logger.info("共享弹幕文件写时分离：%s → %s", path, candidate)
            return candidate
        index += 1


async def cleanup_unreferenced_paths(paths: set[Path]) -> None:
    """持锁在提交后重新查询，只清理零引用文件；无法判断时保留文件。"""
    if not paths:
        return
    db = get_database_service()
    fs = get_file_storage_service()
    try:
        async with db.transaction():
            references = await get_referenced_paths()
    except Exception:
        logger.exception("已提交，但共享引用查询失败，保留全部旧弹幕文件")
        return
    for path in paths:
        try:
            if any(fs.same_file_path(path, other) for other in references):
                logger.info("旧弹幕文件仍有引用，保留：%s", path)
                continue
            if fs.exists(path) and not await fs.delete_file(path):
                logger.warning("已提交，旧弹幕文件清理失败：%s", path)
        except Exception:
            logger.exception("已提交，旧弹幕路径身份判断或清理失败，保留待核查：%s", path)
