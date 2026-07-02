"""删除任务模块"""
import logging
import asyncio
import shutil
from typing import Callable, List, Optional, Set
from pathlib import Path
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.exc import OperationalError

from src.db import orm_models, crud
from src.services import TaskSuccess
from src.core.cache import get_cache_backend

# 从 crud 导入需要的常量和函数
DANMAKU_BASE_DIR = crud.DANMAKU_BASE_DIR
_get_fs_path_from_web_path = crud._get_fs_path_from_web_path

# 后备缓存前缀（与 dandan/constants.py 保持一致，避免循环导入）
_FALLBACK_SEARCH_CACHE_PREFIX = "fallback_search_"
_EPISODE_MAPPING_CACHE_PREFIX = "episode_mapping_"

logger = logging.getLogger(__name__)


async def _clear_fallback_caches_for_anime(
    session: AsyncSession,
    anime_id: int,
    anime_title: str,
    anime_season: int,
    source_orders: List[int],
) -> None:
    """删除作品/源后清理后备映射缓存，作为 id 不重用修复的双保险。

    why: 即使 get_next_real_anime_id 已改为持久化计数器保证 id 不重用，
    但残留缓存（fallback_episode_* / match_season_*）在 TTL 期间（1-3h）内
    仍可能被其他代码路径命中（如整季缓存复用），清理后完全消除串台风险。
    """
    backend = get_cache_backend()

    async def _del(full_cache_key: str) -> None:
        """统一删除：优先走缓存后端，失败则回退到数据库。"""
        if backend is not None:
            try:
                await backend.delete(full_cache_key, region="default")
                logger.debug(f"已从缓存后端清理: {full_cache_key}")
                return
            except Exception as e:
                logger.debug(f"缓存后端删除失败，回退到数据库: {full_cache_key}, {e}")
        try:
            await crud.delete_cache(session, full_cache_key)
            logger.debug(f"已从数据库缓存清理: {full_cache_key}")
        except Exception as e:
            logger.debug(f"清理缓存 {full_cache_key} 失败（可忽略）: {e}")

    # 1. 清整部剧级别的后备映射缓存（格式：fallback_episode_25{animeId:06d}{sourceOrder:02d}0000）
    #    为每个已知 source_order 清理；写入时无前缀（set_db_cache 第二参数为 ""）
    for so in source_orders:
        virtual_base = 25000000000000 + anime_id * 1000000 + so * 10000
        await _del(f"fallback_episode_{virtual_base}")

    # 2. 清整季缓存（格式：fallback_search_match_season_{title}_{season}）
    #    写入时前缀为 FALLBACK_SEARCH_CACHE_PREFIX = "fallback_search_"
    await _del(f"{_FALLBACK_SEARCH_CACHE_PREFIX}match_season_{anime_title}_{anime_season}")

    logger.info(
        f"已清理作品 {anime_id}（{anime_title} S{anime_season}）的后备映射缓存"
        f"（清理了 {len(source_orders)} 个系列缓存 + 1 个整季缓存）"
    )


def _determine_cleanup_stop_dir(fs_path: Path) -> Path:
    """
    根据文件路径确定空目录清理的停止点。

    - 如果路径在 DANMAKU_BASE_DIR 下，停止点为 DANMAKU_BASE_DIR
    - 如果是自定义路径（不在 DANMAKU_BASE_DIR 下），停止点为文件上方3级目录
      （覆盖大多数自定义模板结构：title/season/episode.xml 等）
    """
    try:
        fs_path.resolve().relative_to(DANMAKU_BASE_DIR.resolve())
        return DANMAKU_BASE_DIR
    except ValueError:
        # 自定义路径 — 向上推3级作为安全边界
        stop = fs_path.parent
        for _ in range(3):
            if stop.parent and stop.parent != stop:
                stop = stop.parent
            else:
                break
        return stop


