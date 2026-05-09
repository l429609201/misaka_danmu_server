"""
弹弹Play 兼容 API 的后备搜索功能

当本地库中没有匹配结果时，通过弹幕源进行在线搜索。
"""

import asyncio
import logging
import time
from typing import Dict, Optional

from sqlalchemy.ext.asyncio import AsyncSession
from fastapi import HTTPException, status

from src.db import crud, ConfigManager
from src.services import ScraperManager, TaskManager, MetadataSourceManager, unified_search
from src.utils import (
    parse_search_keyword,
    ai_type_and_season_mapping_and_correction,
    SearchTimer, SEARCH_TYPE_FALLBACK_SEARCH
)
from src.rate_limiter import RateLimiter
from src.ai import AIMatcherManager

# 同包内相对导入
from .models import (
    DandanSearchAnimeResponse, DandanSearchAnimeItem,
    DandanSearchEpisodesResponse, DandanAnimeInfo, DandanEpisodeInfo
)
from .constants import (
    DANDAN_TYPE_MAPPING, DANDAN_TYPE_DESC_MAPPING,
    FALLBACK_SEARCH_BANGUMI_ID, FALLBACK_SEARCH_CACHE_PREFIX,
    FALLBACK_SEARCH_CACHE_TTL, TOKEN_SEARCH_TASKS_PREFIX, TOKEN_SEARCH_TASKS_TTL
)
from .helpers import (
    get_db_cache, set_db_cache, delete_db_cache,
    check_related_match_fallback_task, get_next_virtual_anime_id, format_episode_ranges
)

logger = logging.getLogger(__name__)


