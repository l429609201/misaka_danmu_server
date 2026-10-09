"""通用导入分集获取：源站集号、过滤规则与补充源协调。"""
import logging
from typing import Any, Callable, Dict, List, Optional, Tuple

from src.utils.parsing.episode_filter import get_and_apply_single_episode_filter
from src.workflows.import_episode_indices import resolve_source_episode_indices
from src.workflows.supplement_episodes import fetch_supplement_episodes

from src.workflows.supplement_episodes import get_episodes_routed

logger = logging.getLogger(__name__)


async def fetch_import_episodes(
    parameters: Dict[str, Any], manager: Any, scraper: Any,
    config_service: Any, metadata_manager: Any, title_recognition_manager: Any,
    progress_callback: Callable, profiler: Any,
) -> Tuple[List[Any], Optional[List[int]]]:
    """按源站集号获取分集，保持先过滤主源、再尝试补充源的顺序。"""
    provider = parameters["provider"]
    title = parameters["animeTitle"]
    season = parameters["season"]
    media_id = parameters["mediaId"]
    selected = parameters.get("selectedEpisodes")
    await progress_callback(10, "正在获取分集列表...")
    source_index, source_selected = await resolve_source_episode_indices(
        title, provider, parameters.get("currentEpisodeIndex"), selected,
        title_recognition_manager,
    )
    async with profiler.step("获取分集列表"):
        episodes = await get_episodes_routed(manager,
            provider, media_id,
            target_episode_index=None if selected is not None else source_index,
            db_media_type=parameters["mediaType"],
        )
    if episodes:
        extra_titles = []
        if title_recognition_manager:
            try:
                converted, _, changed, _, _ = await title_recognition_manager.apply_storage_postprocessing(
                    title, season, provider,
                )
                if changed and converted and converted != title:
                    extra_titles.append(converted)
            except Exception as exc:
                logger.warning("单剧过滤识别词转换失败，仅用原名匹配: %s", exc)
        episodes = await get_and_apply_single_episode_filter(
            episodes, config_service, title, provider, media_id, extra_titles=extra_titles,
        )
    if not episodes and parameters.get("supplementProvider") and parameters.get("supplementMediaId"):
        episodes = await fetch_supplement_episodes(
            provider, parameters["supplementProvider"], parameters["supplementMediaId"],
            metadata_manager, scraper, progress_callback,
        )
    return episodes, source_selected
