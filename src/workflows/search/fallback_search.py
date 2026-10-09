"""
后备搜索逻辑

提供多种后备搜索策略和算法
"""

import asyncio
import json
import logging
import time
from typing import Optional, List, Dict, Any, Tuple

from sqlalchemy.ext.asyncio import AsyncSession

from src.schemas.search import ProviderSearchInfo, AnimeSearchResponse
from src.schemas.dandan import DandanSearchAnimeResponse, DandanSearchAnimeItem
from src.services.service_container import (
    get_database_service,
    get_scraper_manager,
    get_metadata_service,
    get_title_recognition_manager,
)
from src.schemas.auth import User
from src.services.config_service import ConfigService
from src.services.scraper_manager import ScraperManager
from src.services.task_manager import TaskManager
from src.services.metadata_service import MetadataService
from src.workflows.search.engine import unified_search, search_all
from src.workflows.search.ui_results import search_aliases_from_enabled_sources
from src.utils.data_processing.name_converter import convert_to_chinese_title
from src.utils import (
    parse_search_keyword,
    SearchTimer, SEARCH_TYPE_FALLBACK_SEARCH,
    is_movie_by_title,
)
from src.workflows.search.ai_correction import correct_search_results
# 外链配置与图片缓存分别复用对应编排流程。
from src.workflows.image_public_url import get_custom_domain
from src.workflows.image_resources import save_public_thumbnail
from src.utils.diagnostics.search_timer import SubStepTiming
from src.rate_limiter import RateLimiter
from src.services.ai_service import AIService
from src.utils.title_recognition_engine import TitleRecognitionEngine
from src.utils.parsing.filename_parser import format_episode_ranges

logger = logging.getLogger(__name__)

# ============ 常量定义（从 api/dandan/constants.py 复制） ============
DANDAN_TYPE_MAPPING = {
    "tv_series": "tvseries",
    "movie": "movie",
    "ova": "ova",
    "web": "web",
    "other": "other",
}

DANDAN_TYPE_DESC_MAPPING = {
    "tv_series": "TV",
    "movie": "剧场版",
    "ova": "OVA",
    "web": "Web",
    "other": "其他",
}

# 复用搜索会话契约，避免详情读取误用旧后备弹幕缓存前缀。
from src.utils.dandan.constants import (
    FALLBACK_SEARCH_SESSION_PREFIX as FALLBACK_SEARCH_CACHE_PREFIX,
    FALLBACK_SEARCH_SESSION_TTL as FALLBACK_SEARCH_CACHE_TTL,
)

FALLBACK_SEARCH_BANGUMI_ID = 999999998
TOKEN_SEARCH_TASKS_PREFIX = "token_search_tasks"
TOKEN_SEARCH_TASKS_TTL = 1800

# ============ 辅助函数（从 api/dandan/helpers.py 复制核心逻辑） ============

async def get_db_cache(session: AsyncSession, prefix: str, key: str) -> Optional[Any]:
    """独立短事务读取缓存，避免请求或任务长事务读到旧状态。"""
    try:
        db = get_database_service()
        cache_key = f"{prefix}:{key}" if prefix else key
        async with db.transaction():
            return await db.cache.get_json(cache_key)
    except Exception as e:
        logger.debug(f"获取缓存失败: {e}")
    return None


async def set_db_cache(session: AsyncSession, prefix: str, key: str, value: Any, ttl: int) -> None:
    """独立提交缓存，使请求与后台任务及时看到状态；保留调用签名。"""
    try:
        db = get_database_service()
        cache_key = f"{prefix}:{key}" if prefix else key
        async with db.transaction():
            await db.cache.set_json(cache_key, value, ttl)
    except Exception as e:
        logger.warning(f"设置缓存失败: {e}")


async def delete_db_cache(session: AsyncSession, prefix: str, key: str) -> None:
    """独立短事务删除缓存，不依赖外部会话提交。"""
    try:
        db = get_database_service()
        cache_key = f"{prefix}:{key}" if prefix else key
        async with db.transaction():
            await db.cache.delete(cache_key)
    except Exception as e:
        logger.debug(f"删除缓存失败: {e}")


