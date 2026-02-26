"""
搜索工具模块
提供统一的搜索功能，用于后备搜索和匹配后备
"""

import asyncio
import logging
import time
from typing import List, Optional, Any, Callable, TYPE_CHECKING
from thefuzz import fuzz
from sqlalchemy.ext.asyncio import AsyncSession

if TYPE_CHECKING:
    from .scraper_manager import ScraperManager
    from .metadata_manager import MetadataSourceManager

logger = logging.getLogger(__name__)


async def unified_search(
    search_term: str,
    session: AsyncSession,
    scraper_manager: "ScraperManager",
    metadata_manager: Optional["MetadataSourceManager"] = None,
    use_alias_expansion: bool = True,
    use_alias_filtering: bool = True,
    use_title_filtering: bool = True,
    use_source_priority_sorting: bool = True,
    strict_filtering: bool = False,
    custom_aliases: Optional[set] = None,
    max_results_per_source: Optional[int] = None,
    progress_callback: Optional[Callable] = None,
    episode_info: Optional[dict] = None,
    alias_similarity_threshold: int = 75
) -> List[Any]:
    """
    统一的搜索函数，用于后备搜索和匹配后备

    Args:
        search_term: 搜索关键词
        session: 数据库会话
        scraper_manager: Scraper管理器
        metadata_manager: 元数据源管理器（用于别名扩展）
        use_alias_expansion: 是否使用别名扩展
        use_alias_filtering: 是否使用别名过滤
        use_title_filtering: 是否使用标题过滤
        use_source_priority_sorting: 是否按源优先级排序
        strict_filtering: 是否使用严格过滤（相似度>=95%或>=85%且长度差异<=30%）
        custom_aliases: 自定义别名集合（如果提供，将与扩展的别名合并）
        max_results_per_source: 每个源最多返回的结果数量（None表示不限制）
        progress_callback: 进度回调函数

    Returns:
        搜索结果列表（ScraperSearchResult对象）
    """
    if progress_callback is None:
        async def progress_callback(_progress: int, _message: str):
            pass

    # 1. 获取别名（如果启用）- 优化：并行执行别名获取和全网搜索
    filter_aliases = {search_term}  # 确保原始搜索词总是在列表中

    # 如果提供了自定义别名，先添加它们
    if custom_aliases:
        filter_aliases.update(custom_aliases)

    # 优化1: 并行执行别名获取和全网搜索
    alias_task = None
    search_task = None

    if use_alias_expansion and metadata_manager:
        await progress_callback(10, "获取别名...")

        # 优化2: 检查别名缓存
        from src.db import crud
        from src.core.cache import get_cache_backend
        from src.utils import parse_search_keyword

        # 提取核心标题（去除季度和集数信息）
        parsed = parse_search_keyword(search_term)
        core_title = parsed["title"]

        # 使用核心标题作为缓存键，这样同一剧的不同集数可以共享别名缓存
        alias_cache_key = f"search_aliases_{core_title}"
        cached_aliases = None
        _backend = get_cache_backend()
        if _backend is not None:
            try:
                cached_aliases = await _backend.get(alias_cache_key, region="search")
            except Exception as e:
                logger.warning(f"缓存后端读取失败，回退到数据库: {e}")
        if cached_aliases is None:
            cached_aliases = await crud.get_cache(session, alias_cache_key)

        if cached_aliases:
            try:
                import json
                cached_alias_list = json.loads(cached_aliases)
                # 使用 ensure_ascii=False 来正确显示中文
                aliases_display = json.dumps(cached_alias_list, ensure_ascii=False, separators=(',', ':'))
                logger.info(f"从缓存中获取'{core_title}'的别名({len(cached_alias_list)}个): {aliases_display}")

                if use_alias_filtering:
                    # 验证缓存的别名相似度（使用核心标题进行比较）
                    for alias in cached_alias_list:
                        similarity = fuzz.token_set_ratio(core_title, alias)
                        if similarity >= alias_similarity_threshold:
                            filter_aliases.add(alias)
                else:
                    filter_aliases.update(cached_alias_list)
            except Exception as e:
                logger.warning(f"解析缓存别名失败: {e}")
        else:
            # 创建别名获取任务（不等待）
            async def get_aliases():
                try:
                    from src.db import models
                    user = models.User(id=0, username="system")
                    # 使用核心标题获取别名
                    all_possible_aliases, _ = await metadata_manager.search_supplemental_sources(core_title, user)

                    # 缓存别名（1小时）
                    import json
                    alias_data = json.dumps(list(all_possible_aliases))
                    _backend = get_cache_backend()
                    if _backend is not None:
                        try:
                            await _backend.set(alias_cache_key, alias_data, ttl=3600, region="search")
                        except Exception:
                            await crud.set_cache(session, alias_cache_key, alias_data, ttl_seconds=3600)
                    else:
                        await crud.set_cache(session, alias_cache_key, alias_data, ttl_seconds=3600)
                    logger.info(f"已缓存'{core_title}'的别名: {len(all_possible_aliases)}个")

                    return all_possible_aliases
                except Exception as e:
                    logger.warning(f"获取别名失败: {e}")
                    return set()

            alias_task = asyncio.create_task(get_aliases())

    # 2. 执行全网搜索（并行）
    await progress_callback(20, "执行全网搜索...")

    # 如果没有指定max_results_per_source，从配置中读取
    if max_results_per_source is None:
        from src.db import crud
        config_value = await crud.get_config_value(session, 'searchMaxResultsPerSource', '30')
        try:
            max_results_per_source = int(config_value)
        except ValueError:
            max_results_per_source = 30
            logger.warning(f"无效的searchMaxResultsPerSource配置值: {config_value}，使用默认值30")

    # 创建搜索任务
    async def perform_search():
        return await scraper_manager.search_all([search_term], episode_info=episode_info, max_results_per_source=max_results_per_source)

    search_task = asyncio.create_task(perform_search())

    # 等待别名和搜索任务完成
    if alias_task:
        all_possible_aliases, all_results = await asyncio.gather(alias_task, search_task)

        # 验证别名相似度（使用核心标题进行比较，与获取别名时保持一致）
        if use_alias_filtering:
            validated_aliases = set()
            for alias in all_possible_aliases:
                similarity = fuzz.token_set_ratio(core_title, alias)
                if similarity >= alias_similarity_threshold:  # 相似度阈值
                    validated_aliases.add(alias)
                else:
                    logger.debug(f"别名验证：已丢弃低相似度的别名 '{alias}' (与 '{core_title}' 相比，相似度={similarity})")
            filter_aliases.update(validated_aliases)
        else:
            filter_aliases.update(all_possible_aliases)

        logger.info(f"用于过滤的别名列表: {list(filter_aliases)}")
    else:
        all_results = await search_task

    await progress_callback(40, "搜索完成...")
    logger.info(f"直接搜索完成，找到 {len(all_results)} 个原始结果。")
    
    # 3. 使用标题过滤（如果启用）
    filtered_results = all_results
    if use_title_filtering:  # 移除别名数量限制,即使只有原始搜索词也要过滤
        await progress_callback(60, "过滤搜索结果...")

        def normalize_for_filtering(title: str) -> str:
            if not title: return ""
            import re
            title = re.sub(r'[\[【(（].*?[\]】)）]', '', title)
            return title.lower().replace(" ", "").replace("：", ":").strip()

        normalized_filter_aliases = {normalize_for_filtering(alias) for alias in filter_aliases if alias}
        filtered_results = []

        # 优化：创建相似度缓存字典
        similarity_cache = {}
        cache_hits = 0
        cache_misses = 0
        skipped_by_length = 0
        skipped_by_chars = 0

        if strict_filtering:
            # 严格过滤模式（用于Webhook任务）
            for item in all_results:
                normalized_item_title = normalize_for_filtering(item.title)
                if not normalized_item_title: continue

                is_relevant = False
                for alias in normalized_filter_aliases:
                    # 优化1: 快速预过滤 - 长度差异过大直接跳过
                    length_diff = abs(len(normalized_item_title) - len(alias))
                    max_allowed_diff = max(len(alias), 20)
                    if length_diff > max_allowed_diff:
                        skipped_by_length += 1
                        continue

                    # 优化2: 快速预过滤 - 没有共同字符直接跳过
                    if not set(normalized_item_title) & set(alias):
                        skipped_by_chars += 1
                        continue

                    # 优化3: 使用缓存避免重复计算
                    cache_key = (normalized_item_title, alias)
                    if cache_key in similarity_cache:
                        similarity = similarity_cache[cache_key]
                        cache_hits += 1
                    else:
                        similarity = fuzz.partial_ratio(normalized_item_title, alias)
                        similarity_cache[cache_key] = similarity
                        cache_misses += 1

                    # 完全匹配或非常高的相似度
                    if similarity >= 95:
                        is_relevant = True
                        break
                    # 高相似度但标题长度差异不大
                    elif similarity >= 85 and length_diff <= max(len(alias) * 0.3, 10):
                        is_relevant = True
                        break

                if is_relevant:
                    filtered_results.append(item)
        else:
            # 标准过滤模式
            for item in all_results:
                normalized_item_title = normalize_for_filtering(item.title)
                if not normalized_item_title: continue

                is_relevant = False
                for alias in normalized_filter_aliases:
                    # 优化1: 快速预过滤 - 长度差异过大直接跳过
                    length_diff = abs(len(normalized_item_title) - len(alias))
                    max_allowed_diff = max(len(alias), 20)
                    if length_diff > max_allowed_diff:
                        skipped_by_length += 1
                        continue

                    # 优化2: 快速预过滤 - 没有共同字符直接跳过
                    if not set(normalized_item_title) & set(alias):
                        skipped_by_chars += 1
                        continue

                    # 优化3: 使用缓存避免重复计算
                    cache_key = (normalized_item_title, alias)
                    if cache_key in similarity_cache:
                        similarity = similarity_cache[cache_key]
                        cache_hits += 1
                    else:
                        similarity = fuzz.partial_ratio(normalized_item_title, alias)
                        similarity_cache[cache_key] = similarity
                        cache_misses += 1

                    if similarity > 85:
                        is_relevant = True
                        break

                if is_relevant:
                    filtered_results.append(item)

        # 输出优化统计信息
        total_comparisons = cache_hits + cache_misses
        if total_comparisons > 0:
            cache_hit_rate = (cache_hits / total_comparisons) * 100
            logger.info(f"相似度计算优化统计: 总计算={total_comparisons}, 缓存命中={cache_hits}({cache_hit_rate:.1f}%), "
                       f"长度跳过={skipped_by_length}, 字符跳过={skipped_by_chars}")

        logger.info(f"别名过滤: 从 {len(all_results)} 个原始结果中，保留了 {len(filtered_results)} 个相关结果。")
    
    # 4. 排序
    await progress_callback(70, "排序搜索结果...")
    
    if use_source_priority_sorting:
        # 按源优先级和相似度排序
        from src.db import crud
        source_settings = await crud.get_all_scraper_settings(session)
        source_order_map = {s['providerName']: s['displayOrder'] for s in source_settings}
        
        def sort_key(item):
            provider_order = source_order_map.get(item.provider, 999)
            similarity_score = fuzz.token_set_ratio(search_term, item.title)
            return (provider_order, -similarity_score)
        
        sorted_results = sorted(filtered_results, key=sort_key)
    else:
        # 仅按相似度排序
        sorted_results = sorted(
            filtered_results,
            key=lambda x: fuzz.token_set_ratio(search_term, x.title),
            reverse=True
        )
    
    return sorted_results

