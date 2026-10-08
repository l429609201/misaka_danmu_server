"""核心导入任务模块"""
import logging
from typing import Callable, Optional, List, Dict, Any
from sqlalchemy.ext.asyncio import AsyncSession

from src.schemas import EditImportRequest
from src.rate_limiter import RateLimiter, RateLimitExceededError
from src.services.scraper_manager import ScraperManager
from src.services.task_manager import TaskManager
from src.utils.diagnostics.task_exceptions import TaskSuccess, TaskPauseForRateLimit, TaskFailed
from src.services.title_recognition import TitleRecognitionManager
from src.services.config_service import ConfigService
from src.services.metadata_service import MetadataService
from src.services.task_profiler import TaskProfiler, FLOW_GENERIC_IMPORT
# 任务仅依赖编排入口，不再直接承担身份创建和故障转移保存。
from src.workflows.edited_import_execution import execute_edited_import
from src.workflows.episode_import import import_episodes_iteratively
from src.workflows.import_candidates import ImportCandidateRejected, with_import_candidates
from src.workflows.import_completion import finish_generic_import
from src.workflows.import_episode_fetch import fetch_import_episodes
from src.workflows.import_episode_selection import select_import_episodes
from src.workflows.import_failover import import_failover_episode
from src.workflows.generic_import_preparation import prepare_generic_import
from src.workflows.source_refresh_import import select_missing_source_episodes

logger = logging.getLogger(__name__)

# 候选顺延统一由导入编排处理，不保留无调用的前置验证分支。


