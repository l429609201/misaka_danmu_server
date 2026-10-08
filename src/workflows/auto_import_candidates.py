"""自动导入的搜索、AI 修正、排序和候选选择流程。"""
import logging
from typing import Any, Dict

from src.utils.diagnostics.search_timer import SubStepTiming
from src.utils.parsing.filename_parser import parse_episode_ranges
from src.workflows.search.ai_correction import correct_search_results
from src.workflows.search.coordinator import SearchCoordinator
from src.workflows.search.library_sources import collect_existing_source_keys
from src.workflows.search.result_selector import ResultSelector
from src.workflows.search.scoring import load_provider_order

logger = logging.getLogger(__name__)


async def search_auto_import_candidates(
    session: Any, prepared: Dict[str, Any], search_title: str, episode_text: Any,
    user: Any, scraper_manager: Any, metadata_manager: Any, config_service: Any,
    ai_service: Any, recognition_manager: Any, progress_callback: Any,
    timer: Any, profiler: Any, warmup_task: Any,
) -> Dict[str, Any]:
    """搜索阶段只冻结候选顺序，弹幕验证由下载阶段负责。"""
    main_title = prepared["main_title"]
    season, year, media_type = prepared["season"], prepared["year"], prepared["media_type"]
    timer.step_start("弹幕源搜索")
    coordinator = SearchCoordinator(
        scraper_manager=scraper_manager, metadata_manager=metadata_manager, user=user,
    )
    results = await coordinator.search_only(
        session=session, search_term=search_title, season=prepared["search_season"],
        episode=episode_text, progress_callback=None,
    )
    timing = [SubStepTiming(name=name, duration_ms=duration, result_count=count)
              for name, duration, count in scraper_manager.last_search_timing]
    duration = timer.step_end(details=f"{len(results)}个结果", sub_steps=timing)
    profiler.record_step("弹幕源搜索", duration)
    mapping_enabled = (await config_service.get("autoImportEnableTmdbSeasonMapping", "false")).lower() == "true"
    if mapping_enabled and media_type != "movie" and await ai_service.is_available():
        timer.step_start("AI映射修正")
        try:
            matcher = await warmup_task if warmup_task else await ai_service.get_matcher()
            if matcher:
                mapping = await correct_search_results(
                    search_title=search_title, search_results=results,
                    metadata_manager=metadata_manager, ai_matcher=matcher,
                    logger=logger, similarity_threshold=60.0,
                )
                results = mapping["corrected_results"]
                duration = timer.step_end(details=f"修正{mapping['total_corrections']}个")
            else:
                duration = timer.step_end(details="匹配器未启用")
            profiler.record_step("AI映射修正", duration)
        except Exception as exc:
            logger.warning("全自动导入 AI映射失败: %s", exc)
            profiler.record_step("AI映射修正", timer.step_end(details=f"失败: {exc}"), success=False)
    await progress_callback(50, "正在准备选择最佳源...")
    results = coordinator.rank_results(
        results=results, query_title=main_title, query_season=season,
        query_year=year, provider_order=await load_provider_order(),
    )
    if not results:
        raise ValueError("没有找到合适的搜索结果")
    requested = parse_episode_ranges(episode_text) if episode_text else []
    existing_keys = await collect_existing_source_keys(session, results, log_prefix="[全自动导入]")
    selector = ResultSelector(
        ai_service=ai_service, title_recognition_manager=recognition_manager,
        config_service=config_service, scraper_manager=scraper_manager,
    )
    best = await selector.select_best(
        candidates=results, query_title=main_title, query_season=season,
        query_episode=requested[0] if len(requested) == 1 else None,
        query_year=year, media_type=media_type, existing_source_keys=existing_keys,
        session=session, enable_ai=await ai_service.is_available(),
        enable_defer_validation=False, enable_fallback=True,
    )
    if not best:
        raise ValueError("未能选择合适的搜索结果")
    fallback_enabled = (await config_service.get("externalApiFallbackEnabled", "false")).lower() == "true"
    candidates = []
    if fallback_enabled:
        candidates = [{"provider": item.provider, "mediaId": item.mediaId,
                       "mediaType": getattr(item, "type", media_type), "title": item.title}
                      for item in results]
    final_title = best.title
    if recognition_manager:
        title, _, _, applied, _ = await recognition_manager.apply_storage_postprocessing(best.title, season)
        if applied:
            final_title = title
    title = f"自动导入: {final_title}"
    if season is not None:
        title += f" S{season:02d}"
    if episode_text:
        title += f" E{episode_text}"
    parameters = {
        "provider": best.provider, "mediaId": best.mediaId, "animeTitle": final_title,
        "mediaType": best.type, "season": season, "year": best.year,
        "currentEpisodeIndex": None, "selectedEpisodes": requested or None,
        "imageUrl": prepared["image_url"] or best.imageUrl,
        "doubanId": prepared["douban_id"], "tmdbId": prepared["tmdb_id"],
        "imdbId": prepared["imdb_id"], "tvdbId": prepared["tvdb_id"],
        "bangumiId": prepared["bangumi_id"], "fallbackCandidates": candidates or None,
    }
    return {"title": title, "parameters": parameters}
