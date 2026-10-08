"""AI 匹配辅助器（封装 AI 匹配调用的标准流程）。

职责：
    封装"构建查询信息 → 收集上下文 → 调用 AI 匹配 → 处理兜底"的标准流程，
    供 webhook / auto_import 复用，消除重复的 AI 匹配调用逻辑。

设计约束：
    · 共享 ai_service，候选上下文与兜底由本编排处理。
    · 不提交任务，只负责匹配和选择，任务提交由调用方负责。
    · 返回选中的候选或 None（兜底到传统匹配）。
"""

import logging
from typing import Any, Dict, List, Optional, Set

# 库内源能力不依赖 AI 辅助器，无需通过延迟导入规避依赖。
from src.workflows.search.library_sources import collect_favorited_source_keys

logger = logging.getLogger(__name__)


async def select_best_with_ai(
    *,
    candidates: List[Any],
    query_title: str,
    query_season: Optional[int],
    query_episode: Optional[int],
    query_year: Optional[int],
    media_type: str,
    existing_source_keys: Set[str],
    session: Any,  # AsyncSession
    ai_service: Any,
    title_recognition_manager: Optional[Any],
    config_service: Any,
    enable_fallback: bool = True,
) -> Optional[Any]:
    """使用 AI 匹配器选择最佳候选源。

    流程：
        1. 构建查询信息（query_info）
        2. 收集收藏源、库内源、识别词上下文
        3. 调用 AI 匹配器
        4. 处理结果：成功返回候选，失败根据兜底配置决定返回 None 或抛异常

    Args:
        candidates: 已排序的候选列表。
        query_title: 查询标题（应为名称转换后的标题）。
        query_season: 季度（电视剧场景）。
        query_episode: 集数。
        query_year: 年份。
        media_type: 媒体类型（'movie' / 'tv_series'）。
        existing_source_keys: 库内已有源集合。
        session: 数据库 session（查询收藏源）。
        ai_service: 共享 AI 服务。
        title_recognition_manager: 识别词管理器（可选）。
        config_service: 配置管理器（读取 aiFallbackEnabled）。
        enable_fallback: 是否允许兜底到传统匹配（默认 True）。

    Returns:
        AI 选中的候选，若匹配失败且允许兜底则返回 None，否则抛出 ValueError。

    Raises:
        ValueError: AI 匹配失败且兜底被禁用时。
    """

    # 不可用时不收集 AI 上下文，但必须保留禁用传统兜底时的失败语义。
    if not await ai_service.is_available():
        ai_fallback_enabled = (
            (await config_service.get("aiFallbackEnabled", "true")).lower() == "true"
            if enable_fallback else False
        )
        if ai_fallback_enabled:
            logger.info("AI 不可用，降级到传统匹配")
            return None
        raise ValueError("AI 不可用且传统匹配兜底已禁用")

    # 1. 构建查询信息
    query_info = {
        'title': query_title,
        'season': query_season if media_type == 'tv_series' else None,
        'episode': query_episode,
        'year': query_year,
        'type': media_type
    }

    # 2. 收集上下文信息
    favorited_keys = await collect_favorited_source_keys(session, candidates)
    favorited_info = {key: True for key in favorited_keys}
    existing_info = {key: True for key in existing_source_keys}

    # 3. 识别词认知校正上下文
    recognition_info, recognition_hint = (
        await title_recognition_manager.build_recognition_context_for_results(candidates)
        if title_recognition_manager else ({}, None)
    )
    if recognition_hint:
        query_info["recognition_hint"] = recognition_hint

    # 4. 调用 AI 匹配器
    try:
        ai_selected_index = await ai_service.select_best_match(
            query_info, candidates, favorited_info, existing_info, recognition_info
        )

        if ai_selected_index is not None:
            best_match = candidates[ai_selected_index]
            logger.info(f"AI 匹配成功选择: {best_match.provider} - {best_match.title}")
            return best_match
        else:
            # AI 未找到合适结果
            ai_fallback_enabled = (
                (await config_service.get("aiFallbackEnabled", "true")).lower() == 'true'
                if enable_fallback else False
            )
            if ai_fallback_enabled:
                logger.info("AI 匹配未找到合适结果，降级到传统匹配")
                return None
            else:
                logger.warning("AI 匹配未找到合适结果，且传统匹配兜底已禁用")
                raise ValueError("AI 匹配失败且传统匹配兜底已禁用")

    except ValueError:
        # ValueError 是预期的兜底禁用异常，直接抛出
        raise
    except Exception as e:
        # 其他异常视为匹配失败，检查兜底配置
        ai_fallback_enabled = (
            (await config_service.get("aiFallbackEnabled", "true")).lower() == 'true'
            if enable_fallback else False
        )
        if ai_fallback_enabled:
            logger.error(f"AI 匹配失败，降级到传统匹配: {e}")
            return None
        else:
            logger.error(f"AI 匹配失败，且传统匹配兜底已禁用: {e}")
            raise ValueError(f"AI 匹配失败且传统匹配兜底已禁用: {e}")
