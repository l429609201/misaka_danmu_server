"""搜索结果验证器（候选源可用性验证）。

职责：
    对已排序的候选列表进行实时验证（如检查是否有目标集数的弹幕），
    支持顺延策略（依次尝试直到找到可用源）。

设计约束：
    · 需要 scraper_manager 进行实时验证（有状态，非纯函数）。
    · 不提交任务，只负责验证和选择，任务提交由调用方负责。
    · 验证逻辑可被 webhook / auto_import / 后备匹配等多个入口复用。

历史背景：
    webhook 中的顺延验证逻辑（786-830行）深度耦合任务编排，
    此处抽取为可复用的验证函数，消除潜在重复。
"""

import logging
from typing import Any, List, Optional

from src.workflows.supplement_episodes import get_episodes_routed

logger = logging.getLogger(__name__)


async def validate_candidates_with_fallback(
    candidates: List[Any],
    scraper_manager: Any,
    media_type: str,
    target_episode: Optional[int] = None,
) -> Optional[Any]:
    """依次验证候选源，返回第一个通过验证的候选（顺延策略）。

    验证规则：
        - 电影：候选源类型必须为 'movie'
        - 电视剧：候选源必须有目标集数（target_episode）

    Args:
        candidates: 已排序的候选列表（按评分降序）。
        scraper_manager: 搜索源管理器（用于调用 get_episodes_routed）。
        media_type: 媒体类型（'movie' / 'tv_series'）。
        target_episode: 目标集数（电视剧场景必填，电影场景忽略）。

    Returns:
        第一个通过验证的候选，若所有候选都失败则返回 None。
    """
    logger.info(f"🔄 顺延验证: 共有 {len(candidates)} 个候选源待验证")

    for attempt, candidate in enumerate(candidates, 1):
        logger.info(
            f"→ [{attempt}/{len(candidates)}] 正在验证: {candidate.provider} - "
            f"{candidate.title} (ID: {candidate.mediaId}, 类型: {candidate.type})"
        )

        try:
            scraper = scraper_manager.get_scraper(candidate.provider)
            if not scraper:
                logger.warning(f"    {attempt}. {candidate.provider} - 无法获取scraper，跳过")
                continue

            # 获取分集列表进行验证
            episodes = await get_episodes_routed(scraper_manager,
                candidate.provider, candidate.mediaId, db_media_type=candidate.type
            )
            if not episodes:
                logger.warning(f"    {attempt}. {candidate.provider} - 没有分集列表，跳过")
                continue

            # 电影：只匹配电影类型的候选源
            if media_type == "movie":
                if candidate.type != "movie":
                    logger.warning(
                        f"    {attempt}. {candidate.provider} - 类型不匹配 "
                        f"(搜索电影，但候选源是{candidate.type})，跳过"
                    )
                    continue
                logger.info(f"    {attempt}. {candidate.provider} - 验证通过 (电影)")
                return candidate

            # 电视剧：检查是否有目标集数
            if target_episode is None:
                logger.warning(f"    {attempt}. {candidate.provider} - 目标集数未指定，跳过")
                continue

            target_ep = None
            for ep in episodes:
                if ep.episodeIndex == target_episode:
                    target_ep = ep
                    break

            if not target_ep:
                logger.warning(f"    {attempt}. {candidate.provider} - 没有第 {target_episode} 集，跳过")
                continue

            logger.info(f"    {attempt}. {candidate.provider} - 验证通过")
            return candidate

        except Exception as e:
            logger.warning(f"    {attempt}. {candidate.provider} - 验证失败: {e}")
            continue

    logger.warning("顺延验证: 所有候选源都未通过验证")
    return None
