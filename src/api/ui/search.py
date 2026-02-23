"""
Search相关的API端点
"""
import asyncio
import logging
import re
from typing import Optional, List

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy.ext.asyncio import AsyncSession
from thefuzz import fuzz

from src import security
from src.db import crud, models, get_db_session, ConfigManager
from src.services import ScraperManager, MetadataSourceManager, TitleRecognitionManager, convert_to_chinese_title
from src.utils import (
    parse_search_keyword, ai_type_and_season_mapping_and_correction,
    SearchTimer, SEARCH_TYPE_HOME, is_movie_by_title,
)
from src.ai.ai_matcher_manager import AIMatcherManager

from src.api.dependencies import (
    get_scraper_manager, get_metadata_manager, get_config_manager,
    get_title_recognition_manager, get_ai_matcher_manager
)
from .models import UIProviderSearchResponse

logger = logging.getLogger(__name__)

router = APIRouter()


def _extract_filter_metadata(items) -> dict:
    """从全量结果中提取可用的过滤元数据（年份、来源、类型）"""
    years = set()
    providers = set()
    types = set()
    for item in items:
        year = item.year if hasattr(item, 'year') else item.get('year')
        provider = item.provider if hasattr(item, 'provider') else item.get('provider')
        item_type = item.type if hasattr(item, 'type') else item.get('type')
        if year:
            years.add(year)
        if provider:
            providers.add(provider)
        if item_type:
            types.add(item_type)
    return {
        'available_years': sorted(years, reverse=True),
        'available_providers': sorted(providers),
        'available_types': sorted(types),
    }

@router.get(
    "/search/anime",
    response_model=models.AnimeSearchResponse,
    summary="搜索本地数据库中的节目信息",
)
async def search_anime_local(
    keyword: str = Query(..., min_length=1, description="搜索关键词"),
    session: AsyncSession = Depends(get_db_session)
):
    db_results = await crud.search_anime(session, keyword)
    animes = [
        models.AnimeInfo(animeId=item["id"], animeTitle=item["title"], type=item["type"])
        for item in db_results
    ]
    return models.AnimeSearchResponse(animes=animes)

