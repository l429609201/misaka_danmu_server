"""Webhook 搜索结果的业务选择编排，不依赖任务入口。"""

import logging
from typing import Any, Optional

from sqlalchemy.ext.asyncio import AsyncSession
from thefuzz import fuzz

from src.workflows.search.library_sources import collect_existing_source_keys
from src.workflows.search.result_selector import ResultSelector
from src.workflows.search.scoring import load_provider_order

logger = logging.getLogger(__name__)


async def select_webhook_result(
    *,
    results: list[Any],
    title: str,
    season: int,
    episode: Optional[int],
    year: Optional[int],
    media_type: str,
    session: AsyncSession,
    coordinator: Any,
    ai_service: Any,
    recognition_manager: Any,
    config_service: Any,
    scraper_manager: Any,
) -> Any:
    """按库内源加分、统一评分及 AI/传统匹配策略选取 Webhook 候选。"""
    provider_order = await load_provider_order()
    before_lines = [f"Webhook 任务: 排序前 media_type='{media_type}', 共 {len(results)} 个结果:"]
    before_lines.extend(
        f"  {i + 1}. '{item.title}' ({item.provider}, {item.type})"
        for i, item in enumerate(results[:5])
    )
    logger.info("\n".join(before_lines))

    # 库内源识别与评分属于业务策略，任务入口不再维护此流程。
    existing_source_keys = await collect_existing_source_keys(
        session, results, log_prefix="Webhook 任务:"
    )
    results = coordinator.rank_results(
        results=results,
        query_title=title,
        query_season=season,
        query_year=year,
        provider_order=provider_order,
        existing_source_keys=existing_source_keys,
        library_source_bonus=3000,
    )
    lines = [f"Webhook 任务: 排序后共 {len(results)} 个结果 (effective_year={year}, match_title='{title}'): "]
    for i, item in enumerate(results[:5]):
        title_match = "✓" if item.title.strip() == title.strip() else "✗"
        year_match = "✓" if year is not None and item.year == year else (
            "✗" if year is not None and item.year is not None else "-"
        )
        long_running = (
            item.title.strip() == title.strip()
            and year is not None and item.year is not None
            and year - item.year >= 3
        )
        long_mark = "📺" if long_running else ""
        library_mark = "📚" if f"{item.provider}:{item.mediaId}" in existing_source_keys else ""
        similarity = fuzz.token_set_ratio(title, item.title)
        year_info = f"年份: {item.year}" if item.year else "年份: 未知"
        order = provider_order.get(item.provider, 999)
        lines.append(
            f"  {i + 1}. [{item._coordinator_score}分] '{item.title}' "
            f"({item.provider}[#{order}], {item.type}, {year_info}, "
            f"年份匹配: {year_match}, 标题匹配: {title_match}, "
            f"相似度: {similarity}%) {long_mark}{library_mark}"
        )
    logger.info("\n".join(lines))

    selector = ResultSelector(
        ai_service=ai_service,
        title_recognition_manager=recognition_manager,
        config_service=config_service,
        scraper_manager=scraper_manager,
    )
    fallback_enabled = (await config_service.get("webhookFallbackEnabled", "false")).lower() == "true"
    best_match = await selector.select_best(
        candidates=results,
        query_info={"title": title, "season": season, "episode": episode, "year": year, "type": media_type},
        existing_source_keys=existing_source_keys,
        session=session,
        enable_ai=True,
        enable_traditional=True,
        enable_defer_validation=fallback_enabled,
        enable_fallback=True,
    )
    if not best_match:
        logger.warning("Webhook 任务: 未能选择合适的搜索结果")
        raise ValueError("未能找到合适的弹幕源")
    return best_match