async def get_raw_cache(session: AsyncSession, key: str) -> Optional[Dict[str, Any]]:
    """获取原始缓存（不带前缀）。"""
    return await get_db_cache(session, "", key)


async def check_related_match_fallback_task(session: AsyncSession, search_term: str) -> Optional[Dict[str, Any]]:
    """检查是否有相关的匹配后备任务正在运行。"""
    return None


async def handle_fallback_search(
    search_term: str,
    token: str,
    session: AsyncSession,
    scraper_manager: ScraperManager,
    metadata_manager: MetadataService,
    config_service: ConfigService,
    rate_limiter: RateLimiter,
    title_recognition_manager,
    task_manager: TaskManager,
    ai_service: AIService
) -> DandanSearchAnimeResponse:
    """
    统一的后备搜索入口函数

    从 api/dandan/fallback_search.py 迁移的完整业务逻辑
    """
    shared_cache_key_out: Dict[str, str] = {}

    search_key = f"search_{hash(search_term + token)}"
    _logo_domain = await get_custom_domain(config_service)
    image_url = f"{_logo_domain}/static/logo.png" if _logo_domain else "/static/logo.png"

    # 最大等待时间由配置控制，默认60秒；-1 表示无限等待。
    timeout_str = await config_service.get("searchFallbackTimeout", "60")
    try:
        max_wait_time = float(timeout_str)
        if max_wait_time != -1 and not 0 <= max_wait_time <= 120:
            raise ValueError("后备搜索超时时间必须为 -1 或 0-120 秒")
    except (ValueError, TypeError):
        max_wait_time = 60.0

    async def _wait_for_search_result(
        wait_key: str, completion_event: Optional[asyncio.Event] = None,
    ) -> DandanSearchAnimeResponse:
        """等待最终结果；缓存暂缺继续等待，接口超时不取消后台任务。"""
        start = time.monotonic()
        while True:
            # 先读取完成信号，再查缓存，避免查询期间任务完成而误判旧状态。
            task_finished = completion_event is not None and completion_event.is_set()
            info = await get_db_cache(session, FALLBACK_SEARCH_CACHE_PREFIX, wait_key)
            status = info.get("status") if isinstance(info, dict) else None
            if status == "failed":
                return DandanSearchAnimeResponse(animes=[])
            if status == "completed" and (completion_event is None or task_finished):
                logger.info(f"搜索完成，等待耗时 {time.monotonic() - start:.2f}s")
                return DandanSearchAnimeResponse(animes=info.get("results") or [])
            if task_finished and status == "running":
                logger.warning("后备搜索任务已结束，但未写入完成状态，任务可能被取消")
                return DandanSearchAnimeResponse(animes=[])
            # 即使任务已结束，缓存暂时不可读也继续重试，不能立即返回空结果。
            elapsed = time.monotonic() - start
            if max_wait_time >= 0 and elapsed >= max_wait_time:
                logger.info(f"搜索等待超过 {max_wait_time}s，返回空结果，后台任务继续")
                return DandanSearchAnimeResponse(animes=[])
            delay = 0.5 if max_wait_time == -1 else min(0.5, max_wait_time - elapsed)
            await asyncio.sleep(delay)

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
    # task_id_ref：submit_task 返回后才有 task_id，用可变容器在闭包里回填，
    # 供任务完成后 update_task_parameters 写入海报URL（事件循环单线程，无竞态）。
    task_id_ref: dict = {}

    async def fallback_search_coro_factory(session_inner: AsyncSession, progress_callback):
        try:
            await execute_fallback_search_task(
                search_term, search_key, token, session_inner, progress_callback,
                scraper_manager, metadata_manager, config_service,
                rate_limiter, title_recognition_manager, ai_service,
                shared_cache_key_out,
            )
            # 任务成功完成后，从缓存结果提取前若干个海报URL写入 task_parameters，
            # 供完成通知（FallbackSearchMessage）聚合九宫格海报。
            # why：海报聚合放在通知链路异步执行，不阻塞搜索返回；此处仅做轻量URL提取。
            await _attach_poster_urls_to_task(session_inner, search_key, task_id_ref.get("id"))
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

    # 海报URL提取器：从后备搜索缓存结果取前 9 个非空 imageUrl，写进 task_parameters
    async def _attach_poster_urls_to_task(session_inner: AsyncSession, s_key: str, t_id):
        if not t_id:
            return
        try:
            info = await get_db_cache(session_inner, FALLBACK_SEARCH_CACHE_PREFIX, s_key)
            results = (info or {}).get("results") or []
            poster_urls = []
            for r in results:
                url = (r or {}).get("imageUrl") or ""
                if url:
                    poster_urls.append(url)
                if len(poster_urls) >= 9:  # 九宫格上限
                    break
            if poster_urls:
                task_manager.update_task_parameters(t_id, {"poster_urls": poster_urls})
        except Exception as e:
            # 海报URL提取失败不影响任务完成与通知发出
            logger.debug(f"提取后备搜索海报URL失败（忽略）: {e}")

    # 提交后备搜索任务
    try:
        task_title = f"后备搜索: {search_term}"
        # 查询 token 名字，写入 task_parameters 供通知格式化使用
        try:
            db = get_database_service()
            async with db.transaction(session=session):
                token_obj = await db.api_token.get_by_token_str(token)
                token_name = token_obj.name if token_obj else token[:8]
        except Exception:
            token_name = token[:8]
        task_id, done_event = await task_manager.submit_task(
            fallback_search_coro_factory, task_title, run_immediately=True, queue_type="fallback",
            task_parameters={"token_name": token_name, "search_term": search_term}
        )
        task_id_ref["id"] = task_id  # 回填，供 coro_factory 完成后写海报URL
        logger.info(f"后备搜索任务已提交: {task_id}")
    except Exception as e:
        logger.error(f"提交后备搜索任务失败: {e}", exc_info=True)
        search_info["status"] = "failed"
        await set_db_cache(session, FALLBACK_SEARCH_CACHE_PREFIX, search_key, search_info, FALLBACK_SEARCH_CACHE_TTL)
        await delete_db_cache(session, TOKEN_SEARCH_TASKS_PREFIX, token)
        return DandanSearchAnimeResponse(animes=[])

    # 等待任务收尾及结果可见；接口超时不会取消后台任务。
    return await _wait_for_search_result(search_key, done_event)


