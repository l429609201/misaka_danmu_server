"""
文件名匹配业务流程

包含：
- 文件名解析和匹配的完整业务逻辑
- 库内匹配、后备搜索、AI匹配的级联流程
- TMDB剧集组映射支持
"""

import asyncio
import json
import logging
import re
import time
from typing import Any, Awaitable, Callable, Optional

from sqlalchemy.ext.asyncio import AsyncSession
from thefuzz import fuzz

# 仅保留实际使用的顶层依赖，数据库访问统一经服务层。
from src.ai.ai_prompts import DEFAULT_AI_MATCH_PROMPT
from src.services.ai_service import get_ai_service
from src.services.cache_service import get_cache_service
from src.schemas import User
from src.schemas.dandan import (
    DandanBatchMatchRequestItem,
    DandanMatchResponse,
    DandanMatchInfo,
)
from src.services.service_container import (
    get_database_service,
    get_task_manager,
    get_scraper_manager,
    get_metadata_service,
    get_title_recognition_manager,
)
from src.services.config_service import get_config_service
from src.workflows.match.helpers import (
    _build_match_info_from_row,
    _matched_response_from_row,
    parse_filename_for_match,
)
from src.utils.dandan.constants import (
    FALLBACK_SEARCH_CACHE_PREFIX,
    FALLBACK_SEARCH_CACHE_TTL,
    SEARCH_TYPE_FALLBACK_MATCH,
)
from src.utils.parsing.filename_parser import format_parse_result_log
from src.utils.diagnostics.search_timer import SearchTimer, SubStepTiming
from src.utils.diagnostics.task_exceptions import TaskSuccess, TaskFailed
from src.workflows.bangumi.helpers import generate_episode_id

# 查询和编排依赖统一置顶，数据库访问仅经 DatabaseService。
from src.utils.parsing.filename_parser import parse_search_keyword
from src.workflows.search.ai_correction import correct_search_results
from src.workflows.search.engine import unified_search

from src.workflows.dandan.helpers import get_next_virtual_anime_id

logger = logging.getLogger(__name__)


# ═══════════ 缓存读写薄壳（已收口至 CacheService） ═══════════
# why: 实现收口到 CacheService.get_with_prefix / set_with_prefix，服务层自持
#      驱动与数据库回退，不再需要显式 session。此处保留 session 形参以兼容
#      本模块内既有的 8 处调用签名，函数体内不使用该参数。


async def get_db_cache(session: AsyncSession, prefix: str, key: str) -> Optional[Any]:
    """按前缀组合键读取缓存。

    Args:
        session: 保留形参以兼容既有调用点，实际不使用
        prefix: 缓存键前缀
        key: 业务键

    Returns:
        缓存值；未命中或缓存服务未初始化时返回 None
    """
    cache = get_cache_service()
    if cache is None:
        logger.debug(f"缓存服务未初始化，读取降级为 None: {prefix}{key}")
        return None
    return await cache.get_with_prefix(prefix, key)


async def set_db_cache(
    session: AsyncSession, prefix: str, key: str, value: Any, ttl: int
) -> None:
    """按前缀组合键写入缓存。

    Args:
        session: 保留形参以兼容既有调用点，实际不使用
        prefix: 缓存键前缀
        key: 业务键
        value: 待缓存的值
        ttl: 过期时间（秒）
    """
    cache = get_cache_service()
    if cache is None:
        logger.debug(f"缓存服务未初始化，跳过写入: {prefix}{key}")
        return
    await cache.set_with_prefix(prefix, key, value, ttl)


