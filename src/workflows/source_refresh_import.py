"""已有源补充导入的参数准备，独立事务不占用任务会话。"""
from typing import Any, Dict, List, Optional

from src.services.service_container import get_database_service
from src.utils.diagnostics.task_exceptions import TaskFailed, TaskSuccess


async def prepare_source_refresh_import(
    source_id: int, title: str, episode_index: Optional[int],
) -> Dict[str, Any]:
    """准备增量刷新或缺集补全的通用导入参数。"""
    db = get_database_service()
    async with db.transaction():
        source = await db.source.get_anime_source_info(source_id)
    if not source:
        raise TaskFailed(f"刷新失败：找不到源 ID {source_id} 的信息")
    return {
        "provider": source["providerName"], "mediaId": source["mediaId"],
        "animeTitle": title, "mediaType": source["type"],
        "season": source.get("season", 1), "year": source.get("year"),
        "currentEpisodeIndex": episode_index, "imageUrl": source.get("imageUrl"),
        "doubanId": None, "tmdbId": source.get("tmdbId"),
        "imdbId": None, "tvdbId": None, "bangumiId": source.get("bangumiId"),
    }


async def select_missing_source_episodes(
    source_id: int, episodes: List[Any], title: str,
    title_recognition_manager: Any,
) -> List[Any]:
    """按源站 ID 和实际存储集号排除已收录分集，不以弹幕数量判断缺集。"""
    db = get_database_service()
    async with db.transaction():
        stored = await db.source.get_episodes_for_source(source_id)
        existing_ids = {ep.providerEpisodeId for ep in stored if ep.providerEpisodeId}
        existing_indices = {ep.episodeIndex for ep in stored}

    missing = []
    for episode in episodes:
        if episode.episodeId and episode.episodeId in existing_ids:
            continue
        stored_index = episode.episodeIndex
        if title_recognition_manager:
            _, _, _, _, converted = await title_recognition_manager.apply_storage_postprocessing(
                title, episode=stored_index,
            )
            if converted is not None:
                stored_index = converted
        if stored_index not in existing_indices:
            missing.append(episode)
    if not missing:
        raise TaskSuccess("补全缺集完成，该源没有缺失分集，未刷新已有分集。")
    return missing
