"""搜索协调器（编排：搜索 → 过滤 → 评分排序）。

职责：
    封装"统一搜索 → 类型修正 → 季度过滤 → 评分排序"的标准流程，
    供 auto_import / webhook / 后备匹配等多个入口复用，消除重复编排逻辑。

设计约束：
    · 不包含元数据获取、识别词预处理、AI匹配等高级能力（由调用方决定是否启用）。
    · 不包含 timer/profiler/progress_callback 等执行时装饰（由调用方自行包装）。
    · 纯业务逻辑编排，依赖已抽取的 search 包纯函数（scoring/filtering/engine）。
"""

import logging
from typing import Any, Dict, List, Optional, Set

# why: engine 与 coordinator 同属 workflows.search 领域内，直接顶层导入具体模块
#      （不经 src.workflows.search 包 __init__）即可，无循环依赖。
from src.workflows.search.filtering import correct_movie_type_by_title, filter_by_season
from src.workflows.search.scoring import compute_score
from src.workflows.search.engine import unified_search

__all__ = ["SearchCoordinator"]

logger = logging.getLogger(__name__)


class SearchCoordinator:
    """搜索协调器（编排核心搜索流程）。

    将 auto_import / webhook / 后备匹配中重复的"搜索→过滤→排序"流程
    统一封装为可复用组件，减少三处各约 50 行的重复逻辑。
    """

    def __init__(
        self,
        scraper_manager: Any,  # ScraperManager
        metadata_manager: Optional[Any] = None,  # MetadataService
        user: Optional[Any] = None,  # User
    ):
        """初始化协调器。

        Args:
            scraper_manager: 搜索源管理器（提供 unified_search 所需依赖）。
            metadata_manager: 元数据管理器（unified_search 的可选依赖）。
            user: 用户对象（可选，某些搜索源需要用户上下文）。
        """
        self.scraper_manager = scraper_manager
        self.metadata_manager = metadata_manager
        self.user = user

    async def search_only(
        self,
        *,
        session: Any,  # AsyncSession
        search_term: str,
        season: Optional[int] = None,
        episode: Optional[int] = None,
        progress_callback: Optional[Any] = None,
    ) -> List[Any]:
        """仅执行搜索，不进行过滤和排序（供需要中间插入AI映射的场景使用）。

        Args:
            session: 数据库 session。
            search_term: 搜索关键词。
            season: 季度号（传给 episode_info）。
            episode: 集数（传给 episode_info）。
            progress_callback: 进度回调（可选）。

        Returns:
            原始搜索结果列表（未过滤、未排序）。
        """
        all_results = await unified_search(
            search_term=search_term,
            session=session,
            scraper_manager=self.scraper_manager,
            metadata_manager=self.metadata_manager,
            use_alias_expansion=True,
            use_alias_filtering=True,
            use_title_filtering=True,
            use_source_priority_sorting=True,
            progress_callback=progress_callback,
            episode_info={"season": season, "episode": episode} if season or episode else None,
            alias_similarity_threshold=70,
        )

        if not all_results:
            logger.warning(f"统一搜索 '{search_term}' 未找到任何结果")
            return []

        logger.info(f"统一搜索返回 {len(all_results)} 个结果")
        return all_results

    async def search_and_rank(
        self,
        *,
        session: Any,  # AsyncSession
        search_term: str,
        season: Optional[int] = None,
        episode: Optional[int] = None,
        year: Optional[int] = None,
        provider_order: Optional[Dict[str, int]] = None,
        existing_source_keys: Optional[Set[str]] = None,
        library_source_bonus: float = 0.0,
        progress_callback: Optional[Any] = None,
    ) -> List[Any]:
        """执行搜索并返回评分排序后的结果列表（有状态主入口）。

        流程：
            1. 统一搜索（调用 search/engine.py 的 unified_search）
            2. 类型修正 + 季度过滤 + 评分排序（委托 rank_results）

        Args:
            session: 数据库 session（unified_search 需要用于别名缓存查询）。
            search_term: 搜索关键词（单个字符串，与 unified_search 签名对齐）。
            season: 季度号（电视剧场景传入）。
            episode: 集数（可选，某些源需要）。
            year: 年份（可选，用于评分）。
            provider_order: 源优先级映射（{provider: displayOrder}）。
            existing_source_keys: 库内已有源集合（webhook 场景传入，用于加分）。
            library_source_bonus: 库内源加分（webhook 场景传入，auto_import 为 0）。
            progress_callback: 进度回调（可选）。

        Returns:
            评分排序后的搜索结果列表（降序，分数高的在前）。
        """


        all_results = await unified_search(
            search_term=search_term,
            session=session,
            scraper_manager=self.scraper_manager,
            metadata_manager=self.metadata_manager,
            use_alias_expansion=True,
            use_alias_filtering=True,
            use_title_filtering=True,
            use_source_priority_sorting=True,
            progress_callback=progress_callback,
            episode_info={"season": season, "episode": episode} if season or episode else None,
            alias_similarity_threshold=70,
        )

        if not all_results:
            logger.warning(f"统一搜索 '{search_term}' 未找到任何结果")
            return []

        logger.info(f"统一搜索返回 {len(all_results)} 个结果")

        # 2. 过滤 + 评分排序（委托无状态纯函数）
        return self.rank_results(
            results=all_results,
            query_title=search_term,
            query_season=season,
            query_year=year,
            provider_order=provider_order,
            existing_source_keys=existing_source_keys,
            library_source_bonus=library_source_bonus,
        )

    @staticmethod
    def rank_results(
        results: List[Any],
        query_title: str,
        query_season: Optional[int] = None,
        query_year: Optional[int] = None,
        provider_order: Optional[Dict[str, int]] = None,
        existing_source_keys: Optional[Set[str]] = None,
        library_source_bonus: float = 0.0,
    ) -> List[Any]:
        """对搜索结果进行过滤和评分排序（无状态纯函数）。

        职责：类型修正 → 季度过滤 → 评分 → 排序。
        可被其他场景复用（如已有搜索结果只需重新排序）。

        Args:
            results: 待处理的搜索结果列表。
            query_title: 查询主标题（用于相似度计算）。
            query_season: 期望季度（用于过滤和评分）。
            query_year: 期望年份（用于评分）。
            provider_order: 源优先级映射（{provider: displayOrder}）。
            existing_source_keys: 库内已有源集合。
            library_source_bonus: 库内源加分。

        Returns:
            评分排序后的结果列表（降序，就地修改原列表）。
        """
        if not results:
            return results

        # 1. 类型修正（委托 search/filtering.py）
        correct_movie_type_by_title(results, log_prefix="SearchCoordinator:")

        # 2. 季度过滤（委托 search/filtering.py）
        if query_season and query_season > 0:
            results, _ = filter_by_season(results, query_season)

        if not results:
            logger.warning("过滤后无结果")
            return []

        # 3. 评分（委托 search/scoring.py）
        for item in results:
            item._coordinator_score = compute_score(
                item=item,
                query_title=query_title,
                query_season=query_season,
                query_year=query_year,
                provider_order=provider_order or {},
                existing_source_keys=existing_source_keys or set(),
                library_source_bonus=library_source_bonus,
            )

        # 4. 排序
        results.sort(key=lambda x: x._coordinator_score, reverse=True)

        logger.info(f"评分排序完成，前3: {[r.title for r in results[:3]]}")
        return results
