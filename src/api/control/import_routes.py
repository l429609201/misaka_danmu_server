"""
外部控制API - 导入相关路由
包含: /import/auto, /import/direct, /import/edited, /import/xml, /import/url, /episodes
"""

import hashlib
import logging
from typing import List, Union

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, Request, status

from src.services.ai_service import AIService
from src.core import get_now
from src.rate_limiter import RateLimiter
from src.schemas.control.import_api import (
    ControlDirectImportRequest,
    ControlEditedImportRequest,
    ControlSearchResponse,
    ControlTaskResponse,
    ControlUrlImportRequest,
    ControlXmlImportRequest,
    EpisodesWithFilteredResponse,
)
from src.schemas.import_schemas import (
    AutoImportMediaType,
    AutoImportSearchType,
    ControlAutoImportRequest,
    EditedImportRequest,
)
from src.schemas.search import ProviderEpisodeInfo
from src.schemas.ui.search import ProviderSearchInfo
from src.services.metadata_service import MetadataService
from src.services.scraper_manager import ScraperManager
from src.services.task_manager import TaskManager
from src.services.config_service import ConfigService
from src.services.service_container import get_database_service
from src.workflows.title_recognition_lookup import find_anime_with_recognition
from src.workflows.search.result_cache import read_search_results
from src.workflows.search.entry_flow import (
    search_control, SearchBusyError, SearchCacheUnavailableError,
)

from .dependencies import (
    get_ai_service,
    get_config_service,
    get_metadata_service,
    get_rate_limiter,
    get_scraper_manager,
    get_task_manager,
    get_title_recognition_manager,
    verify_api_key,
)

from src.workflows.supplement_episodes import get_episodes_routed

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
    metadata_manager: MetadataService = Depends(get_metadata_service),
    rate_limiter: RateLimiter = Depends(get_rate_limiter),
    config_service: ConfigService = Depends(get_config_service),
    ai_service: AIService = Depends(get_ai_service),
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

    # 锁在提交成功前归请求所有，任何预检异常或取消都必须释放。
    task_submitted = False
    try:
        unique_key_parts = [payload.searchType.value, payload.searchTerm]
        if payload.season is not None:
            unique_key_parts.append(f"s{payload.season}")
        if payload.episode is not None:
            unique_key_parts.append(f"e{payload.episode}")
        if payload.mediaType is not None:
            unique_key_parts.append(payload.mediaType.value)
        unique_key = f"auto-import-{'-'.join(unique_key_parts)}"

        threshold_hours_str = await config_service.get("externalApiDuplicateTaskThresholdHours", "3")
        try:
            threshold_hours = int(threshold_hours_str)
        except (ValueError, TypeError):
            threshold_hours = 3

        if threshold_hours > 0:
            db = get_database_service()
            async with db.transaction():
                # task 代理暴露实际查询方法，不能使用不存在的 task_query 域。
                recent_task = await db.task.find_recent_task_by_unique_key(unique_key, threshold_hours)
                if recent_task:
                    # 数据库时间为本地无时区时间，避免与 aware datetime 相减报错。
                    now = get_now()
                    if recent_task.createdAt.tzinfo is None:
                        now = now.replace(tzinfo=None)
                    hours_ago = (now - recent_task.createdAt).total_seconds() / 3600
                    raise HTTPException(
                        status_code=status.HTTP_409_CONFLICT,
                        detail=f"一个相似的任务在 {hours_ago:.1f} 小时前已被提交 (状态: {recent_task.status})。请在 {threshold_hours} 小时后重试。",
                    )

                # 转换重试由 Workflow 编排，仓储只接收标题、季度和年份。
                existing_anime = await find_anime_with_recognition(
                    db, searchTerm, season, None, title_recognition_manager, None
                )
                if existing_anime and episode is None:
                    raise HTTPException(
                        status_code=status.HTTP_409_CONFLICT,
                        detail=f"作品 '{searchTerm}' 已在媒体库中，无需重复导入整季",
                    )

        title_parts = [f"外部API自动导入: {payload.searchTerm} (类型: {payload.searchType})"]
        if payload.season is not None:
            title_parts.append(f"S{payload.season:02d}")
        if payload.episode is not None:
            title_parts.append(f"E{payload.episode}")
        task_title = " ".join(title_parts)

        task_coro = task_manager.build_task_coro_factory(
            "auto_import",
            payload=payload,
            config_service=config_service,
            scraper_manager=manager,
            metadata_manager=metadata_manager,
            task_manager=task_manager,
            ai_service=ai_service,
            rate_limiter=rate_limiter,
            title_recognition_manager=title_recognition_manager,
            api_key=api_key,
        )
        task_id, _ = await task_manager.submit_task(
            task_coro, task_title, unique_key=unique_key,
            task_type="auto_import",
            task_parameters=payload.model_dump(),
            # 父任务负责搜索与派发，实际下载子任务仍走下载队列流控。
            queue_type="search",
        )
        # 提交成功后才将锁交给后台任务，由任务 finally 释放。
        task_submitted = True
        return {"message": "自动导入任务已提交", "taskId": task_id}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"提交自动导入任务时发生未知错误: {e}", exc_info=True)
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="提交任务时发生内部错误。") from e
    finally:
        if not task_submitted:
            await manager.release_search_lock(api_key)




