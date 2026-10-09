"""
搜索执行与结果匹配层

职责（阶段4新增）：
1. 调用 unified_search 执行全网搜索
2. 库内源优先级评分（+3000分加成）
3. 候选源验证（调用阶段3的 validate_candidate_sources）
4. 结果排序与过滤

设计原则：
- 封装搜索+匹配的核心逻辑，输出 SearchResult 契约
- 复用 unified_search + validate_candidate_sources
- 不提交任务、不写业务表（AnimeSource 等）

约束：
- 不导入 tasks 层（避免循环依赖）
"""

import logging
import time
from typing import List, Optional, Dict, Any

from sqlalchemy.ext.asyncio import AsyncSession

from src.schemas.search_flow import SearchResult, PreparedSearch
from src.workflows.search import unified_search

from src.services.scraper_manager import ScraperManager
from src.services.metadata_service import MetadataService

logger = logging.getLogger(__name__)


async def execute_search_and_match(
    prepared: PreparedSearch,
    session: AsyncSession,
    scraper_manager: "ScraperManager",
    metadata_manager: Optional["MetadataService"] = None,
    progress_callback: Optional[callable] = None,
) -> SearchResult:
    """
    执行搜索并匹配候选源。

    Args:
        prepared: 调用方提供的 PreparedSearch 搜索参数契约
        session: 数据库会话
        scraper_manager: 爬虫管理器
        metadata_manager: 元数据管理器（可选）
        progress_callback: 进度回调（可选）

    Returns:
        SearchResult: 搜索结果契约（包含匹配的候选源列表）
    """
    # 编排归属工作流层，计时依赖在模块顶部声明。
    start_time = time.time()

    # 调用统一搜索（复用现有 unified_search）
    search_results = await unified_search(
        search_term=prepared.search_title,
        session=session,
        scraper_manager=scraper_manager,
        metadata_manager=metadata_manager,
        progress_callback=progress_callback,
        use_alias_expansion=True,
        use_source_priority_sorting=True,
    )

    search_duration_ms = (time.time() - start_time) * 1000

    if not search_results:
        logger.info(f"搜索未返回结果: '{prepared.search_title}'")
        return SearchResult(
            candidates=[],
            total_results_count=0,
            filtered_results_count=0,
            search_duration_ms=search_duration_ms,
        )

    logger.info(f"搜索返回 {len(search_results)} 个结果")

    # TODO: 库内源优先级加成（+3000分）
    # TODO: 候选源验证（调用 validate_candidate_sources）
    # TODO: 结果排序与过滤

    # 将搜索结果转换为字典列表（SearchResult.candidates 字段类型）
    candidates = []
    for result in search_results:
        if hasattr(result, '__dict__'):
            candidates.append(result.__dict__)
        else:
            candidates.append(result)

    return SearchResult(
        candidates=candidates,
        total_results_count=len(search_results),
        filtered_results_count=len(candidates),
        search_duration_ms=search_duration_ms,
    )
