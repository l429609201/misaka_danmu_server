"""Webhook 任务入口：管理任务生命周期，业务策略委托 workflows。"""
import logging
from typing import Any, Callable, Optional, List

from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from src.core import get_now
from src.rate_limiter import RateLimiter
from src.services.ai_service import AIService
from src.services.config_service import ConfigService
from src.services.metadata_service import MetadataService
from src.services.scraper_manager import ScraperManager
from src.services.task_manager import TaskManager, TaskSuccess
from src.services.task_profiler import TaskProfiler, FLOW_WEBHOOK_IMPORT
from src.services.title_recognition import TitleRecognitionManager
from src.tasks.import_dispatch import submit_import_task
from src.utils import SearchTimer, SEARCH_TYPE_WEBHOOK
from src.workflows.search.webhook_input import prepare_webhook_search
from src.workflows.search.webhook_flow import search_webhook_source
from src.workflows.search.webhook_library import find_webhook_favorite

logger = logging.getLogger(__name__)


async def _submit_webhook_import_task(
    *,
    best_match: Any,
    webhookSource: str,
    mediaType: str,
    season: int,
    currentEpisodeIndex: int,
    ep_label: str,
    year: Optional[int],
    selectedEpisodes: Optional[List[int]],
    doubanId: Optional[str],
    tmdbId: Optional[str],
    imdbId: Optional[str],
    tvdbId: Optional[str],
    bangumiId: Optional[str],
    mediaServerType: Optional[str],
    mediaServerSeriesId: Optional[str],
    mediaServerSeasonId: Optional[str],
    mediaServerEpisodeId: Optional[str],
    # 执行依赖由统一派发入口获取，辅助函数只负责参数和任务标题。
    task_manager: TaskManager,
) -> str:
    """提交 Webhook 导入任务的辅助函数（消除3处重复）。

    Returns:
        成功消息字符串。
    """
    current_time = get_now().strftime("%H:%M:%S")

    # 根据来源动态生成任务标题前缀
    if webhookSource == "media_server":
        source_prefix = "媒体库读取导入"
    elif webhookSource in ["emby", "jellyfin", "plex"]:
        source_prefix = f"Webhook自动导入 ({webhookSource.capitalize()})"
    else:
        source_prefix = f"Webhook自动导入 ({webhookSource})"

    if mediaType == "tv_series":
        task_title = f"{source_prefix}: {best_match.title} - S{season:02d}{ep_label} ({best_match.provider}) [{current_time}]"
    else:
        task_title = f"{source_prefix}: {best_match.title} ({best_match.provider}) [{current_time}]"

    unique_key = f"import-{best_match.provider}-{best_match.mediaId}-S{season}-ep{currentEpisodeIndex}"

    # 修正：优先使用搜索结果的年份，如果搜索结果没有年份则使用webhook传入的年份
    final_year = best_match.year if best_match.year is not None else year

    # 实际工厂由统一派发入口构建，避免保留一份不执行的参数链。

    # 补齐 task_parameters：供完成通知展示作品名/季/集/类型/来源
    match_task_parameters = {
        "provider": best_match.provider,
        "mediaId": best_match.mediaId,
        "animeTitle": best_match.title,
        "mediaType": best_match.type,
        "season": season,
        "episode": currentEpisodeIndex,
        "currentEpisodeIndex": currentEpisodeIndex,
        "selectedEpisodes": selectedEpisodes,
        "year": final_year,
        "tmdbId": tmdbId,
        "imdbId": imdbId,
        "tvdbId": tvdbId,
        "doubanId": doubanId,
        "bangumiId": bangumiId,
        "imageUrl": best_match.imageUrl,
        "webhookSource": webhookSource,
        # 实际派发只读取此字典，关联 ID 必须在这里持久化。
        "mediaServerType": mediaServerType,
        "mediaServerSeriesId": mediaServerSeriesId,
        "mediaServerSeasonId": mediaServerSeasonId,
        "mediaServerEpisodeId": mediaServerEpisodeId,
    }

    try:
        await submit_import_task(
            task_manager=task_manager,
            task_parameters=match_task_parameters,
            task_title=task_title,
            queue_type="download",
        )
    except HTTPException as e:
        if e.status_code == 409:
            logger.info(f"Webhook 任务: 任务已在队列中 (unique_key={unique_key})，跳过重复提交。")
            raise TaskSuccess(f"相同任务已在处理中，无需重复提交。")
        raise

    # 根据来源动态生成成功消息
    if webhookSource == "media_server":
        return f"已为源 '{best_match.provider}' 创建导入任务。"
    else:
        return f"Webhook: 已为源 '{best_match.provider}' 创建导入任务。"