def _is_safe_to_delete_directory(dir_path: Path, base_dir: Path = DANMAKU_BASE_DIR) -> bool:
    """
    检查目录是否可以安全删除。

    安全条件：
    1. 目录必须存在且是目录
    2. 目录必须为空（没有任何文件或子目录）
    3. 目录必须在 base_dir 下（防止误删系统目录）
    4. 目录不能是 base_dir 本身
    """
    if not dir_path or not dir_path.exists() or not dir_path.is_dir():
        return False

    # 安全检查：确保目录在 base_dir 下
    try:
        dir_path.resolve().relative_to(base_dir.resolve())
    except ValueError:
        # 目录不在 base_dir 下，不允许删除
        logger.warning(f"目录 {dir_path} 不在安全边界 {base_dir} 下，跳过删除")
        return False

    # 不能删除 base_dir 本身
    if dir_path.resolve() == base_dir.resolve():
        return False

    # 检查目录是否为空
    try:
        return not any(dir_path.iterdir())
    except PermissionError:
        logger.warning(f"无权限访问目录 {dir_path}")
        return False


def _cleanup_empty_parent_directories(file_path: Path, stop_at: Path = DANMAKU_BASE_DIR):
    """
    递归清理空的父目录，直到遇到非空目录或到达停止点。

    Args:
        file_path: 被删除文件的路径
        stop_at: 停止清理的目录（不会删除此目录及其父目录）
    """
    if not file_path:
        return

    parent = file_path.parent

    # 向上遍历，清理空目录
    while parent and parent != stop_at and parent.resolve() != stop_at.resolve():
        if _is_safe_to_delete_directory(parent, base_dir=stop_at):
            try:
                parent.rmdir()
                logger.info(f"已清理空目录: {parent}")
                parent = parent.parent
            except OSError as e:
                # 目录可能不为空或无权限，停止清理
                logger.debug(f"无法删除目录 {parent}: {e}")
                break
        else:
            # 目录不为空或不安全，停止清理
            break


def delete_danmaku_file(danmaku_file_path_str: Optional[str], cleanup_empty_dirs: bool = True) -> Optional[Path]:
    """
    根据数据库中存储的Web路径，安全地删除对应的弹幕文件。

    Args:
        danmaku_file_path_str: 数据库中存储的Web路径
        cleanup_empty_dirs: 是否清理空的父目录，默认为True

    Returns:
        被删除文件的路径（用于批量操作后统一清理目录），如果未删除则返回None
    """
    if not danmaku_file_path_str:
        return None
    try:
        # 修正：使用 crud 中的辅助函数来获取正确的文件系统路径
        fs_path = _get_fs_path_from_web_path(danmaku_file_path_str)
        if fs_path and fs_path.is_file():
            fs_path.unlink(missing_ok=True)
            logger.debug(f"已删除弹幕文件: {fs_path}")

            # 清理空的父目录
            if cleanup_empty_dirs:
                stop_at = _determine_cleanup_stop_dir(fs_path)
                _cleanup_empty_parent_directories(fs_path, stop_at)

            return fs_path
    except (ValueError, FileNotFoundError):
        # 如果路径无效或文件不存在，则忽略
        pass
    except Exception as e:
        logger.error(f"删除弹幕文件 '{danmaku_file_path_str}' 时出错: {e}", exc_info=True)
    return None


