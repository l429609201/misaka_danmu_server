"""
弹弹Play 兼容 API 的后备搜索功能

当本地库中没有匹配结果时，通过弹幕源进行在线搜索。
"""

import asyncio
import json
import logging
import time
from typing import Dict, Optional

from sqlalchemy.ext.asyncio import AsyncSession
from fastapi import HTTPException, status

from src.db import crud, ConfigManager
from src.core.cache import get_cache_backend
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

    # 检查该token是否已有正在进行的搜索任务
    existing_search_key = await get_db_cache(session, TOKEN_SEARCH_TASKS_PREFIX, token)
    if existing_search_key:
        existing_search = await get_db_cache(session, FALLBACK_SEARCH_CACHE_PREFIX, existing_search_key)
        if existing_search and existing_search["status"] == "running":
            elapsed_time = time.time() - existing_search["start_time"]
            progress = min(int((elapsed_time / 60) * 100), 95)
            return DandanSearchAnimeResponse(animes=[
                DandanSearchAnimeItem(
                    animeId=999999999, bangumiId=str(FALLBACK_SEARCH_BANGUMI_ID),
                    animeTitle=f"{existing_search['search_term']} 搜索正在运行",
                    type="tvseries", typeDescription=f"{progress}%",
                    imageUrl=image_url, startDate="2025-01-01T00:00:00+08:00",
                    year=2025, episodeCount=1, rating=0.0, isFavorited=False
                )
            ])

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
            elapsed_time = time.time() - search_info["start_time"]
            progress = min(int((elapsed_time / 60) * 100), 95)
            return DandanSearchAnimeResponse(animes=[
                DandanSearchAnimeItem(
                    animeId=999999999, bangumiId=str(FALLBACK_SEARCH_BANGUMI_ID),
                    animeTitle=f"{search_term} 搜索正在运行",
                    type="tvseries", typeDescription=f"{progress}%",
                    imageUrl=image_url, startDate="2025-01-01T00:00:00+08:00",
                    year=2025, episodeCount=1, rating=0.0, isFavorited=False
                )
            ])

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
        task_id, done_event = await task_manager.submit_task(
            fallback_search_coro_factory, task_title, run_immediately=True, queue_type="fallback"
        )
        logger.info(f"后备搜索任务已提交: {task_id}")
    except Exception as e:
        logger.error(f"提交后备搜索任务失败: {e}", exc_info=True)
        search_info["status"] = "failed"
        return DandanSearchAnimeResponse(animes=[])

    # 15秒等待机制
    max_wait_time = 15.0
    start_time = time.time()
    check_interval = 0.5

    while time.time() - start_time < max_wait_time:
        await asyncio.sleep(check_interval)
        await session.commit()
        current_search_info = await get_db_cache(session, FALLBACK_SEARCH_CACHE_PREFIX, search_key)
        if current_search_info:
            if current_search_info["status"] == "completed":
                logger.info(f"后备搜索在 {time.time() - start_time:.2f} 秒内完成")
                return DandanSearchAnimeResponse(animes=current_search_info["results"])
            elif current_search_info["status"] == "failed":
                logger.warning(f"后备搜索在 {time.time() - start_time:.2f} 秒内失败")
                return DandanSearchAnimeResponse(animes=[])

    # 超过15秒仍未完成，返回进度状态
    logger.info(f"后备搜索超过15秒未完成，返回进度状态")
    final_search_info = await get_db_cache(session, FALLBACK_SEARCH_CACHE_PREFIX, search_key)
    elapsed_time = time.time() - search_info["start_time"]
    progress = min(int((elapsed_time / 60) * 100), 95)

    return DandanSearchAnimeResponse(animes=[
        DandanSearchAnimeItem(
            animeId=999999999, bangumiId=str(FALLBACK_SEARCH_BANGUMI_ID),
            animeTitle=f"{search_term} 搜索正在运行",
            type="tvseries", typeDescription=f"{progress}%",
            imageUrl=image_url, startDate="2025-01-01T00:00:00+08:00",
            year=2025, episodeCount=1, rating=0.0, isFavorited=False
        )
    ])


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

        # 后备搜索 AI映射配置检查
        fallback_search_season_mapping_enabled = await config_manager.get("fallbackSearchEnableTmdbSeasonMapping", "false")
        if fallback_search_season_mapping_enabled.lower() != "true":
            logger.info("○ 后备搜索 统一AI映射: 功能未启用")

        timer.step_end()
        await progress_callback(10, "开始搜索...")

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
        # 收集单源搜索耗时信息
        from src.utils.search_timer import SubStepTiming
        source_timing_sub_steps = [
            SubStepTiming(name=name, duration_ms=dur, result_count=cnt)
            for name, dur, cnt in scraper_manager.last_search_timing
        ]
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

        for i, result in enumerate(sorted_results):
            current_virtual_anime_id = next_virtual_anime_id + i
            unique_bangumi_id = f"A{current_virtual_anime_id}"

            year_info = f" 年份：{result.year}" if result.year else ""
            title_with_source = f"{result.title} （来源：{result.provider}{year_info}）"

            # 存储bangumiId到原始信息的映射
            search_info_mapping = await get_db_cache(session, FALLBACK_SEARCH_CACHE_PREFIX, search_key)
            if search_info_mapping:
                if "bangumi_mapping" not in search_info_mapping:
                    search_info_mapping["bangumi_mapping"] = {}
                search_info_mapping["bangumi_mapping"][unique_bangumi_id] = {
                    "provider": result.provider,
                    "media_id": result.mediaId,
                    "original_title": result.title,
                    "type": result.type,
                    "anime_id": current_virtual_anime_id,
                }
                await set_db_cache(session, FALLBACK_SEARCH_CACHE_PREFIX, search_key, search_info_mapping, FALLBACK_SEARCH_CACHE_TTL)

            # 检查库内是否已有相同标题的分集
            base_type_desc = DANDAN_TYPE_DESC_MAPPING.get(result.type, "其他")
            type_description = base_type_desc

            try:
                existing_episodes = await crud.get_episode_indices_by_anime_title(session, result.title)
                if existing_episodes:
                    episode_ranges = format_episode_ranges(existing_episodes)
                    type_description = f"{base_type_desc}（库内：{episode_ranges}）"
            except Exception as e:
                logger.debug(f"查询库内分集信息失败: {e}")
                type_description = base_type_desc

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

        # 更新缓存状态为完成
        search_info_complete = await get_db_cache(session, FALLBACK_SEARCH_CACHE_PREFIX, search_key)
        if search_info_complete:
            search_info_complete["status"] = "completed"
            search_info_complete["results"] = [result.model_dump() for result in search_results]
            await set_db_cache(session, FALLBACK_SEARCH_CACHE_PREFIX, search_key, search_info_complete, FALLBACK_SEARCH_CACHE_TTL)

        # 将搜索结果存储到数据库缓存中
        try:
            parsed = parse_search_keyword(search_term)
            core_title = parsed["title"]
            cache_key = f"fallback_search_{core_title}"
            cache_data = {
                "search_term": core_title,
                "results": [result.model_dump() for result in search_results],
                "timestamp": time.time(),
            }
            _backend = get_cache_backend()
            if _backend is not None:
                try:
                    await _backend.set(cache_key, json.dumps(cache_data), ttl=600, region="default")
                except Exception:
                    await crud.set_cache(session, cache_key, json.dumps(cache_data), ttl_seconds=600)
            else:
                await crud.set_cache(session, cache_key, json.dumps(cache_data), ttl_seconds=600)
            logger.info(f"后备搜索结果已存储到数据库缓存: {cache_key}")
        except Exception as e:
            logger.warning(f"存储后备搜索结果到数据库缓存失败: {e}")

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
    session: AsyncSession
) -> DandanSearchEpisodesResponse:
    """搜索接口的通用实现，避免代码重复。"""
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

        grouped_animes[anime_id].episodes.append(
            DandanEpisodeInfo(episodeId=res['episodeId'], episodeTitle=res['episodeTitle'])
        )

    return DandanSearchEpisodesResponse(animes=list(grouped_animes.values()))