@router.get("/search/provider", response_model=UIProviderSearchResponse, summary="从外部数据源搜索节目")
async def search_anime_provider(
    request: Request,
    keyword: str = Query(..., min_length=1, description="搜索关键词"),
    page: int = Query(1, ge=1, description="页码，从1开始"),
    pageSize: int = Query(10, ge=10, le=100, description="每页数量，10-100"),
    typeFilter: Optional[str] = Query(None, description="类型过滤: tv_series, movie"),
    yearFilter: Optional[int] = Query(None, description="年份过滤"),
    providerFilter: Optional[str] = Query(None, description="来源过滤: bilibili, tencent等"),
    titleFilter: Optional[str] = Query(None, description="标题关键词过滤"),
    manager: ScraperManager = Depends(get_scraper_manager),
    current_user: models.User = Depends(security.get_current_user),
    session: AsyncSession = Depends(get_db_session),
    metadata_manager: MetadataSourceManager = Depends(get_metadata_manager),
    title_recognition_manager: TitleRecognitionManager = Depends(get_title_recognition_manager),
    config_manager: ConfigManager = Depends(get_config_manager),
    ai_matcher_manager: AIMatcherManager = Depends(get_ai_matcher_manager)
):
    """
    从所有已配置的数据源（如腾讯、B站等）搜索节目信息。
    此接口实现了智能的按季缓存机制，并保留了原有的别名搜索、过滤和排序逻辑。
    """
    # 🚀 V2.1.6: 创建搜索计时器
    timer = SearchTimer(SEARCH_TYPE_HOME, keyword, logger)
    timer.start()

    try:
        timer.step_start("关键词解析")
        parsed_keyword = parse_search_keyword(keyword)
        original_title = parsed_keyword["title"]
        season_to_filter = parsed_keyword["season"]
        episode_to_filter = parsed_keyword["episode"]
        timer.step_end()

        # 🚀 名称转换功能 - 检测非中文标题并尝试转换为中文（在所有处理之前执行）
        timer.step_start("名称转换")
        converted_original_title, conversion_applied = await convert_to_chinese_title(
            original_title,
            config_manager,
            metadata_manager,
            ai_matcher_manager,
            current_user
        )
        timer.step_end()

        # 应用搜索预处理规则
        timer.step_start("预处理规则应用")
        search_title = converted_original_title  # 使用转换后的标题作为基础
        search_season = season_to_filter
        if title_recognition_manager:
            processed_title, processed_episode, processed_season, preprocessing_applied = await title_recognition_manager.apply_search_preprocessing(converted_original_title, episode_to_filter, season_to_filter)
            if preprocessing_applied:
                search_title = processed_title
                logger.info(f"✓ WebUI搜索预处理: '{converted_original_title}' -> '{search_title}'")
                # 如果集数发生了变化，更新episode_to_filter
                if processed_episode != episode_to_filter:
                    episode_to_filter = processed_episode
                    logger.info(f"✓ WebUI集数预处理: {parsed_keyword['episode']} -> {episode_to_filter}")
                # 如果季数发生了变化，更新season_to_filter
                if processed_season != season_to_filter:
                    search_season = processed_season
                    season_to_filter = processed_season
                    logger.info(f"✓ WebUI季度预处理: {parsed_keyword['season']} -> {season_to_filter}")
            else:
                logger.info(f"○ WebUI搜索预处理未生效: '{converted_original_title}'")
        timer.step_end()

        # 🚀 新增：季度名称映射 - 如果指定了季度，尝试获取该季度的实际名称
        # 例如：搜索 "唐朝诡事录 S03" 时，通过TMDB查询第3季的实际名称 "唐朝诡事录之西行"
        season_mapped_title = None
        if season_to_filter is not None and season_to_filter > 0:
            timer.step_start("季度名称映射")
            try:
                # 获取AI匹配器（如果可用）
                ai_matcher_for_season = await ai_matcher_manager.get_matcher() if ai_matcher_manager else None
                # 通过元数据源获取季度名称
                season_name = await metadata_manager.season_mapper.get_season_name(
                    search_title,
                    season_to_filter,
                    ai_matcher=ai_matcher_for_season,
                    user=current_user
                )
                if season_name:
                    season_mapped_title = season_name
                    logger.info(f"✓ 季度名称映射: '{search_title}' S{season_to_filter:02d} → '{season_mapped_title}'")
                else:
                    logger.info(f"○ 季度名称映射未找到: '{search_title}' S{season_to_filter:02d}")
            except Exception as e:
                logger.warning(f"季度名称映射失败: {e}")
            timer.step_end()

        # --- 新增：按季缓存逻辑 ---
        timer.step_start("缓存检查")
        # 缓存键基于核心标题和季度，允许在同一季的不同分集搜索中复用缓存
        cache_key = f"provider_search_{search_title}_{season_to_filter or 'all'}"
        supplemental_cache_key = f"supplemental_search_{search_title}"
        cached_results_data = await crud.get_cache(session, cache_key)
        cached_supplemental_results = await crud.get_cache(session, supplemental_cache_key)

        if cached_results_data is not None and cached_supplemental_results is not None:
            logger.info(f"搜索缓存命中: '{cache_key}'")
            timer.step_end(details="缓存命中")
            # 缓存数据已排序和过滤，只需更新当前请求的集数信息
            results = [models.ProviderSearchInfo.model_validate(item) for item in cached_results_data]
            for item in results:
                item.currentEpisodeIndex = episode_to_filter

            # 过滤处理
            filtered_results = results
            if typeFilter:
                filtered_results = [item for item in filtered_results if item.type == typeFilter]
            if yearFilter:
                filtered_results = [item for item in filtered_results if item.year == yearFilter]
            if providerFilter:
                filtered_results = [item for item in filtered_results if item.provider == providerFilter]
            if titleFilter:
                filtered_results = [item for item in filtered_results if titleFilter.lower() in item.title.lower()]

            # 分页处理
            total = len(filtered_results)
            start_idx = (page - 1) * pageSize
            end_idx = start_idx + pageSize
            paginated_results = filtered_results[start_idx:end_idx]

            timer.finish()  # 打印计时报告
            filter_metadata = _extract_filter_metadata(results)
            return UIProviderSearchResponse(
                results=[item.model_dump() for item in paginated_results],
                supplemental_results=[models.ProviderSearchInfo.model_validate(item).model_dump() for item in cached_supplemental_results],
                search_season=season_to_filter,
                search_episode=episode_to_filter,
                total=total,
                page=page,
                pageSize=pageSize,
                **filter_metadata
            )

        timer.step_end(details="缓存未命中")
        logger.info(f"搜索缓存未命中: '{cache_key}'，正在执行完整搜索流程...")
        # --- 缓存逻辑结束 ---

        # V2.1.6: 使用统一的ai_type_and_season_mapping_and_correction函数

        # 获取AI匹配器用于统一的季度映射
        ai_matcher = await ai_matcher_manager.get_matcher() if ai_matcher_manager else None

        episode_info = {
            "season": season_to_filter,
            "episode": episode_to_filter
        } if episode_to_filter is not None else None

        logger.info(f"用户 '{current_user.username}' 正在搜索: '{keyword}' (解析为: title='{search_title}', season={season_to_filter}, episode={episode_to_filter})")

        

        # 第一次检查:在所有搜索之前检查是否有弹幕源
        if not manager.has_enabled_scrapers:
            logger.warning("❌ 没有启用的弹幕搜索源，终止本次搜索")
            logger.info("请在'搜索源-弹幕搜索源'页面中至少启用一个弹幕源，如果没有弹幕源请从资源仓库中加载")
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="没有启用的弹幕搜索源，请在“搜索源”页面中启用至少一个。"
            )

        # --- 原有的复杂搜索流程开始 ---
        # 1. 获取别名和补充结果
        # 修正：检查是否有任何启用的辅助源或强制辅助源
        has_any_aux_source = await metadata_manager.has_any_enabled_aux_source()

        # 🚀 V2.1.6优化: 提前启动元数据查询，与搜索并行
        metadata_prefetch_task = None
        if ai_matcher and metadata_manager:
            async def prefetch_metadata():
                try:
                    from src.utils.season_mapper import _get_cached_metadata_search
                    return await _get_cached_metadata_search(search_title, metadata_manager, logger)
                except Exception:
                    return None
            metadata_prefetch_task = asyncio.create_task(prefetch_metadata())

        # 构建搜索标题列表：包含原始标题和季度映射后的标题
        search_titles = [search_title]
        if season_mapped_title and season_mapped_title != search_title:
            search_titles.append(season_mapped_title)
            logger.info(f"搜索将同时使用: {search_titles}")

        if not has_any_aux_source:
            logger.info("未配置或未启用任何有效的辅助搜索源，直接进行全网搜索。")
            supplemental_results = []
            # 修正:变量名统一
            timer.step_start("弹幕源搜索")
            all_results = await manager.search_all(search_titles, episode_info=episode_info)
            # 收集单源搜索耗时信息
            from src.utils.search_timer import SubStepTiming
            source_timing_sub_steps = [
                SubStepTiming(name=name, duration_ms=dur, result_count=cnt)
                for name, dur, cnt in manager.last_search_timing
            ]
            timer.step_end(details=f"{len(all_results)}个结果", sub_steps=source_timing_sub_steps)
            logger.info(f"直接搜索完成，找到 {len(all_results)} 个原始结果。")
            results = all_results
            filter_aliases = set(search_titles)  # 使用所有搜索标题作为过滤别名
        else:
            # 检查是否有启用的弹幕源 - 在辅助搜索之前先检查
            if not manager.has_enabled_scrapers:
                logger.warning("❌ 辅助搜索已启用，但没有启用的弹幕搜索源，终止本次搜索")
                logger.info("请在'搜索源-弹幕搜索源'页面中至少启用一个弹幕源，如果没有弹幕源请从资源仓库中加载")
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail='没有启用的弹幕搜索源，请在"搜索源-弹幕搜索源"页面下中至少启用一个，如果没有弹幕源就从资源仓库中加载弹幕源。'
                )

            logger.info("一个或多个元数据源已启用辅助搜索，开始执行...")
            # 修正：增加一个“防火墙”来验证从元数据源返回的别名，防止因模糊匹配导致的结果污染。
            # 优化：并行执行辅助搜索和主搜索
            logger.info(f"将使用标题列表 {search_titles} 进行全网搜索...")

            timer.step_start("并行搜索(弹幕源+辅助源)")
            # 1. 并行启动两个任务
            main_task = asyncio.create_task(
                manager.search_all(search_titles, episode_info=episode_info)
            )

            supp_task = asyncio.create_task(
                metadata_manager.search_supplemental_sources(search_title, current_user)
            )

            # 2. 等待两个任务都完成
            all_results, (all_possible_aliases, supplemental_results) = await asyncio.gather(
                main_task, supp_task
            )

            # 收集单源搜索耗时信息
            from src.utils.search_timer import SubStepTiming
            source_timing_sub_steps = [
                SubStepTiming(name=name, duration_ms=dur, result_count=cnt)
                for name, dur, cnt in manager.last_search_timing
            ]
            timer.step_end(
                details=f"弹幕{len(all_results)}个+辅助{len(supplemental_results)}个",
                sub_steps=source_timing_sub_steps
            )

            timer.step_start("别名验证与过滤")
            # 3. 信任元数据源返回的别名，不再用相似度过滤
            # 原因：元数据源（TMDB/Bangumi）返回的别名是可信的，
            # 当搜索词是日文时，中文别名与日文搜索词相似度很低，但仍然是正确的别名
            # 相似度过滤应该用在搜索结果过滤阶段，而不是别名验证阶段
            filter_aliases = set(all_possible_aliases)

            # 确保所有搜索标题都在列表中
            filter_aliases.update(search_titles)

            # 记录统计信息
            logger.info(f"别名验证: 共 {len(filter_aliases)} 个别名（来自元数据源，已信任）")

            logger.info(f"所有辅助搜索完成，最终别名集大小: {len(filter_aliases)}")

            # 新增：根据您的要求，打印最终的别名列表以供调试
            logger.info(f"用于过滤的别名列表: {list(filter_aliases)}")

            def normalize_for_filtering(title: str) -> str:
                """标准化标题用于过滤比较

                1. 移除括号及其内容（如 [僅限港澳台地區]）
                2. 转小写并移除空格
                """
                if not title: return ""
                # 移除各种括号及其内容
                title = re.sub(r'[\[【(（].*?[\]】)）]', '', title)
                return title.lower().replace(" ", "").replace("：", ":").strip()

            # 修正：采用更智能的两阶段过滤策略
            # 阶段1：基于原始搜索词进行初步、宽松的过滤，以确保所有相关系列（包括不同季度和剧场版）都被保留。
            # 只有当用户明确指定季度时，我们才进行更严格的过滤。
            normalized_filter_aliases = {normalize_for_filtering(alias) for alias in filter_aliases if alias}
            filtered_results = []
            excluded_results = []
            for item in all_results:
                normalized_item_title = normalize_for_filtering(item.title)
                if not normalized_item_title: continue

                # 检查搜索结果是否与任何一个别名匹配
                # token_set_ratio 擅长处理单词顺序不同和部分单词匹配的情况。
                # 修正：使用 partial_ratio 来更好地匹配续作和外传 (e.g., "刀剑神域" vs "刀剑神域外传")
                # 85 的阈值可以在保留强相关的同时，过滤掉大部分无关结果。
                if any(fuzz.partial_ratio(normalized_item_title, alias) > 85 for alias in normalized_filter_aliases):
                    filtered_results.append(item)
                else:
                    excluded_results.append(item)

            # 聚合打印过滤结果
            filter_log_lines = [f"别名过滤结果 (保留 {len(filtered_results)}/{len(all_results)}):"]
            for item in excluded_results:
                filter_log_lines.append(f"  - 已过滤: {item.title}")
            for item in filtered_results:
                filter_log_lines.append(f"  - {item.title}")
            logger.info("\n".join(filter_log_lines))
            timer.step_end(details=f"保留{len(filtered_results)}个")
            results = filtered_results

    except httpx.RequestError as e:
        error_message = f"搜索 '{keyword}' 时发生网络错误: {e}"
        logger.error(error_message, exc_info=True)
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=error_message)

    # 根据标题关键词修正媒体类型
    for item in results:
        if item.type == 'tv_series' and is_movie_by_title(item.title):
            logger.info(f"标题 '{item.title}' 包含电影关键词，类型从 'tv_series' 修正为 'movie'。")
            item.type = 'movie'

    # 如果用户在搜索词中明确指定了季度，则对结果进行过滤
    if season_to_filter:
        original_count = len(results)
        # 当指定季度时，我们只关心电视剧类型
        filtered_by_type = [item for item in results if item.type == 'tv_series']
        
        # 然后在电视剧类型中，我们按季度号过滤
        filtered_by_season = []
        for item in filtered_by_type:
            # 使用模型中已解析好的 season 字段进行比较
            if item.season == season_to_filter:
                filtered_by_season.append(item)
        
        logger.info(f"根据指定的季度 ({season_to_filter}) 进行过滤，从 {original_count} 个结果中保留了 {len(filtered_by_season)} 个。")
        results = filtered_by_season

    # 修正：在返回结果前，确保 currentEpisodeIndex 与本次请求的 episode_info 一致。
    # 这可以防止因缓存或其他原因导致的状态泄露。
    current_episode_index_for_this_request = episode_info.get("episode") if episode_info else None
    for item in results:
        item.currentEpisodeIndex = current_episode_index_for_this_request

    # 新增：根据搜索源的显示顺序和标题相似度对结果进行排序
    source_settings = await crud.get_all_scraper_settings(session)
    source_order_map = {s['providerName']: s['displayOrder'] for s in source_settings}

    def sort_key(item: models.ProviderSearchInfo):
        provider_order = source_order_map.get(item.provider, 999)
        # 使用 token_set_ratio 来获得更鲁棒的标题相似度评分
        similarity_score = fuzz.token_set_ratio(search_title, item.title)
        # 主排序键：源顺序（升序）；次排序键：相似度（降序）
        return (provider_order, -similarity_score)

    timer.step_start("结果排序")
    sorted_results = sorted(results, key=sort_key)
    timer.step_end(details=f"{len(sorted_results)}个结果")

    # --- 新增：在返回前缓存最终结果 ---
    timer.step_start("结果缓存")
    # 我们缓存的是整季的结果，所以在存入前清除特定集数的信息
    results_to_cache = []
    for item in sorted_results:
        item_copy = item.model_copy(deep=True)
        item_copy.currentEpisodeIndex = None
        results_to_cache.append(item_copy.model_dump())

    if sorted_results:
        await crud.set_cache(session, cache_key, results_to_cache, ttl_seconds=10800)
    # 缓存补充结果
    if supplemental_results:
        await crud.set_cache(session, supplemental_cache_key, [item.model_dump() for item in supplemental_results], ttl_seconds=10800)
    timer.step_end()
    # --- 缓存逻辑结束 ---



    # 🚀 V2.1.6: 使用统一的AI类型和季度映射修正函数
    if ai_matcher and metadata_manager:
        try:
            timer.step_start("AI映射修正")
            logger.info("🔄 开始AI映射修正...")
            # 获取预取的元数据结果（如果有）
            prefetched_metadata = None
            if metadata_prefetch_task:
                try:
                    prefetched_metadata = await metadata_prefetch_task
                except Exception:
                    pass

            mapping_result = await ai_type_and_season_mapping_and_correction(
                search_title=search_title,
                search_results=sorted_results,
                metadata_manager=metadata_manager,
                ai_matcher=ai_matcher,
                logger=logger,
                similarity_threshold=60.0,
                prefetched_metadata_results=prefetched_metadata
            )

            # 应用修正结果
            if mapping_result['total_corrections'] > 0:
                logger.info(f"✓ 主页搜索 统一AI映射成功: 总计修正了 {mapping_result['total_corrections']} 个结果")
                logger.info(f"  - 类型修正: {len(mapping_result['type_corrections'])} 个")
                logger.info(f"  - 季度修正: {len(mapping_result['season_corrections'])} 个")

                # 更新搜索结果（已经直接修改了sorted_results）
                sorted_results = mapping_result['corrected_results']
                timer.step_end(details=f"修正{mapping_result['total_corrections']}个")
            else:
                logger.info(f"○ 主页搜索 统一AI映射: 未找到需要修正的信息")
                timer.step_end(details="无修正")

        except Exception as e:
            logger.warning(f"主页搜索 统一AI映射任务执行失败: {e}")
            timer.step_end(details=f"失败: {e}")

    # 过滤处理
    filtered_results = sorted_results
    if typeFilter:
        filtered_results = [item for item in filtered_results if item.type == typeFilter]
    if yearFilter:
        filtered_results = [item for item in filtered_results if item.year == yearFilter]
    if providerFilter:
        filtered_results = [item for item in filtered_results if item.provider == providerFilter]
    if titleFilter:
        filtered_results = [item for item in filtered_results if titleFilter.lower() in item.title.lower()]

    # 分页处理
    total = len(filtered_results)
    start_idx = (page - 1) * pageSize
    end_idx = start_idx + pageSize
    paginated_results = filtered_results[start_idx:end_idx]

    timer.finish()  # 打印搜索计时报告
    filter_metadata = _extract_filter_metadata(sorted_results)
    return UIProviderSearchResponse(
        results=[item.model_dump() for item in paginated_results],
        supplemental_results=[item.model_dump() for item in supplemental_results] if supplemental_results else [],
        search_season=season_to_filter,
        search_episode=episode_to_filter,
        total=total,
        page=page,
        pageSize=pageSize,
        **filter_metadata
    )



