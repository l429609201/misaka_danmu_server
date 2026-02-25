"""
外部控制API - 导入相关路由
包含: /import/auto, /import/direct, /import/edited, /import/xml, /import/url, /episodes
"""

import logging
import uuid
import hashlib

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from thefuzz import fuzz

from src.db import crud, models, orm_models, get_db_session, ConfigManager
from src import tasks
from src.utils import common as utils
from src.core import get_now
from src.core.cache import get_cache_backend
from src.services import ScraperManager, TaskManager, MetadataSourceManager, unified_search, convert_to_chinese_title
from src.utils import (
    SearchTimer, SEARCH_TYPE_CONTROL_SEARCH, SubStepTiming,
    ai_type_and_season_mapping_and_correction, is_movie_by_title,
)
from src.rate_limiter import RateLimiter
from src.ai import AIMatcherManager

from .models import (
    AutoImportSearchType, AutoImportMediaType,
    ControlTaskResponse, ControlSearchResponse, ControlSearchResultItem,
    ControlAutoImportRequest, ControlDirectImportRequest,
    ControlEditedImportRequest, ControlXmlImportRequest, ControlUrlImportRequest
)
from .dependencies import (
    verify_api_key, get_scraper_manager, get_metadata_manager,
    get_task_manager, get_config_manager, get_rate_limiter,
    get_ai_matcher_manager, get_title_recognition_manager,
    _normalize_for_filtering,
)

logger = logging.getLogger(__name__)

router = APIRouter()