async def handle_fallback_search(
    search_term: str,
    token: str,
    session: AsyncSession,
    scraper_manager: ScraperManager,
    metadata_manager: MetadataSourceManager,
    config_manager: ConfigManager,
    rate_limiter: RateLimiter,
    title_recognition_manager,
    task_manager: TaskManager,
    ai_matcher_manager: AIMatcherManager
) -> DandanSearchAnimeResponse:
    """处理后备搜索逻辑"""
    search_key = f"search_{hash(search_term + token)}"
    custom_domain = await config_manager.get("customApiDomain", "")
    image_url = f"{custom_domain}/static/logo.png" if custom_domain else "/static/logo.png"

    # 读取可配置的最大等待时间（默认30秒）。-1 表示无限等待直到出结果
    timeout_str = await config_manager.get("searchFallbackTimeout", "30")
    try:
        max_wait_time = float(timeout_str)
    except (ValueError, TypeError):
        max_wait_time = 30.0

    # ── 辅助：等待搜索完成（阻塞式，等到出结果才返回）──
    async def _wait_for_search_result(wait_key: str) -> DandanSearchAnimeResponse:
        """轮询等待搜索任务完成。max_wait_time=-1 时无限等待。"""
        start = time.time()
        while True:
            await asyncio.sleep(0.5)
            await session.commit()
            info = await get_db_cache(session, FALLBACK_SEARCH_CACHE_PREFIX, wait_key)
            if not info or info["status"] == "failed":
                return DandanSearchAnimeResponse(animes=[])
            if info["status"] == "completed":
                logger.info(f"搜索完成，等待耗时 {time.time() - start:.2f}s")
                return DandanSearchAnimeResponse(animes=info["results"])
            # 非 -1 时检查超时
            if max_wait_time >= 0 and time.time() - start >= max_wait_time:
                logger.info(f"搜索等待超过 {max_wait_time}s，返回空结果")
                return DandanSearchAnimeResponse(animes=[])

    # 检查该 token 是否已有正在进行的搜索任务
    existing_search_key = await get_db_cache(session, TOKEN_SEARCH_TASKS_PREFIX, token)
    if existing_search_key:
        existing_search = await get_db_cache(session, FALLBACK_SEARCH_CACHE_PREFIX, existing_search_key)
        if existing_search and existing_search["status"] == "running":
            logger.info(f"Token 已有搜索任务运行中，等待结果: {existing_search_key}")
            return await _wait_for_search_result(existing_search_key)

    # 检查是否有相关的后备匹配任务正在进行
    match_fallback_task = await check_related_match_fallback_task(session, search_term)
    if match_fallback_task:
        progress = match_fallback_task['progress']
        return DandanSearchAnimeResponse(animes=[
            DandanSearchAnimeItem(
                animeId=999999999, bangumiId=str(FALLBACK_SEARCH_BANGUMI_ID),
                animeTitle=f"{search_term} 匹配后备正在运行",
                type="tvseries", typeDescription=f"{progress}%",
                imageUrl=image_url, startDate="2025-01-01T00:00:00+08:00",
                year=2025, episodeCount=1, rating=0.0, isFavorited=False
            )
        ])

    # 检查是否已有正在进行的搜索
    search_info = await get_db_cache(session, FALLBACK_SEARCH_CACHE_PREFIX, search_key)
    if search_info:
        if search_info["status"] == "completed":
            return DandanSearchAnimeResponse(animes=search_info["results"])
        if search_info["status"] == "failed":
            return DandanSearchAnimeResponse(animes=[])
        if search_info["status"] == "running":
            logger.info(f"相同搜索任务正在运行中，等待结果: {search_key}")
            return await _wait_for_search_result(search_key)

    # 解析搜索词，提取季度和集数信息
    parsed_info = parse_search_keyword(search_term)

    # 启动新的搜索任务
    search_info = {
        "status": "running", "start_time": time.time(),
        "search_term": search_term, "parsed_info": parsed_info, "results": []
    }
    await set_db_cache(session, FALLBACK_SEARCH_CACHE_PREFIX, search_key, search_info, FALLBACK_SEARCH_CACHE_TTL)
    await set_db_cache(session, TOKEN_SEARCH_TASKS_PREFIX, token, search_key, TOKEN_SEARCH_TASKS_TTL)

    # 通过任务管理器提交后备搜索任务
    async def fallback_search_coro_factory(session_inner: AsyncSession, progress_callback):
        try:
            ai_matcher_manager_local = AIMatcherManager(config_manager=config_manager)
            await execute_fallback_search_task(
                search_term, search_key, token, session_inner, progress_callback,
                scraper_manager, metadata_manager, config_manager,
                rate_limiter, title_recognition_manager, ai_matcher_manager_local
            )
        except Exception as e:
            logger.error(f"后备搜索任务执行失败: {e}", exc_info=True)
            search_info_failed = await get_db_cache(session_inner, FALLBACK_SEARCH_CACHE_PREFIX, search_key)
            if search_info_failed:
                search_info_failed["status"] = "failed"
                await set_db_cache(session_inner, FALLBACK_SEARCH_CACHE_PREFIX, search_key, search_info_failed, FALLBACK_SEARCH_CACHE_TTL)
        finally:
            existing_token_key = await get_db_cache(session_inner, TOKEN_SEARCH_TASKS_PREFIX, token)
            if existing_token_key == search_key:
                await delete_db_cache(session_inner, TOKEN_SEARCH_TASKS_PREFIX, token)

    # 提交后备搜索任务
    try:
        task_title = f"后备搜索: {search_term}"
        # 查询 token 名字，写入 task_parameters 供通知格式化使用
        try:
            token_obj = await crud.get_api_token_by_token_str(session, token)
            token_name = token_obj["name"] if token_obj else token[:8]
        except Exception:
            token_name = token[:8]
        task_id, done_event = await task_manager.submit_task(
            fallback_search_coro_factory, task_title, run_immediately=True, queue_type="fallback",
            task_parameters={"token_name": token_name, "search_term": search_term}
        )
        logger.info(f"后备搜索任务已提交: {task_id}")
    except Exception as e:
        logger.error(f"提交后备搜索任务失败: {e}", exc_info=True)
        search_info["status"] = "failed"
        return DandanSearchAnimeResponse(animes=[])

    # 等待搜索完成
    return await _wait_for_search_result(search_key)


