"""
弹弹Play 兼容 API 的匹配功能

通过文件名匹配弹幕库，支持库内直接匹配和后备匹配。
"""

import asyncio
import json
import logging
import re
import time
from typing import Dict, List, Optional, Any

from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession
from fastapi import APIRouter, Depends, HTTPException, status
from thefuzz import fuzz

from src.db import crud, orm_models, models, get_db_session, sync_postgres_sequence, ConfigManager
from src.core import get_now
from src.services import ScraperManager, TaskManager, MetadataSourceManager, unified_search, TaskSuccess
from src.utils import (
    parse_search_keyword,
    ai_type_and_season_mapping_and_correction, title_contains_season_name,
    SearchTimer, SEARCH_TYPE_FALLBACK_MATCH, SubStepTiming
)
from src.rate_limiter import RateLimiter
from src.ai import AIMatcherManager
from src.api.control.dependencies import get_title_recognition_manager
from src.api.dependencies import get_ai_matcher_manager

# 从 ai 模块导入需要的常量
from src.ai.ai_prompts import DEFAULT_AI_MATCH_PROMPT

# 从 orm_models 导入需要的模型
AnimeSource = orm_models.AnimeSource

# 同包内相对导入
from .models import (
    DandanMatchInfo, DandanMatchResponse,
    DandanBatchMatchRequestItem, DandanBatchMatchRequest
)
from .constants import (
    DANDAN_TYPE_MAPPING, DANDAN_TYPE_DESC_MAPPING,
    FALLBACK_SEARCH_CACHE_PREFIX, FALLBACK_SEARCH_CACHE_TTL,
)
from .helpers import (
    get_db_cache, set_db_cache,
    store_episode_mapping, find_existing_anime_by_bangumi_id,
    get_next_real_anime_id, get_next_virtual_anime_id
)
from .bangumi import generate_episode_id
from .route_handler import get_token_from_path, DandanApiRoute
from .dependencies import (
    get_config_manager,
    get_task_manager,
    get_rate_limiter,
    get_scraper_manager,
    get_metadata_manager,
)

logger = logging.getLogger(__name__)


def parse_filename_for_match(filename: str) -> Optional[Dict[str, Any]]:
    """
    从文件名中解析出番剧标题和集数。
    委托给统一模块 parse_filename()，并转换为 dict 格式以保持向后兼容。
    """
    from src.utils.filename_parser import parse_filename

    result = parse_filename(filename)
    if result is None:
        return None

    info: Dict[str, Any] = {
        "title": result.title,
        "season": result.season,
        "episode": result.episode,
    }
    if result.is_movie:
        info["is_movie"] = True
    if result.year:
        info["year"] = result.year
    return info