def delete_danmaku_files_batch(file_paths: List[Optional[str]]):
    """
    批量删除弹幕文件，并在最后统一清理空目录。

    这比逐个删除更高效，因为可以避免重复检查同一个目录。

    Args:
        file_paths: 数据库中存储的Web路径列表
    """
    if not file_paths:
        return

    # 收集所有被删除文件的父目录
    affected_dirs: Set[Path] = set()

    for file_path_str in file_paths:
        if not file_path_str:
            continue
        try:
            fs_path = _get_fs_path_from_web_path(file_path_str)
            if fs_path and fs_path.is_file():
                affected_dirs.add(fs_path.parent)
                fs_path.unlink(missing_ok=True)
                logger.debug(f"已删除弹幕文件: {fs_path}")
        except (ValueError, FileNotFoundError):
            pass
        except Exception as e:
            logger.error(f"删除弹幕文件 '{file_path_str}' 时出错: {e}", exc_info=True)

    # 统一清理空目录（从最深的目录开始）
    # 按路径深度排序，先处理深层目录
    sorted_dirs = sorted(affected_dirs, key=lambda p: len(p.parts), reverse=True)
    cleaned_dirs: Set[Path] = set()

    for dir_path in sorted_dirs:
        # 根据目录中任意一个文件的路径确定安全边界
        # dir_path 是被删除文件的父目录，用一个虚拟子路径来推断 stop_at
        dummy_file = dir_path / "dummy.xml"
        stop_at = _determine_cleanup_stop_dir(dummy_file)

        # 向上遍历清理空目录
        current = dir_path
        while current and current.resolve() != stop_at.resolve():
            if current in cleaned_dirs:
                # 已经处理过这个目录，跳过
                break
            if _is_safe_to_delete_directory(current, base_dir=stop_at):
                try:
                    current.rmdir()
                    logger.info(f"已清理空目录: {current}")
                    cleaned_dirs.add(current)
                    current = current.parent
                except OSError:
                    break
            else:
                break


async def delete_anime_task(animeId: int, session: AsyncSession, progress_callback: Callable, delete_files: bool = True):
    """Background task to delete an anime and all its related data.

    Args:
        animeId: 作品ID
        session: 数据库会话
        progress_callback: 进度回调函数
        delete_files: 是否同时删除弹幕XML文件，默认为True
    """
    max_retries = 3
    for attempt in range(max_retries):
        try:
            await progress_callback(0, f"开始删除 (尝试 {attempt + 1}/{max_retries})...")

            # 检查作品是否存在
            anime_stmt = select(orm_models.Anime).where(orm_models.Anime.id == animeId)
            anime_result = await session.execute(anime_stmt)
            anime_exists = anime_result.scalar_one_or_none()
            if not anime_exists:
                raise TaskSuccess("作品未找到，无需删除。")

            # 提前捕获后备缓存清理所需信息（删除后 anime_exists 将失效）
            # why: 删除作品后需清理残留的后备映射缓存，防止新剧命中旧缓存串台
            cache_anime_title = anime_exists.title
            cache_anime_season = anime_exists.season or 1
            so_stmt = select(orm_models.AnimeSource.sourceOrder).where(
                orm_models.AnimeSource.animeId == animeId
            )
            so_result = await session.execute(so_stmt)
            cache_source_orders = [so for so in so_result.scalars().all() if so is not None]

            # 1. 删除关联的弹幕文件（支持自定义路径）
            if delete_files:
                await progress_callback(30, "正在删除关联的弹幕文件...")
                # 查询该作品下所有分集的弹幕文件路径
                from sqlalchemy.orm import selectinload
                sources_stmt = select(orm_models.AnimeSource).where(
                    orm_models.AnimeSource.animeId == animeId
                ).options(selectinload(orm_models.AnimeSource.episodes))
                sources_result = await session.execute(sources_stmt)
                sources = sources_result.scalars().all()

                # 收集所有文件路径
                all_file_paths: List[Optional[str]] = []
                for source in sources:
                    for episode in source.episodes:
                        if episode.danmakuFilePath:
                            all_file_paths.append(episode.danmakuFilePath)

                # 批量删除文件并清理空目录
                if all_file_paths:
                    delete_danmaku_files_batch(all_file_paths)
                    logger.info(f"已删除作品 {animeId} 的 {len(all_file_paths)} 个弹幕文件")

                # 同时尝试删除默认目录（兼容旧数据，确保目录完全清理）
                anime_danmaku_dir = DANMAKU_BASE_DIR / str(animeId)
                if anime_danmaku_dir.exists() and anime_danmaku_dir.is_dir():
                    shutil.rmtree(anime_danmaku_dir)
                    logger.info(f"已删除作品的弹幕目录: {anime_danmaku_dir}")

            # 2. 删除作品本身 (数据库将通过级联删除所有关联记录)
            await progress_callback(90, "正在删除数据库记录...")
            await session.delete(anime_exists)

            await session.commit()

            # 3. 清理残留的后备映射缓存（双保险，防止新剧命中旧缓存串台）
            try:
                await _clear_fallback_caches_for_anime(
                    session, animeId, cache_anime_title, cache_anime_season, cache_source_orders
                )
            except Exception as e:
                logger.warning(f"清理作品 {animeId} 后备缓存失败（不影响删除结果）: {e}")

            msg = "删除成功。" if delete_files else "删除成功（保留了弹幕文件）。"
            raise TaskSuccess(msg)
        except OperationalError as e:
            await session.rollback()
            if "Lock wait timeout exceeded" in str(e) and attempt < max_retries - 1:
                wait_time = 2 ** (attempt + 1) # 2, 4, 8 seconds
                logger.warning(f"删除作品时遇到锁超时，将在 {wait_time} 秒后重试...")
                await progress_callback(0, f"数据库锁定，将在 {wait_time} 秒后重试...")
                await asyncio.sleep(wait_time)
                continue # Retry the loop
            else:
                logger.error(f"删除作品任务 (ID: {animeId}) 失败: {e}", exc_info=True)
                raise # Re-raise if it's not a lock error or retries are exhausted
        except TaskSuccess:
            raise # Propagate success exception
        except Exception as e:
            await session.rollback()
            logger.error(f"删除作品任务 (ID: {animeId}) 失败: {e}", exc_info=True)
            raise


