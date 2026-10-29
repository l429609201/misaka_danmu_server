"""整季导入分集选择与库内去重编排。"""
import logging
from typing import Any, List, Optional

from src.services.service_container import get_database_service
from src.utils.diagnostics.task_exceptions import TaskSuccess
from src.utils.parsing.filename_parser import format_episode_ranges
from src.workflows.import_candidates import ImportCandidateRejected

logger = logging.getLogger(__name__)


async def select_import_episodes(
    episodes: List[Any], selected: List[int], source_selected: List[int],
    provider: str, media_id: str, season: int, tmdb_id: Optional[str],
) -> List[Any]:
    """优先集号匹配，再尝试剧集组映射，最后沿用数量截取策略。"""
    source_set = {index for index in source_selected if index is not None}
    original_set = {index for index in selected if index is not None}
    original_count = len(episodes)
    filtered = [ep for ep in episodes if ep.episodeIndex in source_set]
    db = get_database_service()
    if filtered:
        episodes = filtered
    else:
        translated_set = set()
        if original_set and season and tmdb_id:
            try:
                async with db.transaction():
                    group_id = await db.tmdb.get_episode_group_id_by_tmdb_id(str(tmdb_id))
                    translation = await db.tmdb.get_episode_equivalence_batch(
                        group_id, season, list(original_set),
                    ) if group_id else {}
                translated_set = set(translation.values())
            except Exception as exc:
                logger.warning("剧集组等价映射查询异常，降级为数量截取: %s", exc)
        if translated_set:
            filtered = [ep for ep in episodes if ep.episodeIndex in translated_set]
            episodes = filtered if filtered else episodes[:len(original_set)]
        else:
            limit = len(original_set) if original_set else original_count
            episodes = episodes[:limit]
        if not episodes:
            raise ImportCandidateRejected("源中没有任何分集，未导入新的弹幕。")
    logger.info("媒体库整季导入: 源共有 %s 集，筛选保留 %s 集", original_count, len(episodes))

    indices = [ep.episodeIndex for ep in episodes if ep.episodeIndex is not None]
    existing = []
    if indices:
        async with db.transaction():
            existing = await db.episode.get_existing_danmaku_indices(provider, media_id, indices)
    if indices and set(indices).issubset(set(existing)):
        ranges = format_episode_ranges(sorted(existing), separator=", ")
        raise TaskSuccess(f"导入完成，跳过集: < {ranges} > (已有弹幕)，未新增弹幕。")
    if existing:
        # 首集验证优先选择尚无弹幕的集，已有分集留给保存层判断是否增加。
        existing_set = set(existing)
        episodes = ([ep for ep in episodes if ep.episodeIndex not in existing_set]
                    + [ep for ep in episodes if ep.episodeIndex in existing_set])
    return episodes