@router.post("/import/auto", status_code=status.HTTP_202_ACCEPTED, summary="全自动搜索并导入", response_model=ControlTaskResponse)
async def auto_import(
    request: Request,
    searchType: AutoImportSearchType = Query(..., description="搜索类型。可选值: 'keyword', 'tmdb', 'tvdb', 'douban', 'imdb', 'bangumi'。"),
    searchTerm: str = Query(..., description="搜索内容。根据 searchType 的不同，这里应填入关键词或对应的平台ID。"),
    season: int | None = Query(None, description="季度号。如果未提供，将自动推断或默认为1。"),
    episode: str | None = Query(None, description="集数。支持单集(如'1')或多集(如'1,3,5,7,9,11-13')格式。如果提供，将只导入指定集数（此时必须提供季度）。"),
    mediaType: AutoImportMediaType | None = Query(None, description="媒体类型。当 searchType 为 'keyword' 时必填。如果留空，将根据有无 'season' 参数自动推断。"),
    task_manager: TaskManager = Depends(get_task_manager),
    manager: ScraperManager = Depends(get_scraper_manager),
    metadata_manager: MetadataSourceManager = Depends(get_metadata_manager),
    rate_limiter: RateLimiter = Depends(get_rate_limiter),
    config_manager: ConfigManager = Depends(get_config_manager),
    ai_matcher_manager: AIMatcherManager = Depends(get_ai_matcher_manager),
    title_recognition_manager = Depends(get_title_recognition_manager),
    api_key: str = Depends(verify_api_key)
):
    """
    ### 功能
    这是一个强大的"全自动搜索并导入"接口，它能根据不同的ID类型（如TMDB ID、Bangumi ID等）或关键词进行搜索，并根据一系列智能规则自动选择最佳的数据源进行弹幕导入。

    ### 工作流程
    1.  **元数据获取**: 如果使用ID搜索（如`tmdb`, `bangumi`），接口会首先从对应的元数据网站获取作品的官方标题和别名。
    2.  **媒体库检查**: 检查此作品是否已存在于您的弹幕库中。
        -   如果存在且有精确标记的源，则优先使用该源。
        -   如果存在但无精确标记，则使用已有关联源中优先级最高的那个。
    3.  **全网搜索**: 如果媒体库中不存在，则使用获取到的标题和别名在所有已启用的弹幕源中进行搜索。
    4.  **智能选择**: 从搜索结果中，根据您在"搜索源"页面设置的优先级，选择最佳匹配项。
    5.  **任务提交**: 为最终选择的源创建一个后台导入任务。

    ### 参数使用说明
    -   `searchType`:
        -   `keyword`: 按关键词搜索。此时 `mediaType` 字段**必填**。
        -   `tmdb`, `tvdb`, `douban`, `imdb`, `bangumi`: 按对应平台的ID进行精确搜索。
    -   `season` & `episode`:
        -   **电视剧/番剧**:
            -   提供 `season`，不提供 `episode`: 导入 `season` 指定的整季。
            -   提供 `season` 和 `episode`: 导入指定的集数。支持单集(如'1')或多集(如'1,3,5,7,9,11-13')格式。
        -   **电影**:
            -   `season` 和 `episode` 参数会被忽略。
    -   `mediaType`:
        -   当 `searchType` 为 `keyword` 时，此字段为**必填项**。
        -   当 `searchType` 为其他ID类型时，此字段为可选项。如果留空，系统将根据 `season` 参数是否存在来自动推断媒体类型（有 `season` 则为电视剧，否则为电影）。
    """
    if episode is not None and season is None:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="当提供 'episode' 参数时，'season' 参数也必须提供。")

    payload = ControlAutoImportRequest(
        searchType=searchType,
        searchTerm=searchTerm,
        season=season,
        episode=episode,
        mediaType=mediaType
    )

    # 修正：不再强制将非关键词搜索的 mediaType 设为 None。
    # 允许用户在调用 TMDB 等ID搜索时，预先指定媒体类型，以避免错误的类型推断。
    if payload.searchType == AutoImportSearchType.KEYWORD and not payload.mediaType:
        raise HTTPException(status_code=400, detail="使用 keyword 搜索时，mediaType 字段是必需的。")

    # 新增：如果不是关键词搜索，则检查所选的元数据源是否已启用
    if payload.searchType != AutoImportSearchType.KEYWORD:
        provider_name = payload.searchType.value
        provider_setting = metadata_manager.source_settings.get(provider_name)

        if not provider_setting or not provider_setting.get('isEnabled'):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"元信息搜索源 '{provider_name}' 未启用。请在'设置-元信息搜索源'页面中启用它。"
            )

    if not await manager.acquire_search_lock(api_key):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="已有搜索或自动导入任务正在进行中，请稍后再试。"
        )

    # 修正：将 season、episode 和 mediaType 纳入 unique_key，以允许同一作品不同季/集的导入
    unique_key_parts = [payload.searchType.value, payload.searchTerm]
    if payload.season is not None:
        unique_key_parts.append(f"s{payload.season}")
    if payload.episode is not None:
        # 对于多集格式，保留原始字符串作为 unique_key 的一部分
        unique_key_parts.append(f"e{payload.episode}")
    # 始终包含 mediaType 以区分同名但不同类型的作品，避免重复任务检测问题
    if payload.mediaType is not None:
        unique_key_parts.append(payload.mediaType.value)
    unique_key = f"auto-import-{'-'.join(unique_key_parts)}"

    # 新增：检查最近是否有重复任务
    config_manager_local = get_config_manager(request)
    threshold_hours_str = await config_manager_local.get("externalApiDuplicateTaskThresholdHours", "3")
    try:
        threshold_hours = int(threshold_hours_str)
    except (ValueError, TypeError):
        threshold_hours = 3

    if threshold_hours > 0:
        session_factory = request.app.state.db_session_factory
        async with session_factory() as session:
            recent_task = await crud.find_recent_task_by_unique_key(session, unique_key, threshold_hours)
            if recent_task:
                time_since_creation = get_now() - recent_task.createdAt
                hours_ago = time_since_creation.total_seconds() / 3600
                # 关键修复：抛出异常前释放搜索锁
                await manager.release_search_lock(api_key)
                raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=f"一个相似的任务在 {hours_ago:.1f} 小时前已被提交 (状态: {recent_task.status})。请在 {threshold_hours} 小时后重试。")

            # 关键修复：外部API也应该检查库内是否已存在相同作品
            # 使用与WebUI相同的检查逻辑，通过标题+季度+集数进行检查
            title_recognition_manager_local = get_title_recognition_manager(request)

            # 检查作品是否已存在于库内
            existing_anime = await crud.find_anime_by_title_season_year(
                session, searchTerm, season, None, title_recognition_manager_local, None  # source参数暂时为None，因为这里是查找现有条目
            )

            if existing_anime and episode is not None:
                # 对于单集/多集导入，检查具体集数是否已存在（需要考虑识别词转换）
                # 注意：这里不再拒绝请求，而是在任务执行时跳过已存在的集数
                pass
            elif existing_anime and episode is None:
                # 对于整季导入，如果作品已存在则拒绝
                # 关键修复：抛出异常前释放搜索锁
                await manager.release_search_lock(api_key)
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail=f"作品 '{searchTerm}' 已在媒体库中，无需重复导入整季"
                )

    # 修正：为任务标题添加季/集信息，以确保其唯一性，防止因任务名重复而提交失败。
    title_parts = [f"外部API自动导入: {payload.searchTerm} (类型: {payload.searchType})"]
    if payload.season is not None:
        title_parts.append(f"S{payload.season:02d}")
    if payload.episode is not None:
        # 对于多集格式，直接显示原始字符串
        title_parts.append(f"E{payload.episode}")
    task_title = " ".join(title_parts)

    try:
        task_coro = lambda session, cb: tasks.auto_search_and_import_task(
            payload, cb, session, config_manager, manager, metadata_manager, task_manager,
            ai_matcher_manager=ai_matcher_manager,
            rate_limiter=rate_limiter,
            title_recognition_manager=title_recognition_manager,
            api_key=api_key
        )
        task_id, _ = await task_manager.submit_task(task_coro, task_title, unique_key=unique_key)
        # 注意: 搜索锁由任务内部的 finally 块负责释放,确保任务完成后才释放
        return {"message": "自动导入任务已提交", "taskId": task_id}
    except HTTPException as e:
        # 捕获已知的冲突错误并重新抛出
        # 如果任务提交失败,需要释放锁
        await manager.release_search_lock(api_key)
        raise e
    except Exception as e:
        # 捕获任何在任务提交阶段发生的异常,并确保释放锁
        logger.error(f"提交自动导入任务时发生未知错误: {e}", exc_info=True)
        await manager.release_search_lock(api_key)
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="提交任务时发生内部错误。")




