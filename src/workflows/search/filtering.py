"""搜索结果过滤规则（搜索领域纯能力）。

收敛 auto_import / webhook / ui.search 三处重复的两段逻辑：
    1. 按标题关键词修正媒体类型（电视剧标题含电影关键词 → 修正为 movie）。
    2. 指定季度时的「仅保留电视剧 → 按季度号过滤」。

设计约束：
    · 纯函数，仅就地修正/筛选传入的结果对象，不做 I/O、不提交任务。
    · 不导入 services/task_manager.py（遵循 search 领域分层约定）。
"""

import logging
from typing import Any, List, Tuple

from src.utils.parsing.filename_parser import is_movie_by_title

__all__ = ["correct_movie_type_by_title", "filter_by_season"]

logger = logging.getLogger(__name__)


def correct_movie_type_by_title(results: List[Any], *, log_prefix: str = "") -> List[Any]:
    """按标题关键词修正媒体类型。

    电视剧类型但标题含电影关键词（如「剧场版」「电影」）时，就地将 type 改为 'movie'。
    与 WebUI 行为保持一致。

    Args:
        results: 搜索结果列表（对象需具备 title/type 属性）。
        log_prefix: 日志前缀，便于区分调用方（如 "Control API:" / "Webhook:"）。

    Returns:
        传入的同一列表（就地修改，返回以便链式调用）。
    """
    for item in results:
        if item.type == "tv_series" and is_movie_by_title(item.title):
            prefix = f"{log_prefix} " if log_prefix else ""
            logger.info(f"{prefix}标题 '{item.title}' 包含电影关键词，类型从 'tv_series' 修正为 'movie'。")
            item.type = "movie"
    return results


def filter_by_season(results: List[Any], season: int) -> Tuple[List[Any], List[Any]]:
    """指定季度时过滤结果：仅保留电视剧类型中季度号匹配的项。

    season 为 None 或 <= 0 时视为不过滤，原样返回。

    Args:
        results: 搜索结果列表（对象需具备 type/season 属性）。
        season: 目标季度号。

    Returns:
        (kept, filtered_out)
        · kept: 保留的结果（未启用过滤时即原列表）。
        · filtered_out: 被过滤掉的结果（未启用过滤时为空）。
    """
    if not season or season <= 0:
        return results, []

    original_count = len(results)
    filtered_by_type = [item for item in results if item.type == "tv_series"]

    kept: List[Any] = []
    filtered_out: List[Any] = []
    for item in filtered_by_type:
        if item.season == season:
            kept.append(item)
        else:
            filtered_out.append(item)

    logger.info(f"根据指定的季度 ({season}) 进行过滤，从 {original_count} 个结果中保留了 {len(kept)} 个。")
    if filtered_out:
        logger.info("季度过滤结果:")
        for item in filtered_out:
            logger.info(f"  - 已过滤: {item.title} (Provider: {item.provider}, Season: {item.season})")
    for item in kept:
        logger.info(f"  - {item.title} (Provider: {item.provider}, Season: {item.season})")

    return kept, filtered_out
