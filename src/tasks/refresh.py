"""刷新任务模块，业务交给 Workflow，入口仅管理任务生命周期。"""
import logging
from typing import Callable, List

from sqlalchemy.ext.asyncio import AsyncSession

from src.rate_limiter import RateLimiter
from src.workflows.source_refresh_import import prepare_source_refresh_import
from src.services.scraper_manager import ScraperManager
from src.services.task_manager import TaskManager
from src.utils.diagnostics.task_exceptions import TaskSuccess
from src.services.title_recognition import TitleRecognitionManager
from src.services.metadata_service import MetadataService
from src.services.task_profiler import TaskProfiler, FLOW_FULL_REFRESH, FLOW_SINGLE_REFRESH, FLOW_BULK_REFRESH
from src.utils.parsing.filename_parser import format_episode_ranges
from src.workflows.bulk_episode_refresh import refresh_bulk_item
from src.workflows.episode_refresh import refresh_one_episode
from src.workflows.source_refresh import refresh_stored_source
from .import_core import generic_import_task

logger = logging.getLogger(__name__)

async def full_refresh_task(sourceId: int, session: AsyncSession, scraper_manager: ScraperManager, task_manager: TaskManager, rate_limiter: RateLimiter, progress_callback: Callable, metadata_manager: MetadataService, config_service = None):
    """
    后台任务：全量刷新一个已存在的番剧。

    优化：直接使用数据库中已存储的分集 ID 获取弹幕，不依赖源站的"获取分集列表"接口。
    这样可以避免因源站接口不稳定（如限流）导致的刷新失败。
    """
    profiler = TaskProfiler(FLOW_FULL_REFRESH)
    logger.info(f"开始刷新源 ID: {sourceId}")
    try:
        # 全量刷新业务由 Workflow 自持短事务，任务仅负责生命周期。
        message = await refresh_stored_source(
            sourceId, scraper_manager, rate_limiter, progress_callback, config_service,
        )
        raise TaskSuccess(message)

    except TaskSuccess:
        await profiler.flush(session)
        raise
    except Exception as e:
        await profiler.flush(session)
        # 数据事务由服务上下文负责回滚，不触碰任务注入会话。
        logger.error(f"全量刷新任务 (源ID: {sourceId}) 失败: {e}", exc_info=True)
        raise


async def refresh_episode_task(episodeId: int, session: AsyncSession, manager: ScraperManager, rate_limiter: RateLimiter, progress_callback: Callable, config_service = None):
    """后台任务：刷新单个分集的弹幕"""
    profiler = TaskProfiler(FLOW_SINGLE_REFRESH)
    logger.info(f"开始刷新分集 ID: {episodeId}")
    try:
        # 单集刷新策略统一由 Workflow 承担，任务仅记录完成信号。
        message = await refresh_one_episode(
            episodeId, manager, rate_limiter, progress_callback, config_service, profiler,
        )
        raise TaskSuccess(message)
    except TaskSuccess:
        await profiler.flush(session)
        raise
    except Exception as e:
        await profiler.flush(session)
        logger.error(f"刷新分集 ID: {episodeId} 时发生严重错误: {e}", exc_info=True)
        raise


async def refresh_bulk_episodes_task(episodeIds: List[int], session: AsyncSession, manager: ScraperManager, rate_limiter: RateLimiter, progress_callback: Callable, config_service = None):
    """后台任务：批量刷新多个分集的弹幕"""
    profiler = TaskProfiler(FLOW_BULK_REFRESH)
    total = len(episodeIds)
    logger.info(f"开始批量刷新 {total} 个分集")
    await progress_callback(5, f"准备刷新 {total} 个分集...")

    success_episodes = []  # 存储 (episodeNumber, episodeId) 元组
    failed_episodes = []   # 存储 (episodeNumber, episodeId) 元组
    total_added_comments = 0

    # 区间压缩复用纯工具层；任务报告仅保留展示格式。

    try:

        # 维护受限源的集合（单源配额满时记录）
        rate_limited_providers = set()

        for i, episode_id in enumerate(episodeIds):
            progress = 5 + int(((i + 1) / total) * 90) if total > 0 else 95
            await progress_callback(progress, f"正在刷新分集 {i+1}/{total} (ID: {episode_id})...")

            # 单条查询、流控和保存均由 Workflow 执行，任务只汇总进度。
            episode_index, added_count, succeeded = await refresh_bulk_item(
                episode_id, manager, rate_limiter, progress_callback, config_service,
                rate_limited_providers, progress, i + 1, total,
            )
            total_added_comments += added_count
            target = success_episodes if succeeded else failed_episodes
            target.append((episode_index, episode_id))

        success_count = len(success_episodes)
        failed_count = len(failed_episodes)

        success_ranges = "<" + (format_episode_ranges([item[0] for item in success_episodes], separator=",") or "无") + ">"
        failed_ranges = "<" + (format_episode_ranges([item[0] for item in failed_episodes], separator=",") or "无") + ">"

        comment_part = f"共获取 {total_added_comments} 条弹幕" if total_added_comments > 0 else "暂无新弹幕"
        message = f"批量刷新完成，共处理 {total} 个，成功 {success_count} 个 {success_ranges}，失败 {failed_count} 个 {failed_ranges}，{comment_part}。"
        raise TaskSuccess(message)
    except TaskSuccess:
        await profiler.flush(session)
        raise
    except Exception as e:
        await profiler.flush(session)
        # 每次保存自持事务，失败不再回滚无关的任务会话。
        logger.error(f"批量刷新分集任务失败: {e}", exc_info=True)
        raise


async def incremental_refresh_task(sourceId: int, nextEpisodeIndex: int, session: AsyncSession, manager: ScraperManager, task_manager: TaskManager, config_service, rate_limiter: RateLimiter, metadata_manager: MetadataService, progress_callback: Callable, animeTitle: str, title_recognition_manager: TitleRecognitionManager) -> None:
    """管理单集增量导入生命周期，源信息与参数由 Workflow 准备。"""
    parameters = await prepare_source_refresh_import(sourceId, animeTitle, nextEpisodeIndex)
    await generic_import_task(
        **parameters, session=session, progress_callback=progress_callback,
        manager=manager, task_manager=task_manager, config_service=config_service,
        metadata_manager=metadata_manager, rate_limiter=rate_limiter,
        title_recognition_manager=title_recognition_manager,
    )


async def fill_missing_task(sourceId: int, session: AsyncSession, manager: ScraperManager, task_manager: TaskManager, config_service, rate_limiter: RateLimiter, metadata_manager: MetadataService, progress_callback: Callable, animeTitle: str, title_recognition_manager: TitleRecognitionManager) -> None:
    """仅补全未收录分集，下载前过滤已有集，保存时再次查重。"""
    # 获取完整目录用于查缺，但不把已有集交给弹幕下载流程。
    parameters = await prepare_source_refresh_import(sourceId, animeTitle, None)
    await generic_import_task(
        **parameters, session=session, progress_callback=progress_callback,
        manager=manager, task_manager=task_manager, config_service=config_service,
        metadata_manager=metadata_manager, rate_limiter=rate_limiter,
        title_recognition_manager=title_recognition_manager,
        only_missing_source_id=sourceId,
    )