@router.get("/search", response_model=ControlSearchResponse, summary="搜索媒体")
async def search_media(
    keyword: str,
    season: int | None = Query(None, description="要搜索的季度 (可选)"),
    episode: int | None = Query(None, description="要搜索的集数 (可选)"),
    session: AsyncSession = Depends(get_db_session),
    manager: ScraperManager = Depends(get_scraper_manager),
    metadata_manager: MetadataSourceManager = Depends(get_metadata_manager),
    config_manager: ConfigManager = Depends(get_config_manager),
    ai_matcher_manager: AIMatcherManager = Depends(get_ai_matcher_manager),
    api_key: str = Depends(verify_api_key)
):
    """
    ### 功能
    根据关键词从所有启用的弹幕源搜索媒体。这是执行导入操作的第一步。

    ### 工作流程
    1.  接收关键词，以及可选的季度和集数。
    2.  并发地在所有已启用的弹幕源上进行搜索。
    3.  返回一个包含`searchId`和结果列表的响应。`searchId`是本次搜索的唯一标识，用于后续的导入操作。
    4.  搜索结果会在服务器上缓存10分钟。

    ### 参数使用说明
    -   `keyword`: 必需的搜索关键词。
    -   `season`: (可选) 如果提供，搜索将更倾向于或只返回电视剧类型的结果。
    -   `episode`: (可选) 必须与`season`一同提供，用于更精确的单集匹配。
    """
    if episode is not None and season is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="指定集数时必须同时提供季度信息。"
        )

    if not await manager.acquire_search_lock(api_key):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="已有搜索或自动导入任务正在进行中，请稍后再试。"
        )

    # 初始化计时器并开始计时
    timer = SearchTimer(SEARCH_TYPE_CONTROL_SEARCH, keyword, logger)
    timer.start()

    try:
        timer.step_start("关键词解析")
        # --- Start of new logic, copied and adapted from ui_api.py ---
        parsed_keyword = utils.parse_search_keyword(keyword)
        search_title = parsed_keyword["title"]
        original_title = search_title  # 保存原始标题用于日志
        # Prioritize explicit query params over parsed ones
        final_season = season if season is not None else parsed_keyword.get("season")
        final_episode = episode if episode is not None else parsed_keyword.get("episode")

        episode_info = {"season": final_season, "episode": final_episode} if final_season is not None or final_episode is not None else None

        # Create a dummy user for metadata calls, as this API is not user-specific
        user = models.User(id=0, username="control_api")

        logger.info(f"Control API 正在搜索: '{keyword}' (解析为: title='{search_title}', season={final_season}, episode={final_episode})")
        timer.step_end()

        # 🚀 名称转换功能 - 检测非中文标题并尝试转换为中文（在所有处理之前执行）
        timer.step_start("名称转换")
        converted_title, conversion_applied = await convert_to_chinese_title(
            search_title,
            config_manager,
            metadata_manager,
            ai_matcher_manager,
            user
        )
        if conversion_applied:
            logger.info(f"✓ Control API 名称转换: '{original_title}' → '{converted_title}'")
            search_title = converted_title
        else:
            logger.info(f"○ Control API 名称转换未生效: '{original_title}'")
        timer.step_end()

        if not manager.has_enabled_scrapers:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="没有启用的弹幕搜索源，请在'搜索源'页面中启用至少一个。"
            )

        # 外部控制搜索 AI映射配置检查
        external_search_season_mapping_enabled = await config_manager.get("externalSearchEnableTmdbSeasonMapping", "false")
        if external_search_season_mapping_enabled.lower() != "true":
            logger.info("○ 外部控制-搜索媒体 统一AI映射: 功能未启用")

        timer.step_start("弹幕源搜索")
        # 使用统一的搜索函数（不进行排序，后面自己处理）
        results = await unified_search(
            search_term=search_title,
            session=session,
            scraper_manager=manager,
            metadata_manager=metadata_manager,
            use_alias_expansion=True,
            use_alias_filtering=True,
            use_title_filtering=True,
            use_source_priority_sorting=False,  # 不排序，后面自己处理
            progress_callback=None
        )
        # 收集单源搜索耗时信息
        source_timing_sub_steps = [
            SubStepTiming(name=name, duration_ms=dur, result_count=cnt)
            for name, dur, cnt in manager.last_search_timing
        ]
        timer.step_end(details=f"{len(results)}个结果", sub_steps=source_timing_sub_steps)

        logger.info(f"搜索完成，共 {len(results)} 个结果")

        for item in results:
            if item.type == 'tv_series' and is_movie_by_title(item.title):
                item.type = 'movie'

            # 修正：如果用户指定了集数，则设置 currentEpisodeIndex
            # 这样后续的导入逻辑会自动识别为单集导入
            if final_episode is not None:
                item.currentEpisodeIndex = final_episode

        if final_season:
            original_count = len(results)
            filtered_by_type = [item for item in results if item.type == 'tv_series']
            results = [item for item in filtered_by_type if item.season == final_season]
            logger.info(f"根据指定的季度 ({final_season}) 进行过滤，从 {original_count} 个结果中保留了 {len(results)} 个。")

        source_settings = await crud.get_all_scraper_settings(session)
        source_order_map = {s['providerName']: s['displayOrder'] for s in source_settings}

        def sort_key(item: models.ProviderSearchInfo):
            return (source_order_map.get(item.provider, 999), -fuzz.token_set_ratio(keyword, item.title))

        sorted_results = sorted(results, key=sort_key)

        # 使用统一的AI类型和季度映射修正函数
        if external_search_season_mapping_enabled.lower() == "true":
            try:
                timer.step_start("AI映射修正")
                # 获取AI匹配器（使用依赖注入的实例）
                ai_matcher = await ai_matcher_manager.get_matcher()
                if ai_matcher:
                    logger.info(f"○ 外部控制-搜索媒体 开始统一AI映射修正: '{search_title}' ({len(sorted_results)} 个结果)")

                    # 使用新的统一函数进行类型和季度修正
                    mapping_result = await ai_type_and_season_mapping_and_correction(
                        search_title=search_title,
                        search_results=sorted_results,
                        metadata_manager=metadata_manager,
                        ai_matcher=ai_matcher,
                        logger=logger,
                        similarity_threshold=60.0
                    )

                    # 应用修正结果
                    if mapping_result['total_corrections'] > 0:
                        logger.info(f"✓ 外部控制-搜索媒体 统一AI映射成功: 总计修正了 {mapping_result['total_corrections']} 个结果")
                        logger.info(f"  - 类型修正: {len(mapping_result['type_corrections'])} 个")
                        logger.info(f"  - 季度修正: {len(mapping_result['season_corrections'])} 个")

                        # 更新搜索结果（已经直接修改了sorted_results）
                        sorted_results = mapping_result['corrected_results']
                        timer.step_end(details=f"修正{mapping_result['total_corrections']}个")
                    else:
                        logger.info(f"○ 外部控制-搜索媒体 统一AI映射: 未找到需要修正的信息")
                        timer.step_end(details="无修正")
                else:
                    logger.warning("○ 外部控制-搜索媒体 AI映射: AI匹配器未启用或初始化失败")
                    timer.step_end(details="匹配器未启用")

            except Exception as e:
                logger.warning(f"外部控制-搜索媒体 统一AI映射任务执行失败: {e}")
                timer.step_end(details=f"失败: {e}")
        else:
            logger.info("○ 外部控制-搜索媒体 统一AI映射: 功能未启用")

        timer.step_start("结果缓存")
        search_id = str(uuid.uuid4())
        indexed_results = [ControlSearchResultItem(**r.model_dump(), resultIndex=i) for i, r in enumerate(sorted_results)]
        _cache_data = [r.model_dump() for r in sorted_results]
        _cache_key = f"control_search_{search_id}"
        _backend = get_cache_backend()
        if _backend is not None:
            try:
                await _backend.set(_cache_key, _cache_data, ttl=600, region="default")
            except Exception:
                await crud.set_cache(session, _cache_key, _cache_data, 600)
        else:
            await crud.set_cache(session, _cache_key, _cache_data, 600)
        timer.step_end()

        timer.finish()  # 打印计时报告
        return ControlSearchResponse(searchId=search_id, results=indexed_results)
    finally:
        await manager.release_search_lock(api_key)