async def execute_fallback_search_task(
    search_term: str,
    search_key: str,
    token: str,
    session: AsyncSession,
    progress_callback,
    scraper_manager: ScraperManager,
    metadata_manager: MetadataSourceManager,
    config_manager: ConfigManager,
    rate_limiter: RateLimiter,
    title_recognition_manager,
    ai_matcher_manager: AIMatcherManager
):
    """执行后备搜索任务。"""
    timer = SearchTimer(SEARCH_TYPE_FALLBACK_SEARCH, search_term, logger)
    timer.start()

    # 【性能优化】AI初始化预热：如果AI映射已启用，提前开始初始化（不阻塞）
    ai_matcher_warmup_task = None
    fallback_search_season_mapping_enabled_check = "false"
    try:
        fallback_search_season_mapping_enabled_check = await config_manager.get("fallbackSearchEnableTmdbSeasonMapping", "false")
        if fallback_search_season_mapping_enabled_check.lower() == "true":
            ai_matcher_warmup_task = asyncio.create_task(ai_matcher_manager.get_matcher())
            logger.debug("后备搜索 AI匹配器预热已启动（并行）")
    except Exception as e:
        logger.warning(f"后备搜索 AI预热失败: {e}")

    try:
        timer.step_start("关键词解析与预处理")
        # 1. 解析搜索词
        parsed_info = parse_search_keyword(search_term)
        original_title = parsed_info["title"]
        season_to_filter = parsed_info.get("season")
        episode_to_filter = parsed_info.get("episode")

        # 2. 应用标题预处理规则
        search_title = original_title
        if title_recognition_manager:
            (processed_title, processed_episode, processed_season, preprocessing_applied) = \
                await title_recognition_manager.apply_search_preprocessing(
                    original_title, episode_to_filter, season_to_filter
                )
            if preprocessing_applied:
                search_title = processed_title
                logger.info(f"✓ 后备搜索预处理: '{original_title}' -> '{search_title}'")
                if processed_episode != episode_to_filter:
                    logger.info(f"✓ 后备搜索集数预处理: {episode_to_filter} -> {processed_episode}")
                    episode_to_filter = processed_episode
                if processed_season != season_to_filter:
                    logger.info(f"✓ 后备搜索季度预处理: {season_to_filter} -> {processed_season}")
                    season_to_filter = processed_season
            else:
                logger.info(f"○ 后备搜索预处理未生效: '{original_title}'")
        else:
            logger.info("○ 未配置标题识别管理器，跳过后备搜索预处理。")

        # 3. 同步更新缓存中的 parsed_info
        search_info = await get_db_cache(session, FALLBACK_SEARCH_CACHE_PREFIX, search_key)
        if search_info:
            cached_parsed = search_info.get("parsed_info") or {}
            cached_parsed["season"] = season_to_filter
            cached_parsed["episode"] = episode_to_filter
            cached_parsed["title"] = search_title
            search_info["parsed_info"] = cached_parsed
            await set_db_cache(session, FALLBACK_SEARCH_CACHE_PREFIX, search_key, search_info, FALLBACK_SEARCH_CACHE_TTL)

        # 4. 构造 episode_info
        episode_info = (
            {"season": season_to_filter, "episode": episode_to_filter}
            if episode_to_filter is not None else None
        )

        # 【性能优化④】复用前面已查询的 AI映射配置，避免重复DB查询
        fallback_search_season_mapping_enabled = fallback_search_season_mapping_enabled_check
        if fallback_search_season_mapping_enabled.lower() != "true":
            logger.info("○ 后备搜索 统一AI映射: 功能未启用")

        timer.step_end()
        await progress_callback(10, "开始搜索...")

        # 检查是否有同标题的搜索结果缓存（不含集数，10分钟内复用）
        from src.db.crud.utility import get_cache as get_raw_cache
        fallback_result_cache_key = f"fallback_result_{search_title}"
        cached_fallback_result = await get_raw_cache(session, fallback_result_cache_key)
        if cached_fallback_result and isinstance(cached_fallback_result, dict):
            cached_time = cached_fallback_result.get("timestamp", 0)
            if time.time() - cached_time < 600:  # 10分钟
                cached_results = cached_fallback_result.get("results", [])
                if cached_results:
                    logger.info(f"后备搜索: 复用同标题缓存 '{search_title}'，{len(cached_results)} 个结果")
                    timer.step_start("弹幕源搜索")
                    sorted_results = [
                        models.ProviderSearchInfo(**r) if isinstance(r, dict) else r
                        for r in cached_results
                    ]
                    timer.step_end(details=f"{len(sorted_results)}个结果 (缓存)")
                    # 跳过搜索，直接进入后续处理
                else:
                    cached_fallback_result = None  # 缓存为空，继续搜索

        if not cached_fallback_result or not isinstance(cached_fallback_result, dict) or time.time() - cached_fallback_result.get("timestamp", 0) >= 600:
            timer.step_start("弹幕源搜索")
            # 使用统一的搜索函数
            sorted_results = await unified_search(
                search_term=search_title,
                session=session,
                scraper_manager=scraper_manager,
                metadata_manager=metadata_manager,
                use_alias_expansion=True,
                use_alias_filtering=True,
                use_title_filtering=True,
                use_source_priority_sorting=True,
                progress_callback=progress_callback,
                episode_info=episode_info,
                alias_similarity_threshold=70,
            )
            # 收集单源搜索耗时信息（分组显示，与主页搜索一致）
            from src.utils.search_timer import SubStepTiming
            source_timing_sub_steps = []

            # 弹幕源 + 补充源分组
            for name, dur, cnt in scraper_manager.last_search_timing:
                if name.startswith("补充:"):
                    source_timing_sub_steps.append(
                        SubStepTiming(name=name[3:], duration_ms=dur, result_count=cnt, group="补充源")
                    )
                else:
                    source_timing_sub_steps.append(
                        SubStepTiming(name=name, duration_ms=dur, result_count=cnt, group="弹幕源")
                    )

            # 辅助源分组（别名获取）
            if hasattr(metadata_manager, 'last_aux_search_timing') and metadata_manager.last_aux_search_timing:
                for name, dur, cnt in metadata_manager.last_aux_search_timing:
                    source_timing_sub_steps.append(
                        SubStepTiming(name=name, duration_ms=dur, result_count=cnt, group="辅助源(别名)")
                    )

            timer.step_end(details=f"{len(sorted_results)}个结果", sub_steps=source_timing_sub_steps)

        # 5. 根据标题关键词修正媒体类型
        def is_movie_by_title(title: str) -> bool:
            if not title:
                return False
            movie_keywords = ["剧场版", "劇場版", "movie", "映画"]
            title_lower = title.lower()
            return any(keyword in title_lower for keyword in movie_keywords)

        for item in sorted_results:
            if item.type == "tv_series" and is_movie_by_title(item.title):
                logger.info(f"标题 '{item.title}' 包含电影关键词，类型从 'tv_series' 修正为 'movie'。")
                item.type = "movie"

        # 6. 如果搜索词中明确指定了季度，对结果进行过滤
        if season_to_filter:
            original_count = len(sorted_results)
            filtered_by_type = [item for item in sorted_results if item.type == "tv_series"]
            filtered_by_season = [item for item in filtered_by_type if item.season == season_to_filter]
            logger.info(f"根据指定的季度 ({season_to_filter}) 进行过滤，从 {original_count} 个结果中保留了 {len(filtered_by_season)} 个。")
            sorted_results = filtered_by_season

        # 使用统一的AI类型和季度映射修正函数
        if fallback_search_season_mapping_enabled.lower() == "true":
            try:
                timer.step_start("AI映射修正")
                # 【性能优化】使用预热的AI匹配器
                ai_matcher = None
                if ai_matcher_warmup_task:
                    ai_matcher = await ai_matcher_warmup_task
                    ai_matcher_warmup_task = None  # 清空task，避免重复await
                else:
                    ai_matcher = await ai_matcher_manager.get_matcher()
                if ai_matcher:
                    logger.info(f"○ 后备搜索 开始统一AI映射修正: '{search_title}' ({len(sorted_results)} 个结果)")
                    mapping_result = await ai_type_and_season_mapping_and_correction(
                        search_title=search_title,
                        search_results=sorted_results,
                        metadata_manager=metadata_manager,
                        ai_matcher=ai_matcher,
                        logger=logger,
                        similarity_threshold=60.0
                    )
                    if mapping_result['total_corrections'] > 0:
                        logger.info(f"✓ 后备搜索 统一AI映射成功: 总计修正了 {mapping_result['total_corrections']} 个结果")
                        sorted_results = mapping_result['corrected_results']
                        timer.step_end(details=f"修正{mapping_result['total_corrections']}个")
                    else:
                        logger.info(f"○ 后备搜索 统一AI映射: 未找到需要修正的信息")
                        timer.step_end(details="无修正")
                else:
                    logger.warning("○ 后备搜索 AI映射: AI匹配器未启用或初始化失败")
                    timer.step_end(details="匹配器未启用")
            except Exception as e:
                logger.warning(f"后备搜索 统一AI映射任务执行失败: {e}")
                timer.step_end(details=f"失败: {e}")
        else:
            logger.info("○ 后备搜索 统一AI映射: 功能未启用")

        timer.step_start("结果转换与缓存")
        await progress_callback(80, "转换搜索结果...")
        search_results = []

        # 获取下一个虚拟animeId
        next_virtual_anime_id = await get_next_virtual_anime_id(session)

        # 获取自定义域名
        custom_domain = await config_manager.get("customApiDomain", "")

        # 【性能优化①】循环前一次性读取缓存，循环中只修改内存，循环后一次性写回
        search_info_mapping = await get_db_cache(session, FALLBACK_SEARCH_CACHE_PREFIX, search_key)
        if search_info_mapping and "bangumi_mapping" not in search_info_mapping:
            search_info_mapping["bangumi_mapping"] = {}

        # 【性能优化②】批量查询所有 (provider, mediaId) 的库内分集信息
        provider_media_pairs = [(r.provider, r.mediaId) for r in sorted_results]
        library_episodes_map = await crud.get_episode_indices_by_source_media_ids_batch(
            session, provider_media_pairs
        )

        for i, result in enumerate(sorted_results):
            current_virtual_anime_id = next_virtual_anime_id + i
            unique_bangumi_id = f"A{current_virtual_anime_id}"

            year_info = f" 年份：{result.year}" if result.year else ""
            title_with_source = f"{result.title} （来源：{result.provider}{year_info}）"

            # 存储bangumiId到原始信息的映射（仅修改内存中的dict）
            if search_info_mapping:
                search_info_mapping["bangumi_mapping"][unique_bangumi_id] = {
                    "provider": result.provider,
                    "media_id": result.mediaId,
                    "original_title": result.title,
                    "type": result.type,
                    "anime_id": current_virtual_anime_id,
                }

            # 检查库内是否已有该精确源(provider+mediaId)的分集，写入 typeDescription
            base_type_desc = DANDAN_TYPE_DESC_MAPPING.get(result.type, "其他")
            type_description = base_type_desc

            pair_key = (result.provider, result.mediaId)
            existing_episodes = library_episodes_map.get(pair_key, [])
            if existing_episodes:
                episode_ranges = format_episode_ranges(existing_episodes)
                type_description = f"{base_type_desc}（库内：{episode_ranges}）"

            search_results.append(
                DandanSearchAnimeItem(
                    animeId=current_virtual_anime_id,
                    bangumiId=unique_bangumi_id,
                    animeTitle=title_with_source,
                    type=DANDAN_TYPE_MAPPING.get(result.type, "other"),
                    typeDescription=type_description,
                    imageUrl=result.imageUrl,
                    startDate=f"{result.year}-01-01T00:00:00+08:00" if result.year else None,
                    year=result.year,
                    episodeCount=result.episodeCount or 0,
                    rating=0.0,
                    isFavorited=False,
                )
            )

        await progress_callback(90, "整理搜索结果...")

        # 【性能优化①续】循环结束后一次性写回 bangumi_mapping + 更新缓存状态为完成
        if search_info_mapping:
            search_info_mapping["status"] = "completed"
            search_info_mapping["results"] = [result.model_dump() for result in search_results]
            await set_db_cache(session, FALLBACK_SEARCH_CACHE_PREFIX, search_key, search_info_mapping, FALLBACK_SEARCH_CACHE_TTL)

        # 将搜索结果按标题存储到缓存，供相同标题重复搜索时复用
        try:
            parsed = parse_search_keyword(search_term)
            core_title = parsed["title"]
            cache_key = f"fallback_result_{core_title}"
            cache_data = {
                "search_term": core_title,
                "results": [result.model_dump() for result in search_results],
                "timestamp": time.time(),
            }
            await set_db_cache(session, "", cache_key, cache_data, 600)
            logger.info(f"后备搜索结果已存储到缓存: {cache_key}")
        except Exception as e:
            logger.warning(f"存储后备搜索结果到缓存失败: {e}")

        timer.step_end(details=f"{len(search_results)}个结果")
        await progress_callback(100, "搜索完成")
        timer.finish()

    except Exception as e:
        logger.error(f"后备搜索任务执行失败: {e}", exc_info=True)
        timer.finish()
        search_info_failed = await get_db_cache(session, FALLBACK_SEARCH_CACHE_PREFIX, search_key)
        if search_info_failed:
            search_info_failed["status"] = "failed"
            await set_db_cache(session, FALLBACK_SEARCH_CACHE_PREFIX, search_key, search_info_failed, FALLBACK_SEARCH_CACHE_TTL)
    finally:
        existing_token_key = await get_db_cache(session, TOKEN_SEARCH_TASKS_PREFIX, token)
        if existing_token_key == search_key:
            await delete_db_cache(session, TOKEN_SEARCH_TASKS_PREFIX, token)