async def execute_fallback_search_task(
    search_term: str,
    search_key: str,
    token: str,
    session: AsyncSession,
    progress_callback,
    scraper_manager: ScraperManager,
    metadata_manager: MetadataService,
    config_service: ConfigService,
    rate_limiter: RateLimiter,
    title_recognition_manager,
    ai_service: AIService,
    shared_cache_key_out: Optional[Dict[str, str]] = None,
):
    """执行后备搜索任务。"""
    timer = SearchTimer(SEARCH_TYPE_FALLBACK_SEARCH, search_term, logger)
    timer.start()

    # 【性能优化】AI初始化预热：如果AI映射已启用，提前开始初始化（不阻塞）
    ai_matcher_warmup_task = None
    fallback_search_season_mapping_enabled_check = "false"
    try:
        fallback_search_season_mapping_enabled_check = await config_service.get("fallbackSearchEnableTmdbSeasonMapping", "false")
        if fallback_search_season_mapping_enabled_check.lower() == "true" and await ai_service.is_available():
            ai_matcher_warmup_task = asyncio.create_task(ai_service.get_matcher())
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
        # 原始完整关键词（未拆解），供识别词反向映射使用
        original_keyword = parsed_info.get("original_keyword") or search_term.strip()

        # 🚀 识别词反向映射（最高优先级）：用户用"入库名"搜索时，自动改用源站真实名去搜
        recognition_title = None  # 识别词指定的入库正确名，命中后写入每条结果
        # why：规则形如 source=iqiyi 表示该识别词仅对爱奇艺源生效，记录源限定，
        # 写 recognitionTitle 时仅打给匹配源结果，避免 renren 等无关源被误标。
        recognition_source_restriction = "all"
        recognition_rule_source = None  # 规则左侧源站标题，用于标题精确校验
        recognition_mapping_applied = False
        if title_recognition_manager:
            try:
                mapping = await title_recognition_manager.apply_search_title_mapping(original_keyword)
                if mapping:
                    recognition_title = mapping["recognition_title"]
                    recognition_source_restriction = mapping.get("rule_source_restriction", "all") or "all"
                    recognition_rule_source = mapping.get("search_title")  # 规则 source 值
                    recognition_mapping_applied = True
                    # why：反向映射把搜索词换成源站真实名，但 season_to_filter 仍是用户输入
                    # "入库名"解析出的目标季。源站结果是源季，若不修正会被 line 358 季度过滤删光。
                    # 用规则 season_offset 解析出的"源站季度"覆盖过滤季度（通配/无法解析则不按季过滤）。
                    mapped_source_season = mapping.get("search_season")
                    if mapped_source_season is not None:
                        if season_to_filter != mapped_source_season:
                            logger.info(
                                f"✓ 后备搜索反向映射季度修正: 过滤季度 {season_to_filter} → "
                                f"源站季度 {mapped_source_season}"
                            )
                        season_to_filter = mapped_source_season
                    else:
                        season_to_filter = None
                    logger.info(
                        f"✓ 后备搜索识别词反向映射: '{search_term}' → 实际搜索 "
                        f"'{mapping['search_title']}'，入库名标记为 '{recognition_title}'"
                    )
            except Exception as e:
                logger.warning(f"后备搜索识别词反向映射失败: {e}")

        # 2. 名称转换（非中文→中文，与 Webhook 搜索一致）
        # 识别词反向映射命中时跳过（用户已显式指定真实搜索词）
        fallback_user = User(id=0, username="fallback_search")
        if recognition_mapping_applied:
            original_title = mapping["search_title"]
        else:
            converted_title, conversion_applied = await convert_to_chinese_title(
                original_title, config_service, metadata_manager,
                ai_service, fallback_user
            )
            if conversion_applied:
                logger.info(f"✓ 后备搜索名称转换: '{original_title}' → '{converted_title}'")
                original_title = converted_title

        # 3. 应用标题预处理规则（识别词反向映射命中时跳过，避免二次改写真实搜索词）
        search_title = original_title
        if title_recognition_manager and not recognition_mapping_applied:
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

        # 4. 同步更新缓存中的 parsed_info
        search_info = await get_db_cache(session, FALLBACK_SEARCH_CACHE_PREFIX, search_key)
        if search_info:
            cached_parsed = search_info.get("parsed_info") or {}
            cached_parsed["season"] = season_to_filter
            cached_parsed["episode"] = episode_to_filter
            cached_parsed["title"] = search_title
            search_info["parsed_info"] = cached_parsed
            await set_db_cache(session, FALLBACK_SEARCH_CACHE_PREFIX, search_key, search_info, FALLBACK_SEARCH_CACHE_TTL)

        # 5. 构造 episode_info
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

        # 继续下一部分...
        await _execute_fallback_search_core(
            search_term, search_key, token, session, progress_callback,
            scraper_manager, metadata_manager, config_service,
            title_recognition_manager, ai_service,
            search_title, season_to_filter, episode_to_filter, episode_info,
            recognition_title, recognition_source_restriction, recognition_rule_source,
            fallback_search_season_mapping_enabled, ai_matcher_warmup_task,
            timer, shared_cache_key_out
        )

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