@router.get("/search", response_model=ControlSearchResponse, response_model_exclude_defaults=True, summary="搜索媒体")
async def search_media(
    keyword: str,
    season: int | None = Query(None, description="要搜索的季度 (可选)"),
    episode: int | None = Query(None, description="要搜索的集数 (可选)"),
    manager: ScraperManager = Depends(get_scraper_manager),
    metadata_manager: MetadataService = Depends(get_metadata_service),
    config_service: ConfigService = Depends(get_config_service),
    ai_service: AIService = Depends(get_ai_service),
    title_recognition_manager = Depends(get_title_recognition_manager),
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

    # 互斥、搜索策略和会话缓存由编排层管理；与主页一致，不持有请求级数据库会话。
    try:
        payload = await search_control(
            keyword, season=season, episode=episode, session=None,
            scraper_manager=manager, metadata_manager=metadata_manager,
            config_service=config_service, ai_service=ai_service,
            title_recognition_manager=title_recognition_manager, api_key=api_key,
        )
        return ControlSearchResponse(**payload)
    except SearchBusyError as exc:
        raise HTTPException(status_code=429, detail=str(exc)) from exc
    except SearchCacheUnavailableError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except httpx.RequestError as exc:
        raise HTTPException(status_code=503, detail=f"搜索时发生网络错误: {exc}") from exc



@router.post("/import/direct", status_code=status.HTTP_202_ACCEPTED, summary="直接导入搜索结果", response_model=ControlTaskResponse)
async def direct_import(
    payload: ControlDirectImportRequest,
    task_manager: TaskManager = Depends(get_task_manager),
    manager: ScraperManager = Depends(get_scraper_manager),
    metadata_manager: MetadataService = Depends(get_metadata_service),
    config_service: ConfigService = Depends(get_config_service),
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
    # 不指定分页，按原始顺序读取全部会话结果，保持 resultIndex 语义。
    cached_data = await read_search_results(cache_key, region="default")
    cached_results_raw = cached_data["results"] if cached_data is not None else None

    if cached_results_raw is None:
        raise HTTPException(status_code=404, detail="搜索会话已过期或无效，请重新搜索。")

    try:
        cached_results = [ProviderSearchInfo.model_validate(r) for r in cached_results_raw]
    except Exception:
        raise HTTPException(status_code=500, detail="无法解析缓存的搜索结果。")

    if not (0 <= payload.resultIndex < len(cached_results)):
        raise HTTPException(status_code=400, detail="提供的 result_index 无效。")

    item_to_import = cached_results[payload.resultIndex]

    # 与主页共用现有精确判重接口，避免调用已移除的数据域和旧参数。
    db = get_database_service()
    async with db.transaction():
        duplicate_reason = await db.episode.check_duplicate_import(
            provider=item_to_import.provider,
            media_id=item_to_import.mediaId,
            anime_title=item_to_import.title,
            season=item_to_import.season,
            is_single_episode=item_to_import.currentEpisodeIndex is not None,
            episode_index=item_to_import.currentEpisodeIndex,
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
        task_coro = task_manager.build_task_coro_factory(
            "generic_import",
            provider=item_to_import.provider,
            mediaId=item_to_import.mediaId,
            animeTitle=item_to_import.title,
            mediaType=item_to_import.type,
            season=item_to_import.season,
            year=item_to_import.year,
            currentEpisodeIndex=item_to_import.currentEpisodeIndex,
            imageUrl=item_to_import.imageUrl,
            config_service=config_service,
            metadata_manager=metadata_manager,
            manager=manager,
            task_manager=task_manager,
            rate_limiter=rate_limiter, title_recognition_manager=title_recognition_manager,
            doubanId=payload.doubanId, tmdbId=payload.tmdbId, imdbId=payload.imdbId,
            tvdbId=payload.tvdbId, bangumiId=payload.bangumiId,
        )
        # 补齐 task_parameters：供完成通知展示作品名/季/集/类型/来源
        direct_task_parameters = {
            "provider": item_to_import.provider,
            "mediaId": item_to_import.mediaId,
            "animeTitle": item_to_import.title,
            "mediaType": item_to_import.type,
            "season": item_to_import.season,
            "episode": item_to_import.currentEpisodeIndex,
            "year": item_to_import.year,
            "imageUrl": item_to_import.imageUrl,
            "tmdbId": payload.tmdbId or "",
        }
        task_id, _ = await task_manager.submit_task(
            task_coro, task_title, unique_key=unique_key,
            task_parameters=direct_task_parameters,
        )
        return {"message": "导入任务已提交", "taskId": task_id}
    except HTTPException as e:
        raise e
    except Exception as e:
        logger.error(f"提交直接导入任务时发生未知错误: {e}", exc_info=True)
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="提交任务时发生内部错误。")


@router.get("/episodes", summary="获取搜索结果的分集列表")
async def get_episodes(
    searchId: str = Query(..., description="来自/search接口的searchId"),
    result_index: int = Query(..., ge=0, description="要获取分集的结果的索引"),
    includeFiltered: int = Query(0, ge=0, le=1, description="是否返回被过滤的分集：0=否(默认，仅返回保留分集数组)，1=是(返回 {episodes, filteredEpisodes} 对象)"),
    manager: ScraperManager = Depends(get_scraper_manager),
) -> Union[List[ProviderEpisodeInfo], EpisodesWithFilteredResponse]:
    """
    ### 功能
    在执行`/search`后，获取指定搜索结果的完整分集列表。

    ### 工作流程
    此接口主要用于"编辑后导入"的场景。您可以先获取原始的分集列表，在您的客户端进行修改（例如，删除预告、调整顺序），然后再通过`/import/edited`接口提交修改后的列表进行导入。

    ### includeFiltered 参数
    - **0（默认）**: 返回 `List[ProviderEpisodeInfo]`，即直接返回保留的分集数组，与旧版本行为一致。
    - **1**: 返回 `EpisodesWithFilteredResponse` 对象，包含 `episodes`（保留分集）和 `filteredEpisodes`（被黑名单/正则过滤掉的分集，如预告、花絮）。可用于判断是否需要通过 `/import/edited` 手动纳入这些被过滤的分集。
    """
    cache_key = f"control_search_{searchId}"
    # 不指定分页，保持搜索会话的完整索引顺序。
    cached_data = await read_search_results(cache_key, region="default")
    cached_results_raw = cached_data["results"] if cached_data is not None else None

    if cached_results_raw is None:
        raise HTTPException(status_code=404, detail="搜索会话已过期或无效，请重新搜索。")

    try:
        cached_results = [ProviderSearchInfo.model_validate(r) for r in cached_results_raw]
    except Exception:
        raise HTTPException(status_code=500, detail="无法解析缓存的搜索结果。")

    if not (0 <= result_index < len(cached_results)):
        raise HTTPException(status_code=400, detail="提供的 result_index 无效。")

    item_to_fetch = cached_results[result_index]

    try:
        result = await get_episodes_routed(manager,
            item_to_fetch.provider, item_to_fetch.mediaId,
            db_media_type=item_to_fetch.type,
            return_filtered=bool(includeFiltered)
        )
        if includeFiltered:
            kept, filtered = result
            return EpisodesWithFilteredResponse(episodes=kept, filteredEpisodes=filtered)
        else:
            # 默认行为：直接返回分集数组，与旧版本兼容
            return result
    except httpx.RequestError as e:
        logger.error(f"获取分集列表时发生网络错误 (provider={item_to_fetch.provider}, media_id={item_to_fetch.mediaId}): {e}", exc_info=True)
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=f"从 {item_to_fetch.provider} 获取分集列表时发生网络错误: {e}")



@router.post("/import/edited", status_code=status.HTTP_202_ACCEPTED, summary="导入编辑后的分集列表", response_model=ControlTaskResponse)
async def edited_import(
    payload: ControlEditedImportRequest,
    task_manager: TaskManager = Depends(get_task_manager),
    manager: ScraperManager = Depends(get_scraper_manager),
    rate_limiter: RateLimiter = Depends(get_rate_limiter),
    config_service: ConfigService = Depends(get_config_service),
    metadata_manager: MetadataService = Depends(get_metadata_service),
    title_recognition_manager = Depends(get_title_recognition_manager)
):
    """
    ### 功能
    导入一个经过用户编辑和调整的分集列表。

    ### 工作流程
    这是最灵活的导入方式。它允许您完全控制要导入的分集，包括标题、顺序等。您可以在请求中覆盖作品标题和附加元数据ID。
    """
    cache_key = f"control_search_{payload.searchId}"
    # 编辑导入同样读取全量结果，不能对搜索索引再次分页。
    cached_data = await read_search_results(cache_key, region="default")
    cached_results_raw = cached_data["results"] if cached_data is not None else None

    if cached_results_raw is None:
        raise HTTPException(status_code=404, detail="搜索会话已过期或无效，请重新搜索。")

    try:
        cached_results = [ProviderSearchInfo.model_validate(r) for r in cached_results_raw]
    except Exception:
        raise HTTPException(status_code=500, detail="无法解析缓存的搜索结果。")

    if not (0 <= payload.resultIndex < len(cached_results)):
        raise HTTPException(status_code=400, detail="提供的 result_index 无效。")

    item_to_import = cached_results[payload.resultIndex]

    # 关键修复：恢复并完善在任务提交前的重复检查。
    # 对于编辑后导入，我们需要检查每个单集是否已存在（必须是相同数据源 + 季度）
    # 检查数据源是否已存在
    db = get_database_service()
    async with db.transaction():
        source_exists = await db.source.check_exists_by_media_id(item_to_import.provider, item_to_import.mediaId, season=item_to_import.season)

    if source_exists:
        # 数据源已存在时，仅通过 Repository 检查待导入分集，避免 API 层拼装 ORM 查询。
        existing_episodes = []
        async with db.transaction():
            for episode in payload.episodes:
                exists = await db.episode.check_episode_exists_with_danmaku(
                    provider=item_to_import.provider,
                    media_id=item_to_import.mediaId,
                    episode_index=episode.episodeIndex,
                )
                if exists:
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
    edited_request = EditedImportRequest(
        provider=item_to_import.provider,
        mediaId=item_to_import.mediaId,
        animeTitle=payload.title or item_to_import.title,
        mediaType=item_to_import.type,
        season=item_to_import.season,
        year=item_to_import.year,
        # 控制层与任务层使用不同分集 DTO，经过字典转换保持字段契约。
        episodes=[episode.model_dump() for episode in payload.episodes],
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

    # 构造 task_parameters，供完成通知格式化使用（否则媒体信息段为空）
    first_episode = payload.episodes[0].episodeIndex if payload.episodes else None
    edited_task_parameters = {
        "animeTitle": edited_request.animeTitle,
        "season": edited_request.season,
        "episode": first_episode,
        "episodeCount": len(payload.episodes),
        "provider": edited_request.provider,
        "mediaId": edited_request.mediaId,
        "type": edited_request.mediaType,
        "mediaType": edited_request.mediaType,
        "tmdbId": edited_request.tmdbId or "",
        "imageUrl": edited_request.imageUrl or "",
        "bangumiId": edited_request.bangumiId or "",
    }

    try:
        task_coro = task_manager.build_task_coro_factory(
            "edited_import",
            request_data=edited_request,
            config_service=config_service, manager=manager, rate_limiter=rate_limiter,
            metadata_manager=metadata_manager, title_recognition_manager=title_recognition_manager
        )
        task_id, _ = await task_manager.submit_task(
            task_coro, task_title, unique_key=unique_key,
            task_parameters=edited_task_parameters,
        )
        return {"message": "编辑后导入任务已提交", "taskId": task_id}
    except HTTPException as e:
        raise e
    except Exception as e:
        logger.error(f"提交编辑后导入任务时发生未知错误: {e}", exc_info=True)
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="提交任务时发生内部错误。")



@router.post("/import/xml", status_code=status.HTTP_202_ACCEPTED, summary="从XML/文本导入弹幕", response_model=ControlTaskResponse)
async def xml_import(
    payload: ControlXmlImportRequest,
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
    db = get_database_service()
    async with db.transaction():
        source_info = await db.source.get_anime_source_info(payload.sourceId)
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
        task_coro = task_manager.build_task_coro_factory(
            "manual_import",
            sourceId=payload.sourceId,
            animeId=anime_id,
            title=payload.title,
            episodeIndex=payload.episodeIndex,
            content=payload.content,
            providerName='custom',
            manager=manager,
            rate_limiter=rate_limiter
        )
        task_id, _ = await task_manager.submit_task(
            task_coro, task_title, unique_key=unique_key,
            task_type="manual_import",
            task_parameters={"sourceId": payload.sourceId, "episodeIndex": payload.episodeIndex, "providerName": "custom"}
        )
        return {"message": "XML导入任务已提交", "taskId": task_id}
    except HTTPException as e:
        raise e
    except Exception as e:
        logger.error(f"提交XML导入任务时发生未知错误: {e}", exc_info=True)
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="提交任务时发生内部错误。")


@router.post("/import/url", status_code=status.HTTP_202_ACCEPTED, summary="从URL导入", response_model=ControlTaskResponse)
async def url_import(
    payload: ControlUrlImportRequest,
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
    db = get_database_service()
    async with db.transaction():
        source_info = await db.source.get_anime_source_info(payload.sourceId)
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
        task_coro = task_manager.build_task_coro_factory(
            "manual_import",
            sourceId=payload.sourceId,
            animeId=anime_id,
            title=payload.title,
            episodeIndex=payload.episodeIndex,
            content=payload.url,
            providerName=provider_name,
            manager=manager,
            rate_limiter=rate_limiter
        )
        task_id, _ = await task_manager.submit_task(
            task_coro, task_title, unique_key=unique_key,
            task_type="manual_import",
            task_parameters={"sourceId": payload.sourceId, "episodeIndex": payload.episodeIndex, "providerName": provider_name}
        )
        return {"message": "URL导入任务已提交", "taskId": task_id}
    except HTTPException as e:
        raise e
    except Exception as e:
        logger.error(f"提交URL导入任务时发生未知错误: {e}", exc_info=True)
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="提交任务时发生内部错误。")