@router.post("/import/direct", status_code=status.HTTP_202_ACCEPTED, summary="直接导入搜索结果", response_model=ControlTaskResponse)
async def direct_import(
    payload: ControlDirectImportRequest,
    session: AsyncSession = Depends(get_db_session),
    task_manager: TaskManager = Depends(get_task_manager),
    manager: ScraperManager = Depends(get_scraper_manager),
    metadata_manager: MetadataSourceManager = Depends(get_metadata_manager),
    config_manager: ConfigManager = Depends(get_config_manager),
    rate_limiter: RateLimiter = Depends(get_rate_limiter),
    title_recognition_manager = Depends(get_title_recognition_manager)
):
    """
    ### 功能
    在执行`/search`后，使用返回的`searchId`和您选择的结果索引（`resultIndex`）来直接导入弹幕。

    ### 工作流程
    这是一个简单、直接的导入方式。它会为选定的媒体创建一个后台导入任务。您也可以在请求中附加元数据ID（如`tmdbId`）来覆盖或补充作品信息。
    """
    cache_key = f"control_search_{payload.searchId}"
    cached_results_raw = None
    _backend = get_cache_backend()
    if _backend is not None:
        try:
            cached_results_raw = await _backend.get(cache_key, region="default")
        except Exception:
            pass
    if cached_results_raw is None:
        cached_results_raw = await crud.get_cache(session, cache_key)

    if cached_results_raw is None:
        raise HTTPException(status_code=404, detail="搜索会话已过期或无效，请重新搜索。")

    try:
        cached_results = [models.ProviderSearchInfo.model_validate(r) for r in cached_results_raw]
    except Exception:
        raise HTTPException(status_code=500, detail="无法解析缓存的搜索结果。")

    if not (0 <= payload.resultIndex < len(cached_results)):
        raise HTTPException(status_code=400, detail="提供的 result_index 无效。")

    item_to_import = cached_results[payload.resultIndex]

    # 关键修复：恢复并完善在任务提交前的重复检查。
    # 这确保了直接导入的行为与UI导入完全一致。
    duplicate_reason = await crud.check_duplicate_import(
        session=session,
        provider=item_to_import.provider,
        media_id=item_to_import.mediaId,
        anime_title=item_to_import.title,
        media_type=item_to_import.type,
        season=item_to_import.season,
        year=item_to_import.year,
        is_single_episode=item_to_import.currentEpisodeIndex is not None,
        episode_index=item_to_import.currentEpisodeIndex,
        title_recognition_manager=title_recognition_manager
    )
    if duplicate_reason:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=duplicate_reason
        )

    # 修正：为任务标题添加季/集信息，以确保其唯一性，防止因任务名重复而提交失败。
    title_parts = [f"外部API导入: {item_to_import.title} ({item_to_import.provider})"]
    if item_to_import.currentEpisodeIndex is not None and item_to_import.season is not None:
        title_parts.append(f"S{item_to_import.season:02d}E{item_to_import.currentEpisodeIndex:02d}")
    task_title = " ".join(title_parts)

    # 修正：为单集导入任务生成更具体的唯一键，以允许对同一作品的不同单集进行排队。
    unique_key = f"import-{item_to_import.provider}-{item_to_import.mediaId}"
    if item_to_import.currentEpisodeIndex is not None:
        unique_key += f"-ep{item_to_import.currentEpisodeIndex}"

    try:
        task_coro = lambda session, cb: tasks.generic_import_task(
            provider=item_to_import.provider,
            mediaId=item_to_import.mediaId,
            animeTitle=item_to_import.title,
            mediaType=item_to_import.type,
            season=item_to_import.season,
            year=item_to_import.year,
            currentEpisodeIndex=item_to_import.currentEpisodeIndex,
            imageUrl=item_to_import.imageUrl,
            doubanId=payload.doubanId,
            config_manager=config_manager,
            metadata_manager=metadata_manager, tmdbId=payload.tmdbId, imdbId=payload.imdbId,
            tvdbId=payload.tvdbId, bangumiId=payload.bangumiId,
            progress_callback=cb, session=session, manager=manager, task_manager=task_manager,
            rate_limiter=rate_limiter, title_recognition_manager=title_recognition_manager
        )
        task_id, _ = await task_manager.submit_task(task_coro, task_title, unique_key=unique_key)
        return {"message": "导入任务已提交", "taskId": task_id}
    except HTTPException as e:
        raise e
    except Exception as e:
        logger.error(f"提交直接导入任务时发生未知错误: {e}", exc_info=True)
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="提交任务时发生内部错误。")


