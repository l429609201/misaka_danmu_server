"""
弹弹Play 兼容 API 的搜索功能

包含搜索节目、搜索分集等功能。
"""

import json
import logging
import re
from typing import Optional
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from fastapi import APIRouter, Depends, HTTPException, Query, status

from src.db import crud, orm_models, get_db_session, ConfigManager, CacheManager
from src.core import get_app_timezone
from src.services import ScraperManager, TaskManager, MetadataSourceManager
from src.utils import parse_search_keyword
from src.rate_limiter import RateLimiter
from src.ai import AIMatcherManager
from src.api.control.dependencies import get_title_recognition_manager
from src.api.dependencies import get_cache_manager
from src.commands import handle_command

# 同包内相对导入
from .models import (
    DandanSearchEpisodesResponse,
    DandanSearchAnimeItem,
    DandanSearchAnimeResponse,
)
from .constants import (
    DANDAN_TYPE_MAPPING,
    DANDAN_TYPE_DESC_MAPPING,
    FALLBACK_SEARCH_CACHE_PREFIX,
)
from .route_handler import get_token_from_path, DandanApiRoute
from .dependencies import (
    get_config_manager,
    get_task_manager,
    get_rate_limiter,
    get_scraper_manager,
    get_metadata_manager,
)
from .fallback_search import (
    handle_fallback_search,
    search_implementation,
)
from .helpers import format_episode_ranges, get_db_cache

logger = logging.getLogger(__name__)

def _format_db_results(db_results):
    """将数据库搜索结果转换为 DandanSearchAnimeItem 列表"""
    animes = []
    for res in db_results:
        dandan_type = DANDAN_TYPE_MAPPING.get(res.get('type'), "other")
        dandan_type_desc = DANDAN_TYPE_DESC_MAPPING.get(res.get('type'), "其他")
        year = res.get('year')
        start_date_str = None
        if year:
            start_date_str = datetime(year, 1, 1, tzinfo=get_app_timezone()).isoformat()
        elif res.get('startDate'):
            start_date_str = res.get('startDate').isoformat()
        animes.append(DandanSearchAnimeItem(
            animeId=res['animeId'],
            bangumiId=res.get('bangumiId') or f"A{res['animeId']}",
            animeTitle=res['animeTitle'],
            type=dandan_type,
            typeDescription=dandan_type_desc,
            imageUrl=res.get('imageUrl'),
            startDate=start_date_str,
            year=year,
            episodeCount=res.get('episodeCount', 0),
            rating=0.0,
            isFavorited=False
        ))
    return animes

# 创建搜索路由器
search_router = APIRouter(route_class=DandanApiRoute)


@search_router.get(
    "/search/episodes",
    response_model=DandanSearchEpisodesResponse,
    summary="[dandanplay兼容] 搜索节目和分集"
)
async def search_episodes_for_dandan(
    anime: str = Query(..., description="节目名称"),
    episode: Optional[str] = Query(None, description="分集标题 (通常是数字)"),
    token: str = Depends(get_token_from_path),
    session: AsyncSession = Depends(get_db_session),
    scraper_manager: ScraperManager = Depends(get_scraper_manager),
    config_manager: ConfigManager = Depends(get_config_manager),
    rate_limiter: RateLimiter = Depends(get_rate_limiter),
):
    """
    模拟 dandanplay 的 /api/v2/search/episodes 接口。
    它会搜索 **本地弹幕库** 中的番剧和分集信息。
    当启用并行搜索时，还会从源站补充缺失的分集。
    """
    search_term = anime.strip()
    return await search_implementation(
        search_term, episode, session,
        scraper_manager=scraper_manager,
        config_manager=config_manager,
        rate_limiter=rate_limiter,
    )