async def get_match_for_item(
    item: DandanBatchMatchRequestItem,
    session: AsyncSession,
    task_manager: TaskManager,
    scraper_manager: ScraperManager,
    metadata_manager: MetadataSourceManager,
    config_manager: ConfigManager,
    ai_matcher_manager: AIMatcherManager,
    rate_limiter: RateLimiter,
    title_recognition_manager,
    current_token: Optional[str] = None
) -> DandanMatchResponse:
    """
    通过文件名匹配弹幕库的核心逻辑。此接口不使用文件Hash。
    优先进行库内直接匹配，失败后回退到TMDB剧集组映射。
    新增：如果所有匹配都失败，且启用了后备机制，则触发自动搜索导入任务。
    """
    logger.info(f"执行匹配逻辑, 文件名: '{item.fileName}'")
    parsed_info = parse_filename_for_match(item.fileName)
    logger.info(f"文件名解析结果: {parsed_info}")
    if not parsed_info:
        response = DandanMatchResponse(isMatched=False)
        logger.info(f"发送匹配响应 (解析失败): {response.model_dump_json(indent=2)}")
        return response

    # --- 步骤 1: 优先进行库内直接搜索 ---
    logger.info("正在进行库内直接搜索...")
    results = await crud.search_episodes_in_library(
        session, parsed_info["title"], parsed_info.get("episode"), parsed_info.get("season")
    )
    logger.info(f"直接搜索为 '{parsed_info['title']}' (季:{parsed_info.get('season')} 集:{parsed_info.get('episode')}) 找到 {len(results)} 条记录")
    
    if results:
        # 对结果进行严格的标题过滤，避免模糊匹配带来的问题
        normalized_search_title = parsed_info["title"].replace("：", ":").replace(" ", "")
        exact_matches = []
        for r in results:
            all_titles_to_check = [
                r.get('animeTitle'), r.get('nameEn'), r.get('nameJp'), r.get('nameRomaji'),
                r.get('aliasCn1'), r.get('aliasCn2'), r.get('aliasCn3'),
            ]
            aliases_to_check = {t for t in all_titles_to_check if t}
            # 使用normalized_search_title进行更精确的匹配
            if any(fuzz.partial_ratio(alias.replace("：", ":").replace(" ", ""), normalized_search_title) > 85 for alias in aliases_to_check):
                exact_matches.append(r)

        if len(exact_matches) < len(results):
            logger.info(f"过滤掉 {len(results) - len(exact_matches)} 条模糊匹配的结果。")
            results = exact_matches

        if results:
            # 优先处理被精确标记的源
            favorited_results = [r for r in results if r.get('isFavorited')]
            if favorited_results:
                res = favorited_results[0]
                dandan_type = DANDAN_TYPE_MAPPING.get(res.get('type'), "other")
                dandan_type_desc = DANDAN_TYPE_DESC_MAPPING.get(res.get('type'), "其他")
                match = DandanMatchInfo(
                    episodeId=res['episodeId'], animeId=res['animeId'], animeTitle=res['animeTitle'],
                    episodeTitle=res['episodeTitle'], type=dandan_type, typeDescription=dandan_type_desc,
                    imageUrl=res.get('imageUrl')
                )
                response = DandanMatchResponse(isMatched=True, matches=[match])
                logger.info(f"发送匹配响应 (精确标记匹配): {response.model_dump_json(indent=2)}")
                return response

            # 如果没有精确标记，检查所有匹配项是否都指向同一个番剧ID
            first_animeId = results[0]['animeId']
            all_from_same_anime = all(res['animeId'] == first_animeId for res in results)

            if all_from_same_anime:
                res = results[0]
                dandan_type = DANDAN_TYPE_MAPPING.get(res.get('type'), "other")
                dandan_type_desc = DANDAN_TYPE_DESC_MAPPING.get(res.get('type'), "其他")
                match = DandanMatchInfo(
                    episodeId=res['episodeId'], animeId=res['animeId'], animeTitle=res['animeTitle'],
                    episodeTitle=res['episodeTitle'], type=dandan_type, typeDescription=dandan_type_desc,
                    imageUrl=res.get('imageUrl')
                )
                response = DandanMatchResponse(isMatched=True, matches=[match])
                logger.info(f"发送匹配响应 (单一作品匹配): {response.model_dump_json(indent=2)}")
                return response

            # 如果匹配到了多个不同的番剧，则返回所有结果让用户选择
            matches = []
            for res in results:
                dandan_type = DANDAN_TYPE_MAPPING.get(res.get('type'), "other")
                dandan_type_desc = DANDAN_TYPE_DESC_MAPPING.get(res.get('type'), "其他")
                matches.append(DandanMatchInfo(
                    episodeId=res['episodeId'], animeId=res['animeId'], animeTitle=res['animeTitle'],
                    episodeTitle=res['episodeTitle'], type=dandan_type, typeDescription=dandan_type_desc,
                    imageUrl=res.get('imageUrl')
                ))
            response = DandanMatchResponse(isMatched=False, matches=matches)
            logger.info(f"发送匹配响应 (多个匹配): {response.model_dump_json(indent=2)}")
            return response

    # --- 步骤 2: 如果直接搜索无果，则回退到 TMDB 映射 ---
    # 注意：TMDB映射仅适用于TV系列，电影跳过此步骤
    potential_animes = []
    if not parsed_info.get("is_movie"):
        logger.info("直接搜索未找到精确匹配，回退到 TMDB 映射匹配。")
        potential_animes = await crud.find_animes_for_matching(session, parsed_info["title"])
        logger.info(f"为标题 '{parsed_info['title']}' 找到 {len(potential_animes)} 个可能的库内作品进行TMDB匹配。")

        for anime in potential_animes:
            if anime.get("tmdbId") and anime.get("tmdbEpisodeGroupId"):
                logger.info(f"正在为作品 ID {anime['animeId']} (TMDB ID: {anime['tmdbId']}) 尝试 TMDB 映射匹配...")
                tmdb_results = await crud.find_episode_via_tmdb_mapping(
                    session,
                    tmdb_id=anime["tmdbId"],
                    group_id=anime["tmdbEpisodeGroupId"],
                    custom_season=parsed_info.get("season"),
                    custom_episode=parsed_info.get("episode")
                )
                if tmdb_results:
                    logger.info(f"TMDB 映射匹配成功，找到 {len(tmdb_results)} 个结果。")
                    res = tmdb_results[0]
                    dandan_type = DANDAN_TYPE_MAPPING.get(res.get('type'), "other")
                    dandan_type_desc = DANDAN_TYPE_DESC_MAPPING.get(res.get('type'), "其他")
                    match = DandanMatchInfo(
                        episodeId=res['episodeId'], animeId=res['animeId'], animeTitle=res['animeTitle'],
                        episodeTitle=res['episodeTitle'], type=dandan_type, typeDescription=dandan_type_desc,
                        imageUrl=res.get('imageUrl')
                    )
                    response = DandanMatchResponse(isMatched=True, matches=[match])
                    logger.info(f"发送匹配响应 (TMDB 映射匹配): {response.model_dump_json(indent=2)}")
                    return response

            elif anime.get("tmdbId"):
                # AI剧集组自动选择增强：有tmdbId但没有tmdbEpisodeGroupId
                ai_episode_group_enabled = (await config_manager.get("aiEpisodeGroupEnabled", "false")).lower() == "true"
                if not ai_episode_group_enabled:
                    continue

                tmdb_id = anime["tmdbId"]
                anime_id = anime["animeId"]
                logger.info(f"AI剧集组选择: 作品 ID {anime_id} (TMDB ID: {tmdb_id}) 有tmdbId但无剧集组，尝试自动选择...")

                try:
                    # 获取TMDB所有剧集组
                    tmdb_source = metadata_manager.sources.get("tmdb")
                    if not tmdb_source:
                        logger.warning("AI剧集组选择: TMDB元数据源未加载，跳过")
                        continue

                    virtual_user = models.User(id=0, username="match_ai_group")
                    all_groups = await tmdb_source.get_all_episode_groups(int(tmdb_id), virtual_user)

                    if not all_groups:
                        logger.info(f"AI剧集组选择: TMDB ID {tmdb_id} 没有剧集组，跳过")
                        continue

                    logger.info(f"AI剧集组选择: TMDB ID {tmdb_id} 找到 {len(all_groups)} 个剧集组: {[g.get('name') for g in all_groups]}")

                    # 使用混合策略选择最佳剧集组
                    selected_index = await ai_matcher_manager.select_best_episode_group(
                        title=parsed_info["title"],
                        season=parsed_info.get("season"),
                        episode=parsed_info.get("episode"),
                        episode_groups=all_groups
                    )

                    if selected_index is None:
                        logger.info(f"AI剧集组选择: 未能选择合适的剧集组，跳过")
                        continue

                    selected_group = all_groups[selected_index]
                    group_id = selected_group["id"]
                    logger.info(f"AI剧集组选择: 选中 '{selected_group.get('name')}' (ID: {group_id})")

                    # 下载并保存映射
                    await metadata_manager.update_tmdb_mappings(int(tmdb_id), group_id, virtual_user)

                    # 关联anime
                    await crud.update_anime_tmdb_group_id(session, anime_id, group_id)
                    logger.info(f"AI剧集组选择: 已为作品 ID {anime_id} 关联剧集组 {group_id}")

                    # 重试TMDB映射匹配
                    tmdb_results = await crud.find_episode_via_tmdb_mapping(
                        session,
                        tmdb_id=tmdb_id,
                        group_id=group_id,
                        custom_season=parsed_info.get("season"),
                        custom_episode=parsed_info.get("episode")
                    )
                    if tmdb_results:
                        logger.info(f"AI剧集组选择 + TMDB映射匹配成功，找到 {len(tmdb_results)} 个结果。")
                        res = tmdb_results[0]
                        dandan_type = DANDAN_TYPE_MAPPING.get(res.get('type'), "other")
                        dandan_type_desc = DANDAN_TYPE_DESC_MAPPING.get(res.get('type'), "其他")
                        match = DandanMatchInfo(
                            episodeId=res['episodeId'], animeId=res['animeId'], animeTitle=res['animeTitle'],
                            episodeTitle=res['episodeTitle'], type=dandan_type, typeDescription=dandan_type_desc,
                            imageUrl=res.get('imageUrl')
                        )
                        response = DandanMatchResponse(isMatched=True, matches=[match])
                        logger.info(f"发送匹配响应 (AI剧集组 + TMDB映射): {response.model_dump_json(indent=2)}")
                        return response
                    else:
                        logger.info(f"AI剧集组选择: 映射已保存但当前集数未在映射中找到匹配，继续后备搜索")

                except Exception as e:
                    logger.error(f"AI剧集组选择失败: {e}", exc_info=True)
    else:
        logger.info("检测到电影文件，跳过 TMDB 映射匹配。")

    # --- 步骤 3: 如果所有方法都失败 ---
    # 新增：后备机制 (Fallback Mechanism)
    fallback_enabled_str = await config_manager.get("matchFallbackEnabled", "false")
    if fallback_enabled_str.lower() == 'true':
        # 检查Token是否被允许使用匹配后备功能
        if current_token:
            try:
                # 获取当前token的信息
                token_stmt = select(orm_models.ApiToken).where(orm_models.ApiToken.token == current_token)
                token_result = await session.execute(token_stmt)
                current_token_obj = token_result.scalar_one_or_none()

                if current_token_obj:
                    # 获取允许的token列表
                    allowed_tokens_str = await config_manager.get("matchFallbackTokens", "[]")
                    allowed_token_ids = json.loads(allowed_tokens_str)

                    # 如果配置了允许的token列表且当前token不在列表中，跳过后备机制
                    if allowed_token_ids and current_token_obj.id not in allowed_token_ids:
                        logger.info(f"Token '{current_token_obj.name}' (ID: {current_token_obj.id}) 未被授权使用匹配后备功能，跳过后备机制。")
                        response = DandanMatchResponse(isMatched=False, matches=[])
                        logger.info(f"发送匹配响应 (Token未授权): {response.model_dump_json(indent=2)}")
                        return response
                    else:
                        logger.info(f"Token '{current_token_obj.name}' (ID: {current_token_obj.id}) 已被授权使用匹配后备功能。")
            except (json.JSONDecodeError, Exception) as e:
                logger.warning(f"检查匹配后备Token授权时发生错误: {e}，继续执行后备机制")

        # 检查黑名单
        blacklist_pattern = await config_manager.get("matchFallbackBlacklist", "")
        if blacklist_pattern.strip():
            try:
                if re.search(blacklist_pattern, item.fileName, re.IGNORECASE):
                    logger.info(f"文件 '{item.fileName}' 匹配黑名单规则 '{blacklist_pattern}'，跳过后备机制。")
                    response = DandanMatchResponse(isMatched=False, matches=[])
                    logger.info(f"发送匹配响应 (黑名单过滤): {response.model_dump_json(indent=2)}")
                    return response
            except re.error as e:
                logger.warning(f"黑名单正则表达式 '{blacklist_pattern}' 格式错误: {e}，忽略黑名单检查")

        # 方案C: 防重复机制 - 检查5分钟内是否已完成过相同的后备任务
        recent_fallback_key = f"recent_fallback_{parsed_info['title']}_{parsed_info.get('season')}_{parsed_info.get('episode')}"
        recent_fallback_data = await get_db_cache(session, FALLBACK_SEARCH_CACHE_PREFIX, recent_fallback_key)
        if recent_fallback_data:
            cached_time = recent_fallback_data.get("timestamp", 0)
            if time.time() - cached_time < 300:  # 5分钟内
                logger.info(f"检测到5分钟内已完成的后备任务，直接返回缓存结果")
                cached_response = recent_fallback_data.get("response")
                if cached_response:
                    return cached_response

        # 方案D: 整季缓存复用 - 同标题同季的不同集，复用之前的匹配结果
        season_cache_key = f"match_season_{parsed_info['title']}_{parsed_info.get('season', 1)}"
        season_cache = await get_db_cache(session, FALLBACK_SEARCH_CACHE_PREFIX, season_cache_key)
        if season_cache and not parsed_info.get("is_movie"):
            cached_time = season_cache.get("timestamp", 0)
            if time.time() - cached_time < 3600:  # 1小时内
                logger.info(f"整季缓存命中: {season_cache_key}，复用匹配结果（跳过搜索）")
                episode_number = parsed_info.get("episode") or 1

                # 从缓存中恢复匹配信息
                cached_provider = season_cache["provider"]
                cached_mediaId = season_cache["mediaId"]
                cached_real_anime_id = season_cache["real_anime_id"]
                cached_title = season_cache["final_title"]
                cached_season = season_cache["final_season"]
                cached_source_order = season_cache.get("source_order", 1)

                # 直接生成 episodeId
                from .bangumi import generate_episode_id
                real_episode_id = generate_episode_id(cached_real_anime_id, cached_source_order, episode_number)
                virtual_anime_id = season_cache.get("virtual_anime_id", 900000)

                # 存储本集的 episodeId 映射（供 comment 接口使用）
                episode_mapping_key = f"fallback_episode_{real_episode_id}"
                episode_mapping_data = {
                    "virtual_anime_id": virtual_anime_id,
                    "real_anime_id": cached_real_anime_id,
                    "provider": cached_provider,
                    "mediaId": cached_mediaId,
                    "episode_number": episode_number,
                    "final_title": cached_title,
                    "final_season": cached_season,
                    "media_type": season_cache.get("media_type", "tv_series"),
                    "imageUrl": season_cache.get("imageUrl"),
                    "year": season_cache.get("year"),
                    "timestamp": time.time()
                }
                await set_db_cache(session, FALLBACK_SEARCH_CACHE_PREFIX, episode_mapping_key, episode_mapping_data, FALLBACK_SEARCH_CACHE_TTL)

                match_result = DandanMatchInfo(
                    episodeId=real_episode_id,
                    animeId=virtual_anime_id,
                    animeTitle=cached_title,
                    episodeTitle=f"第{episode_number}集",
                    type="tvseries",
                    typeDescription="缓存匹配",
                    imageUrl=season_cache.get("imageUrl")
                )
                logger.info(f"整季缓存匹配: {cached_title} S{cached_season:02d}E{episode_number:02d} → episodeId={real_episode_id}")
                return DandanMatchResponse(isMatched=True, matches=[match_result])

        logger.info(f"匹配失败，已启用后备机制，正在为 '{item.fileName}' 创建自动搜索任务。")

        # 将匹配后备逻辑包装成协程工厂
        match_fallback_result = {"response": None}  # 用于存储结果
        task_id_ref = {"id": None}  # 用于在 coro_factory 内部回写更新后的标题

        async def match_fallback_coro_factory(session_inner: AsyncSession, progress_callback):
            """匹配后备任务的协程工厂"""
            # 初始化计时器并开始计时
            match_timer = SearchTimer(SEARCH_TYPE_FALLBACK_MATCH, item.fileName, logger)
            match_timer.start()

            try:
                match_timer.step_start("初始化与解析")
                # 构造 auto_search_and_import_task 需要的 payload
                # 根据 is_movie 标记判断媒体类型
                media_type_for_fallback = "movie" if parsed_info.get("is_movie") else "tv_series"

                logger.info(f"开始匹配后备流程: {item.fileName}")

                # 解析搜索关键词，提取纯标题
                search_parsed_info = parse_search_keyword(parsed_info["title"])
                base_title = search_parsed_info["title"]
                is_movie = parsed_info.get("is_movie", False)
                season = parsed_info.get("season") or 1
                # 电影不设置episode_number,保持为None
                episode_number = None if is_movie else (parsed_info.get("episode") or 1)

                # 【性能优化】并行预获取所有配置值
                _cfg_tasks = await asyncio.gather(
                    config_manager.get("matchFallbackEnableTmdbSeasonMapping", "false"),
                    config_manager.get("aiMatchEnabled", "false"),
                    config_manager.get("aiFallbackEnabled", "true"),
                    config_manager.get("externalApiFallbackEnabled", "false"),
                    config_manager.get("aiEpisodeGroupEnabled", "false"),
                )
                match_fallback_tmdb_enabled = _cfg_tasks[0]
                ai_match_enabled = _cfg_tasks[1].lower() == "true"
                ai_fallback_enabled = _cfg_tasks[2].lower() == "true"
                fallback_enabled = _cfg_tasks[3].lower() == "true"
                ai_episode_group_enabled = _cfg_tasks[4].lower() == "true"

                if match_fallback_tmdb_enabled.lower() != "true":
                    logger.info("○ 匹配后备 统一AI映射: 功能未启用")

                # 【性能优化】AI初始化预热：如果AI匹配已启用，提前开始初始化（不阻塞）
                ai_matcher_warmup_task = None
                if ai_match_enabled:
                    ai_matcher_warmup_task = asyncio.create_task(ai_matcher_manager.get_matcher())
                    logger.debug("AI匹配器预热已启动（并行）")

                match_timer.step_end()

                # 全并行优化：TMDB搜索 + 弹幕源搜索 同时启动
                # 注意：辅助源别名不需要单独调用，因为：
                #   - TMDB 别名已从 _do_tmdb_prefetch 获取
                #   - 360 补充已在 unified_search → search_all → supplement_empty_search_results 中处理
                pre_fetched_aliases = set()
                pre_fetched_equiv = None
                _prefetch_tmdb_id = None

                match_timer.step_start("并行搜索(弹幕源+TMDB)")

                # 定义 TMDB 搜索协程（获取别名+ID）
                async def _do_tmdb_prefetch():
                    if not (ai_episode_group_enabled and not is_movie):
                        return None, set()
                    tmdb_source = metadata_manager.sources.get("tmdb")
                    if not tmdb_source:
                        return None, set()
                    try:
                        virtual_user = models.User(id=0, username="match_prefetch")
                        tmdb_search_results = await tmdb_source.search(base_title, virtual_user, mediaType='tv')
                        aliases = set()
                        tmdb_id = None
                        if tmdb_search_results:
                            tmdb_id = tmdb_search_results[0].id
                            logger.info(f"TMDB预获取: 搜索命中 '{tmdb_search_results[0].title}' (ID: {tmdb_id})")
                            for sr in tmdb_search_results[:3]:
                                aliases.add(sr.title)
                                if sr.aliasesCn: aliases.update(sr.aliasesCn)
                                if sr.aliasesJp: aliases.update(sr.aliasesJp)
                                if sr.nameEn: aliases.add(sr.nameEn)
                                if sr.nameJp: aliases.add(sr.nameJp)
                        return tmdb_id, aliases
                    except Exception as e:
                        logger.warning(f"TMDB预获取搜索失败: {e}")
                        return None, set()

                # 定义弹幕源搜索协程
                async def _do_unified_search():
                    return await unified_search(
                        search_term=base_title,
                        session=session_inner,
                        scraper_manager=scraper_manager,
                        metadata_manager=metadata_manager,
                        use_alias_expansion=True,
                        use_alias_filtering=True,
                        use_title_filtering=True,
                        use_source_priority_sorting=False,
                        strict_filtering=True,
                        alias_similarity_threshold=70,
                        progress_callback=progress_callback
                    )

                # 并行启动：先 TMDB，再弹幕源
                tmdb_task = asyncio.create_task(_do_tmdb_prefetch())
                await asyncio.sleep(0)
                search_task = asyncio.create_task(_do_unified_search())

                # 等待完成
                (tmdb_id_result, tmdb_aliases), search_result = await asyncio.gather(
                    tmdb_task, search_task
                )

                _prefetch_tmdb_id = tmdb_id_result
                pre_fetched_aliases = tmdb_aliases
                all_results = search_result if isinstance(search_result, list) else []

                # 剧集组处理（需要 TMDB ID）
                if _prefetch_tmdb_id:
                    # 剧集组处理（需要 TMDB ID，串行但在搜索完成后执行）
                    try:
                        _vu = models.User(id=0, username="match_group")
                        _tmdb_src = metadata_manager.sources.get("tmdb")
                        if _tmdb_src:
                            all_groups = await _tmdb_src.get_all_episode_groups(int(_prefetch_tmdb_id), _vu)
                            if all_groups:
                                logger.info(f"剧集组: 找到 {len(all_groups)} 个剧集组")
                                selected_idx = await ai_matcher_manager.select_best_episode_group(
                                    title=base_title, season=season, episode=episode_number,
                                    episode_groups=all_groups
                                )
                                if selected_idx is not None:
                                    group_id = all_groups[selected_idx]["id"]
                                    logger.info(f"剧集组: 选中 '{all_groups[selected_idx].get('name')}' (ID: {group_id})")
                                    await metadata_manager.update_tmdb_mappings(int(_prefetch_tmdb_id), group_id, _vu)
                                    equiv = await crud.get_episode_equivalence(
                                        session_inner, group_id, season, episode_number
                                    )
                                    if equiv:
                                        pre_fetched_equiv = equiv
                                        logger.info(f"剧集组: 等价映射获取成功")
                    except Exception as eg_err:
                        logger.warning(f"剧集组处理失败: {eg_err}")

                    # 写入别名缓存
                    if pre_fetched_aliases:
                        from src.core.cache import get_cache_backend
                        alias_cache_key = f"search_aliases_{base_title}"
                        alias_data = json.dumps(list(pre_fetched_aliases))
                        _backend = get_cache_backend()
                        try:
                            if _backend is not None:
                                await _backend.set(alias_cache_key, alias_data, ttl=3600, region="search")
                            else:
                                await crud.set_cache(session_inner, alias_cache_key, alias_data, ttl_seconds=3600)
                        except Exception:
                            pass

                # 收集单源搜索耗时信息（分组显示）
                from src.utils.search_timer import SubStepTiming
                source_timing_sub_steps = []
                for name, dur, cnt in scraper_manager.last_search_timing:
                    if name.startswith("补充:"):
                        source_timing_sub_steps.append(
                            SubStepTiming(name=name[3:], duration_ms=dur, result_count=cnt, group="补充源")
                        )
                    else:
                        source_timing_sub_steps.append(
                            SubStepTiming(name=name, duration_ms=dur, result_count=cnt, group="弹幕源")
                        )
                # 辅助源别名计时
                if hasattr(metadata_manager, 'last_aux_search_timing') and metadata_manager.last_aux_search_timing:
                    for name, dur, cnt in metadata_manager.last_aux_search_timing:
                        source_timing_sub_steps.append(
                            SubStepTiming(name=name, duration_ms=dur, result_count=cnt, group="辅助源(别名)")
                        )

                if not all_results:
                    logger.warning(f"匹配后备失败：没有找到任何搜索结果")
                    match_timer.step_end(details="无结果", sub_steps=source_timing_sub_steps)
                    match_timer.finish()  # 打印计时报告
                    response = DandanMatchResponse(isMatched=False, matches=[])
                    match_fallback_result["response"] = response
                    return

                match_timer.step_end(details=f"{len(all_results)}个结果", sub_steps=source_timing_sub_steps)
                logger.info(f"搜索完成，共 {len(all_results)} 个结果")

                # 使用统一的AI类型和季度映射修正函数
                if match_fallback_tmdb_enabled.lower() == "true":
                    try:
                        match_timer.step_start("AI映射修正")
                        # 【性能优化】使用预热的AI匹配器
                        ai_matcher = None
                        if ai_matcher_warmup_task:
                            ai_matcher = await ai_matcher_warmup_task
                            ai_matcher_warmup_task = None  # 清空task，避免重复await
                        else:
                            ai_matcher = await ai_matcher_manager.get_matcher()
                        if ai_matcher:
                            logger.info(f"○ 匹配后备 开始统一AI映射修正: '{base_title}' ({len(all_results)} 个结果)")

                            # 使用新的统一函数进行类型和季度修正
                            mapping_result = await ai_type_and_season_mapping_and_correction(
                                search_title=base_title,
                                search_results=all_results,
                                metadata_manager=metadata_manager,
                                ai_matcher=ai_matcher,
                                logger=logger,
                                similarity_threshold=60.0
                            )

                            # 应用修正结果
                            if mapping_result['total_corrections'] > 0:
                                logger.info(f"✓ 匹配后备 统一AI映射成功: 总计修正了 {mapping_result['total_corrections']} 个结果")
                                logger.info(f"  - 类型修正: {len(mapping_result['type_corrections'])} 个")
                                logger.info(f"  - 季度修正: {len(mapping_result['season_corrections'])} 个")

                                # 更新搜索结果（已经直接修改了all_results）
                                all_results = mapping_result['corrected_results']
                                match_timer.step_end(details=f"修正{mapping_result['total_corrections']}个")
                            else:
                                logger.info(f"○ 匹配后备 统一AI映射: 未找到需要修正的信息")
                                match_timer.step_end(details="无修正")
                        else:
                            logger.warning("○ 匹配后备 AI映射: AI匹配器未启用或初始化失败")
                            match_timer.step_end(details="匹配器未启用")

                    except Exception as e:
                        logger.warning(f"匹配后备 统一AI映射任务执行失败: {e}")
                        match_timer.step_end(details=f"失败: {e}")
                else:
                    logger.info("○ 匹配后备 统一AI映射: 功能未启用")

                # 步骤2：智能排序 (类型匹配优先)
                match_timer.step_start("智能排序与匹配")

                # 确定目标类型
                target_type = "movie" if is_movie else "tv_series"

                # 获取源的优先级顺序
                source_settings = await crud.get_all_scraper_settings(session_inner)
                source_order_map = {s['providerName']: s['displayOrder'] for s in source_settings}

                def calculate_match_score(result):
                    """计算匹配分数，分数越高越优先"""
                    score = 0

                    # 1. 类型匹配 (最高优先级，+1000分)
                    if result.type == target_type:
                        score += 1000
                        logger.debug(f"  - {result.provider} - {result.title}: 类型匹配 +1000")

                    # 2. 标题相似度 (0-100分)
                    similarity = fuzz.token_set_ratio(base_title, result.title)
                    score += similarity
                    logger.debug(f"  - {result.provider} - {result.title}: 相似度{similarity} +{similarity}")

                    # 3. 年份匹配 (如果有年份信息，匹配+50分，不匹配-200分)
                    parsed_year = parsed_info.get("year")
                    if parsed_year and result.year:
                        if str(result.year) == str(parsed_year):
                            score += 50
                            logger.debug(f"  - {result.provider} - {result.title}: 年份匹配({parsed_year}) +50")
                        else:
                            score -= 200
                            logger.debug(f"  - {result.provider} - {result.title}: 年份不匹配({parsed_year}≠{result.year}) -200")

                    return score

                # 【性能优化】按分数排序 + 缓存分数（避免日志打印时重复计算）
                score_cache = {}  # 缓存每个结果的分数
                for result in all_results:
                    score_cache[id(result)] = calculate_match_score(result)

                sorted_results = sorted(
                    all_results,
                    key=lambda r: (score_cache[id(r)], -source_order_map.get(r.provider, 999)),
                    reverse=True
                )

                # 打印排序后的结果列表（使用缓存的分数）
                lines = [f"步骤2：智能排序 - 排序后的搜索结果列表 (共 {len(sorted_results)} 条, 按匹配分数):"]
                for idx, result in enumerate(sorted_results, 1):
                    score = score_cache[id(result)]  # 直接从缓存获取
                    type_match = "✓" if result.type == target_type else "✗"
                    lines.append(f"  {idx}. [{type_match}] {result.provider} - {result.title} (ID: {result.mediaId}, 类型: {result.type}, 年份: {result.year or 'N/A'}, 分数: {score:.0f})")
                logger.info("\n".join(lines))

                # 步骤3：自动选择最佳源
                logger.info(f"步骤3：自动选择最佳源")

                # 【性能优化】批量查询精确标记信息（1次IN查询替代N次单独查询）
                favorited_info = {}
                provider_media_pairs = [(r.provider, r.mediaId) for r in sorted_results]
                if provider_media_pairs:
                    from sqlalchemy import tuple_
                    favorited_stmt = (
                        select(AnimeSource.providerName, AnimeSource.mediaId)
                        .where(
                            tuple_(AnimeSource.providerName, AnimeSource.mediaId).in_(provider_media_pairs),
                            AnimeSource.isFavorited == True
                        )
                    )
                    favorited_rows = await session_inner.execute(favorited_stmt)
                    for row in favorited_rows:
                        key = f"{row.providerName}:{row.mediaId}"
                        favorited_info[key] = True

                # 【性能优化】使用预获取的配置值（不再重复获取）
                # ai_match_enabled, ai_fallback_enabled, fallback_enabled 已在初始化阶段获取

                # 如果启用AI匹配，尝试使用AI选择
                ai_selected_index = None
                if ai_match_enabled:
                    try:
                        # 动态注册AI提示词配置(如果不存在则创建,使用硬编码默认值)
                        await crud.initialize_configs(session_inner, {
                            "aiMatchPrompt": (DEFAULT_AI_MATCH_PROMPT, "AI智能匹配提示词")
                        })

                        # 构建查询信息
                        query_info = {
                            "title": base_title,
                            "season": season,
                            "episode": episode_number,
                            "year": None,  # 匹配后备场景通常没有年份信息
                            "type": "movie" if is_movie else "tv_series"
                        }

                        # 等价上下文注入：利用剧集组映射帮助AI更准确地选择弹幕源
                        episode_group_context = None

                        if not is_movie and ai_episode_group_enabled:
                            # 方式1: 从库内已有作品获取等价信息
                            for pa in potential_animes:
                                pa_group_id = pa.get("tmdbEpisodeGroupId")
                                if pa.get("tmdbId") and pa_group_id:
                                    try:
                                        equiv = await crud.get_episode_equivalence(
                                            session_inner, pa_group_id, season, episode_number
                                        )
                                        if equiv:
                                            episode_group_context = equiv
                                            logger.info(f"等价上下文(库内): 从作品ID {pa['animeId']} 获取映射")
                                            break
                                    except Exception as eq_err:
                                        logger.debug(f"等价上下文(库内)查询失败: {eq_err}")

                            # 方式2: 使用预获取的等价映射（已在弹幕源搜索前完成，无需重复搜索TMDB）
                            if not episode_group_context and pre_fetched_equiv:
                                episode_group_context = pre_fetched_equiv
                                logger.info(f"等价上下文(预获取): 使用预获取的TMDB等价映射")

                        if episode_group_context:
                            query_info["episode_group_context"] = episode_group_context
                            logger.info(
                                f"等价上下文注入: S{season}E{episode_number} "
                                f"↔ custom=S{episode_group_context['custom_season']}E{episode_group_context['custom_episode']} "
                                f"/ tmdb=S{episode_group_context['tmdb_season']}E{episode_group_context['tmdb_episode']} "
                                f"(方向: {episode_group_context['match_direction']}, 该季{episode_group_context['season_total_episodes']}集)"
                            )

                        # 使用AIMatcherManager进行匹配
                        ai_selected_index = await ai_matcher_manager.select_best_match(
                            query_info, sorted_results, favorited_info
                        )

                        if ai_selected_index is None:
                            # 使用预获取的配置值
                            if ai_fallback_enabled:
                                logger.info("AI匹配未找到合适结果，降级到传统匹配")
                            else:
                                logger.warning("AI匹配未找到合适结果，且传统匹配兜底已禁用，将不使用任何结果")

                    except Exception as e:
                        # 使用预获取的配置值
                        if ai_fallback_enabled:
                            logger.error(f"AI匹配失败，降级到传统匹配: {e}", exc_info=True)
                        else:
                            logger.error(f"AI匹配失败，且传统匹配兜底已禁用: {e}", exc_info=True)
                        ai_selected_index = None

                # 使用预获取的配置值
                # fallback_enabled 已在初始化阶段获取

                best_match = None

                # 如果AI选择成功，使用AI选择的结果
                if ai_selected_index is not None:
                    best_match = sorted_results[ai_selected_index]
                    logger.info(f"  - 使用AI选择的结果: {best_match.provider} - {best_match.title}")
                elif ai_match_enabled:
                    # AI匹配已启用但失败，使用预获取的配置值
                    if not ai_fallback_enabled:
                        logger.warning("AI匹配失败且传统匹配兜底已禁用，匹配后备失败")
                        return DandanMatchResponse(isMatched=False, matches=[])
                    # 允许降级，继续使用传统匹配
                    logger.info("AI匹配失败，使用传统匹配兜底")
                    # 传统匹配: 优先查找精确标记源 (需验证类型匹配和标题相似度)
                    favorited_match = None
                    for result in sorted_results:
                        key = f"{result.provider}:{result.mediaId}"
                        if favorited_info.get(key):
                            # 验证类型匹配和标题相似度
                            type_matched = result.type == target_type
                            similarity = fuzz.token_set_ratio(base_title, result.title)
                            logger.info(f"  - 找到精确标记源: {result.provider} - {result.title} "
                                       f"(类型: {result.type}, 类型匹配: {'✓' if type_matched else '✗'}, 相似度: {similarity}%)")

                            # 必须满足：类型匹配 AND 相似度 >= 70%
                            if type_matched and similarity >= 70:
                                favorited_match = result
                                logger.info(f"  - 精确标记源验证通过 (类型匹配: ✓, 相似度: {similarity}% >= 70%)")
                                break
                            else:
                                logger.warning(f"  - 精确标记源验证失败 (类型匹配: {'✓' if type_matched else '✗'}, "
                                             f"相似度: {similarity}% {'<' if similarity < 70 else '>='} 70%)，跳过")

                    if favorited_match:
                        best_match = favorited_match
                        logger.info(f"  - 使用精确标记源: {best_match.provider} - {best_match.title}")
                    elif not fallback_enabled:
                        # 顺延机制关闭，验证第一个结果是否满足条件
                        if sorted_results:
                            first_result = sorted_results[0]
                            type_matched = first_result.type == target_type
                            similarity = fuzz.token_set_ratio(base_title, first_result.title)
                            score = score_cache.get(id(first_result), calculate_match_score(first_result))

                            # 必须满足：类型匹配 AND 相似度 >= 70%
                            if type_matched and similarity >= 70:
                                best_match = first_result
                                logger.info(f"  - 传统匹配成功: {first_result.provider} - {first_result.title} "
                                           f"(类型匹配: ✓, 相似度: {similarity}%, 总分: {score})")
                            else:
                                best_match = None
                                logger.warning(f"  - 传统匹配失败: 第一个结果不满足条件 "
                                             f"(类型匹配: {'✓' if type_matched else '✗'}, 相似度: {similarity}%, 要求: ≥70%)")
                        else:
                            best_match = None
                            logger.warning("  - 传统匹配失败: 没有搜索结果")
                else:
                    # AI未启用，使用传统匹配
                    # 传统匹配: 优先查找精确标记源 (需验证类型匹配和标题相似度)
                    favorited_match = None
                    for result in sorted_results:
                        key = f"{result.provider}:{result.mediaId}"
                        if favorited_info.get(key):
                            # 验证类型匹配和标题相似度
                            type_matched = result.type == target_type
                            similarity = fuzz.token_set_ratio(base_title, result.title)
                            logger.info(f"  - 找到精确标记源: {result.provider} - {result.title} "
                                       f"(类型: {result.type}, 类型匹配: {'✓' if type_matched else '✗'}, 相似度: {similarity}%)")

                            # 必须满足：类型匹配 AND 相似度 >= 70%
                            if type_matched and similarity >= 70:
                                favorited_match = result
                                logger.info(f"  - 精确标记源验证通过 (类型匹配: ✓, 相似度: {similarity}% >= 70%)")
                                break
                            else:
                                logger.warning(f"  - 精确标记源验证失败 (类型匹配: {'✓' if type_matched else '✗'}, "
                                             f"相似度: {similarity}% {'<' if similarity < 70 else '>='} 70%)，跳过")

                    if favorited_match:
                        best_match = favorited_match
                        logger.info(f"  - 使用精确标记源: {best_match.provider} - {best_match.title}")
                    elif not fallback_enabled:
                        # 顺延机制关闭，验证第一个结果是否满足条件
                        if sorted_results:
                            first_result = sorted_results[0]
                            type_matched = first_result.type == target_type
                            similarity = fuzz.token_set_ratio(base_title, first_result.title)
                            score = score_cache.get(id(first_result), calculate_match_score(first_result))

                            # 必须满足：类型匹配 AND 相似度 >= 70%
                            if type_matched and similarity >= 70:
                                best_match = first_result
                                logger.info(f"  - 传统匹配成功: {first_result.provider} - {first_result.title} "
                                           f"(类型匹配: ✓, 相似度: {similarity}%, 总分: {score})")
                            else:
                                best_match = None
                                logger.warning(f"  - 传统匹配失败: 第一个结果不满足条件 "
                                             f"(类型匹配: {'✓' if type_matched else '✗'}, 相似度: {similarity}%, 要求: ≥70%)")
                        else:
                            best_match = None
                            logger.warning("  - 传统匹配失败: 没有搜索结果")

                # 用于保存从来源端获取的剧集标题
                matched_episode_title = None

                # ===== 反向偏移：将文件名/用户传入的偏移后集号还原为源站原始集号 =====
                # episode_number 来自文件名解析，等同于用户期望的集号（偏移后的值）
                # 需要反向偏移才能在源站找到正确的分集
                source_episode_number = episode_number
                if title_recognition_manager and episode_number is not None and not is_movie:
                    try:
                        reversed_ep = await title_recognition_manager.reverse_episode_offset(
                            base_title, episode_number, None  # 这里还不知道具体provider
                        )
                        if reversed_ep != episode_number:
                            logger.info(f"  - 反向偏移: 文件集号{episode_number} => 源站集号{reversed_ep}")
                            source_episode_number = reversed_ep
                    except Exception as e:
                        logger.warning(f"  - 反向偏移失败，使用原始集号: {e}")

                if best_match is None and fallback_enabled:
                    # 顺延机制启用：依次验证候选源 (按分数从高到低)
                    logger.info(f"  - 顺延机制启用，依次验证候选源")
                    for attempt, candidate in enumerate(sorted_results, 1):
                        logger.info(f"    {attempt}. 正在验证: {candidate.provider} - {candidate.title} (ID: {candidate.mediaId}, 类型: {candidate.type})")
                        try:
                            scraper = scraper_manager.get_scraper(candidate.provider)
                            if not scraper:
                                logger.warning(f"    {attempt}. {candidate.provider} - 无法获取scraper，跳过")
                                continue

                            # 获取分集列表进行验证
                            episodes = await scraper_manager.get_episodes_routed(candidate.provider, candidate.mediaId, db_media_type=candidate.type)
                            if not episodes:
                                logger.warning(f"    {attempt}. {candidate.provider} - 没有分集列表，跳过")
                                continue

                            # 如果用户搜索的是电影，只匹配电影类型的候选源
                            if is_movie:
                                if candidate.type != "movie":
                                    logger.warning(f"    {attempt}. {candidate.provider} - 类型不匹配 (搜索电影，但候选源是{candidate.type})，跳过")
                                    continue
                                logger.info(f"    {attempt}. {candidate.provider} - 验证通过 (电影)")
                            else:
                                # 非电影场景，也需要验证候选源类型匹配
                                if candidate.type != target_type:
                                    logger.warning(f"    {attempt}. {candidate.provider} - 类型不匹配({candidate.type}≠{target_type})，跳过")
                                    continue
                            # 如果指定了集数，检查是否有目标集数
                            # 使用反向偏移后的 source_episode_number 去匹配源站分集
                            if not is_movie and source_episode_number is not None:
                                target_episode = None
                                for ep in episodes:
                                    if ep.episodeIndex == source_episode_number:
                                        target_episode = ep
                                        break

                                if not target_episode:
                                    logger.warning(f"    {attempt}. {candidate.provider} - 没有第 {source_episode_number} 集"
                                                   f"{f' (原始集号: {episode_number})' if source_episode_number != episode_number else ''}，跳过")
                                    continue

                                # 保存来源端的剧集标题
                                matched_episode_title = target_episode.title
                                logger.info(f"    {attempt}. {candidate.provider} - 验证通过，剧集标题: '{matched_episode_title}'")
                            else:
                                logger.info(f"    {attempt}. {candidate.provider} - 验证通过")
                            best_match = candidate
                            break
                        except Exception as e:
                            logger.warning(f"    {attempt}. {candidate.provider} - 验证失败: {e}")
                            continue

                if not best_match:
                    logger.warning(f"匹配后备失败：所有候选源都无法提供有效分集")
                    match_timer.step_end(details="无有效分集")
                    match_timer.finish()  # 打印计时报告
                    response = DandanMatchResponse(isMatched=False, matches=[])
                    match_fallback_result["response"] = response
                    return

                # 如果还没有获取剧集标题（非顺延机制匹配成功的情况），主动获取
                # 使用反向偏移后的 source_episode_number 匹配源站分集
                if matched_episode_title is None and not is_movie and source_episode_number is not None:
                    try:
                        episodes = await scraper_manager.get_episodes_routed(best_match.provider, best_match.mediaId, db_media_type=best_match.type)
                        if episodes:
                            for ep in episodes:
                                if ep.episodeIndex == source_episode_number:
                                    matched_episode_title = ep.title
                                    logger.info(f"  - 获取到来源端剧集标题: '{matched_episode_title}'"
                                               f"{f' (源站集号: {source_episode_number})' if source_episode_number != episode_number else ''}")
                                    break
                    except Exception as e:
                        logger.warning(f"  - 获取剧集标题失败: {e}")

                # 步骤4：应用入库后处理规则
                # 关键：传入源站原始集号(source_episode_number)，让 partial_offset 正向偏移为存储集号
                logger.info(f"步骤4：应用入库后处理规则")
                final_title = best_match.title
                final_season = season if season is not None else 1  # 默认为第1季
                if title_recognition_manager:
                    converted_title, converted_season, was_converted, _, converted_episode = await title_recognition_manager.apply_storage_postprocessing(
                        best_match.title, season, best_match.provider, episode=source_episode_number
                    )
                    if was_converted:
                        final_title = converted_title
                        final_season = converted_season if converted_season is not None else 1
                        logger.info(f"  - 应用入库后处理: '{best_match.title}' S{season or 1:02d} -> '{final_title}' S{final_season:02d}")
                    # 无论标题/季度是否转换，都需独立检查集数偏移（partial_offset 规则）
                    if converted_episode is not None and converted_episode != source_episode_number:
                        logger.info(f"  - 应用入库后处理集数偏移: 源站第{source_episode_number}集 -> 存储第{converted_episode}集")
                        episode_number = converted_episode

                # 选出 best_match 后，更新任务标题以显示来源和媒体ID
                if task_id_ref["id"]:
                    ep_label = f" 第{episode_number}集" if episode_number and not is_movie else ""
                    new_title = f"匹配后备: {final_title}{ep_label} [{best_match.provider}:{best_match.mediaId}]"
                    try:
                        await crud.update_task_title_in_history(session_inner, task_id_ref["id"], new_title)
                    except Exception as _title_err:
                        logger.debug(f"更新任务标题失败（非关键）: {_title_err}")

                # 步骤5：分配虚拟animeId和真实episodeId
                logger.info(f"步骤5：分配虚拟animeId和真实episodeId")

                # 分配虚拟animeId
                virtual_anime_id = await get_next_virtual_anime_id(session_inner)
                logger.info(f"  - 分配虚拟animeId: {virtual_anime_id}")

                # 分配真实anime_id（用于生成episodeId）
                # 注意：Anime和AnimeSource已经在orm_models中定义，这里直接使用
                stmt = select(orm_models.Anime.id, orm_models.Anime.title).where(
                    orm_models.Anime.title == final_title,
                    orm_models.Anime.season == final_season
                )
                result = await session_inner.execute(stmt)
                existing_db_anime = result.mappings().first()

                if existing_db_anime:
                    real_anime_id = existing_db_anime['id']
                    logger.info(f"  - 复用已存在的番剧: '{final_title}' (real_anime_id={real_anime_id})")
                else:
                    real_anime_id = await get_next_real_anime_id(session_inner)
                    logger.info(f"  - 分配新的real_anime_id: {real_anime_id}")

                # 获取或创建source，以获取正确的source_order
                # 检查是否已有该源
                source_stmt = select(orm_models.AnimeSource.id, orm_models.AnimeSource.sourceOrder).where(
                    orm_models.AnimeSource.animeId == real_anime_id,
                    orm_models.AnimeSource.providerName == best_match.provider,
                    orm_models.AnimeSource.mediaId == best_match.mediaId
                )
                source_result = await session_inner.execute(source_stmt)
                existing_source = source_result.mappings().first()

                if existing_source:
                    source_order = existing_source['sourceOrder']
                    logger.info(f"  - 复用已存在的源: source_order={source_order}")
                else:
                    # 查找当前最大的source_order
                    max_order_stmt = select(func.max(orm_models.AnimeSource.sourceOrder)).where(orm_models.AnimeSource.animeId == real_anime_id)
                    max_order_result = await session_inner.execute(max_order_stmt)
                    current_max_order = max_order_result.scalar_one_or_none()
                    logger.debug(f"  - 查询到的最大source_order: {current_max_order}")
                    source_order = (current_max_order or 0) + 1
                    logger.info(f"  - 分配新的source_order: {source_order}")

                # 生成真实episodeId (电影使用1作为episode_number)
                final_episode_number = 1 if is_movie else episode_number
                real_episode_id = generate_episode_id(real_anime_id, source_order, final_episode_number)
                logger.info(f"  - 生成真实episodeId: {real_episode_id}")

                # 步骤6：存储映射关系到数据库缓存
                mapping_key = f"fallback_anime_{virtual_anime_id}"
                mapping_data = {
                    "real_anime_id": real_anime_id,
                    "provider": best_match.provider,
                    "mediaId": best_match.mediaId,
                    "final_title": final_title,
                    "final_season": final_season,
                    "media_type": best_match.type,
                    "imageUrl": best_match.imageUrl,
                    "year": best_match.year,
                    "timestamp": time.time()
                }
                await set_db_cache(session_inner, FALLBACK_SEARCH_CACHE_PREFIX, mapping_key, mapping_data, FALLBACK_SEARCH_CACHE_TTL)

                # 存储episodeId映射
                episode_mapping_key = f"fallback_episode_{real_episode_id}"
                episode_mapping_data = {
                    "virtual_anime_id": virtual_anime_id,
                    "real_anime_id": real_anime_id,
                    "provider": best_match.provider,
                    "mediaId": best_match.mediaId,
                    "episode_number": final_episode_number,  # 使用final_episode_number (电影为1)
                    "final_title": final_title,
                    "final_season": final_season,
                    "media_type": best_match.type,
                    "imageUrl": best_match.imageUrl,
                    "year": best_match.year,
                    "timestamp": time.time()
                }
                await set_db_cache(session_inner, FALLBACK_SEARCH_CACHE_PREFIX, episode_mapping_key, episode_mapping_data, FALLBACK_SEARCH_CACHE_TTL)

                logger.info(f"匹配后备完成: virtual_anime_id={virtual_anime_id}, real_anime_id={real_anime_id}, episodeId={real_episode_id}")

                # 不在 match 时写入数据库，由 comment 接口在下载弹幕成功后才创建记录
                # 缓存映射已在上方存储（fallback_anime_ / fallback_episode_），comment 接口会读取

                # 返回真实的匹配结果
                match_result = DandanMatchInfo(
                    episodeId=real_episode_id,
                    animeId=virtual_anime_id,  # 返回虚拟animeId
                    animeTitle=final_title,
                    episodeTitle=f"第{episode_number}集" if not parsed_info.get("is_movie") else final_title,
                    type="tvseries" if not parsed_info.get("is_movie") else "movie",
                    typeDescription="匹配成功",
                    imageUrl=best_match.imageUrl
                )
                response = DandanMatchResponse(isMatched=True, matches=[match_result])
                logger.info(f"发送匹配响应 (匹配后备): episodeId={real_episode_id}, animeId={virtual_anime_id}")

                # 存储到防重复缓存（按集）
                recent_fallback_key = f"recent_fallback_{parsed_info['title']}_{parsed_info.get('season')}_{parsed_info.get('episode')}"
                recent_fallback_data = {
                    "response": response,
                    "timestamp": time.time()
                }
                await set_db_cache(session, FALLBACK_SEARCH_CACHE_PREFIX, recent_fallback_key, recent_fallback_data, 300)  # 5分钟TTL

                # 存储整季缓存（TV系列专用，1小时TTL）
                if not is_movie:
                    season_cache_key = f"match_season_{parsed_info['title']}_{parsed_info.get('season', 1)}"
                    season_cache_data = {
                        "provider": best_match.provider,
                        "mediaId": best_match.mediaId,
                        "real_anime_id": real_anime_id,
                        "virtual_anime_id": virtual_anime_id,
                        "final_title": final_title,
                        "final_season": final_season,
                        "source_order": source_order,
                        "media_type": best_match.type,
                        "imageUrl": best_match.imageUrl,
                        "year": best_match.year,
                        "timestamp": time.time()
                    }
                    await set_db_cache(session_inner, FALLBACK_SEARCH_CACHE_PREFIX, season_cache_key, season_cache_data, 3600)
                    logger.info(f"整季缓存已存储: {season_cache_key}")

                match_timer.step_end(details="匹配成功")
                match_timer.finish()  # 打印计时报告
                match_fallback_result["response"] = response
                # 保存匹配详情供后续使用
                match_fallback_result["match_details"] = {
                    "provider": best_match.provider,
                    "mediaId": best_match.mediaId,
                    "final_title": final_title,
                    "final_season": final_season,
                    "episode_number": episode_number,
                    "is_movie": is_movie
                }
                # 将匹配详情写入 task 对象的 parameters（供通知消息使用）
                _tid = task_id_ref.get("id")
                if _tid:
                    task_manager.update_task_parameters(_tid, match_fallback_result["match_details"])
                # 构造包含匹配详情的成功消息
                if is_movie:
                    success_msg = f"匹配成功：[{best_match.provider}] {final_title}"
                else:
                    success_msg = f"匹配成功：[{best_match.provider}] {final_title} S{final_season:02d}E{episode_number:02d}"
                raise TaskSuccess(success_msg)
            except TaskSuccess:
                raise  # 让 TaskSuccess 穿透到任务管理器处理
            except Exception as e:
                logger.error(f"匹配后备失败: {e}", exc_info=True)
                match_timer.step_end(details=f"失败: {e}")
                match_timer.finish()  # 打印计时报告
                response = DandanMatchResponse(isMatched=False, matches=[])
                match_fallback_result["response"] = response

        # 提交匹配后备任务(立即执行模式)
        try:
            task_title = f"匹配后备: {item.fileName}"
            task_id, done_event = await task_manager.submit_task(
                match_fallback_coro_factory,
                task_title,
                run_immediately=True,
                queue_type="fallback",
                task_parameters={"file_name": item.fileName}
            )
            task_id_ref["id"] = task_id  # 赋值后 coro_factory 内部才能用来更新标题
            logger.info(f"匹配后备任务已提交: {task_id}")

            # 等待任务完成(最多30秒)
            try:
                await asyncio.wait_for(done_event.wait(), timeout=30.0)
                logger.info(f"匹配后备任务完成: {task_id}")
            except asyncio.TimeoutError:
                logger.warning(f"匹配后备任务超时: {task_id}")
                match_fallback_result["response"] = DandanMatchResponse(isMatched=False, matches=[])

            # 返回结果
            if match_fallback_result["response"]:
                return match_fallback_result["response"]
            else:
                return DandanMatchResponse(isMatched=False, matches=[])
        except Exception as e:
            logger.error(f"提交匹配后备任务失败: {e}", exc_info=True)
            return DandanMatchResponse(isMatched=False, matches=[])

    response = DandanMatchResponse(isMatched=False, matches=[])
    logger.info(f"发送匹配响应 (所有方法均未匹配): {response.model_dump_json(indent=2)}")
    return response