async def delete_source_task(sourceId: int, session: AsyncSession, progress_callback: Callable, delete_files: bool = True):
    """Background task to delete a source and all its related data.

    Args:
        sourceId: 数据源ID
        session: 数据库会话
        progress_callback: 进度回调函数
        delete_files: 是否同时删除弹幕XML文件，默认为True
    """
    from sqlalchemy import func

    await progress_callback(0, "开始删除...")
    try:
        # 检查源是否存在
        source_stmt = select(orm_models.AnimeSource).where(orm_models.AnimeSource.id == sourceId)
        source_result = await session.execute(source_stmt)
        source_exists = source_result.scalar_one_or_none()
        if not source_exists:
            raise TaskSuccess("数据源未找到，无需删除。")

        # 记录 anime ID，用于后续检查是否需要删除 anime
        anime_id = source_exists.animeId
        # 提前捕获后备缓存清理所需信息（删除后 source_exists/anime 将失效）
        # why: 删除源后需清理残留的后备映射缓存，防止新剧命中旧缓存串台
        cache_source_order = source_exists.sourceOrder or 1
        anime_for_cache = await session.get(orm_models.Anime, anime_id)
        cache_anime_title = anime_for_cache.title if anime_for_cache else ""
        cache_anime_season = (anime_for_cache.season or 1) if anime_for_cache else 1

        # 在删除数据库记录前，先删除关联的物理文件
        if delete_files:
            await progress_callback(30, "正在删除关联的弹幕文件...")
            episodes_to_delete_res = await session.execute(
                select(orm_models.Episode.danmakuFilePath).where(orm_models.Episode.sourceId == sourceId)
            )
            file_paths = [fp for fp in episodes_to_delete_res.scalars().all() if fp]
            # 使用批量删除，统一清理空目录
            delete_danmaku_files_batch(file_paths)

        # 删除源记录，数据库将级联删除其下的所有分集记录
        await progress_callback(70, "正在删除数据库记录...")
        await session.delete(source_exists)
        await session.commit()

        # 检查该 anime 是否还有其他源，如果没有则删除 anime 条目
        await progress_callback(90, "正在检查并清理空作品...")
        count_stmt = select(func.count(orm_models.AnimeSource.id)).where(
            orm_models.AnimeSource.animeId == anime_id
        )
        count_result = await session.execute(count_stmt)
        source_count = count_result.scalar_one()

        orphan_msg = ""
        if source_count == 0:
            anime = await session.get(orm_models.Anime, anime_id)
            if anime:
                await session.delete(anime)
                await session.commit()
                orphan_msg = "，并清理了空的作品条目"
                logger.info(f"已删除没有源的作品条目 (ID: {anime_id})")

        # 清理残留的后备映射缓存（防止新剧命中旧缓存串台）；失败不影响删除结果
        try:
            await _clear_fallback_caches_for_anime(
                session, anime_id, cache_anime_title, cache_anime_season, [cache_source_order]
            )
        except Exception as e:
            logger.warning(f"清理后备缓存失败（不影响删除）: {e}")

        msg = f"删除成功{orphan_msg}。" if delete_files else f"删除成功（保留了弹幕文件）{orphan_msg}。"
        raise TaskSuccess(msg)
    except TaskSuccess:
        # 显式地重新抛出 TaskSuccess，以确保它被 TaskManager 正确处理
        raise
    except Exception as e:
        await session.rollback()
        logger.error(f"删除源任务 (ID: {sourceId}) 失败: {e}", exc_info=True)
        raise