from typing import List

@router.get("/episodes", response_model=List[models.ProviderEpisodeInfo], summary="获取搜索结果的分集列表")
async def get_episodes(
    searchId: str = Query(..., description="来自/search接口的searchId"),
    result_index: int = Query(..., ge=0, description="要获取分集的结果的索引"),
    session: AsyncSession = Depends(get_db_session),
    manager: ScraperManager = Depends(get_scraper_manager),
):
    """
    ### 功能
    在执行`/search`后，获取指定搜索结果的完整分集列表。

    ### 工作流程
    此接口主要用于"编辑后导入"的场景。您可以先获取原始的分集列表，在您的客户端进行修改（例如，删除预告、调整顺序），然后再通过`/import/edited`接口提交修改后的列表进行导入。
    """
    cache_key = f"control_search_{searchId}"
    cached_results_raw = None
    _backend = get_cache_backend()
    if _backend is not None:
        try:
            cached_results_raw = await _backend.get(cache_key, region="default")
        except Exception:
            pass
    if cached_results_raw is None:
        cached_results_raw = await crud.get_cache(session, cache_key)

    if cached_results_raw is None:
        raise HTTPException(status_code=404, detail="搜索会话已过期或无效，请重新搜索。")

    try:
        cached_results = [models.ProviderSearchInfo.model_validate(r) for r in cached_results_raw]
    except Exception:
        raise HTTPException(status_code=500, detail="无法解析缓存的搜索结果。")

    if not (0 <= result_index < len(cached_results)):
        raise HTTPException(status_code=400, detail="提供的 result_index 无效。")

    item_to_fetch = cached_results[result_index]

    scraper = manager.get_scraper(item_to_fetch.provider)
    try:
        return await scraper.get_episodes(item_to_fetch.mediaId, db_media_type=item_to_fetch.type)
    except httpx.RequestError as e:
        logger.error(f"获取分集列表时发生网络错误 (provider={item_to_fetch.provider}, media_id={item_to_fetch.mediaId}): {e}", exc_info=True)
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=f"从 {item_to_fetch.provider} 获取分集列表时发生网络错误: {e}")



