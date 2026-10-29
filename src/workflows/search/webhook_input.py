"""Webhook 搜索输入准备，收藏源与普通搜索共用一次预处理。"""
from typing import Any, Optional

from src.schemas import User
from src.utils import parse_search_keyword
from src.utils.parsing.filename_parser import parse_filename
from src.utils.data_processing.name_converter import convert_to_chinese_title


async def prepare_webhook_search(
    keyword: str,
    season: int,
    episode: Optional[int],
    selected_episodes: Optional[list[int]],
    config_service: Any,
    metadata_manager: Any,
    ai_service: Any,
    recognition_manager: Any,
) -> tuple[str, Optional[int], int, Optional[list[int]]]:
    """解析并转换 Webhook 输入，避免收藏源绕过规则或搜索分支重复偏移。"""
    parsed = parse_filename(keyword)
    if parsed is not None:
        title = parsed.title
        parsed_season = parsed.season
        parsed_episode = parsed.episode
    else:
        fallback = parse_search_keyword(keyword)
        title = fallback["title"]
        parsed_season = fallback.get("season")
        parsed_episode = fallback.get("episode")
    input_season = parsed_season if parsed_season is not None else season
    input_episode = parsed_episode if parsed_episode is not None else episode
    converted, applied = await convert_to_chinese_title(
        title, config_service, metadata_manager, ai_service,
        User(id=0, username="webhook"),
    )
    if applied:
        title = converted
    if not title:
        raise ValueError("Webhook 搜索标题为空")
    search_title, search_episode, search_season = title, input_episode, input_season
    if recognition_manager:
        search_title, search_episode, search_season, _ = (
            await recognition_manager.apply_search_preprocessing(title, input_episode, input_season)
        )
        if selected_episodes is not None:
            mapped = []
            for index in selected_episodes:
                _, mapped_episode, mapped_season, _ = (
                    await recognition_manager.apply_search_preprocessing(title, index, input_season)
                )
                if mapped_season != search_season:
                    raise ValueError("多集预处理结果跨季度，无法作为同一导入任务执行")
                if mapped_episode is not None:
                    mapped.append(mapped_episode)
            selected_episodes = sorted(set(mapped))
    if not search_title or search_season is None:
        raise ValueError("Webhook 预处理后的标题或季度无效")
    return search_title, search_episode, search_season, selected_episodes