async def search_implementation(
    search_term: str,
    episode: Optional[str],
    session: AsyncSession,
    scraper_manager=None,
    config_manager=None,
    rate_limiter=None,
) -> DandanSearchEpisodesResponse:
    """搜索接口的通用实现，避免代码重复。
    当启用并行搜索时，还会从源站补充库内缺失的分集。
    """
    search_term = search_term.strip()
    if not search_term:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Missing required query parameter: 'anime' or 'keyword'"
        )

    parsed_info = parse_search_keyword(search_term)
    title_to_search = parsed_info["title"]
    season_to_search = parsed_info.get("season")
    episode_from_title = parsed_info.get("episode")

    episode_number_from_param = int(episode) if episode and episode.isdigit() else None
    final_episode_to_search = episode_number_from_param if episode_number_from_param is not None else episode_from_title

    flat_results = await crud.search_episodes_in_library(
        session,
        anime_title=title_to_search,
        episode_number=final_episode_to_search,
        season_number=season_to_search
    )

    grouped_animes: Dict[int, DandanAnimeInfo] = {}
    # 收集每个 anime 涉及的 provider 集合，用于多源时在分集标题加前缀
    anime_providers: Dict[int, set] = {}
    # 暂存原始分集数据（含 providerName），分组后统一处理
    anime_raw_episodes: Dict[int, list] = {}

    for res in flat_results:
        anime_id = res['animeId']
        if anime_id not in grouped_animes:
            dandan_type = DANDAN_TYPE_MAPPING.get(res.get('type'), "other")
            dandan_type_desc = DANDAN_TYPE_DESC_MAPPING.get(res.get('type'), "其他")

            grouped_animes[anime_id] = DandanAnimeInfo(
                animeId=anime_id,
                animeTitle=res['animeTitle'],
                imageUrl=res.get('imageUrl') or "",
                searchKeyword=search_term or "",
                type=dandan_type,
                typeDescription=dandan_type_desc,
                isFavorited=res.get('isFavorited', False),
                episodes=[]
            )
            anime_providers[anime_id] = set()
            anime_raw_episodes[anime_id] = []

        provider = res.get('providerName', '')
        if provider:
            anime_providers[anime_id].add(provider)
        anime_raw_episodes[anime_id].append(res)

    # 多源时在分集标题前加 【provider】 前缀
    for anime_id, raw_eps in anime_raw_episodes.items():
        is_multi_source = len(anime_providers.get(anime_id, set())) > 1
        for res in raw_eps:
            title = res['episodeTitle']
            if is_multi_source:
                provider = res.get('providerName', '')
                if provider and not title.startswith("【"):
                    title = f"【{provider}】{title}"
            grouped_animes[anime_id].episodes.append(
                DandanEpisodeInfo(episodeId=res['episodeId'], episodeTitle=title, isLibrary=True, episodeIndex=res.get('episodeIndex'))
            )

    # ─── 并行搜索：从源站补充库内缺失的分集 ───
    parallel_search_enabled = False
    if config_manager and scraper_manager:
        parallel_search_enabled = (await config_manager.get("parallelSearchEnabled", "false")).lower() == 'true'

    if parallel_search_enabled and grouped_animes:
        await _merge_source_episodes(
            session, grouped_animes, scraper_manager, config_manager
        )

        # 为并行搜索结果的 animeTitle 加集数区分标注
        for anime_info in grouped_animes.values():
            if not anime_info.isParallelResult:
                continue
            library_indices = sorted([
                ep.episodeIndex
                for ep in anime_info.episodes if ep.isLibrary and ep.episodeIndex is not None
            ])
            source_indices = sorted([
                ep.episodeIndex
                for ep in anime_info.episodes if not ep.isLibrary and ep.episodeIndex is not None
            ])

            def format_indices(indices):
                """将集数列表格式化为紧凑字符串，连续区间用'-'表示"""
                if not indices:
                    return ""
                result = []
                start = prev = indices[0]
                for idx in indices[1:]:
                    if idx == prev + 1:
                        prev = idx
                    else:
                        result.append(f"{start}-{prev}" if start != prev else str(start))
                        start = prev = idx
                result.append(f"{start}-{prev}" if start != prev else str(start))
                return ",".join(result)

            parts = []
            if library_indices:
                parts.append(f"库内：{format_indices(library_indices)}")
            if source_indices:
                parts.append(f"搜索：{format_indices(source_indices)}")
            if parts:
                anime_info.animeTitle = anime_info.animeTitle + "（" + "）（".join(parts) + "）"

    return DandanSearchEpisodesResponse(animes=list(grouped_animes.values()))



