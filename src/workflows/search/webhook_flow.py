"""Webhook 站外搜索编排：搜索、修正、过滤与候选选择，不提交任务。"""

import logging
from typing import Any, Optional

from sqlalchemy.ext.asyncio import AsyncSession

from src.services.task_profiler import TaskProfiler
from src.utils import SearchTimer
from src.utils.diagnostics.search_timer import SubStepTiming
from src.workflows.search.ai_correction import correct_search_results
from src.workflows.search.coordinator import SearchCoordinator
from src.workflows.search.filtering import correct_movie_type_by_title, filter_by_season
from src.workflows.search.webhook_selection import select_webhook_result

logger = logging.getLogger(__name__)


async def search_webhook_source(
    *, title: str, season: int, episode: Optional[int], year: Optional[int],
    media_type: str, session: AsyncSession, scraper_manager: Any,
    metadata_manager: Any, config_service: Any, ai_service: Any,
    recognition_manager: Any, timer: SearchTimer, profiler: TaskProfiler,
) -> Any:
    """消费已预处理的输入并返回最佳源，不再次应用识别规则。"""
    coordinator = SearchCoordinator(
        scraper_manager=scraper_manager, metadata_manager=metadata_manager, user=None,
    )
    timer.step_start("统一搜索")
    results = await coordinator.search_only(
        session=session, search_term=title, season=season,
        episode=episode, progress_callback=None,
    )
    sub_steps = [
        SubStepTiming(
            name=name[3:] if name.startswith("补充:") else name,
            duration_ms=duration, result_count=count,
            group="补充源" if name.startswith("补充:") else "弹幕源",
        )
        for name, duration, count in scraper_manager.last_search_timing
    ]
    profiler.record_step("统一搜索", timer.step_end(
        details=f"{len(results)}个结果", sub_steps=sub_steps,
    ))
    if not results:
        raise ValueError(f"未找到 '{title}' 的任何可用源。")

    mapping_enabled = (
        await config_service.get("webhookEnableTmdbSeasonMapping", "true")
    ).lower() == "true"
    if not mapping_enabled:
        logger.info("○ Webhook 统一AI映射: 功能未启用")
    elif await ai_service.is_available():
        timer.step_start("AI映射修正")
        try:
            # 按需获取共享匹配器，避免收藏源快速返回时遗留无人等待的预热任务。
            matcher = await ai_service.get_matcher()
            details = "匹配器未启用"
            if matcher:
                logger.info(f"○ Webhook 开始统一AI映射修正: '{title}' ({len(results)} 个结果)")
                mapping = await correct_search_results(
                    search_title=title, search_results=results,
                    metadata_manager=metadata_manager, ai_matcher=matcher,
                    logger=logger, similarity_threshold=60.0,
                )
                if mapping["total_corrections"] > 0:
                    results = mapping["corrected_results"]
                    details = f"修正{mapping['total_corrections']}个"
                    logger.info(f"✓ Webhook 统一AI映射成功: {details}")
                else:
                    details = "无修正"
                    logger.info("○ Webhook 统一AI映射: 未找到需要修正的信息")
            else:
                logger.warning("○ Webhook AI映射: AI匹配器未启用或初始化失败")
            profiler.record_step("AI映射修正", timer.step_end(details=details))
        except Exception as exc:
            logger.warning(f"Webhook 统一AI映射任务执行失败: {exc}")
            profiler.record_step(
                "AI映射修正", timer.step_end(details=f"失败: {exc}"), success=False,
            )

    correct_movie_type_by_title(results, log_prefix="Webhook:")
    if season and season > 0 and media_type != "movie":
        results, _ = filter_by_season(results, season)
    timer.step_start("结果排序与匹配")
    best_match = await select_webhook_result(
        results=results, title=title, season=season, episode=episode,
        year=year, media_type=media_type, session=session,
        coordinator=coordinator, ai_service=ai_service,
        recognition_manager=recognition_manager,
        config_service=config_service, scraper_manager=scraper_manager,
    )
    profiler.record_step("结果排序与匹配", timer.step_end())
    return best_match