@router.post("/import/edited", status_code=status.HTTP_202_ACCEPTED, summary="导入编辑后的分集列表", response_model=ControlTaskResponse)
async def edited_import(
    payload: ControlEditedImportRequest,
    session: AsyncSession = Depends(get_db_session),
    task_manager: TaskManager = Depends(get_task_manager),
    manager: ScraperManager = Depends(get_scraper_manager),
    rate_limiter: RateLimiter = Depends(get_rate_limiter),
    config_manager: ConfigManager = Depends(get_config_manager),
    metadata_manager: MetadataSourceManager = Depends(get_metadata_manager),
    title_recognition_manager = Depends(get_title_recognition_manager)
):
    """
    ### 功能
    导入一个经过用户编辑和调整的分集列表。

    ### 工作流程
    这是最灵活的导入方式。它允许您完全控制要导入的分集，包括标题、顺序等。您可以在请求中覆盖作品标题和附加元数据ID。
    """
    cache_key = f"control_search_{payload.searchId}"
    cached_results_raw = None
    _backend = get_cache_backend()
    if _backend is not None:
        try:
            cached_results_raw = await _backend.get(cache_key, region="default")
        except Exception:
            pass
    if cached_results_raw is None:
        cached_results_raw = await crud.get_cache(session, cache_key)

    if cached_results_raw is None:
        raise HTTPException(status_code=404, detail="搜索会话已过期或无效，请重新搜索。")

    try:
        cached_results = [models.ProviderSearchInfo.model_validate(r) for r in cached_results_raw]
    except Exception:
        raise HTTPException(status_code=500, detail="无法解析缓存的搜索结果。")

    if not (0 <= payload.resultIndex < len(cached_results)):
        raise HTTPException(status_code=400, detail="提供的 result_index 无效。")

    item_to_import = cached_results[payload.resultIndex]

    # 关键修复：恢复并完善在任务提交前的重复检查。
    # 对于编辑后导入，我们需要检查每个单集是否已存在（必须是相同数据源）
    # 检查数据源是否已存在
    source_exists = await crud.check_source_exists_by_media_id(session, item_to_import.provider, item_to_import.mediaId)

    if source_exists:
        # 数据源已存在，检查每个要导入的单集是否已有弹幕（必须是相同 provider + media_id）
        existing_episodes = []
        for episode in payload.episodes:
            # 使用精确检查：provider + media_id + episode_index
            stmt = select(orm_models.Episode.id).join(
                orm_models.AnimeSource, orm_models.Episode.sourceId == orm_models.AnimeSource.id
            ).where(
                orm_models.AnimeSource.providerName == item_to_import.provider,
                orm_models.AnimeSource.mediaId == item_to_import.mediaId,
                orm_models.Episode.episodeIndex == episode.episodeIndex,
                orm_models.Episode.danmakuFilePath.isnot(None),
                orm_models.Episode.commentCount > 0
            ).limit(1)
            result = await session.execute(stmt)
            if result.scalar_one_or_none() is not None:
                existing_episodes.append(episode.episodeIndex)

        # 如果所有集都已存在，则阻止导入
        if len(existing_episodes) == len(payload.episodes):
            episode_list = ", ".join(map(str, existing_episodes))
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"所有要导入的分集 ({episode_list}) 都已在该数据源（{item_to_import.provider}）中存在弹幕"
            )
        # 如果部分集已存在，给出警告但允许导入
        elif existing_episodes:
            episode_list = ", ".join(map(str, existing_episodes))
            logger.warning(f"外部API编辑导入: 分集 {episode_list} 已在该数据源（{item_to_import.provider}）中存在，将跳过这些分集")


    # 构建编辑导入请求
    edited_request = models.EditedImportRequest(
        provider=item_to_import.provider,
        mediaId=item_to_import.mediaId,
        animeTitle=payload.title or item_to_import.title,
        mediaType=item_to_import.type,
        season=item_to_import.season,
        year=item_to_import.year,
        episodes=payload.episodes,
        tmdbId=payload.tmdbId,
        imdbId=payload.imdbId,
        tvdbId=payload.tvdbId,
        doubanId=payload.doubanId,
        bangumiId=payload.bangumiId,
        tmdbEpisodeGroupId=payload.tmdbEpisodeGroupId,
        imageUrl=item_to_import.imageUrl
    )

    # 修正：为任务标题添加季/集信息，以确保其唯一性，防止因任务名重复而提交失败。
    title_parts = [f"外部API编辑后导入: {edited_request.animeTitle} ({edited_request.provider})"]
    if edited_request.season is not None:
        title_parts.append(f"S{edited_request.season:02d}")
    if payload.episodes:
        episode_indices = sorted([ep.episodeIndex for ep in payload.episodes])
        if len(episode_indices) == 1:
            title_parts.append(f"E{episode_indices[0]:02d}")
        else:
            title_parts.append(f"({len(episode_indices)}集)")
    task_title = " ".join(title_parts)

    # 修正：使 unique_key 更具体，以允许对同一媒体的不同分集列表进行排队导入。
    episode_indices_str = ",".join(sorted([str(ep.episodeIndex) for ep in payload.episodes]))
    episodes_hash = hashlib.md5(episode_indices_str.encode('utf-8')).hexdigest()[:8]
    unique_key = f"import-{edited_request.provider}-{edited_request.mediaId}-{episodes_hash}"

    try:
        task_coro = lambda session, cb: tasks.edited_import_task(
            request_data=edited_request, progress_callback=cb, session=session,
            config_manager=config_manager, manager=manager, rate_limiter=rate_limiter,
            metadata_manager=metadata_manager, title_recognition_manager=title_recognition_manager
        )
        task_id, _ = await task_manager.submit_task(task_coro, task_title, unique_key=unique_key)
        return {"message": "编辑后导入任务已提交", "taskId": task_id}
    except HTTPException as e:
        raise e
    except Exception as e:
        logger.error(f"提交编辑后导入任务时发生未知错误: {e}", exc_info=True)
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="提交任务时发生内部错误。")