async def _execute_fallback_search_core(
    search_term: str,
    search_key: str,
    token: str,
    session: AsyncSession,
    progress_callback,
    scraper_manager: ScraperManager,
    metadata_manager: MetadataService,
    config_service: ConfigService,
    title_recognition_manager,
    ai_service: AIService,
    search_title: str,
    season_to_filter: Optional[int],
    episode_to_filter: Optional[int],
    episode_info: Optional[Dict],
    recognition_title: Optional[str],
    recognition_source_restriction: str,
    recognition_rule_source: Optional[str],
    fallback_search_season_mapping_enabled: str,
    ai_matcher_warmup_task,
    timer: SearchTimer,
    shared_cache_key_out: Optional[Dict[str, str]] = None,
):
    """后备搜索核心逻辑（搜索、过滤、转换）"""

    timer.step_start("弹幕源搜索")
    sorted_results = await unified_search(
        search_term=search_title,
        session=session,
        scraper_manager=scraper_manager,
        metadata_manager=metadata_manager,
        use_alias_expansion=True,
        # 对齐主页搜索：信任元数据源别名，不做相似度预筛。
        use_alias_filtering=False,
        use_title_filtering=True,
        use_source_priority_sorting=True,
        progress_callback=progress_callback,
        episode_info=episode_info,
        alias_similarity_threshold=70,
        shared_cache_key_out=shared_cache_key_out,
    )
    # 收集单源搜索耗时信息（分组显示，与主页搜索一致）
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
    if hasattr(metadata_manager, "last_aux_search_timing") and metadata_manager.last_aux_search_timing:
        for name, dur, cnt in metadata_manager.last_aux_search_timing:
            source_timing_sub_steps.append(
                SubStepTiming(name=name, duration_ms=dur, result_count=cnt, group="辅助源(别名)")
            )

    timer.step_end(details=f"{len(sorted_results)}个结果", sub_steps=source_timing_sub_steps)

    # 注：dandanplay 的 bgmtv 软429兜底已内聚到源内部 search()（对上层透明），
    # unified_search 会自动拿到兜底结果，此处无需再单独编排。

    # 6. 根据标题关键词修正媒体类型（复用统一的 is_movie_by_title）
    for item in sorted_results:
        if item.type == "tv_series" and is_movie_by_title(item.title):
            logger.info(f"标题 '{item.title}' 包含电影关键词，类型从 'tv_series' 修正为 'movie'。")
            item.type = "movie"

    # 7. 如果搜索词中明确指定了季度，对结果进行过滤
    if season_to_filter:
        original_count = len(sorted_results)
        filtered_by_type = [item for item in sorted_results if item.type == "tv_series"]
        filtered_by_season = [item for item in filtered_by_type if item.season == season_to_filter]
        logger.info(f"根据指定的季度 ({season_to_filter}) 进行过滤，从 {original_count} 个结果中保留了 {len(filtered_by_season)} 个。")
        sorted_results = filtered_by_season

    # 使用统一的AI类型和季度映射修正函数
    if fallback_search_season_mapping_enabled.lower() == "true" and await ai_service.is_available():
        try:
            timer.step_start("AI映射修正")
            # 【性能优化】使用预热的AI匹配器
            ai_matcher = None
            if ai_matcher_warmup_task:
                ai_matcher = await ai_matcher_warmup_task
                ai_matcher_warmup_task = None  # 清空task，避免重复await
            else:
                # 预热未命中时仍复用共享 AI 服务。
                ai_matcher = await ai_service.get_matcher()
            if ai_matcher:
                logger.info(f"○ 后备搜索 开始统一AI映射修正: '{search_title}' ({len(sorted_results)} 个结果)")
                mapping_result = await correct_search_results(
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

    # 继续结果转换...
    await _convert_and_cache_results(
        search_term, search_key, token, session, progress_callback,
        config_service, title_recognition_manager,
        sorted_results, search_title,
        recognition_title, recognition_source_restriction, recognition_rule_source,
        timer, shared_cache_key_out.get("key") if shared_cache_key_out else None,
    )


async def _convert_and_cache_results(
    search_term: str,
    search_key: str,
    token: str,
    session: AsyncSession,
    progress_callback,
    config_service: ConfigService,
    title_recognition_manager,
    sorted_results: List[ProviderSearchInfo],
    search_title: str,
    recognition_title: Optional[str],
    recognition_source_restriction: str,
    recognition_rule_source: Optional[str],
    timer: SearchTimer,
    shared_cache_key: Optional[str] = None,
):
    """结果转换与缓存（后备搜索最后阶段）"""

    timer.step_start("结果转换与缓存")
    await progress_callback(80, "转换搜索结果...")
    search_results = []

    # 虚拟编号只存在于缓存，不能用 Anime 表 MAX(id) 分配，否则每次搜索都会重用。
    # 在独立短事务中锁定计数器并预留整批编号，提交后才发布搜索结果。
    db = get_database_service()
    async with db.transaction():
        # 首次切换计数器时也跳过尚未过期的旧缓存编号，避免新旧结果碰撞。
        virtual_floor = 900000000
        cached_keys = await db.cache.get_keys_by_prefix(f"{FALLBACK_SEARCH_CACHE_PREFIX}:search_")
        for cached_key in cached_keys:
            cached_info = await db.cache.get_json(cached_key)
            if isinstance(cached_info, dict):
                for cached_id in (cached_info.get("bangumi_mapping") or {}):
                    if cached_id.startswith("A") and cached_id[1:].isdigit():
                        virtual_floor = max(virtual_floor, int(cached_id[1:]))
        next_virtual_anime_id = await db.config.allocate_next_counter_value(
            "lastAllocatedVirtualAnimeId", floor=virtual_floor,
            description="后备搜索虚拟编号上界，避免不同搜索缓存串台",
        )
        await db.config.update(
            "lastAllocatedVirtualAnimeId",
            str(next_virtual_anime_id + max(len(sorted_results), 1) - 1),
        )

    # 获取自定义域名（统一通过 get_custom_domain 读取，http/https 均支持）
    custom_domain = await get_custom_domain(config_service) or ""

    # 判断当前 token 是否在外联海报模式授权列表中
    # why：posterProxyTokens 为空列表时所有 token 均不启用外联海报（需显式授权）；
    # 列表中有当前 token 时才下载海报到本地并返回外联地址，否则透传源站原始 imageUrl。
    poster_proxy_enabled = False
    try:
        poster_proxy_tokens_str = await config_service.get("posterProxyTokens", "[]")
        poster_proxy_token_ids = json.loads(poster_proxy_tokens_str)
        if poster_proxy_token_ids and token:
            # token 是字符串（Token 值），需要查 DB 获取对应 token id 再比对
            db = get_database_service()
            async with db.transaction(session=session):
                token_obj = await db.api_token.get_by_token_str(token)
                if token_obj and token_obj.id in poster_proxy_token_ids:
                    poster_proxy_enabled = True
    except Exception as e:
        logger.debug(f"外联海报Token授权检查失败（忽略）: {e}")

    # 【性能优化①】循环前一次性读取缓存，循环中只修改内存，循环后一次性写回
    search_info_mapping = await get_db_cache(session, FALLBACK_SEARCH_CACHE_PREFIX, search_key)
    if search_info_mapping and "bangumi_mapping" not in search_info_mapping:
        search_info_mapping["bangumi_mapping"] = {}
    if search_info_mapping is not None and shared_cache_key:
        search_info_mapping["shared_cache_key"] = shared_cache_key

    # 【性能优化②】批量查询所有 (provider, mediaId) 的库内分集信息
    provider_media_pairs = [(r.provider, r.mediaId) for r in sorted_results]
    db = get_database_service()
    async with db.transaction(session=session):
        library_episodes_map = await db.episode.get_episode_indices_by_source_media_ids_batch(
            provider_media_pairs
        )

    for i, result in enumerate(sorted_results):
        current_virtual_anime_id = next_virtual_anime_id + i
        unique_bangumi_id = f"A{current_virtual_anime_id}"

        year_info = f" 年份：{result.year}" if result.year else ""
        title_with_source = f"{result.title} （来源：{result.provider}{year_info}）"

        # 外联海报模式：把源站海报下载到本地并拼接外联地址。
        # why：dandanplay 等客户端可能无法直接访问源站图片（防盗链/地区限制），
        # 本地缓存后通过 customApiDomain 返回公网可访问的外联地址。
        # 使用 save_public_thumbnail：内容 sha256 去重，不重复下载同一张海报。
        effective_image_url = result.imageUrl or ""
        if poster_proxy_enabled and effective_image_url:
            try:
                local_path = await save_public_thumbnail(effective_image_url)
                if local_path:
                    # 拼成完整外联地址；custom_domain 未配置时降级为相对路径
                    effective_image_url = f"{custom_domain}{local_path}" if custom_domain else local_path
                    logger.debug(f"后备搜索海报外联: {result.imageUrl} → {effective_image_url}")
            except Exception as e:
                # 下载失败不影响搜索结果，降级为原 URL
                logger.debug(f"后备搜索海报外联下载失败（忽略）: {e}")

        # 存储bangumiId到原始信息的映射（仅修改内存中的dict）
        # why：同时把处理后的 imageUrl 存入映射，供 /bangumi/{animeId} 详情接口使用；
        # 若是外联地址则详情接口可直接使用无需再次下载。
        if search_info_mapping:
            search_info_mapping["bangumi_mapping"][unique_bangumi_id] = {
                "provider": result.provider,
                "media_id": result.mediaId,
                "original_title": result.title,
                "type": result.type,
                "season": result.season,
                "anime_id": current_virtual_anime_id,
                "image_url": effective_image_url,
                "year": result.year,
            }

        # 检查库内是否已有该精确源(provider+mediaId)的分集，写入 typeDescription
        base_type_desc = DANDAN_TYPE_DESC_MAPPING.get(result.type, "其他")
        type_description = base_type_desc

        pair_key = (result.provider, result.mediaId)
        existing_episodes = library_episodes_map.get(pair_key, [])
        if existing_episodes:
            episode_ranges = format_episode_ranges(existing_episodes)
            type_description = f"{base_type_desc}（库内：{episode_ranges}）"

        # why：识别词带 source=xxx 时仅标记该源结果；且标题需精确匹配规则 source，
        # 避免同源下无关结果（如 iqiyi 的"中国说唱巅峰对决2022"）被误标识别词。
        item_recognition_title = recognition_title
        if recognition_title:
            # 源限定校验
            if recognition_source_restriction != "all" and result.provider != recognition_source_restriction:
                item_recognition_title = None
            # 标题精确校验（复用识别词管理器 _exact_match，与命中判定一致）
            elif recognition_rule_source and title_recognition_manager:
                if not TitleRecognitionEngine().exact_match(result.title or "", recognition_rule_source):
                    item_recognition_title = None

        search_results.append(
            DandanSearchAnimeItem(
                animeId=current_virtual_anime_id,
                bangumiId=unique_bangumi_id,
                animeTitle=title_with_source,
                type=DANDAN_TYPE_MAPPING.get(result.type, "other"),
                typeDescription=type_description,
                imageUrl=effective_image_url,  # 外联模式时为本地缓存地址，否则为源站原始URL
                startDate=f"{result.year}-01-01T00:00:00+08:00" if result.year else None,
                year=result.year,
                episodeCount=result.episodeCount or 0,
                rating=0.0,
                isFavorited=False,
                recognitionTitle=item_recognition_title,  # 识别词反向映射命中且源匹配时的入库正确名
            )
        )

    await progress_callback(90, "整理搜索结果...")

    # 【性能优化①续】循环结束后一次性写回 bangumi_mapping + 更新缓存状态为完成
    if search_info_mapping:
        search_info_mapping["status"] = "completed"
        search_info_mapping["results"] = [result.model_dump() for result in search_results]
        await set_db_cache(session, FALLBACK_SEARCH_CACHE_PREFIX, search_key, search_info_mapping, FALLBACK_SEARCH_CACHE_TTL)

    timer.step_end(details=f"{len(search_results)}个结果")
    await progress_callback(100, "搜索完成")
    timer.finish()


async def fallback_search_by_title(
    title: str,
    season: Optional[int] = None,
    session: Optional[AsyncSession] = None
) -> List[ProviderSearchInfo]:
    """
    通过标题进行后备搜索

    :param title: 标题
    :param season: 季度
    :param session: 数据库会话
    :return: 搜索结果列表
    """
    try:
        scraper_manager = get_scraper_manager()

        # 从所有启用的源搜索
        results = await search_all(scraper_manager, [title], episode_info={"season": season})

        logger.info(f"标题 '{title}' 后备搜索找到 {len(results)} 个结果")
        return results

    except Exception as e:
        logger.error(f"后备搜索失败: {e}", exc_info=True)
        return []


async def fallback_search_with_metadata(
    title: str,
    season: Optional[int] = None,
    year: Optional[int] = None
) -> List[ProviderSearchInfo]:
    """
    使用元数据源进行后备搜索

    :param title: 标题
    :param season: 季度
    :param year: 年份
    :return: 搜索结果列表
    """
    try:
        metadata_manager = get_metadata_service()

        # 从元数据源获取别名
        aliases = await search_aliases_from_enabled_sources(metadata_manager, title, User(id=0, username="fallback_search"))

        # 使用别名进行搜索
        all_results = []
        for alias in aliases:
            results = await fallback_search_by_title(alias, season)
            all_results.extend(results)

        logger.info(f"元数据后备搜索找到 {len(all_results)} 个结果")
        return all_results

    except Exception as e:
        logger.error(f"元数据后备搜索失败: {e}", exc_info=True)
        return []


async def fallback_search_with_recognition(
    title: str,
    episode_index: int,
    session: Optional[AsyncSession] = None
) -> Optional[ProviderSearchInfo]:
    """
    使用标题识别进行后备搜索

    :param title: 标题
    :param episode_index: 集数
    :param session: 数据库会话
    :return: 最佳匹配结果或None
    """
    try:
        recognition_manager = get_title_recognition_manager()

        # 识别标题
        recognition_result = await recognition_manager.recognize(title, episode_index)

        if recognition_result:
            # 使用识别结果搜索
            results = await fallback_search_by_title(
                recognition_result.get("title"),
                recognition_result.get("season")
            )
            if results:
                return results[0]

        return None

    except Exception as e:
        logger.error(f"标题识别后备搜索失败: {e}", exc_info=True)
        return None


# ============ 搜索分集业务逻辑（从 API 层迁移） ============


async def search_implementation(
    search_term: str,
    episode: Optional[str],
    session: AsyncSession,
    scraper_manager=None,
    config_service=None,
    rate_limiter=None,
) -> "DandanSearchEpisodesResponse":
    """
    搜索接口的通用实现，避免代码重复。
    当启用并行搜索时，还会从源站补充库内缺失的分集。

    🚀 新架构：已迁移到 Workflow 层

    Args:
        search_term: 搜索关键词
        episode: 分集标题（通常是数字）
        session: 数据库会话
        scraper_manager: 弹幕源管理器（可选，并行搜索需要）
        config_service: 配置服务（可选，并行搜索需要）
        rate_limiter: 速率限制器（可选）

    Returns:
        DandanSearchEpisodesResponse 对象
    """
    from src.schemas.dandan import DandanSearchEpisodesResponse, DandanAnimeInfo, DandanEpisodeInfo
    from src.api.dandan.constants import DANDAN_TYPE_MAPPING, DANDAN_TYPE_DESC_MAPPING
    from src.utils import parse_search_keyword
    from fastapi import HTTPException, status

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

    db = get_database_service()
    async with db.transaction(session=session):
        flat_results = await db.episode.search_episodes_in_library(
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
    if config_service and scraper_manager:
        parallel_search_enabled = (await config_service.get("parallelSearchEnabled", "false")).lower() == 'true'

    if parallel_search_enabled and grouped_animes:
        await _merge_source_episodes(
            session, grouped_animes, scraper_manager, config_service
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