@with_import_candidates
async def generic_import_task(
    provider: str,
    mediaId: str,
    animeTitle: str,
    mediaType: str,
    season: int,
    year: Optional[int],
    currentEpisodeIndex: Optional[int],
    imageUrl: Optional[str],
    config_service: ConfigService,
    metadata_manager: MetadataService,
    progress_callback: Callable,
    session: AsyncSession,
    manager: ScraperManager,
    task_manager: TaskManager,
    rate_limiter: RateLimiter,
    title_recognition_manager: TitleRecognitionManager,
    # 元数据 ID 参数（可选，带默认值）
    doubanId: Optional[str] = None,
    tmdbId: Optional[str] = None,
    imdbId: Optional[str] = None,
    tvdbId: Optional[str] = None,
    bangumiId: Optional[str] = None,
    # 新增: 补充源信息
    supplementProvider: Optional[str] = None,
    supplementMediaId: Optional[str] = None,
    # 新增: 后备任务标识
    is_fallback: bool = False,
    fallback_type: Optional[str] = None,
    # 新增: 预分配的anime_id（用于匹配后备）
    preassignedAnimeId: Optional[int] = None,
    # 新增: 媒体库整季导入时, 指定要导入的分集索引列表
    selectedEpisodes: Optional[List[int]] = None,
    # 新增: 追更任务标识,用于失败计数
    is_incremental_refresh: bool = False,
    incremental_refresh_source_id: Optional[int] = None,
    # 新增: 媒体服务三级 ID（用于 webhook 删除联动）
    mediaServerType: Optional[str] = None,
    mediaServerSeriesId: Optional[str] = None,
    mediaServerSeasonId: Optional[str] = None,
    mediaServerEpisodeId: Optional[str] = None,
    # 新增: 任务完成后是否自动开启增量追更（用于日历订阅等需要持续追更的入口）
    fallbackCandidates: Optional[List[Dict[str, Any]]] = None,

    enable_incremental_refresh: bool = False,
    only_missing_source_id: Optional[int] = None,
):
    """
    后台任务：执行从指定数据源导入弹幕的完整流程。
    修改流程：先获取弹幕，成功后再创建数据库条目。

    Args:
        is_fallback: 是否为后备任务（默认False）
        fallback_type: 后备类型 ("match" 或 "search"，仅当is_fallback=True时需要）
    """
    profiler = TaskProfiler(FLOW_GENERIC_IMPORT)

    scraper = manager.get_scraper(provider)
    # why：后续识别词偏移、单剧过滤和条目创建统一使用可调整标题变量。
    title_to_use = animeTitle
    season_to_use = season

    # 获取与过滤分集整体交给编排层，任务不参与源站和识别词策略。
    episodes, source_selected_episodes = await fetch_import_episodes(
        {
            "provider": provider, "mediaId": mediaId, "animeTitle": title_to_use,
            "mediaType": mediaType, "season": season_to_use,
            "currentEpisodeIndex": currentEpisodeIndex, "selectedEpisodes": selectedEpisodes,
            "supplementProvider": supplementProvider, "supplementMediaId": supplementMediaId,
        },
        manager, scraper, config_service, metadata_manager,
        title_recognition_manager, progress_callback, profiler,
    )

    if not episodes:
        if only_missing_source_id is not None:
            # 空目录不能证明缺集，不转入单集故障转移或刷新已有分集。
            await profiler.flush()
            raise TaskFailed("补全缺集失败，未获取到该源的分集目录。")
        # 故障转移的验证、建库及保存整体交给编排层。
        try:
            final_message = await import_failover_episode(
                {
                    "provider": provider, "mediaId": mediaId,
                    "animeTitle": title_to_use, "mediaType": mediaType,
                    "season": season_to_use, "year": year, "imageUrl": imageUrl,
                    "currentEpisodeIndex": currentEpisodeIndex,
                    "is_fallback": is_fallback, "fallback_type": fallback_type,
                    "tmdbId": tmdbId, "tvdbId": tvdbId, "imdbId": imdbId,
                    "doubanId": doubanId, "bangumiId": bangumiId,
                    "mediaServerType": mediaServerType,
                    "mediaServerSeriesId": mediaServerSeriesId,
                    "mediaServerSeasonId": mediaServerSeasonId,
                    "mediaServerEpisodeId": mediaServerEpisodeId,
                    "enable_incremental_refresh": enable_incremental_refresh,
                },
                scraper, config_service, metadata_manager,
                title_recognition_manager, progress_callback,
            )
        finally:
            await profiler.flush()
        raise TaskSuccess(final_message)

    # 集号映射及已有弹幕检查由 Workflow 负责，任务仅记录提前结束。
    if selectedEpisodes is not None:
        try:
            episodes = await select_import_episodes(
                episodes, selectedEpisodes, source_selected_episodes,
                provider, mediaId, season, tmdbId,
            )
        except (ImportCandidateRejected, TaskSuccess):
            await profiler.flush()
            raise


    # 缺集补全在首集验证之前过滤，避免为已有集请求或刷新弹幕。
    if only_missing_source_id is not None:
        try:
            episodes = await select_missing_source_episodes(
                only_missing_source_id, episodes, title_to_use, title_recognition_manager,
            )
        except TaskSuccess:
            await profiler.flush()
            raise

    # 首集验证和建库保持同一业务边界，任务只处理暂停和性能记录。
    try:
        anime_id, source_id, image_download_failed, first_comments = await prepare_generic_import(
            {
                "provider": provider, "mediaId": mediaId,
                "animeTitle": title_to_use, "mediaType": mediaType,
                "season": season_to_use, "year": year, "imageUrl": imageUrl,
                "preassignedAnimeId": preassignedAnimeId,
                "is_fallback": is_fallback, "fallback_type": fallback_type,
                "tmdbId": tmdbId, "tvdbId": tvdbId, "imdbId": imdbId,
                "doubanId": doubanId, "bangumiId": bangumiId,
                "mediaServerType": mediaServerType,
                "mediaServerSeriesId": mediaServerSeriesId,
                "mediaServerSeasonId": mediaServerSeasonId,
                "enable_incremental_refresh": enable_incremental_refresh,
                "only_missing_source_id": only_missing_source_id,
            },
            episodes, scraper, rate_limiter, metadata_manager,
            title_recognition_manager, progress_callback, profiler,
        )
    except RateLimitExceededError as exc:
        raise TaskPauseForRateLimit(
            retry_after_seconds=exc.retry_after_seconds,
            message=f"速率受限，将在 {exc.retry_after_seconds:.0f} 秒后自动重试...",
        ) from exc
    except ImportCandidateRejected:
        await profiler.flush()
        raise

    # 处理所有分集（包括第一集）
    try:
        async with profiler.step("批量下载并写入弹幕"):
            total_comments_added, successful_episodes_indices, skipped_episodes_indices, failed_episodes_count, failed_episodes_details = await import_episodes_iteratively(
                scraper=scraper,
                rate_limiter=rate_limiter,
                progress_callback=progress_callback,
                episodes=episodes,
                anime_id=anime_id,
                source_id=source_id,
                first_episode_comments=first_comments,  # 传递第一集已获取的弹幕
                config_service=config_service,
                is_single_episode=currentEpisodeIndex is not None,  # 传递是否为单集下载模式
                is_fallback=is_fallback,  # 传递后备任务标识
                fallback_type=fallback_type,  # 传递后备类型
                title_recognition_manager=title_recognition_manager,  # 传递识别词管理器（用于 partial_offset）
                anime_title=title_to_use,  # 传递番剧标题（用于 partial_offset 规则匹配）
                only_missing=only_missing_source_id is not None,
            )
    except RateLimitExceededError as e:
        # 单源配额已满，转为任务暂停，释放 worker 给其他源
        logger.warning(f"下载分集时触发单源流控，暂停任务等待重试: {e}")
        raise TaskPauseForRateLimit(
            retry_after_seconds=e.retry_after_seconds,
            message=f"速率受限，将在 {e.retry_after_seconds:.0f} 秒后自动重试..."
        )

    # 收尾业务统一在 Workflow 执行，任务保证失败时也写入性能记录。
    try:
        final_message = await finish_generic_import(
            (total_comments_added, successful_episodes_indices, skipped_episodes_indices,
             failed_episodes_count, failed_episodes_details),
            is_incremental_refresh, incremental_refresh_source_id,
            mediaServerEpisodeId, currentEpisodeIndex, source_id, image_download_failed,
        )
    finally:
        await profiler.flush()
    raise TaskSuccess(final_message)


async def edited_import_task(
    request_data: "EditImportRequest",
    progress_callback: Callable,
    session: AsyncSession,
    config_service: ConfigService,
    manager: ScraperManager,
    rate_limiter: RateLimiter,
    title_recognition_manager: TitleRecognitionManager
) -> None:
    """后台任务：处理编辑后的导入请求，业务准备由 Workflow 承接。"""
    profiler = TaskProfiler(FLOW_GENERIC_IMPORT)
    try:
        message = await execute_edited_import(
            request_data, progress_callback, config_service,
            manager, rate_limiter, title_recognition_manager,
        )
    except RateLimitExceededError as exc:
        raise TaskPauseForRateLimit(
            retry_after_seconds=exc.retry_after_seconds,
            message=f"速率受限，将在 {exc.retry_after_seconds:.0f} 秒后自动重试...",
        ) from exc
    finally:
        # 任务入口负责性能记录收尾，业务流程不持有任务会话。
        await profiler.flush()
    raise TaskSuccess(message)