@search_router.get(
    "/search/anime",
    response_model=DandanSearchAnimeResponse,
    summary="[dandanplay兼容] 搜索作品"
)
async def search_anime_for_dandan(
    keyword: Optional[str] = Query(None, description="节目名称 (兼容 keyword)"),
    anime: Optional[str] = Query(None, description="节目名称 (兼容 anime)"),
    episode: Optional[str] = Query(None, description="分集标题 (此接口中未使用)"),
    token: str = Depends(get_token_from_path),
    session: AsyncSession = Depends(get_db_session),
    scraper_manager: ScraperManager = Depends(get_scraper_manager),
    metadata_manager: MetadataSourceManager = Depends(get_metadata_manager),
    config_manager: ConfigManager = Depends(get_config_manager),
    cache_manager: CacheManager = Depends(get_cache_manager),
    rate_limiter: RateLimiter = Depends(get_rate_limiter),
    title_recognition_manager = Depends(get_title_recognition_manager),
    task_manager: TaskManager = Depends(get_task_manager)
):
    """
    模拟 dandanplay 的 /api/v2/search/anime 接口。
    它会搜索 **本地弹幕库** 中的番剧信息，不包含分集列表。
    新增：支持后备搜索功能，当库内无结果或指定集数不存在时，触发全网搜索。
    支持SXXEXX格式的季度和集数搜索。
    支持指令功能：以@开头的搜索词作为指令。
    """
    search_term = keyword or anime
    if not search_term:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Missing required query parameter: 'keyword' or 'anime'"
        )

    # ===== 指令处理 =====
    command_response = await handle_command(
        search_term, token, session, config_manager, cache_manager,
        scraper_manager=scraper_manager,
        metadata_manager=metadata_manager,
        rate_limiter=rate_limiter,
        title_recognition_manager=title_recognition_manager,
        task_manager=task_manager
    )
    if command_response:
        return command_response
    # ===== 指令处理结束 =====

    # 解析搜索关键词，提取标题、季数和集数
    parsed_info = parse_search_keyword(search_term)
    title_to_search = parsed_info["title"]
    season_to_search = parsed_info.get("season")
    episode_to_search = parsed_info.get("episode")

    # 首先搜索本地库（使用解析后的标题，而非原始搜索词）
    db_results = await crud.search_animes_for_dandan(session, title_to_search)

    # 如果指定了具体集数，需要检查该集数是否存在
    should_trigger_fallback = False

    # 并行搜索：开关开启时总是触发后备搜索
    parallel_search_enabled = (await config_manager.get("parallelSearchEnabled", "false")).lower() == 'true'
    if parallel_search_enabled:
        should_trigger_fallback = True
        logger.info(f"并行搜索已启用，将同时搜索库内和在线源站")

    if not should_trigger_fallback and db_results and episode_to_search is not None:
        # 检查是否存在指定的集数
        episode_exists = False
        for anime_result in db_results:
            anime_id = anime_result['animeId']
            # 查询该番剧的所有分集
            episodes = await crud.search_episodes_in_library(
                session,
                anime_title=title_to_search,
                episode_number=episode_to_search,
                season_number=season_to_search
            )
            if episodes:
                episode_exists = True
                break

        if not episode_exists:
            logger.info(f"本地库中找到番剧但不存在指定集数 E{episode_to_search:02d}，将触发后备搜索")
            should_trigger_fallback = True

    # 如果本地库有结果且不需要触发后备搜索，直接返回
    if db_results and not should_trigger_fallback:
        return DandanSearchAnimeResponse(animes=_format_db_results(db_results))

    # 如果本地库无结果或需要触发后备搜索，检查是否启用了后备搜索
    search_fallback_enabled = await config_manager.get("searchFallbackEnabled", "false")
    if search_fallback_enabled.lower() == 'true' and (not db_results or should_trigger_fallback):
        # 检查Token是否被允许使用后备搜索功能
        try:
            # 获取当前token的信息
            token_stmt = select(orm_models.ApiToken).where(orm_models.ApiToken.token == token)
            token_result = await session.execute(token_stmt)
            current_token_obj = token_result.scalar_one_or_none()

            if current_token_obj:
                # 获取允许的token列表
                allowed_tokens_str = await config_manager.get("matchFallbackTokens", "[]")
                allowed_token_ids = json.loads(allowed_tokens_str)

                # 如果配置了允许的token列表且当前token不在列表中，跳过后备搜索
                if allowed_token_ids and current_token_obj.id not in allowed_token_ids:
                    logger.info(f"Token '{current_token_obj.name}' (ID: {current_token_obj.id}) 未被授权使用后备搜索功能，跳过后备搜索。")
                    return DandanSearchAnimeResponse(animes=[])
                else:
                    logger.info(f"Token '{current_token_obj.name}' (ID: {current_token_obj.id}) 已被授权使用后备搜索功能。")
        except (json.JSONDecodeError, Exception) as e:
            logger.warning(f"检查后备搜索Token授权时发生错误: {e}，继续执行后备搜索")

        # 使用解析后的标题进行后备搜索，但保留原始搜索词用于缓存键
        search_title_for_fallback = title_to_search
        if episode_to_search is not None:
            # 如果指定了集数，在后备搜索中包含季度和集数信息
            if season_to_search is not None:
                search_title_for_fallback = f"{title_to_search} S{season_to_search:02d}E{episode_to_search:02d}"
            else:
                search_title_for_fallback = f"{title_to_search} E{episode_to_search:02d}"
        elif season_to_search is not None:
            search_title_for_fallback = f"{title_to_search} S{season_to_search:02d}"

        # 创建一个临时的ai_matcher_manager用于传递（实际会在协程工厂中重新创建）
        ai_matcher_manager_local = AIMatcherManager(config_manager=config_manager)

        fallback_response = await handle_fallback_search(
            search_title_for_fallback, token, session, scraper_manager,
            metadata_manager, config_manager, rate_limiter, title_recognition_manager,
            task_manager, ai_matcher_manager_local
        )

        # 并行搜索：将库内结果和后备搜索结果合并返回
        if parallel_search_enabled and fallback_response.animes:
            # 从缓存获取 bangumi_mapping，用于精确匹配 provider+mediaId
            search_key = f"search_{hash(search_title_for_fallback + token)}"
            bangumi_mapping = {}
            try:
                cached_data = await get_db_cache(session, FALLBACK_SEARCH_CACHE_PREFIX, search_key)
                if cached_data and isinstance(cached_data, dict):
                    bangumi_mapping = cached_data.get("bangumi_mapping", {})
            except Exception as e:
                logger.debug(f"读取 bangumi_mapping 缓存失败: {e}")

            # 获取源 displayOrder 映射，用于排序
            source_order_map = {}
            try:
                for provider_name, settings in scraper_manager.scraper_settings.items():
                    source_order_map[provider_name] = settings.get('displayOrder', 99)
            except Exception:
                pass

            labeled_fallback = []
            for a in fallback_response.animes:
                # 通过 bangumi_mapping 精确获取该结果的 provider 和 media_id
                mapping = bangumi_mapping.get(a.bangumiId)
                provider = None
                media_id = None
                if mapping:
                    provider = mapping.get("provider")
                    media_id = mapping.get("media_id")
                else:
                    # 降级：从 animeTitle 中正则提取 provider
                    m = re.search(r'（来源：(\S+?)[\s）]', a.animeTitle)
                    if m:
                        provider = m.group(1)

                # 用精确的 provider+mediaId 查库内已有集数
                library_eps = []
                if provider and media_id:
                    try:
                        library_eps = await crud.get_episode_indices_by_source_media_id(
                            session, provider, media_id
                        )
                    except Exception:
                        library_eps = []

                source_total = a.episodeCount or 0

                if library_eps:
                    # 该精确源在库内存在：标注「并行」
                    new_title = a.animeTitle.replace('（来源：', '（并行 来源：')
                    # 计算搜索补充集数范围：源站 1~N 中，库内没有的集数
                    library_eps_set = set(library_eps)
                    search_eps = [i for i in range(1, source_total + 1) if i not in library_eps_set]
                    search_label = f"（搜索：{format_episode_ranges(search_eps)}）" if search_eps else ""
                    new_type_desc = f"{a.typeDescription}{search_label}"
                    labeled_fallback.append(a.model_copy(update={
                        'animeTitle': new_title,
                        'typeDescription': new_type_desc,
                    }))
                else:
                    # 该精确源不在库内，保持原样
                    labeled_fallback.append(a)

            # 排序：并行结果优先（按源 displayOrder），后备结果其次（按源 displayOrder）
            def _get_provider(item):
                """从 animeTitle 中提取 provider 名称"""
                m = re.search(r'（(?:并行 )?来源：(\S+?)[\s）]', item.animeTitle)
                return m.group(1) if m else ""

            def _sort_key(item):
                p = _get_provider(item)
                order = source_order_map.get(p, 99)
                is_parallel = 0 if '（并行' in item.animeTitle else 1
                return (is_parallel, order)

            labeled_fallback.sort(key=_sort_key)

            # 补充自定义源条目：从 db_results 中找到有 custom 源的 anime，追加到最后
            if db_results:
                db_anime_ids = [res['animeId'] for res in db_results]
                custom_anime_ids = await crud.get_anime_ids_with_custom_source(session, db_anime_ids)
                if custom_anime_ids:
                    custom_anime_ids_set = set(custom_anime_ids)
                    for res in db_results:
                        if res['animeId'] in custom_anime_ids_set:
                            dandan_type = DANDAN_TYPE_MAPPING.get(res.get('type'), "other")
                            dandan_type_desc = DANDAN_TYPE_DESC_MAPPING.get(res.get('type'), "其他")
                            year = res.get('year')
                            start_date_str = None
                            if year:
                                start_date_str = datetime(year, 1, 1, tzinfo=get_app_timezone()).isoformat()
                            elif res.get('startDate'):
                                start_date_str = res.get('startDate').isoformat()
                            labeled_fallback.append(DandanSearchAnimeItem(
                                animeId=res['animeId'],
                                bangumiId=res.get('bangumiId') or f"A{res['animeId']}",
                                animeTitle=f"{res['animeTitle']}（自定义源）",
                                type=dandan_type,
                                typeDescription=dandan_type_desc,
                                imageUrl=res.get('imageUrl'),
                                startDate=start_date_str,
                                year=year,
                                episodeCount=res.get('episodeCount', 0),
                                rating=0.0,
                                isFavorited=False
                            ))
                    logger.info(f"并行搜索: 追加 {len(custom_anime_ids)} 个自定义源条目")

            parallel_count = sum(1 for a in labeled_fallback if '（并行' in a.animeTitle)
            logger.info(f"并行搜索: 后备 {len(fallback_response.animes)} 个结果，其中 {parallel_count} 个精确源匹配标注并行")
            return DandanSearchAnimeResponse(animes=labeled_fallback)
        
        # 并行搜索模式下后备搜索无结果，回退到库内结果
        if parallel_search_enabled and db_results and not fallback_response.animes:
            logger.info(f"并行搜索: 后备搜索无结果，回退到库内 {len(db_results)} 个结果")
            return DandanSearchAnimeResponse(animes=_format_db_results(db_results))

        return fallback_response

    # 本地库无结果且未启用后备搜索，返回空结果
    return DandanSearchAnimeResponse(animes=[])