async def delete_episode_task(episodeId: int, session: AsyncSession, progress_callback: Callable, delete_files: bool = True):
    """Background task to delete an episode and its comments.

    Args:
        episodeId: 分集ID
        session: 数据库会话
        progress_callback: 进度回调函数
        delete_files: 是否同时删除弹幕XML文件，默认为True
    """
    await progress_callback(0, "开始删除...")
    try:
        # 检查分集是否存在
        episode_stmt = select(orm_models.Episode).where(orm_models.Episode.id == episodeId)
        episode_result = await session.execute(episode_stmt)
        episode_exists = episode_result.scalar_one_or_none()
        if not episode_exists:
            raise TaskSuccess("分集未找到，无需删除。")

        # 在删除数据库记录前，先删除物理文件
        if delete_files:
            delete_danmaku_file(episode_exists.danmakuFilePath)

        await session.delete(episode_exists)
        await session.commit()
        msg = "删除成功。" if delete_files else "删除成功（保留了弹幕文件）。"
        raise TaskSuccess(msg)
    except TaskSuccess:
        # 显式地重新抛出 TaskSuccess，以确保它被 TaskManager 正确处理
        raise
    except Exception as e:
        await session.rollback()
        logger.error(f"删除分集任务 (ID: {episodeId}) 失败: {e}", exc_info=True)
        raise


async def delete_bulk_episodes_task(episodeIds: List[int], session: AsyncSession, progress_callback: Callable, delete_files: bool = True):
    """后台任务：批量删除多个分集。

    Args:
        episodeIds: 分集ID列表
        session: 数据库会话
        progress_callback: 进度回调函数
        delete_files: 是否同时删除弹幕XML文件，默认为True
    """
    total = len(episodeIds)
    await progress_callback(5, f"准备删除 {total} 个分集...")
    deleted_count = 0

    # 收集所有要删除的文件路径，最后统一清理空目录
    all_file_paths: List[Optional[str]] = []

    try:
        for i, episode_id in enumerate(episodeIds):
            progress = 5 + int(((i + 1) / total) * 90) if total > 0 else 95
            await progress_callback(progress, f"正在删除分集 {i+1}/{total} (ID: {episode_id}) 的数据...")

            episode_stmt = select(orm_models.Episode).where(orm_models.Episode.id == episode_id)
            episode_result = await session.execute(episode_stmt)
            episode = episode_result.scalar_one_or_none()
            if episode:
                if delete_files and episode.danmakuFilePath:
                    all_file_paths.append(episode.danmakuFilePath)
                await session.delete(episode)
                deleted_count += 1

                # 为每个分集提交一次事务，以尽快释放锁
                await session.commit()

                # 短暂休眠，以允许其他数据库操作有机会执行
                await asyncio.sleep(0.1)

        # 批量删除文件并统一清理空目录
        if delete_files and all_file_paths:
            delete_danmaku_files_batch(all_file_paths)

        suffix = "" if delete_files else "（保留了弹幕文件）"
        raise TaskSuccess(f"批量删除完成，共处理 {total} 个，成功删除 {deleted_count} 个。{suffix}")
    except TaskSuccess:
        # 显式地重新抛出 TaskSuccess，以确保它被 TaskManager 正确处理
        raise
    except Exception as e:
        await session.rollback()
        logger.error(f"批量删除分集任务失败: {e}", exc_info=True)
        raise


