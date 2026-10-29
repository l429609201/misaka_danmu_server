"""删除编排：先提交记录删除，再持锁清理零引用文件。"""

import logging
from pathlib import Path

from src.services.cache_service import get_cache_service
from src.services.file_storage_service import get_file_storage_service, wait_for_settlement
from src.services.service_container import get_database_service
from src.workflows.danmaku_paths import cleanup_unreferenced_paths

logger = logging.getLogger(__name__)


async def delete_episode(episode_id: int, delete_files: bool = True) -> bool:
    """独立删除单集，提交失败或结果未知时不删除物理文件。"""
    db = get_database_service()
    fs = get_file_storage_service()
    paths: set[Path] = set()
    outcome = db.TransactionOutcome()
    async with fs.danmaku_mutation():
        try:
            async with db.transaction(outcome=outcome):
                episode = await db.episode.get_by_id(episode_id)
                if episode is None:
                    return False
                if delete_files and episode.danmakuFilePath:
                    path = fs.resolve_fs_path(episode.danmakuFilePath)
                    if path is None:
                        # 数据库删除可以继续，但无法识别的文件必须保留待核查。
                        logger.warning("分集 %s 路径无法解析，保留文件", episode_id)
                    else:
                        paths.add(path)
                if not await db.episode.delete(episode_id):
                    raise ValueError("删除目标分集时记录已不存在")
        finally:
            if outcome.status == "committed":
                # 取消不能跳过已提交事务的收尾，也不能回头恢复数据库记录。
                await wait_for_settlement(_after_episode_delete(episode_id, paths))
    return True


async def _after_episode_delete(episode_id: int, paths: set[Path]) -> None:
    """在删除锁内清理旧文件及分集缓存，各项失败仅记录日志。"""
    await cleanup_unreferenced_paths(paths)
    try:
        cache = get_cache_service()
        await cache.delete(f"fetch_comments_{episode_id}", region="default")
        for key in await cache.keys(f"sampled_{episode_id}_*", region="default"):
            await cache.delete(key, region="default")
    except Exception:
        logger.exception("分集 %s 已删除，但缓存清理失败", episode_id)


async def delete_library_entry(
    entry_id: int, *, is_source: bool, delete_files: bool = True,
) -> tuple[bool, bool]:
    """删除作品或源，源删除与空作品清理同事务，返回是否删除及是否清空作品。"""
    db = get_database_service()
    fs = get_file_storage_service()
    outcome = db.TransactionOutcome()
    paths: set[Path] = set()
    episode_ids: list[int] = []
    cache_keys: list[str] = []
    orphan_deleted = False
    async with fs.danmaku_mutation():
        try:
            async with db.transaction(outcome=outcome):
                if is_source:
                    source = await db.source.get_by_id(entry_id)
                    if source is None:
                        return False, False
                    anime = source.anime
                    sources = [source]
                else:
                    anime = await db.anime.get_by_id(entry_id)
                    if anime is None:
                        return False, False
                    # 仓储已预加载源列表；分集通过现有服务接口显式加载。
                    sources = [await db.source.get_by_id(s.id) for s in anime.sources]
                anime_id = anime.id
                cache_keys.append(
                    f"fallback_search_match_season_{anime.title}_{anime.season or 1}"
                )
                for source in sources:
                    order = source.sourceOrder or 1
                    virtual_base = 25000000000000 + anime_id * 1000000 + order * 10000
                    cache_keys.append(f"fallback_episode_{virtual_base}")
                    for episode in source.episodes:
                        episode_ids.append(episode.id)
                        if delete_files and episode.danmakuFilePath:
                            path = fs.resolve_fs_path(episode.danmakuFilePath)
                            if path is None:
                                logger.warning("分集 %s 路径无法解析，保留文件", episode.id)
                            else:
                                paths.add(path)
                if is_source:
                    await db.source.delete(entry_id)
                    if not await db.source.get_anime_sources(anime_id):
                        orphan_deleted = await db.anime.delete(anime_id)
                else:
                    await db.anime.delete(entry_id)
        finally:
            if outcome.status == "committed":
                await wait_for_settlement(_after_library_delete(paths, episode_ids, cache_keys))
    return True, orphan_deleted


async def _after_library_delete(
    paths: set[Path], episode_ids: list[int], cache_keys: list[str],
) -> None:
    """提交后仅删除零引用文件，不递归强删目录，缓存失败不撤销删除。"""
    await cleanup_unreferenced_paths(paths)
    for episode_id in episode_ids:
        await _after_episode_delete(episode_id, set())
    for key in cache_keys:
        try:
            # 服务获取同样属于非致命收尾，不能把已提交删除误报为失败。
            await get_cache_service().delete(key, region="default")
        except Exception:
            logger.exception("删除已提交，但后备缓存清理失败：%s", key)
