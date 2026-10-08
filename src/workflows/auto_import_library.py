"""自动导入的库内去重判定，不持有任务长会话。"""
from typing import Any, Dict, Optional, Tuple

from src.services.service_container import get_database_service
from src.utils.parsing.filename_parser import parse_episode_ranges


async def check_auto_import_library(
    search_type: Any, search_term: str, main_title: str, media_type: Any,
    season: Optional[int], year: Optional[int], episode_text: Optional[str],
    recognition_manager: Any,
) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    """返回已有作品与可提前结束的原因，保留单集缺失时继续导入的策略。"""
    db = get_database_service()
    existing = None
    season_for_check = 1 if media_type == "movie" and season is None else season
    columns = {"tmdb": "tmdbId", "tvdb": "tvdbId", "imdb": "imdbId",
               "douban": "doubanId", "bangumi": "bangumiId"}
    if search_type != "keyword" and season is not None:
        column = columns.get(search_type.value)
        if column:
            async with db.transaction():
                existing = await db.anime.find_by_metadata_id_and_season(column, search_term, season)
    if not existing:
        async with db.transaction():
            existing = await db.anime.find_by_title_season_year_with_recognition(
                main_title, season_for_check, year, recognition_manager, None,
            )
    if not existing:
        return None, None
    if episode_text is None:
        return existing, f"作品 '{main_title}' 已在媒体库中，无需重复导入整季。"
    anime_id = existing.get("id") or existing.get("animeId")
    if not anime_id:
        raise ValueError("在已存在的作品记录中未能找到有效的ID。")
    requested = parse_episode_ranges(episode_text)
    for index in requested:
        converted_index = index
        if recognition_manager:
            _, converted, _, _, _ = await recognition_manager.apply_title_recognition(
                main_title, index, season_for_check,
            )
            if converted is not None:
                converted_index = converted
        async with db.transaction():
            found = await db.episode.find_by_anime_id_and_index(anime_id, converted_index)
        if not found:
            return existing, None
    return existing, f"作品 '{main_title}' 的所有请求集数 {requested} 已在媒体库中，无需重复导入。"
