"""自动导入的库内源选择与下载参数准备。"""
from typing import Any, Dict, Optional

from src.services.service_container import get_database_service
from src.utils.parsing.filename_parser import parse_episode_ranges
from src.workflows.search.scoring import load_provider_order


async def prepare_library_import(
    existing: Dict[str, Any], media_type: Any, season: Optional[int],
    episode_text: str, enable_incremental_refresh: bool,
) -> Optional[Dict[str, Any]]:
    """优先使用收藏源，返回可持久化的参数快照而不提交后台任务。"""
    anime_id = existing.get("id") or existing.get("animeId")
    if not anime_id:
        raise ValueError("在已存在的作品记录中未能找到有效的ID。")
    db = get_database_service()
    async with db.transaction():
        source = await db.source.find_favorited_source_for_anime(anime_id)
        sources = [] if source else await db.source.get_anime_sources(anime_id)
    if not source and sources:
        order = await load_provider_order()
        source = min(sources, key=lambda item: order.get(item["providerName"], 999))
    if not source:
        return None
    # 与库内去重一致，电影未显式指定季度时使用第一季。
    target_season = 1 if media_type == "movie" and season is None else season
    title = f"自动导入 (库内): {existing['title']}"
    unique_key = f"import-{source['providerName']}-{source['mediaId']}"
    if target_season is not None:
        title += f" S{target_season:02d}"
        unique_key += f"-s{target_season}"
    parameters = {
        "provider": source["providerName"], "mediaId": source["mediaId"],
        "animeTitle": existing["title"], "mediaType": existing.get("type", "tv_series"),
        "season": target_season, "year": existing.get("year"),
        "currentEpisodeIndex": None, "selectedEpisodes": parse_episode_ranges(episode_text),
        "imageUrl": existing.get("imageUrl"), "preassignedAnimeId": anime_id,
        "enableIncrementalRefresh": enable_incremental_refresh,
    }
    for key in ("doubanId", "tmdbId", "imdbId", "tvdbId", "bangumiId"):
        parameters[key] = existing.get(key)
    return {"title": f"{title} E{episode_text}",
            "unique_key": f"{unique_key}-e{episode_text}", "parameters": parameters}
