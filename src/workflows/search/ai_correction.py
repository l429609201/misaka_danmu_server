"""搜索结果 AI 类型与季度修正编排。"""

from typing import Any, Dict, List, Optional

from src.utils.parsing.season_ai_mapping import (
    ai_type_and_season_mapping_and_correction,
)


async def correct_search_results(
    search_title: str,
    search_results: List[Any],
    *,
    metadata_manager: Any,
    ai_matcher: Any,
    logger: Any,
    similarity_threshold: float = 60.0,
    prefetched_metadata_results: Optional[Any] = None,
    prefetched_seasons_info: Optional[Any] = None,
    user: Any = None,
) -> Dict[str, Any]:
    """组合统一 AI 映射能力，返回修正结果及统计信息。

    该函数只负责搜索结果修正这一项业务编排，不承担缓存、任务提交或响应组装。
    各搜索入口可按自身策略决定是否调用，并继续处理入口专属的日志与后续流程。
    """
    return await ai_type_and_season_mapping_and_correction(
        search_title=search_title,
        search_results=search_results,
        metadata_manager=metadata_manager,
        ai_matcher=ai_matcher,
        logger=logger,
        similarity_threshold=similarity_threshold,
        prefetched_metadata_results=prefetched_metadata_results,
        prefetched_seasons_info=prefetched_seasons_info,
        user=user,
    )


__all__ = ["correct_search_results"]