async def _merge_source_episodes(
    session: AsyncSession,
    grouped_animes: Dict[int, DandanAnimeInfo],
    scraper_manager: ScraperManager,
    config_manager: ConfigManager,
):
    """
    并行搜索核心逻辑：从源站获取完整分集列表，补充库内缺失的分集。

    对每个库内 anime 的每个 source：
    1. 获取库内已有的分集 episodeIndex 集合
    2. 调用 scraper.get_episodes(mediaId) 获取源站完整分集列表
    3. 缺失的分集用虚拟 episodeId 补充
    4. 创建映射缓存，让 /comment/{虚拟id} 能找到正确的源
    """
    from sqlalchemy import select
    from src.db.orm_models import AnimeSource, Episode, Anime
    from src.core.cache import get_cache_backend

    for anime_id, anime_info in list(grouped_animes.items()):
        try:
            # 获取该 anime 的所有 source
            sources = await crud.get_anime_sources(session, anime_id)
            if not sources:
                continue

            # 获取 anime 基本信息
            anime_stmt = select(Anime).where(Anime.id == anime_id)
            anime_result = await session.execute(anime_stmt)
            anime_obj = anime_result.scalar_one_or_none()
            if not anime_obj:
                continue

            # 收集库内已有的 episodeId（从 episodes 列表里提取）
            existing_episode_ids = {ep.episodeId for ep in anime_info.episodes}

            # 【性能优化③】预收集所有需要查询的源，然后用 asyncio.gather 并发获取分集
            valid_sources = []
            for source in sources:
                source_id = source['sourceId']
                provider = source['providerName']
                media_id = source['mediaId']
                source_order = source.get('sourceOrder', 1)

                # 自定义源不参与并行搜索
                if provider == 'custom':
                    continue

                scraper = scraper_manager.get_scraper(provider)
                if not scraper:
                    logger.warning(f"[并行搜索] 无法获取 {provider} 的 scraper")
                    continue

                valid_sources.append((source_id, provider, media_id, source_order, scraper))

            if not valid_sources:
                continue

            # 【性能优化⑤】批量查询所有 source 的已有分集 episodeIndex，避免逐源DB查询
            all_source_ids = [sid for sid, _, _, _, _ in valid_sources]
            if all_source_ids:
                batch_ep_stmt = (
                    select(Episode.sourceId, Episode.episodeIndex)
                    .where(Episode.sourceId.in_(all_source_ids))
                )
                batch_ep_result = await session.execute(batch_ep_stmt)
                existing_indices_map: dict = {}
                for sid, ep_idx in batch_ep_result.fetchall():
                    existing_indices_map.setdefault(sid, set()).add(ep_idx)
            else:
                existing_indices_map = {}

            # 并发获取所有源的分集列表
            fetch_tasks = [scraper.get_episodes(media_id) for _, _, media_id, _, scraper in valid_sources]
            fetch_results = await asyncio.gather(*fetch_tasks, return_exceptions=True)

            # 逐个处理并发结果
            for (source_id, provider, media_id, source_order, _scraper), fetch_result in zip(valid_sources, fetch_results):
                if isinstance(fetch_result, Exception):
                    logger.warning(f"[并行搜索] 从 {provider} 获取分集列表失败: {fetch_result}")
                    continue

                source_episodes = fetch_result
                if not source_episodes:
                    logger.debug(f"[并行搜索] {provider} 源站无分集: mediaId={media_id}")
                    continue

                # 从批量查询结果中获取该 source 已有的分集 episodeIndex
                existing_indices = existing_indices_map.get(source_id, set())

                logger.info(f"[并行搜索] {provider} 源站返回 {len(source_episodes)} 个分集，库内已有 {len(existing_indices)} 个")

                # 找出缺失的分集
                missing_episodes = []
                for ep in source_episodes:
                    if ep.episodeIndex not in existing_indices:
                        # 生成虚拟 episodeId（与入库格式完全一致）
                        virtual_ep_id = int(f"25{anime_id:06d}{source_order:02d}{ep.episodeIndex:04d}")
                        if virtual_ep_id not in existing_episode_ids:
                            missing_episodes.append((virtual_ep_id, ep))

                if not missing_episodes:
                    logger.debug(f"[并行搜索] {provider} 无缺失分集")
                    continue

                logger.info(f"[并行搜索] {provider} 补充 {len(missing_episodes)} 个缺失分集")

                # 补充缺失分集到结果中
                for virtual_ep_id, ep in missing_episodes:
                    anime_info.episodes.append(
                        DandanEpisodeInfo(
                            episodeId=virtual_ep_id,
                            episodeTitle=ep.title or f"第{ep.episodeIndex}集",
                            isLibrary=False,  # 标记为源站补充分集
                            episodeIndex=ep.episodeIndex
                        )
                    )
                    existing_episode_ids.add(virtual_ep_id)

                # 标记该 anime 为并行搜索结果
                anime_info.isParallelResult = True
                anime_info.parallelProvider = provider
                anime_info.parallelYear = anime_obj.year

                # 创建映射缓存（整部剧级别），让 /comment/{虚拟episodeId} 能找到源信息
                virtual_anime_base = int(f"25{anime_id:06d}{source_order:02d}0000")
                fallback_series_key = f"fallback_episode_{virtual_anime_base}"

                cache_data = {
                    "real_anime_id": anime_id,
                    "provider": provider,
                    "mediaId": media_id,
                    "final_title": anime_obj.title,
                    "final_season": anime_obj.season or 1,
                    "media_type": anime_obj.type or "tvseries",
                    "imageUrl": anime_obj.imageUrl,
                    "year": anime_obj.year,
                }

                # 写入缓存（先内存缓存，再数据库缓存）
                try:
                    _backend = get_cache_backend()
                    if _backend is not None:
                        await _backend.set(fallback_series_key, cache_data, ttl=10800, region="default")
                    await crud.set_cache(session, fallback_series_key, cache_data, ttl=10800)
                    logger.debug(f"[并行搜索] 已创建映射缓存: {fallback_series_key}")
                except Exception as e:
                    logger.warning(f"[并行搜索] 创建映射缓存失败: {e}")

            # 按 episodeId 排序分集列表
            anime_info.episodes.sort(key=lambda ep: ep.episodeId)

        except Exception as e:
            logger.error(f"[并行搜索] 处理 anime_id={anime_id} 时出错: {e}", exc_info=True)
