"""搜索结果选择器（统一的候选选择策略）。

职责：
    封装"AI 匹配 → 传统匹配 → 顺延验证"的完整选择流程，
    供 webhook / auto_import 复用，消除重复的选择逻辑。

设计约束：
    · 根据配置和上下文自动选择策略（AI 优先，兜底到传统匹配，最后顺延验证）。
    · 需要 session、ai_service、config_service、scraper_manager 等有状态依赖。
    · 不提交任务，只负责选择，任务提交由调用方负责。

历史背景：
    原先的 result_selector.py 只有简单的传统匹配函数，
    ai_matcher_helper.py 和 validator.py 分别处理 AI 和顺延，
    导致职责分散。现统一为单一的 ResultSelector 类。
"""

import logging
from typing import Any, Optional, Set

from thefuzz import fuzz

# 依赖均为下层搜索能力，不反向依赖选择器，统一使用顶部导入。
from src.workflows.search.ai_matcher_helper import select_best_with_ai
from src.workflows.search.library_sources import collect_favorited_source_keys
from src.workflows.search.validator import validate_candidates_with_fallback

logger = logging.getLogger(__name__)

# 传统匹配的相似度阈值
TRADITIONAL_MATCH_SIMILARITY_THRESHOLD = 70


class ResultSelector:
    """结果选择器（AI匹配 + 传统匹配 + 顺延验证）。"""

    def __init__(
        self,
        ai_service: Any,
        config_service: Any,
        scraper_manager: Any,
        title_recognition_manager: Optional[Any] = None,
    ):
        """初始化选择器。

        Args:
            ai_service: 共享 AI 服务。
            config_service: 配置服务（读取兜底配置）。
            scraper_manager: 搜索源管理器（顺延验证时需要）。
            title_recognition_manager: 识别词管理器（可选）。
        """
        self.ai_service = ai_service
        self.config_service = config_service
        self.scraper_manager = scraper_manager
        self.title_recognition_manager = title_recognition_manager

    async def select_best(
        self,
        *,
        candidates: list,
        query_title: str,
        query_season: Optional[int],
        query_episode: Optional[int],
        query_year: Optional[int],
        media_type: str,
        existing_source_keys: Set[str],
        session: Any,
        enable_ai: bool = True,
        enable_fallback: bool = True,
        enable_defer_validation: bool = False,
    ) -> Optional[Any]:
        """选择最佳候选源（三层策略）。

        策略顺序：
            1. AI 匹配（enable_ai=True 时）
            2. 传统匹配（精确标记源 > 库内源 > 第一结果验证）
            3. 顺延验证（enable_defer_validation=True 时）

        Args:
            candidates: 已排序的候选列表。
            query_title: 查询标题。
            query_season: 季度。
            query_episode: 集数。
            query_year: 年份。
            media_type: 媒体类型（'movie' / 'tv_series'）。
            existing_source_keys: 库内已有源集合。
            session: 数据库 session。
            enable_ai: 是否启用 AI 匹配。
            enable_fallback: AI 失败时是否允许兜底到传统匹配。
            enable_defer_validation: 是否启用顺延验证。

        Returns:
            选中的候选，若所有策略都失败则返回 None。

        Raises:
            ValueError: AI 匹配失败且兜底被禁用时。
        """
        if not candidates:
            logger.warning("候选列表为空，无法选择")
            return None

        # 只有开关开启且密钥有效时才进入 AI 分支，避免无效配置触发 AI 编排。
        if enable_ai and await self.ai_service.is_available():
            best_match = await self._try_ai_match(
                candidates=candidates,
                query_title=query_title,
                query_season=query_season,
                query_episode=query_episode,
                query_year=query_year,
                media_type=media_type,
                existing_source_keys=existing_source_keys,
                session=session,
                enable_fallback=enable_fallback,
            )
            if best_match is not None:
                return best_match

        # 2. 传统匹配（兜底）
        best_match = await self._try_traditional_match(
            candidates=candidates,
            query_title=query_title,
            media_type=media_type,
            existing_source_keys=existing_source_keys,
            session=session,
        )
        if best_match is not None:
            return best_match

        # 3. 顺延验证（最后手段）
        if enable_defer_validation:
            best_match = await self._try_defer_validation(
                candidates=candidates,
                media_type=media_type,
                target_episode=query_episode,
            )
            return best_match

        logger.warning("所有选择策略都失败")
        return None

    async def _try_ai_match(
        self,
        *,
        candidates: list,
        query_title: str,
        query_season: Optional[int],
        query_episode: Optional[int],
        query_year: Optional[int],
        media_type: str,
        existing_source_keys: Set[str],
        session: Any,
        enable_fallback: bool,
    ) -> Optional[Any]:
        """尝试 AI 匹配。

        Returns:
            AI 选中的候选，若失败且允许兜底则返回 None。
        """

        try:
            best_match = await select_best_with_ai(
                candidates=candidates,
                query_title=query_title,
                query_season=query_season,
                query_episode=query_episode,
                query_year=query_year,
                media_type=media_type,
                existing_source_keys=existing_source_keys,
                session=session,
                ai_service=self.ai_service,
                title_recognition_manager=self.title_recognition_manager,
                config_service=self.config_service,
                enable_fallback=enable_fallback,
            )
            return best_match
        except ValueError as e:
            # AI 匹配失败且兜底被禁用
            logger.error(f"AI 匹配失败: {e}")
            raise
        except Exception as e:
            logger.error(f"AI 匹配异常: {e}")
            return None

    async def _try_traditional_match(
        self,
        *,
        candidates: list,
        query_title: str,
        media_type: str,
        existing_source_keys: Set[str],
        session: Any,
    ) -> Optional[Any]:
        """传统匹配策略（三层优先级）。

        优先级顺序：
            1. 精确标记源（isFavorited=True）
            2. 库内已有源（已存在于 AnimeSource 表）
            3. 第一结果验证（标题相似度 >= 70）

        Returns:
            选中的候选，若所有策略都失败则返回 None。
        """

        if not candidates:
            return None

        # 1. 精确标记源（最高优先级）
        favorited_keys = await collect_favorited_source_keys(session, candidates)
        if favorited_keys:
            for candidate in candidates:
                source_key = f"{candidate.provider}:{candidate.mediaId}"
                if source_key in favorited_keys:
                    logger.info(
                        f"✓ 传统匹配: 命中精确标记源 - {candidate.provider} - {candidate.title}"
                    )
                    return candidate

        # 2. 库内已有源（第二优先级）
        if existing_source_keys:
            for candidate in candidates:
                source_key = f"{candidate.provider}:{candidate.mediaId}"
                if source_key in existing_source_keys:
                    logger.info(
                        f"✓ 传统匹配: 命中库内已有源 - {candidate.provider} - {candidate.title}"
                    )
                    return candidate

        # 3. 第一结果验证（最低优先级）
        first_candidate = candidates[0]
        similarity = fuzz.token_set_ratio(query_title, first_candidate.title)
        if similarity >= TRADITIONAL_MATCH_SIMILARITY_THRESHOLD:
            logger.info(
                f"✓ 传统匹配: 第一结果验证通过 - {first_candidate.provider} - "
                f"{first_candidate.title} (相似度: {similarity})"
            )
            return first_candidate

        logger.warning(
            f"传统匹配: 第一结果相似度不足 ({similarity} < {TRADITIONAL_MATCH_SIMILARITY_THRESHOLD})"
        )
        return None

    async def _try_defer_validation(
        self,
        *,
        candidates: list,
        media_type: str,
        target_episode: Optional[int],
    ) -> Optional[Any]:
        """顺延验证（最后手段）。

        依次验证候选源的实际可用性（分集列表、目标集数）。

        Returns:
            第一个通过验证的候选，若所有候选都失败则返回 None。
        """

        best_match = await validate_candidates_with_fallback(
            candidates=candidates,
            scraper_manager=self.scraper_manager,
            media_type=media_type,
            target_episode=target_episode,
        )

        if best_match:
            logger.info(f"✓ 顺延验证: 选中 - {best_match.provider} - {best_match.title}")
        else:
            logger.warning("顺延验证: 所有候选源都未通过验证")

        return best_match