async def webhook_search_and_dispatch_task(
    animeTitle: str,
    mediaType: str,
    season: int,
    currentEpisodeIndex: Optional[int],
    searchKeyword: str,
    doubanId: Optional[str],
    tmdbId: Optional[str],
    imdbId: Optional[str],
    tvdbId: Optional[str],
    bangumiId: Optional[str],
    webhookSource: str,
    year: Optional[int],
    progress_callback: Callable,
    session: AsyncSession,
    manager: ScraperManager,
    task_manager: TaskManager, # type: ignore
    metadata_manager: MetadataService,
    config_service: ConfigService,
    ai_service: AIService,
    rate_limiter: RateLimiter,
    title_recognition_manager: TitleRecognitionManager,
    # 媒体库整季导入时, 可选: 指定已在媒体库中选中的分集索引列表
    selectedEpisodes: Optional[List[int]] = None,
    # 媒体服务三级 ID（用于 webhook 删除联动）
    mediaServerType: Optional[str] = None,
    mediaServerSeriesId: Optional[str] = None,
    mediaServerSeasonId: Optional[str] = None,
    mediaServerEpisodeId: Optional[str] = None,
):
    """
    Webhook 触发的后台任务：搜索所有源，找到最佳匹配，并为该匹配分发一个新的、具体的导入任务。
    """

    # 初始化搜索计时器（打日志）+ 性能统计 profiler（写 DB）
    ep_label = f"E{currentEpisodeIndex:02d}" if currentEpisodeIndex is not None else "全季"
    timer = SearchTimer(SEARCH_TYPE_WEBHOOK, f"{animeTitle} S{season:02d}{ep_label}", logger)
    timer.start()
    profiler = TaskProfiler(FLOW_WEBHOOK_IMPORT)

    # 仅跳过相同导入范围，不把同季不同集误当作已合并请求。
    episode_scope = sorted(set(selectedEpisodes)) if selectedEpisodes is not None else currentEpisodeIndex
    webhook_lock_key = f"webhook-{animeTitle}-S{season}-{episode_scope}-{mediaServerType}-{mediaServerEpisodeId}"
    lock_acquired = await manager.acquire_webhook_search_lock(webhook_lock_key)
    if not lock_acquired:
        # 已有相同作品的搜索任务在运行，直接返回成功（任务已在处理中）
        logger.info(f"Webhook 任务: '{animeTitle}' S{season:02d} 已有搜索任务在运行，跳过重复请求。")
        raise TaskSuccess(f"相同作品已有搜索任务在处理中，无需重复提交。")

    try:
        logger.info(f"Webhook 任务: 开始为 '{animeTitle}' (S{season:02d}{ep_label}) 查找最佳源...")
        await progress_callback(5, "正在检查已收藏的源...")

        # 在任何收藏源查询前统一准备输入，避免快速返回绕过识别词规则。
        timer.step_start("关键词解析与预处理")
        search_title, currentEpisodeIndex, season, selectedEpisodes = await prepare_webhook_search(
            searchKeyword, season, currentEpisodeIndex, selectedEpisodes,
            config_service, metadata_manager, ai_service, title_recognition_manager,
        )
        ep_label = f"E{currentEpisodeIndex:02d}" if currentEpisodeIndex is not None else "全季"
        logger.info(f"Webhook 输入准备完成: '{searchKeyword}' → '{search_title}', 季度={season}, 集数={currentEpisodeIndex}, 多集={selectedEpisodes}")
        profiler.record_step("关键词解析与预处理", timer.step_end())

        timer.step_start("查找收藏源")

        # 收藏源查找与年份决策由搜索编排层统一负责，任务入口只处理任务生命周期。
        favorited_source, effective_year = await find_webhook_favorite(
            title=search_title,
            season=season,
            year=year,
            session=session,
            recognition_manager=title_recognition_manager,
        )
        if favorited_source:
            logger.info(
                f"Webhook 任务: 找到已收藏的源 '{favorited_source['providerName']}'，将直接使用此源。"
            )
            await progress_callback(10, f"找到已收藏的源: {favorited_source['providerName']}")

            if webhookSource == "media_server":
                source_prefix = "媒体库读取导入"
            elif webhookSource in ["emby", "jellyfin", "plex"]:
                source_prefix = f"Webhook自动导入 ({webhookSource.capitalize()})"
            else:
                source_prefix = f"Webhook自动导入 ({webhookSource})"

            task_title = (
                f"{source_prefix}: {favorited_source['animeTitle']} - "
                f"S{season:02d}{ep_label} ({favorited_source['providerName']})"
            )
            unique_key = (
                f"import-{favorited_source['providerName']}-"
                f"{favorited_source['mediaId']}-S{season}-ep{currentEpisodeIndex}"
            )
            fav_task_parameters = {
                "provider": favorited_source["providerName"],
                "mediaId": favorited_source["mediaId"],
                "animeTitle": favorited_source["animeTitle"],
                "mediaType": favorited_source.get("mediaType"),
                "season": season,
                "episode": currentEpisodeIndex,
                "currentEpisodeIndex": currentEpisodeIndex,
                "selectedEpisodes": selectedEpisodes,
                "year": effective_year,
                "tmdbId": tmdbId,
                "imdbId": imdbId,
                "tvdbId": tvdbId,
                "doubanId": doubanId,
                "bangumiId": bangumiId,
                "imageUrl": favorited_source.get("imageUrl"),
                "webhookSource": webhookSource,
                "mediaServerType": mediaServerType,
                "mediaServerSeriesId": mediaServerSeriesId,
                "mediaServerSeasonId": mediaServerSeasonId,
                "mediaServerEpisodeId": mediaServerEpisodeId,
            }
            try:
                await submit_import_task(
                    task_manager=task_manager,
                    task_parameters=fav_task_parameters,
                    task_title=task_title,
                    queue_type="download",
                )
            except HTTPException as e:
                if e.status_code == 409:
                    logger.info(
                        f"Webhook 任务: 收藏源任务已在队列中 (unique_key={unique_key})，跳过重复提交。"
                    )
                    raise TaskSuccess("相同任务已在处理中，无需重复提交。")
                raise

            duration = timer.step_end(details="找到收藏源")
            profiler.record_step("查找收藏源", duration)
            timer.finish()
            await profiler.flush(session)
            if webhookSource == "media_server":
                success_message = f"已为收藏源 '{favorited_source['providerName']}' 创建导入任务。"
            else:
                success_message = f"Webhook: 已为收藏源 '{favorited_source['providerName']}' 创建导入任务。"
            raise TaskSuccess(success_message)

        timer.step_end(details="无收藏源")

        logger.info("Webhook 任务: 未找到收藏源，开始并发搜索所有启用的源...")
        await progress_callback(20, "并发搜索所有源...")
        best_match = await search_webhook_source(
            title=search_title, season=season, episode=currentEpisodeIndex,
            year=effective_year, media_type=mediaType, session=session,
            scraper_manager=manager, metadata_manager=metadata_manager,
            config_service=config_service, ai_service=ai_service,
            recognition_manager=title_recognition_manager, timer=timer, profiler=profiler,
        )

        logger.info(f"✓ Webhook 任务: 选择最佳匹配: {best_match.provider} - {best_match.title}")
        await progress_callback(50, f"在 {best_match.provider} 中找到最佳匹配项")

        timer.step_start("触发导入任务")
        success_message = await _submit_webhook_import_task(
            best_match=best_match,
            webhookSource=webhookSource,
            mediaType=mediaType,
            season=season,
            currentEpisodeIndex=currentEpisodeIndex,
            ep_label=ep_label,
            year=year,
            selectedEpisodes=selectedEpisodes,
            doubanId=doubanId,
            tmdbId=tmdbId,
            imdbId=imdbId,
            tvdbId=tvdbId,
            bangumiId=bangumiId,
            mediaServerType=mediaServerType,
            mediaServerSeriesId=mediaServerSeriesId,
            mediaServerSeasonId=mediaServerSeasonId,
            mediaServerEpisodeId=mediaServerEpisodeId,
            task_manager=task_manager,
        )

        profiler.record_step("触发导入任务", timer.step_end())
        timer.finish()
        raise TaskSuccess(success_message)
    except TaskSuccess:
        await profiler.flush(session)
        raise
    except Exception as e:
        await profiler.flush(session)
        timer.finish()  # 打印计时报告（即使失败也打印）
        logger.error(f"Webhook 搜索与分发任务发生严重错误: {e}", exc_info=True)
        raise
    finally:
        # 🔓 释放 Webhook 搜索锁
        await manager.release_webhook_search_lock(webhook_lock_key)

