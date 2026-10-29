"""导入集号编排：将媒体库集号还原为源站集号。"""
import logging
from typing import Any, List, Optional, Tuple

logger = logging.getLogger(__name__)


async def resolve_source_episode_indices(
    title: str,
    provider: str,
    current_episode_index: Optional[int],
    selected_episodes: Optional[List[int]],
    title_recognition_manager: Any,
) -> Tuple[Optional[int], Optional[List[int]]]:
    """反向应用识别词偏移；单个集号转换失败时保留原值。"""
    source_index = current_episode_index
    source_selected = selected_episodes
    if not title_recognition_manager or not title:
        return source_index, source_selected

    if current_episode_index is not None:
        try:
            source_index = await title_recognition_manager.reverse_episode_offset(
                title, current_episode_index, provider,
            )
            if source_index != current_episode_index:
                logger.info("反向偏移: '%s' 媒体库第%s集 => 源站第%s集",
                            title, current_episode_index, source_index)
        except Exception as exc:
            logger.warning("反向偏移失败，使用原始集数: %s", exc)

    if selected_episodes is not None:
        reversed_selected = []
        for index in selected_episodes:
            if index is None:
                reversed_selected.append(index)
                continue
            try:
                reversed_index = await title_recognition_manager.reverse_episode_offset(
                    title, index, provider,
                )
                reversed_selected.append(reversed_index)
            except Exception as exc:
                logger.warning("反向偏移分集 %s 失败，使用原始值: %s", index, exc)
                reversed_selected.append(index)
        if reversed_selected != list(selected_episodes):
            logger.info("指定分集反向偏移: %s => %s", selected_episodes, reversed_selected)
            source_selected = reversed_selected
    return source_index, source_selected