# ==================== 路由定义 ====================

# 创建匹配路由器
match_router = APIRouter(route_class=DandanApiRoute)


@match_router.post(
    "/match",
    response_model=DandanMatchResponse,
    summary="[dandanplay兼容] 匹配单个文件"
)
async def match_single_file(
    request: DandanBatchMatchRequestItem,
    token: str = Depends(get_token_from_path),
    session: AsyncSession = Depends(get_db_session),
    task_manager: TaskManager = Depends(get_task_manager),
    scraper_manager: ScraperManager = Depends(get_scraper_manager),
    metadata_manager: MetadataSourceManager = Depends(get_metadata_manager),
    config_manager: ConfigManager = Depends(get_config_manager),
    ai_matcher_manager: AIMatcherManager = Depends(get_ai_matcher_manager),
    rate_limiter: RateLimiter = Depends(get_rate_limiter),
    title_recognition_manager = Depends(get_title_recognition_manager)
):
    """
    通过文件名匹配弹幕库。此接口不使用文件Hash。
    优先进行库内直接匹配，失败后回退到TMDB剧集组映射。
    """
    return await get_match_for_item(
        request, session, task_manager, scraper_manager,
        metadata_manager, config_manager, ai_matcher_manager, rate_limiter, title_recognition_manager,
        current_token=token
    )


@match_router.post(
    "/match/batch",
    response_model=List[DandanMatchResponse],
    summary="[dandanplay兼容] 批量匹配文件"
)
async def match_batch_files(
    request: DandanBatchMatchRequest,
    token: str = Depends(get_token_from_path),
    session: AsyncSession = Depends(get_db_session),
    task_manager: TaskManager = Depends(get_task_manager),
    scraper_manager: ScraperManager = Depends(get_scraper_manager),
    metadata_manager: MetadataSourceManager = Depends(get_metadata_manager),
    config_manager: ConfigManager = Depends(get_config_manager),
    ai_matcher_manager: AIMatcherManager = Depends(get_ai_matcher_manager),
    rate_limiter: RateLimiter = Depends(get_rate_limiter),
    title_recognition_manager = Depends(get_title_recognition_manager)
):
    """
    批量匹配文件。
    """
    if len(request.requests) > 32:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="批量匹配请求不能超过32个文件。")

    tasks = [
        get_match_for_item(
            item, session, task_manager, scraper_manager, metadata_manager, config_manager, ai_matcher_manager, rate_limiter, title_recognition_manager,
            current_token=token
        ) for item in request.requests
    ]
    results = await asyncio.gather(*tasks)
    return results