async def get_match_for_item(
    item: DandanBatchMatchRequestItem,
    token: str
) -> DandanMatchResponse:
    """
    通过文件名匹配弹幕库的核心逻辑

    Args:
        item: 匹配请求项（包含文件名等信息）
        token: 用户token

    Returns:
        DandanMatchResponse: 匹配响应
    """
    # 使用全局单例获取所有服务
    db = get_database_service()
    task_manager = get_task_manager()
    scraper_manager = get_scraper_manager()
    metadata_manager = get_metadata_service()
    # 与启动阶段共享唯一 AI 服务，避免访问已停用的管理器容器。
    ai_service = get_ai_service()
    title_recognition_manager = get_title_recognition_manager()

    logger.info(f"执行匹配逻辑, 文件名: '{item.fileName}'")
    parsed_info = parse_filename_for_match(item.fileName)
    # 用统一格式化函数逐行打印解析结果（含年份/分辨率/发布组等全字段）
    logger.info(format_parse_result_log("后备匹配", item.fileName, parsed_info))
    if not parsed_info:
        response = DandanMatchResponse(isMatched=False)
        logger.info(f"发送匹配响应 (解析失败): {response.model_dump_json(indent=2)}")
        return response

    # --- 步骤 1: 优先进行库内直接搜索 ---
    logger.info("正在进行库内直接搜索...")
    # 查询仓储必须使用事务内的会话；不复用已关闭的会话。
    async with db.transaction():
        results = await db.episode.search_in_library(
            keyword=parsed_info["title"],
            season=parsed_info.get("season"),
            episode=parsed_info.get("episode")
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
                response = _matched_response_from_row(res, "精确标记匹配")
                return response

            # 如果没有精确标记，检查所有匹配项是否都指向同一个番剧ID
            first_animeId = results[0]['animeId']
            all_from_same_anime = all(res['animeId'] == first_animeId for res in results)

            if all_from_same_anime:
                res = results[0]
                response = _matched_response_from_row(res, "单一作品匹配")
                return response

            # 如果匹配到了多个不同的番剧，则返回所有结果让用户选择
            matches = []
            for res in results:
                matches.append(_build_match_info_from_row(res))
            response = DandanMatchResponse(isMatched=False, matches=matches)
            logger.info(f"发送匹配响应 (多个匹配): {response.model_dump_json(indent=2)}")
            return response

    # --- 步骤 2: 如果直接搜索无果，则回退到 TMDB 映射 ---
    # 注意：TMDB映射仅适用于TV系列，电影跳过此步骤
    potential_animes = []
    if not parsed_info.get("is_movie"):
        logger.info("直接搜索未找到精确匹配，回退到 TMDB 映射匹配。")
        db = get_database_service()
        async with db.transaction():
            # 数据域代理统一暴露复杂查询，避免访问已移除的 _query 属性。
            potential_animes = await db.anime.find_animes_for_matching(parsed_info["title"])
        logger.info(f"为标题 '{parsed_info['title']}' 找到 {len(potential_animes)} 个可能的库内作品进行TMDB匹配。")

        for anime in potential_animes:
            if anime.get("tmdbId") and anime.get("tmdbEpisodeGroupId"):
                logger.info(f"正在为作品 ID {anime['animeId']} (TMDB ID: {anime['tmdbId']}) 尝试 TMDB 映射匹配...")
                async with db.transaction():
                    tmdb_results = await db.episode.find_episode_via_tmdb_mapping(
                        tmdb_id=anime["tmdbId"],
                        group_id=anime["tmdbEpisodeGroupId"],
                        custom_season=parsed_info.get("season"),
                        custom_episode=parsed_info.get("episode")
                    )
                if tmdb_results:
                    logger.info(f"TMDB 映射匹配成功，找到 {len(tmdb_results)} 个结果。")
                    res = tmdb_results[0]
                    response = _matched_response_from_row(res, "TMDB 映射匹配")
                    return response

            elif anime.get("tmdbId"):
                # AI剧集组自动选择增强：有tmdbId但没有tmdbEpisodeGroupId
                ai_episode_group_enabled = (await get_config_service().get("aiEpisodeGroupEnabled", "false")).lower() == "true"
                # AI剧集组选择必须同时满足功能开关和密钥可用，避免无效配置触发TMDB及AI流程。
                if not ai_episode_group_enabled or not await ai_service.is_available():
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

                    virtual_user = User(id=0, username="match_ai_group")
                    all_groups = await tmdb_source.get_all_episode_groups(int(tmdb_id), virtual_user)

                    if not all_groups:
                        logger.info(f"AI剧集组选择: TMDB ID {tmdb_id} 没有剧集组，跳过")
                        continue

                    logger.info(f"AI剧集组选择: TMDB ID {tmdb_id} 找到 {len(all_groups)} 个剧集组: {[g.get('name') for g in all_groups]}")

                    # 使用混合策略选择最佳剧集组
                    selected_index = await ai_service.select_best_episode_group(
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

                    # 关联作品与剧集组，事务由 DatabaseService 管理提交。
                    async with db.transaction():
                        await db.anime.update_tmdb_group_id(anime_id, group_id)

                    logger.info(f"AI剧集组选择: 已为作品 ID {anime_id} 关联剧集组 {group_id}")

                    # 重试TMDB映射匹配
                    async with db.transaction():
                        tmdb_results = await db.episode.find_episode_via_tmdb_mapping(
                            tmdb_id=tmdb_id,
                            group_id=group_id,
                            custom_season=parsed_info.get("season"),
                            custom_episode=parsed_info.get("episode")
                        )
                    if tmdb_results:
                        logger.info(f"AI剧集组选择 + TMDB映射匹配成功，找到 {len(tmdb_results)} 个结果。")
                        res = tmdb_results[0]
                        response = _matched_response_from_row(res, "AI剧集组 + TMDB映射")
                        return response
                    else:
                        logger.info(f"AI剧集组选择: 映射已保存但当前集数未在映射中找到匹配，继续后备搜索")

                except Exception as e:
                    logger.error(f"AI剧集组选择失败: {e}", exc_info=True)
    else:
        logger.info("检测到电影文件，跳过 TMDB 映射匹配。")

    # --- 步骤 3: 如果所有方法都失败 ---
    # 新增：后备机制 (Fallback Mechanism)
    fallback_enabled_str = await get_config_service().get("matchFallbackEnabled", "false")
    if fallback_enabled_str.lower() == 'true':
        # 检查Token是否被允许使用匹配后备功能
        if token:
            try:
                # 获取当前token的信息
                async with db.transaction():
                    current_token_obj = await db.api_token.get_by_token_str(token)


                if current_token_obj:
                    # 获取允许的token列表
                    allowed_tokens_str = await get_config_service().get("matchFallbackTokens", "[]")
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
        blacklist_pattern = await get_config_service().get("matchFallbackBlacklist", "")
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
        recent_fallback_data = await get_db_cache(None, FALLBACK_SEARCH_CACHE_PREFIX, recent_fallback_key)
        if recent_fallback_data:
            cached_time = recent_fallback_data.get("timestamp", 0)
            if time.time() - cached_time < 300:  # 5分钟内
                logger.info(f"检测到5分钟内已完成的后备任务，直接返回缓存结果")
                cached_response = recent_fallback_data.get("response")
                if cached_response:
                    # 缓存后端返回字典，统一恢复响应模型，避免内存与持久化后端行为不同。
                    return DandanMatchResponse.model_validate(cached_response)

        # 方案D: 整季缓存复用 - 同标题同季的不同集，复用之前的匹配结果
        season_cache_key = f"match_season_{parsed_info['title']}_{parsed_info.get('season', 1)}"
        season_cache = await get_db_cache(None, FALLBACK_SEARCH_CACHE_PREFIX, season_cache_key)
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

                # 直接生成 episodeId（generate_episode_id 已在模块顶部导入）
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
                    "original_title": season_cache.get("original_title", cached_title),
                    "final_season": cached_season,
                    "media_type": season_cache.get("media_type", "tv_series"),
                    "imageUrl": season_cache.get("imageUrl"),
                    "year": season_cache.get("year"),
                    "timestamp": time.time()
                }
                await set_db_cache(None, FALLBACK_SEARCH_CACHE_PREFIX, episode_mapping_key, episode_mapping_data, FALLBACK_SEARCH_CACHE_TTL)

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

        async def match_fallback_coro_factory(
            session_inner: AsyncSession,
            progress_callback: Callable[..., Awaitable[None]],
        ) -> None:
            """匹配后备任务的协程工厂"""
            # 数据库操作各自使用短事务，不向只读会话属性赋值。
            db = get_database_service()

            # 初始化计时器并开始计时
            match_timer = SearchTimer(SEARCH_TYPE_FALLBACK_MATCH, item.fileName, logger)
            match_timer.start()

            try:
                match_timer.step_start("初始化与解析")
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
                    get_config_service().get("matchFallbackEnableTmdbSeasonMapping", "false"),
                    get_config_service().get("aiMatchEnabled", "false"),
                    get_config_service().get("aiFallbackEnabled", "true"),
                    get_config_service().get("externalApiFallbackEnabled", "false"),
                    get_config_service().get("aiEpisodeGroupEnabled", "false"),
                )
                match_fallback_tmdb_enabled = _cfg_tasks[0]
                # AI 匹配入口必须同时满足开关与密钥可用，避免仅因开关开启就记录或执行 AI 流程。
                ai_match_enabled = (
                    _cfg_tasks[1].lower() == "true" and await ai_service.is_available()
                )
                ai_fallback_enabled = _cfg_tasks[2].lower() == "true"
                fallback_enabled = _cfg_tasks[3].lower() == "true"
                ai_episode_group_enabled = _cfg_tasks[4].lower() == "true"

                if match_fallback_tmdb_enabled.lower() != "true":
                    logger.info("○ 匹配后备 统一AI映射: 功能未启用")

                # 【性能优化】AI初始化预热：如果AI匹配已启用，提前开始初始化（不阻塞）
                ai_matcher_warmup_task = None
                if ai_match_enabled:
                    ai_matcher_warmup_task = asyncio.create_task(ai_service.get_matcher())
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
                    # TMDB剧集组预获取属于AI增强流程，密钥不可用时完全跳过。
                    if not await ai_service.is_available():
                        return None, set()
                    tmdb_source = metadata_manager.sources.get("tmdb")
                    if not tmdb_source:
                        return None, set()
                    try:
                        virtual_user = User(id=0, username="match_prefetch")
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
                        # 严格过滤已改为阈值参数，不能再传旧布尔开关。
                        title_similarity_strict_threshold=95,
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
                        _vu = User(id=0, username="match_group")
                        _tmdb_src = metadata_manager.sources.get("tmdb")
                        if _tmdb_src and ai_episode_group_enabled and await ai_service.is_available():
                            all_groups = await _tmdb_src.get_all_episode_groups(int(_prefetch_tmdb_id), _vu)
                            if all_groups:
                                logger.info(f"剧集组: 找到 {len(all_groups)} 个剧集组")
                                selected_idx = await ai_service.select_best_episode_group(
                                    title=base_title, season=season, episode=episode_number,
                                    episode_groups=all_groups
                                )
                                if selected_idx is not None:
                                    group_id = all_groups[selected_idx]["id"]
                                    logger.info(f"剧集组: 选中 '{all_groups[selected_idx].get('name')}' (ID: {group_id})")
                                    await metadata_manager.update_tmdb_mappings(int(_prefetch_tmdb_id), group_id, _vu)
                                    async with db.transaction():
                                        equiv = await db.tmdb.get_episode_equivalence(
                                            group_id, season, episode_number
                                        )
                                    if equiv:
                                        pre_fetched_equiv = equiv
                                        logger.info(f"剧集组: 等价映射获取成功")
                    except Exception as eg_err:
                        logger.warning(f"剧集组处理失败: {eg_err}")

                    # 写入别名缓存
                    if pre_fetched_aliases:
                        alias_cache_key = f"search_aliases_{base_title}"
                        alias_data = json.dumps(list(pre_fetched_aliases))
                        _backend = get_cache_service()
                        # CacheService 已处理数据库回退，不再调用已删除的 set_cache。
                        try:
                            if _backend is not None:
                                await _backend.set(alias_cache_key, alias_data, ttl=3600, region="search")
                        except Exception as cache_error:
                            logger.debug(f"别名缓存写入失败: {cache_error}")

                # 收集单源搜索耗时信息（分组显示）
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
                if match_fallback_tmdb_enabled.lower() == "true" and await ai_service.is_available():
                    try:
                        match_timer.step_start("AI映射修正")
                        # 【性能优化】使用预热的AI匹配器
                        ai_matcher = None
                        if ai_matcher_warmup_task:
                            ai_matcher = await ai_matcher_warmup_task
                            ai_matcher_warmup_task = None  # 清空task，避免重复await
                        else:
                            ai_matcher = await ai_service.get_matcher()
                        if ai_matcher:
                            logger.info(f"○ 匹配后备 开始统一AI映射修正: '{base_title}' ({len(all_results)} 个结果)")

                            # 使用新的统一函数进行类型和季度修正
                            mapping_result = await correct_search_results(
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
                _match_sub_steps = []
                _sub_start = time.perf_counter()

                # 确定目标类型
                target_type = "movie" if is_movie else "tv_series"

                # 获取源的优先级顺序
                async with db.transaction():
                    source_settings = await db.scraper.get_all_scraper_settings()
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

                    # 4. 季度匹配 (电视剧才判；同名不同季必须靠季度区分，权重高于标题/年份)
                    #    修复：此前打分不含季度，导致搜"爱情公寓 S01"时第1季与第2季标题相似度均为100、
                    #    源优先级相同 → 两季得分完全一样。季度匹配 +500 / 不匹配 -500 以拉开差距。
                    if not is_movie and season is not None and getattr(result, "season", None) is not None:
                        if result.season == season:
                            score += 500
                            logger.debug(f"  - {result.provider} - {result.title}: 季度匹配(S{season}) +500")
                        else:
                            score -= 500
                            logger.debug(f"  - {result.provider} - {result.title}: 季度不匹配(S{season}≠S{result.season}) -500")

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

                _match_sub_steps.append(SubStepTiming(name="排序打分", duration_ms=(time.perf_counter() - _sub_start) * 1000))
                _sub_start = time.perf_counter()

                # 步骤3：自动选择最佳源
                logger.info(f"步骤3：自动选择最佳源")

                # 【性能优化】批量查询精确标记信息（1次IN查询替代N次单独查询）
                favorited_info = {}
                provider_media_pairs = [(r.provider, r.mediaId) for r in sorted_results]
                if provider_media_pairs:
                    # 复用批量仓储查询，并在会话关闭前提取普通字典。
                    async with db.transaction():
                        favorited_rows = await db.source.get_favorited_by_provider_media_pairs(
                            provider_media_pairs
                        )
                        favorited_info = {
                            f"{row.providerName}:{row.mediaId}": True for row in favorited_rows
                        }

                # 识别词认知校正上下文（统一函数，命中标记+提示文案；不改排序）
                recognition_info, recognition_hint = (
                    await title_recognition_manager.build_recognition_context_for_results(sorted_results)
                    if title_recognition_manager else ({}, None)
                )

                # 【性能优化】使用预获取的配置值（不再重复获取）
                # ai_match_enabled, ai_fallback_enabled, fallback_enabled 已在初始化阶段获取

                # 如果启用AI匹配，尝试使用AI选择
                ai_selected_index = None
                if ai_match_enabled:
                    try:
                        # 动态注册AI提示词配置(如果不存在则创建,使用硬编码默认值)
                        async with db.transaction():
                            await db.config.initialize_configs({
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

                        if not is_movie and ai_episode_group_enabled and await ai_service.is_available():
                            # 方式1: 从库内已有作品获取等价信息
                            for pa in potential_animes:
                                pa_group_id = pa.get("tmdbEpisodeGroupId")
                                if pa.get("tmdbId") and pa_group_id:
                                    try:
                                        async with db.transaction():
                                            equiv = await db.tmdb.get_episode_equivalence(
                                                pa_group_id, season, episode_number
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

                        # 注入识别词认知校正提示（仅帮助AI理解条目身份，不改排序）
                        if recognition_hint:
                            query_info["recognition_hint"] = recognition_hint

                        # 所有匹配调用共享启动阶段初始化的 AI 服务。
                        ai_selected_index = await ai_service.select_best_match(
                            query_info, sorted_results, favorited_info, None, recognition_info
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

                _match_sub_steps.append(SubStepTiming(name="AI匹配" if ai_match_enabled else "传统匹配", duration_ms=(time.perf_counter() - _sub_start) * 1000))
                _sub_start = time.perf_counter()

                best_match = None

                # 如果AI选择成功，使用AI选择的结果
                if ai_selected_index is not None:
                    best_match = sorted_results[ai_selected_index]
                    logger.info(f"  - 使用AI选择的结果: {best_match.provider} - {best_match.title}")
                elif ai_match_enabled:
                    # AI匹配已启用但失败，使用预获取的配置值
                    if not ai_fallback_enabled:
                        logger.warning("AI匹配失败且传统匹配兜底已禁用，匹配后备失败")
                        # 任务返回值不会作为接口响应，失败由统一异常分支回填。
                        raise TaskFailed("AI匹配失败且传统匹配兜底已禁用")
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
                # 分集列表缓存（顺延验证时填充，后续复用避免重复请求）
                episodes_cache = {}

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
                    # 顺延机制启用：并行预取前N个高分候选源的分集列表，然后内存验证
                    logger.info(f"  - 顺延机制启用，并行预取分集列表")
                    MAX_PREFETCH = 5  # 并行预取前5个候选源
                    prefetch_candidates = sorted_results[:MAX_PREFETCH]

                    # 并行获取分集列表
                    async def _fetch_episodes(candidate):
                        try:
                            eps = await scraper_manager.get_episodes_routed(
                                candidate.provider, candidate.mediaId, db_media_type=candidate.type
                            )
                            return candidate, eps
                        except Exception as e:
                            logger.debug(f"预取分集失败: {candidate.provider} - {e}")
                            return candidate, None

                    prefetch_results = await asyncio.gather(
                        *[_fetch_episodes(c) for c in prefetch_candidates]
                    )

                    # 构建缓存：provider:mediaId -> episodes
                    episodes_cache = {}
                    for candidate, eps in prefetch_results:
                        episodes_cache[f"{candidate.provider}:{candidate.mediaId}"] = eps

                    # 在内存中验证（包括预取的和剩余的）
                    for attempt, candidate in enumerate(sorted_results, 1):
                        cache_key = f"{candidate.provider}:{candidate.mediaId}"
                        logger.info(f"    {attempt}. 正在验证: {candidate.provider} - {candidate.title} (ID: {candidate.mediaId}, 类型: {candidate.type})")

                        # 优先从缓存取，没有则实时获取（超出预取范围的候选源）
                        if cache_key in episodes_cache:
                            episodes = episodes_cache[cache_key]
                        else:
                            try:
                                episodes = await scraper_manager.get_episodes_routed(
                                    candidate.provider, candidate.mediaId, db_media_type=candidate.type
                                )
                                episodes_cache[cache_key] = episodes
                            except Exception as e:
                                logger.warning(f"    {attempt}. {candidate.provider} - 获取分集失败: {e}")
                                continue

                        if not episodes:
                            logger.warning(f"    {attempt}. {candidate.provider} - 没有分集列表，跳过")
                            continue

                        # 类型验证
                        if is_movie:
                            if candidate.type != "movie":
                                logger.warning(f"    {attempt}. {candidate.provider} - 类型不匹配 (搜索电影，但候选源是{candidate.type})，跳过")
                                continue
                            logger.info(f"    {attempt}. {candidate.provider} - 验证通过 (电影)")
                        else:
                            if candidate.type != target_type:
                                logger.warning(f"    {attempt}. {candidate.provider} - 类型不匹配({candidate.type}≠{target_type})，跳过")
                                continue

                        # 集数验证
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

                            matched_episode_title = target_episode.title
                            logger.info(f"    {attempt}. {candidate.provider} - 验证通过，剧集标题: '{matched_episode_title}'")
                        else:
                            logger.info(f"    {attempt}. {candidate.provider} - 验证通过")
                        best_match = candidate
                        break

                if not best_match:
                    logger.warning(f"匹配后备失败：所有候选源都无法提供有效分集")
                    _match_sub_steps.append(SubStepTiming(name="顺延验证", duration_ms=(time.perf_counter() - _sub_start) * 1000))
                    match_timer.step_end(details="无有效分集", sub_steps=_match_sub_steps)
                    match_timer.finish()
                    response = DandanMatchResponse(isMatched=False, matches=[])
                    match_fallback_result["response"] = response
                    return

                # 如果还没有获取剧集标题（非顺延机制匹配成功的情况），主动获取
                # 复用预取缓存，避免重复HTTP请求
                if matched_episode_title is None and not is_movie and source_episode_number is not None:
                    try:
                        cache_key = f"{best_match.provider}:{best_match.mediaId}"
                        episodes = episodes_cache.get(cache_key) if episodes_cache else None
                        if episodes is None:
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
                        async with db.transaction():
                            await db.task.update(task_id_ref["id"], title=new_title)
                    except Exception as _title_err:
                        logger.debug(f"更新任务标题失败（非关键）: {_title_err}")

                # 步骤5：分配虚拟animeId和真实episodeId
                logger.info(f"步骤5：分配虚拟animeId和真实episodeId")

                # 复用六位虚拟 ID 编排；真实 ID 计数器必须先提交再发布缓存映射。
                virtual_anime_id = await get_next_virtual_anime_id(session_inner)
                async with db.transaction():
                    real_anime_id = await db.fallback.find_anime_by_title_season(
                        best_match.title, final_title, final_season
                    )
                    if real_anime_id is None:
                        real_anime_id = await db.fallback.get_next_real_anime_id()
                    source_order = await db.fallback.get_or_predict_source_order(
                        real_anime_id, best_match.provider, best_match.mediaId
                    )
                logger.info(f"  - 分配ID: virtual_anime_id={virtual_anime_id}, real_anime_id={real_anime_id}, source_order={source_order}")

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
                    "original_title": best_match.title,
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
                    "original_title": best_match.title,
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
                    # 提前序列化为基础类型，保证所有缓存后端均可读取。
                    "response": response.model_dump(mode="json"),
                    "timestamp": time.time()
                }
                await set_db_cache(session_inner, FALLBACK_SEARCH_CACHE_PREFIX, recent_fallback_key, recent_fallback_data, 300)  # 5分钟TTL

                # 存储整季缓存（TV系列专用，1小时TTL）
                if not is_movie:
                    season_cache_key = f"match_season_{parsed_info['title']}_{parsed_info.get('season', 1)}"
                    season_cache_data = {
                        "provider": best_match.provider,
                        "mediaId": best_match.mediaId,
                        "real_anime_id": real_anime_id,
                        "virtual_anime_id": virtual_anime_id,
                        "final_title": final_title,
                        "original_title": best_match.title,
                        "final_season": final_season,
                        "source_order": source_order,
                        "media_type": best_match.type,
                        "imageUrl": best_match.imageUrl,
                        "year": best_match.year,
                        "timestamp": time.time()
                    }
                    await set_db_cache(session_inner, FALLBACK_SEARCH_CACHE_PREFIX, season_cache_key, season_cache_data, 3600)
                    logger.info(f"整季缓存已存储: {season_cache_key}")

                _match_sub_steps.append(SubStepTiming(name="验证+获取分集", duration_ms=(time.perf_counter() - _sub_start) * 1000))
                match_timer.step_end(details="匹配成功", sub_steps=_match_sub_steps)
                match_timer.finish()  # 打印计时报告
                match_fallback_result["response"] = response
                # 保存匹配详情供后续使用
                match_fallback_result["match_details"] = {
                    "provider": best_match.provider,
                    "mediaId": best_match.mediaId,
                    "final_title": final_title,
                    "final_season": final_season,
                    "episode_number": episode_number,
                    "is_movie": is_movie,
                    # 海报URL：供匹配后备完成通知渲染海报图片（task_manager 会把 imageUrl 映射为通知的 image_url）
                    "imageUrl": best_match.imageUrl,
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
                # 不能吞掉异常后正常返回，否则任务管理器会把匹配异常记为成功。
                raise TaskFailed(f"匹配后备失败: {e}") from e

        # 提交匹配后备任务
        # why: 移除 run_immediately=True，让任务正常进入search队列，不绕过队列隔离
        # why: 改为search队列，因为这是搜索+匹配任务，不占用下载配额
        try:
            task_title = f"匹配后备: {item.fileName}"
            task_id, done_event = await task_manager.submit_task(
                match_fallback_coro_factory,
                task_title,
                run_immediately=True,
                queue_type="search",  # 搜索匹配任务入search队列
                task_parameters={"file_name": item.fileName}
            )
            task_id_ref["id"] = task_id  # 赋值后 coro_factory 内部才能用来更新标题
            logger.info(f"匹配后备任务已提交: {task_id}")

            # 等待任务完成。超时时长由配置 matchFallbackTimeout 控制（秒）：
            # -1 表示无限等待直到匹配完成；其余正数为最大等待秒数，超时返回未匹配，任务继续在后台跑。
            # why：此前硬编码 30s，导致 WebUI"后备匹配超时时间"配置无法生效。
            timeout_str = await get_config_service().get("matchFallbackTimeout", "60")
            try:
                match_wait_timeout = float(timeout_str)
            except (ValueError, TypeError):
                match_wait_timeout = 30.0

            try:
                if match_wait_timeout < 0:
                    # 无限等待：不设超时，直到后备匹配任务完成
                    await done_event.wait()
                    logger.info(f"匹配后备任务完成: {task_id}")
                else:
                    await asyncio.wait_for(done_event.wait(), timeout=match_wait_timeout)
                    logger.info(f"匹配后备任务完成: {task_id}")
            except asyncio.TimeoutError:
                logger.warning(f"匹配后备任务超时（{match_wait_timeout:.0f}秒）: {task_id}")
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