async def delete_bulk_sources_task(sourceIds: List[int], session: AsyncSession, progress_callback: Callable, delete_files: bool = True):
    """Background task to delete multiple sources.

    Args:
        sourceIds: 数据源ID列表
        session: 数据库会话
        progress_callback: 进度回调函数
        delete_files: 是否同时删除弹幕XML文件，默认为True
    """
    from sqlalchemy import func

    total = len(sourceIds)
    deleted_count = 0
    affected_anime_ids = set()  # 记录受影响的 anime ID

    # 收集所有要删除的文件路径，最后统一清理空目录
    all_file_paths: List[Optional[str]] = []

    for i, sourceId in enumerate(sourceIds):
        progress = int((i / total) * 90)  # 留10%给清理空anime
        await progress_callback(progress, f"正在删除源 {i+1}/{total} (ID: {sourceId})...")
        try:
            source_stmt = select(orm_models.AnimeSource).where(orm_models.AnimeSource.id == sourceId)
            source_result = await session.execute(source_stmt)
            source = source_result.scalar_one_or_none()
            if source:
                # 记录受影响的 anime ID
                affected_anime_ids.add(source.animeId)

                # 收集关联的弹幕文件路径
                if delete_files:
                    episodes_to_delete_res = await session.execute(
                        select(orm_models.Episode.danmakuFilePath).where(orm_models.Episode.sourceId == sourceId)
                    )
                    for file_path in episodes_to_delete_res.scalars().all():
                        if file_path:
                            all_file_paths.append(file_path)

                await session.delete(source)
                await session.commit()
                deleted_count += 1
        except Exception as e:
            logger.error(f"批量删除源任务中，删除源 (ID: {sourceId}) 失败: {e}", exc_info=True)
            # Continue to the next one

    # 批量删除文件并统一清理空目录
    if delete_files and all_file_paths:
        delete_danmaku_files_batch(all_file_paths)

    # 清理没有源的 anime 条目
    orphan_anime_count = 0
    if affected_anime_ids:
        await progress_callback(95, "正在清理空的作品条目...")
        for anime_id in affected_anime_ids:
            try:
                # 检查该 anime 是否还有源
                count_stmt = select(func.count(orm_models.AnimeSource.id)).where(
                    orm_models.AnimeSource.animeId == anime_id
                )
                count_result = await session.execute(count_stmt)
                source_count = count_result.scalar_one()

                if source_count == 0:
                    # 没有源了，删除 anime 条目
                    anime = await session.get(orm_models.Anime, anime_id)
                    if anime:
                        await session.delete(anime)
                        await session.commit()
                        orphan_anime_count += 1
                        logger.info(f"已删除没有源的作品条目 (ID: {anime_id})")
            except Exception as e:
                logger.error(f"清理空 anime 条目 (ID: {anime_id}) 时出错: {e}", exc_info=True)

    await session.commit()
    suffix = "" if delete_files else "（保留了弹幕文件）"
    orphan_msg = f"，清理了 {orphan_anime_count} 个空作品条目" if orphan_anime_count > 0 else ""
    raise TaskSuccess(f"批量删除完成，共处理 {total} 个，成功删除 {deleted_count} 个{orphan_msg}。{suffix}")