@router.get("/search/episodes", response_model=List[models.ProviderEpisodeInfo], summary="获取搜索结果的分集列表")
async def get_episodes_for_search_result(
    provider: str = Query(...),
    media_id: str = Query(...),
    media_type: Optional[str] = Query(None), # Pass media_type to help scraper
    supplement_provider: Optional[str] = Query(None, description="补充源provider"),
    supplement_media_id: Optional[str] = Query(None, description="补充源mediaId"),
    manager: ScraperManager = Depends(get_scraper_manager),
    metadata_manager: MetadataSourceManager = Depends(get_metadata_manager),
    current_user: models.User = Depends(security.get_current_user)
):
    """为指定的搜索结果获取完整的分集列表。支持从补充源获取分集URL。"""
    try:
        episodes = []

        # 如果提供了补充源参数,从补充源获取分集URL
        if supplement_provider and supplement_media_id:
            logger.info(f"使用补充源 {supplement_provider} 获取分集列表")

            try:
                # 获取补充源实例
                supplement_source = metadata_manager.sources.get(supplement_provider)
                if not supplement_source:
                    logger.warning(f"补充源 {supplement_provider} 不可用")
                elif not getattr(supplement_source, 'supports_episode_urls', False):
                    logger.warning(f"补充源 {supplement_provider} 不支持分集URL获取")
                else:
                    # 使用补充源获取分集URL列表
                    episode_urls = await supplement_source.get_episode_urls(
                        supplement_media_id, provider  # 目标平台
                    )
                    logger.info(f"补充源获取到 {len(episode_urls)} 个分集URL")

                    if episode_urls:
                        # 获取主源scraper用于解析URL
                        scraper = manager.get_scraper(provider)

                        # 解析URL获取分集信息
                        for i, url in episode_urls:
                            try:
                                # 从URL提取episode_id
                                episode_id = await scraper.get_id_from_url(url)
                                if episode_id:
                                    episodes.append(models.ProviderEpisodeInfo(
                                        provider=provider,
                                        episodeId=episode_id,
                                        title=f"第{i}集",
                                        episodeIndex=i,
                                        url=url
                                    ))
                            except Exception as e:
                                logger.warning(f"解析URL失败 (第{i}集): {e}")

                        logger.info(f"补充源成功解析 {len(episodes)} 个分集")
            except Exception as e:
                logger.error(f"使用补充源获取分集失败: {e}", exc_info=True)
        else:
            # 从主源获取分集列表
            scraper = manager.get_scraper(provider)
            # 将 db_media_type 传递给 get_episodes 以帮助需要它的刮削器（如 mgtv）
            episodes = await scraper.get_episodes(media_id, db_media_type=media_type)

        return episodes
    except httpx.RequestError as e:
        # 新增：捕获网络错误
        error_message = f"从 {provider} 获取分集列表时发生网络错误: {e}"
        logger.error(f"获取分集列表失败 (provider={provider}, media_id={media_id}): {error_message}", exc_info=True)
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=error_message)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))
    except Exception as e:
        logger.error(f"获取分集列表失败 (provider={provider}, media_id={media_id}): {e}", exc_info=True)
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="获取分集列表失败。")