@router.post("/import/xml", status_code=status.HTTP_202_ACCEPTED, summary="从XML/文本导入弹幕", response_model=ControlTaskResponse)
async def xml_import(
    payload: ControlXmlImportRequest,
    session: AsyncSession = Depends(get_db_session),
    task_manager: TaskManager = Depends(get_task_manager),
    manager: ScraperManager = Depends(get_scraper_manager),
    rate_limiter: RateLimiter = Depends(get_rate_limiter)
):
    """
    ### 功能
    为一个已存在的数据源，导入指定集数的弹幕（通过XML或纯文本内容）。
    ### 工作流程
    1.  您需要提供一个已存在于系统中的 `sourceId`。
    2.  提供要导入的 `episodeIndex` (集数) 和弹幕 `content`。
    3.  系统会为该数据源创建一个后台任务，将内容解析并导入到指定分集。

    此接口非常适合用于对已有的数据源进行单集补全或更新。
    """
    source_info = await crud.get_anime_source_info(session, payload.sourceId)
    if not source_info:
        raise HTTPException(status_code=404, detail=f"数据源 ID: {payload.sourceId} 未找到。")

    # This type of import should only be for 'custom' provider
    if source_info["providerName"] != 'custom':
        raise HTTPException(status_code=400, detail=f"XML/文本导入仅支持 'custom' 类型的源，但目标源类型为 '{source_info['providerName']}'。")

    anime_id = source_info["animeId"]
    anime_title = source_info["title"]

    task_title = f"外部API XML导入: {anime_title} - 第 {payload.episodeIndex} 集"
    unique_key = f"manual-import-{payload.sourceId}-{payload.episodeIndex}"

    try:
        task_coro = lambda s, cb: tasks.manual_import_task(
            sourceId=payload.sourceId,
            animeId=anime_id,
            title=payload.title,
            episodeIndex=payload.episodeIndex,
            content=payload.content,
            providerName='custom',
            progress_callback=cb,
            session=s,
            manager=manager,
            rate_limiter=rate_limiter
        )
        task_id, _ = await task_manager.submit_task(task_coro, task_title, unique_key=unique_key)
        return {"message": "XML导入任务已提交", "taskId": task_id}
    except HTTPException as e:
        raise e
    except Exception as e:
        logger.error(f"提交XML导入任务时发生未知错误: {e}", exc_info=True)
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="提交任务时发生内部错误。")


@router.post("/import/url", status_code=status.HTTP_202_ACCEPTED, summary="从URL导入", response_model=ControlTaskResponse)
async def url_import(
    payload: ControlUrlImportRequest,
    session: AsyncSession = Depends(get_db_session),
    task_manager: TaskManager = Depends(get_task_manager),
    manager: ScraperManager = Depends(get_scraper_manager),
    rate_limiter: RateLimiter = Depends(get_rate_limiter)
):
    """
    ### 功能
    为一个已存在的数据源，导入指定集数的弹幕。
    ### 工作流程
    1.  您需要提供一个已存在于系统中的 `sourceId`。
    2.  提供要导入的 `episodeIndex` (集数) 和包含弹幕的视频页面 `url`。
    3.  系统会为该数据源创建一个后台任务，精确地获取并导入指定集数的弹幕。

    此接口非常适合用于对已有的数据源进行单集补全或更新。
    """
    source_info = await crud.get_anime_source_info(session, payload.sourceId)
    if not source_info:
        raise HTTPException(status_code=404, detail=f"数据源 ID: {payload.sourceId} 未找到。")

    provider_name = source_info["providerName"]
    anime_id = source_info["animeId"]
    anime_title = source_info["title"]

    scraper = manager.get_scraper(provider_name)
    if not hasattr(scraper, 'get_info_from_url'):
        raise HTTPException(status_code=400, detail=f"数据源 '{provider_name}' 不支持从URL导入。")

    task_title = f"外部API URL导入: {anime_title} - 第 {payload.episodeIndex} 集 ({provider_name})"
    unique_key = f"manual-import-{payload.sourceId}-{payload.episodeIndex}"

    try:
        task_coro = lambda s, cb: tasks.manual_import_task(
            sourceId=payload.sourceId,
            animeId=anime_id,
            title=payload.title,
            episodeIndex=payload.episodeIndex,
            content=payload.url,
            providerName=provider_name,
            progress_callback=cb,
            session=s,
            manager=manager,
            rate_limiter=rate_limiter
        )
        task_id, _ = await task_manager.submit_task(task_coro, task_title, unique_key=unique_key)
        return {"message": "URL导入任务已提交", "taskId": task_id}
    except HTTPException as e:
        raise e
    except Exception as e:
        logger.error(f"提交URL导入任务时发生未知错误: {e}", exc_info=True)
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="提交任务时发生内部错误。")