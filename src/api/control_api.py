import logging
import secrets
import uuid
import re
from enum import Enum
from typing import List, Optional, Dict, Any, Callable

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, Request, status, Path
from fastapi.security import APIKeyQuery
from thefuzz import fuzz
from pydantic import BaseModel, Field, model_validator
from sqlalchemy.ext.asyncio import AsyncSession

from .. import crud, models, tasks, utils
from ..rate_limiter import RateLimiter
from ..config_manager import ConfigManager
from ..database import get_db_session
from ..metadata_manager import MetadataSourceManager
from ..scheduler import SchedulerManager
from ..scraper_manager import ScraperManager
from ..task_manager import TaskManager, TaskSuccess, TaskStatus

logger = logging.getLogger(__name__)
router = APIRouter()

# --- 依赖项 ---

def get_scraper_manager(request: Request) -> ScraperManager:
    """依赖项：从应用状态获取 Scraper 管理器"""
    return request.app.state.scraper_manager

def get_metadata_manager(request: Request) -> MetadataSourceManager:
    """依赖项：从应用状态获取元数据源管理器"""
    return request.app.state.metadata_manager

def get_task_manager(request: Request) -> TaskManager:
    """依赖项：从应用状态获取任务管理器"""
    return request.app.state.task_manager

def get_scheduler_manager(request: Request) -> SchedulerManager:
    """依赖项：从应用状态获取定时任务调度器"""
    return request.app.state.scheduler_manager

def get_config_manager(request: Request) -> ConfigManager:
    """依赖项：从应用状态获取配置管理器"""
    return request.app.state.config_manager

def get_rate_limiter(request: Request) -> RateLimiter:
    """依赖项：从应用状态获取速率限制器"""
    return request.app.state.rate_limiter

# 新增：定义API Key的安全方案，这将自动在Swagger UI中生成“Authorize”按钮
api_key_scheme = APIKeyQuery(name="api_key", auto_error=False, description="用于所有外部控制API的访问密钥。")

async def verify_api_key(
    request: Request,
    api_key: str = Depends(api_key_scheme),
    session: AsyncSession = Depends(get_db_session),
) -> str:
    """依赖项：验证API密钥并记录请求。如果验证成功，返回 API Key。"""
    endpoint = request.url.path
    ip_address = request.client.host

    if not api_key:
        await crud.create_external_api_log(
            session, ip_address, endpoint, status.HTTP_401_UNAUTHORIZED, "API Key缺失"
        )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated: API Key is missing.",
        )

    stored_key = await crud.get_config_value(session, "externalApiKey", "")

    if not stored_key or not secrets.compare_digest(api_key, stored_key):
        await crud.create_external_api_log(
            session, ip_address, endpoint, status.HTTP_401_UNAUTHORIZED, "无效的API密钥"
        )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="无效的API密钥"
        )
    # 记录成功的API Key验证
    await crud.create_external_api_log(
        session, ip_address, endpoint, status.HTTP_200_OK, "API Key验证通过"
    )
    return api_key

# --- Pydantic 模型 ---

class ControlActionResponse(BaseModel):
    """通用操作成功响应模型"""
    message: str

class ControlTaskResponse(BaseModel):
    """任务提交成功响应模型"""
    message: str
    taskId: str

class ControlSearchResultItem(models.ProviderSearchInfo):
    resultIndex: int = Field(..., alias="result_index", description="结果在列表中的顺序索引，从0开始")

    class Config:
        populate_by_name = True

class ControlSearchResponse(BaseModel):
    searchId: str = Field(..., description="本次搜索操作的唯一ID，用于后续操作")
    results: List[ControlSearchResultItem] = Field(..., description="搜索结果列表")

class ControlDirectImportRequest(BaseModel):
    searchId: str = Field(..., description="来自搜索响应的searchId")
    resultIndex: int = Field(..., alias="result_index", ge=0, description="要导入的结果的索引 (从0开始)")
    tmdbId: Optional[str] = Field(None, description="强制指定TMDB ID")
    tvdbId: Optional[str] = Field(None, description="强制指定TVDB ID")
    bangumiId: Optional[str] = Field(None, description="强制指定Bangumi ID")
    imdbId: Optional[str] = Field(None, description="强制指定IMDb ID")
    doubanId: Optional[str] = Field(None, description="强制指定豆瓣 ID")

    class Config:
        populate_by_name = True

class ControlEditedImportRequest(BaseModel):
    searchId: str = Field(..., description="来自搜索响应的searchId")
    resultIndex: int = Field(..., alias="result_index", ge=0, description="要编辑的结果的索引 (从0开始)")
    title: Optional[str] = Field(None, description="覆盖原始标题")
    tmdbId: Optional[str] = Field(None, description="强制指定TMDB ID")
    tmdbEpisodeGroupId: Optional[str] = Field(None, description="强制指定TMDB剧集组ID")
    tvdbId: Optional[str] = Field(None, description="强制指定TVDB ID")
    bangumiId: Optional[str] = Field(None, description="强制指定Bangumi ID")
    imdbId: Optional[str] = Field(None, description="强制指定IMDb ID")
    doubanId: Optional[str] = Field(None, description="强制指定豆瓣 ID")
    episodes: List[models.ProviderEpisodeInfo] = Field(..., description="编辑后的分集列表")

    class Config:
        populate_by_name = True

class ControlUrlImportRequest(BaseModel):
    """用于外部API通过URL导入的请求模型"""
    provider: str = Field(..., description="要导入的源，例如 'bilibili'")
    url: str = Field(..., description="要导入的作品的URL (例如B站番剧主页)")
    title: Optional[str] = Field(None, description="强制覆盖从URL页面获取的标题")
    season: Optional[int] = Field(None, description="强制覆盖从URL页面获取的季度")
    tmdbId: Optional[str] = Field(None, description="强制指定TMDB ID")
    tvdbId: Optional[str] = Field(None, description="强制指定TVDB ID")
    bangumiId: Optional[str] = Field(None, description="强制指定Bangumi ID")
    imdbId: Optional[str] = Field(None, description="强制指定IMDb ID")
    doubanId: Optional[str] = Field(None, description="强制指定豆瓣 ID")
    episodeIndex: int = Field(..., alias="episode_index", description="要导入的集数", gt=0)

class DanmakuOutputSettings(BaseModel):
    limitPerSource: int = Field(..., alias="limit_per_source")
    aggregationEnabled: bool = Field(..., alias="aggregation_enabled")

    class Config:
        populate_by_name = True

class ControlAnimeDetailsResponse(BaseModel):
    """用于外部API的番剧详情响应模型，不包含冗余的anime_id。"""
    title: str
    type: str
    season: int
    year: Optional[int] = None
    episodeCount: Optional[int] = None
    localImagePath: Optional[str] = None
    imageUrl: Optional[str] = None
    tmdbId: Optional[str] = None
    tmdbEpisodeGroupId: Optional[str] = None
    bangumiId: Optional[str] = None
    tvdbId: Optional[str] = None
    doubanId: Optional[str] = None
    imdbId: Optional[str] = None
    nameEn: Optional[str] = None
    nameJp: Optional[str] = None
    nameRomaji: Optional[str] = None
    aliasCn1: Optional[str] = None
    aliasCn2: Optional[str] = None
    aliasCn3: Optional[str] = None

class AutoImportSearchType(str, Enum):
    KEYWORD = "keyword"
    TMDB = "tmdb"
    TVDB = "tvdb"
    DOUBAN = "douban"
    IMDB = "imdb"
    BANGUMI = "bangumi"

class AutoImportMediaType(str, Enum):
    TV_SERIES = "tv_series"
    MOVIE = "movie"

class ControlAutoImportRequest(BaseModel):
    searchType: AutoImportSearchType
    searchTerm: str
    season: Optional[int] = 1
    episode: Optional[int] = None
    mediaType: Optional[AutoImportMediaType] = None

# --- API 路由 ---

@router.post("/import/auto", status_code=status.HTTP_202_ACCEPTED, summary="全自动搜索并导入", response_model=ControlTaskResponse)
async def auto_import(
    searchType: AutoImportSearchType = Query(..., description="搜索类型。可选值: 'keyword', 'tmdb', 'tvdb', 'douban', 'imdb', 'bangumi'。"),
    searchTerm: str = Query(..., description="搜索内容。根据 searchType 的不同，这里应填入关键词或对应的平台ID。"),
    season: Optional[int] = Query(1, description="季度号。"),
    episode: Optional[int] = Query(None, description="集数。如果提供，将只导入单集。"),
    mediaType: Optional[AutoImportMediaType] = Query(None, description="媒体类型。当 searchType 为 'keyword' 时必填。如果留空，将根据有无 'season' 参数自动推断。"),
    task_manager: TaskManager = Depends(get_task_manager),
    manager: ScraperManager = Depends(get_scraper_manager),
    metadata_manager: MetadataSourceManager = Depends(get_metadata_manager),
    rate_limiter: RateLimiter = Depends(get_rate_limiter),
    api_key: str = Depends(verify_api_key),
):
    """
    ### 功能
    这是一个强大的“全自动搜索并导入”接口，它能根据不同的ID类型（如TMDB ID、Bangumi ID等）或关键词进行搜索，并根据一系列智能规则自动选择最佳的数据源进行弹幕导入。

    ### 工作流程
    1.  **元数据获取**: 如果使用ID搜索（如`tmdb`, `bangumi`），接口会首先从对应的元数据网站获取作品的官方标题和别名。
    2.  **媒体库检查**: 检查此作品是否已存在于您的弹幕库中。
        -   如果存在且有精确标记的源，则优先使用该源。
        -   如果存在但无精确标记，则使用已有关联源中优先级最高的那个。
    3.  **全网搜索**: 如果媒体库中不存在，则使用获取到的标题和别名在所有已启用的弹幕源中进行搜索。
    4.  **智能选择**: 从搜索结果中，根据您在“搜索源”页面设置的优先级，选择最佳匹配项。
    5.  **任务提交**: 为最终选择的源创建一个后台导入任务。

    ### 参数使用说明
    -   `searchType`:
        -   `keyword`: 按关键词搜索。此时 `mediaType` 字段**必填**。
        -   `tmdb`, `tvdb`, `douban`, `imdb`, `bangumi`: 按对应平台的ID进行精确搜索。
    -   `season` & `episode`:
        -   **电视剧/番剧**:
            -   提供 `season`，不提供 `episode`: 导入 `season` 指定的整季。
            -   提供 `season` 和 `episode`: 只导入指定的单集。
        -   **电影**:
            -   `season` 和 `episode` 参数会被忽略。
    -   `mediaType`:
        -   当 `searchType` 为 `keyword` 时，此字段为必填项。
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

    task_title = f"外部API自动导入: {payload.searchTerm} (类型: {payload.searchType})"
    try:
        task_coro = lambda session, cb: tasks.auto_search_and_import_task(
            payload, cb, session, manager, metadata_manager, task_manager,
            rate_limiter=rate_limiter
        )
        task_id, _ = await task_manager.submit_task(task_coro, task_title)
        return {"message": "自动导入任务已提交", "taskId": task_id}
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(e))
@router.get("/search", response_model=ControlSearchResponse, summary="搜索媒体")
async def search_media(
    keyword: str,
    season: Optional[int] = Query(None, description="要搜索的季度 (可选)"),
    episode: Optional[int] = Query(None, description="要搜索的集数 (可选)"),
    session: AsyncSession = Depends(get_db_session),
    manager: ScraperManager = Depends(get_scraper_manager),
    api_key: str = Depends(verify_api_key),
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
            detail="A search is already in progress for this API key. Please wait for it to complete."
        )
    try:
        # --- Start of WebUI Search Logic ---
        try:
            episode_info = {"season": season, "episode": episode} if season is not None or episode is not None else None
            all_results = await manager.search_all([keyword], episode_info=episode_info)
        except httpx.RequestError as e:
            logger.error(f"搜索媒体 '{keyword}' 时发生网络错误: {e}", exc_info=True)
            raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=f"搜索时发生网络错误: {e}")

        def normalize_for_filtering(title: str) -> str:
            if not title: return ""
            return re.sub(r'[\[【(（].*?[\]】)）]', '', title).lower().replace(" ", "").replace("：", ":").strip()

        normalized_search_title = normalize_for_filtering(keyword)
        filtered_results = [item for item in all_results if fuzz.token_set_ratio(normalize_for_filtering(item.title), normalized_search_title) > 80]
        logger.info(f"模糊标题过滤: 从 {len(all_results)} 个原始结果中，保留了 {len(filtered_results)} 个相关结果。")
        results = filtered_results

        def is_movie_by_title(title: str) -> bool:
            if not title: return False
            return any(kw in title.lower() for kw in ["剧场版", "劇場版", "movie", "映画"])

        for item in results:
            if item.type == 'tv_series' and is_movie_by_title(item.title):
                item.type = 'movie'

        if season:
            original_count = len(results)
            filtered_by_type = [item for item in results if item.type == 'tv_series']
            results = [item for item in filtered_by_type if item.season == season]
            logger.info(f"根据指定的季度 ({season}) 进行过滤，从 {original_count} 个结果中保留了 {len(results)} 个。")

        source_settings = await crud.get_all_scraper_settings(session)
        source_order_map = {s['providerName']: s['displayOrder'] for s in source_settings}

        def sort_key(item: models.ProviderSearchInfo):
            return (source_order_map.get(item.provider, 999), -fuzz.token_set_ratio(keyword, item.title))

        sorted_results = sorted(results, key=sort_key)
        # --- End of WebUI Search Logic ---

        search_id = str(uuid.uuid4())
        indexed_results = [ControlSearchResultItem(**r.model_dump(), resultIndex=i) for i, r in enumerate(sorted_results)]
        await crud.set_cache(session, f"control_search_{search_id}", [r.model_dump() for r in sorted_results], 600)
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
    rate_limiter: RateLimiter = Depends(get_rate_limiter),
    api_key: str = Depends(verify_api_key),
):
    """
    ### 功能
    在执行`/search`后，使用返回的`searchId`和您选择的结果索引（`resultIndex`）来直接导入弹幕。

    ### 工作流程
    这是一个简单、直接的导入方式。它会为选定的媒体创建一个后台导入任务。您也可以在请求中附加元数据ID（如`tmdbId`）来覆盖或补充作品信息。
    """
    cache_key = f"control_search_{payload.searchId}"
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

    task_title = f"外部API导入: {item_to_import.title} ({item_to_import.provider})"
    try:
        task_coro = lambda session, cb: tasks.generic_import_task(
            provider=item_to_import.provider,
            mediaId=item_to_import.mediaId,
            animeTitle=item_to_import.title, 
            mediaType=item_to_import.type,
            season=item_to_import.season, 
            currentEpisodeIndex=item_to_import.currentEpisodeIndex,
            imageUrl=item_to_import.imageUrl, 
            year=item_to_import.year, doubanId=payload.doubanId,
            metadata_manager=metadata_manager, tmdbId=payload.tmdbId, imdbId=payload.imdbId,
            tvdbId=payload.tvdbId, bangumiId=payload.bangumiId,
            progress_callback=cb, session=session, manager=manager, task_manager=task_manager,
            rate_limiter=rate_limiter
        )
        task_id, _ = await task_manager.submit_task(task_coro, task_title)
        return {"message": "导入任务已提交", "taskId": task_id}
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(e))

@router.get("/episodes", response_model=List[models.ProviderEpisodeInfo], summary="获取搜索结果的分集列表")
async def get_episodes(
    searchId: str = Query(..., description="来自/search接口的searchId"),
    result_index: int = Query(..., ge=0, description="要获取分集的结果的索引"),
    session: AsyncSession = Depends(get_db_session),
    manager: ScraperManager = Depends(get_scraper_manager),
    api_key: str = Depends(verify_api_key),
):
    """
    ### 功能
    在执行`/search`后，获取指定搜索结果的完整分集列表。

    ### 工作流程
    此接口主要用于“编辑后导入”的场景。您可以先获取原始的分集列表，在您的客户端进行修改（例如，删除预告、调整顺序），然后再通过`/import/edited`接口提交修改后的列表进行导入。
    """
    cache_key = f"control_search_{searchId}"
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
    api_key: str = Depends(verify_api_key),
):
    """
    ### 功能
    导入一个经过用户编辑和调整的分集列表。

    ### 工作流程
    这是最灵活的导入方式。它允许您完全控制要导入的分集，包括标题、顺序等。您可以在请求中覆盖作品标题和附加元数据ID。
    """
    cache_key = f"control_search_{payload.searchId}"
    cached_results_raw = await crud.get_cache(session, cache_key)
    
    if cached_results_raw is None:
        raise HTTPException(status_code=404, detail="搜索会话已过期或无效，请重新搜索。")
    
    try:
        cached_results = [models.ProviderSearchInfo.model_validate(r) for r in cached_results_raw]
    except Exception:
        raise HTTPException(status_code=500, detail="无法解析缓存的搜索结果。")

    if not (0 <= payload.resultIndex < len(cached_results)):
        raise HTTPException(status_code=400, detail="提供的 result_index 无效。")
        
    item_info = cached_results[payload.resultIndex]

    # Construct the full EditedImportRequest for the task
    task_payload = models.EditedImportRequest(
        provider=item_info.provider,
        mediaId=item_info.mediaId,
        animeTitle=payload.title or item_info.title, # type: ignore
        year=item_info.year,
        mediaType=item_info.type,
        season=item_info.season,
        imageUrl=item_info.imageUrl,
        doubanId=payload.doubanId,
        tmdbId=payload.tmdbId,
        imdbId=payload.imdbId,
        tvdbId=payload.tvdbId,
        bangumiId=payload.bangumiId,
        tmdbEpisodeGroupId=payload.tmdbEpisodeGroupId,
        episodes=payload.episodes
    )

    task_title = f"外部API编辑后导入: {task_payload.animeTitle} ({task_payload.provider})"
    try:
        task_coro = lambda session, cb: tasks.edited_import_task(
            request_data=task_payload, progress_callback=cb, session=session, manager=manager,
            rate_limiter=rate_limiter
        )
        task_id, _ = await task_manager.submit_task(task_coro, task_title)
        return {"message": "编辑后导入任务已提交", "taskId": task_id}
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(e))


@router.post("/import/url", status_code=status.HTTP_202_ACCEPTED, summary="从URL导入整个作品", response_model=ControlTaskResponse)
async def url_import(
    payload: ControlUrlImportRequest,
    task_manager: TaskManager = Depends(get_task_manager),
    manager: ScraperManager = Depends(get_scraper_manager),
    metadata_manager: MetadataSourceManager = Depends(get_metadata_manager),
    rate_limiter: RateLimiter = Depends(get_rate_limiter),
    api_key: str = Depends(verify_api_key),
):
    """
    ### 功能
    从一个作品的URL（例如B站番剧主页、腾讯视频播放页等）直接导入其所有分集的弹幕。

    ### 工作流程
    1.  服务会尝试从URL中解析出对应的弹幕源和媒体ID。
    2.  然后为该媒体创建一个后台导入任务。
    """
    scraper = manager.get_scraper_by_domain(payload.url)
    if not scraper:
        raise HTTPException(status_code=400, detail="不支持的URL或视频源。")

    # This is the new method we need to implement for each scraper
    try:
        media_info = await scraper.get_info_from_url(payload.url)
        if not media_info:
            raise HTTPException(status_code=404, detail="无法从提供的URL中获取有效的作品信息。")
    except httpx.RequestError as e:
        logger.error(f"从URL '{payload.url}' 导入时发生网络错误: {e}", exc_info=True)
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=f"从URL导入时发生网络错误: {e}")

       # 获取指定分集的progress_callback
    final_title = payload.title or media_info.title
    final_season = payload.season if payload.season is not None else media_info.season

    task_title = f"外部API URL导入: {final_title} ({scraper.provider_name})"
    try:
        task_coro = lambda session, cb: tasks.generic_import_task(
            provider=scraper.provider_name,
            mediaId=media_info.mediaId,
            animeTitle=final_title,
            mediaType=media_info.type, # type: ignore
            currentEpisodeIndex=payload.episodeIndex,
            season=final_season,
            year=media_info.year,
            imageUrl=media_info.imageUrl,
            doubanId=payload.doubanId, metadata_manager=metadata_manager, tmdbId=payload.tmdbId, imdbId=payload.imdbId,
            tvdbId=payload.tvdbId, bangumiId=payload.bangumiId, rate_limiter=rate_limiter,
            progress_callback=cb, session=session, manager=manager, task_manager=task_manager
        )
        task_id, _ = await task_manager.submit_task(task_coro, task_title)
        return {"message": f"'{final_title}' 的URL单集导入任务已提交。", "taskId": task_id}
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(e))

# --- 媒体库管理 ---
library_router = APIRouter(prefix="/library", dependencies=[Depends(verify_api_key)])

@library_router.get("", response_model=List[models.LibraryAnimeInfo], summary="获取媒体库列表")
async def get_library(session: AsyncSession = Depends(get_db_session)):
    """获取当前弹幕库中所有已收录的作品列表。"""
    db_results = await crud.get_library_anime(session)
    return [models.LibraryAnimeInfo.model_validate(item) for item in db_results]

@library_router.get("/anime/{animeId}", response_model=ControlAnimeDetailsResponse, summary="获取作品详情")
async def get_anime_details(animeId: int, session: AsyncSession = Depends(get_db_session)):
    """获取弹幕库中单个作品的完整详细信息，包括所有元数据ID和别名。"""
    details = await crud.get_anime_full_details(session, animeId)
    if not details: raise HTTPException(404, "作品未找到")
    return ControlAnimeDetailsResponse.model_validate(details)

@library_router.get("/anime/{animeId}/sources", response_model=List[models.SourceInfo], summary="获取作品的所有数据源")
async def get_anime_sources(animeId: int, session: AsyncSession = Depends(get_db_session)):
    """获取指定作品已关联的所有弹幕源列表。"""
    # First, check if the anime exists to provide a proper 404.
    anime_exists = await crud.get_anime_full_details(session, animeId)
    if not anime_exists:
        raise HTTPException(status_code=404, detail="作品未找到")
    return await crud.get_anime_sources(session, animeId)

@library_router.put("/anime/{animeId}", response_model=ControlActionResponse, summary="编辑作品信息")
async def edit_anime(animeId: int, payload: models.AnimeDetailUpdate, session: AsyncSession = Depends(get_db_session)):
    """更新弹幕库中单个作品的详细信息。"""
    if not await crud.update_anime_details(session, animeId, payload):
        raise HTTPException(404, "作品未找到")
    return {"message": "作品信息更新成功。"}

@library_router.delete("/anime/{animeId}", status_code=202, summary="删除作品", response_model=ControlTaskResponse)
async def delete_anime(animeId: int, session: AsyncSession = Depends(get_db_session), task_manager: TaskManager = Depends(get_task_manager)):
    """提交一个后台任务，以删除弹幕库中的一个作品及其所有关联的数据源、分集和弹幕。"""
    details = await crud.get_anime_full_details(session, animeId)
    if not details: raise HTTPException(404, "作品未找到")
    try:
        task_id, _ = await task_manager.submit_task(
            lambda s, cb: tasks.delete_anime_task(animeId, s, cb),
            f"外部API删除作品: {details['title']}"
        )
        return {"message": "删除作品任务已提交", "taskId": task_id}
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(e))

@library_router.delete("/source/{sourceId}", status_code=202, summary="删除数据源", response_model=ControlTaskResponse)
async def delete_source(sourceId: int, session: AsyncSession = Depends(get_db_session), task_manager: TaskManager = Depends(get_task_manager)):
    """提交一个后台任务，以删除一个已关联的数据源及其所有分集和弹幕。"""
    info = await crud.get_anime_source_info(session, sourceId)
    if not info: raise HTTPException(404, "数据源未找到")
    try:
        task_id, _ = await task_manager.submit_task(
            lambda s, cb: tasks.delete_source_task(sourceId, s, cb),
            f"外部API删除源: {info['title']} ({info['providerName']})"
        )
        return {"message": "删除源任务已提交", "taskId": task_id}
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(e))

@library_router.put("/source/{sourceId}/favorite", response_model=ControlActionResponse, summary="精确标记数据源")
async def favorite_source(sourceId: int, session: AsyncSession = Depends(get_db_session)):
    """切换数据源的“精确标记”状态。一个作品只能有一个精确标记的源，它将在自动匹配时被优先使用。"""
    new_status = await crud.toggle_source_favorite_status(session, sourceId)
    if new_status is None:
        raise HTTPException(404, "数据源未找到")
    message = "数据源已标记为精确。" if new_status else "数据源已取消精确标记。"
    return {"message": message}

@library_router.get("/source/{sourceid}/episodes", response_model=List[models.EpisodeDetail], summary="获取源的分集列表")
async def get_source_episodes(sourceid: int, session: AsyncSession = Depends(get_db_session)):
    """获取指定数据源下所有已收录的分集列表。"""
    return await crud.get_episodes_for_source(session, sourceid)

@library_router.put("/episode/{episodeid}", response_model=ControlActionResponse, summary="编辑分集信息")
async def edit_episode(episodeid: int, payload: models.EpisodeInfoUpdate, session: AsyncSession = Depends(get_db_session)):
    """更新单个分集的标题、集数和官方链接。"""
    if not await crud.update_episode_info(session, episodeid, payload):
        raise HTTPException(404, "分集未找到")
    return {"message": "分集信息更新成功。"}

@library_router.post("/episode/{episodeId}/refresh", status_code=202, summary="刷新分集弹幕", response_model=ControlTaskResponse)
async def refresh_episode(
    episodeId: int,
    session: AsyncSession = Depends(get_db_session),
    task_manager: TaskManager = Depends(get_task_manager),
    manager: ScraperManager = Depends(get_scraper_manager),
    rate_limiter: RateLimiter = Depends(get_rate_limiter),
):
    """提交一个后台任务，为单个分集重新从其源网站获取最新的弹幕。"""
    info = await crud.get_episode_for_refresh(session, episodeId)
    if not info: raise HTTPException(404, "分集未找到")
    task_id, _ = await task_manager.submit_task(
        lambda s, cb: tasks.refresh_episode_task(episodeId, s, manager, rate_limiter, cb),
        f"外部API刷新分集: {info['title']}"
    )
    return {"message": "刷新分集任务已提交", "taskId": task_id}

@library_router.delete("/episode/{episodeId}", status_code=202, summary="删除分集", response_model=ControlTaskResponse)
async def delete_episode(episodeId: int, session: AsyncSession = Depends(get_db_session), task_manager: TaskManager = Depends(get_task_manager)):
    """提交一个后台任务，以删除单个分集及其所有弹幕。"""
    info = await crud.get_episode_for_refresh(session, episodeId)
    if not info: raise HTTPException(404, "分集未找到")
    try:
        task_id, _ = await task_manager.submit_task(
            lambda s, cb: tasks.delete_episode_task(episodeId, s, cb),
            f"外部API删除分集: {info['title']}"
        )
        return {"message": "删除分集任务已提交", "taskId": task_id}
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(e))

# --- 弹幕管理 ---
danmaku_router = APIRouter(prefix="/danmaku", dependencies=[Depends(verify_api_key)])

@danmaku_router.get("/{episodeId}", response_model=models.CommentResponse, summary="获取弹幕")
async def get_danmaku(episodeId: int, session: AsyncSession = Depends(get_db_session)):
    """获取指定分集的所有弹幕，返回dandanplay兼容格式。"""
    if not await crud.check_episode_exists(session, episodeId): raise HTTPException(404, "分集未找到")
    comments = await crud.fetch_comments(session, episodeId)
    return models.CommentResponse(count=len(comments), comments=[models.Comment.model_validate(c) for c in comments])

@danmaku_router.post("/{episodeId}", status_code=202, summary="覆盖弹幕", response_model=ControlTaskResponse)
async def overwrite_danmaku(episodeId: int, payload: models.DanmakuUpdateRequest, task_manager: TaskManager = Depends(get_task_manager)):
    """提交一个后台任务，用请求体中提供的弹幕列表完全覆盖指定分集的现有弹幕。"""
    async def overwrite_task(session: AsyncSession, cb: Callable):
        await cb(10, "清空中...")
        await crud.clear_episode_comments(session, episodeId)
        await cb(50, f"插入 {len(payload.comments)} 条新弹幕...")
        
        comments_to_insert = []
        for c in payload.comments:
            comment_dict = c.model_dump()
            try:
                # 从 'p' 字段解析时间戳，并添加到字典中
                timestamp_str = comment_dict['p'].split(',')[0]
                comment_dict['t'] = float(timestamp_str)
            except (IndexError, ValueError):
                comment_dict['t'] = 0.0 # 如果解析失败，则默认为0
            comments_to_insert.append(comment_dict)

        added = await crud.bulk_insert_comments(session, episodeId, comments_to_insert)
        raise TaskSuccess(f"弹幕覆盖完成，新增 {added} 条。")
    try:
        task_id, _ = await task_manager.submit_task(overwrite_task, f"外部API覆盖弹幕 (分集ID: {episodeId})")
        return {"message": "弹幕覆盖任务已提交", "taskId": task_id}
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(e))

# --- Token 管理 ---
token_router = APIRouter(prefix="/tokens", dependencies=[Depends(verify_api_key)])

@token_router.get("", response_model=List[models.ApiTokenInfo], summary="获取所有Token")
async def get_tokens(session: AsyncSession = Depends(get_db_session)):
    """获取所有为dandanplay客户端创建的API Token。"""
    tokens = await crud.get_all_api_tokens(session)
    return [models.ApiTokenInfo.model_validate(t) for t in tokens]

@token_router.post("", response_model=models.ApiTokenInfo, status_code=201, summary="创建Token")
async def create_token(payload: models.ApiTokenCreate, session: AsyncSession = Depends(get_db_session)):
    """
    创建一个新的API Token。

    ### 请求体说明
    - `name`: (string, 必需) Token的名称，用于在UI中识别。
    - `validityPeriod`: (string, 必需) Token的有效期。
        - 填入数字（如 "30", "90"）表示有效期为多少天。
        - 填入 "permanent" 表示永久有效。
    """
    token_str = secrets.token_urlsafe(16)
    try:
        token_id = await crud.create_api_token(session, payload.name, token_str, payload.validityPeriod)
        new_token = await crud.get_api_token_by_id(session, token_id)
        return models.ApiTokenInfo.model_validate(new_token)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(e))

@token_router.get("/{tokenId}", response_model=models.ApiTokenInfo, summary="获取单个Token详情")
async def get_token(tokenId: int, session: AsyncSession = Depends(get_db_session)):
    """获取单个API Token的详细信息。"""
    token = await crud.get_api_token_by_id(session, tokenId)
    if not token: raise HTTPException(404, "Token未找到")
    return models.ApiTokenInfo.model_validate(token)

@token_router.get("/{tokenId}/logs", response_model=List[models.TokenAccessLog], summary="获取Token访问日志")
async def get_token_logs(tokenId: int, session: AsyncSession = Depends(get_db_session)):
    """获取单个API Token最近的访问日志。"""
    logs = await crud.get_token_access_logs(session, tokenId)
    return [models.TokenAccessLog.model_validate(log) for log in logs]

@token_router.put("/{tokenId}/toggle", response_model=ControlActionResponse, summary="启用/禁用Token")
async def toggle_token(tokenId: int, session: AsyncSession = Depends(get_db_session)):
    """切换API Token的启用/禁用状态。"""
    new_status = await crud.toggle_api_token(session, tokenId)
    if new_status is None:
        raise HTTPException(404, "Token未找到")
    message = "Token 已启用。" if new_status else "Token 已禁用。"
    return {"message": message}

@token_router.delete("/{tokenId}", response_model=ControlActionResponse, summary="删除Token")
async def delete_token(tokenId: int, session: AsyncSession = Depends(get_db_session)):
    """删除一个API Token。"""
    if not await crud.delete_api_token(session, tokenId):
        raise HTTPException(404, "Token未找到")
    return {"message": "Token 删除成功。"}

# --- 设置管理 ---
settings_router = APIRouter(prefix="/settings", dependencies=[Depends(verify_api_key)])

@settings_router.get("/danmaku-output", response_model=DanmakuOutputSettings, summary="获取弹幕输出设置")
async def get_danmaku_output_settings(session: AsyncSession = Depends(get_db_session)):
    """获取全局的弹幕输出设置，如数量限制和是否聚合。"""
    limit = await crud.get_config_value(session, 'danmaku_output_limit_per_source', '-1')
    enabled = await crud.get_config_value(session, 'danmaku_aggregation_enabled', 'true')
    return DanmakuOutputSettings(limit_per_source=int(limit), aggregation_enabled=(enabled.lower() == 'true'))

@settings_router.put("/danmaku-output", response_model=ControlActionResponse, summary="更新弹幕输出设置")
async def update_danmaku_output_settings(payload: DanmakuOutputSettings, session: AsyncSession = Depends(get_db_session), config_manager: ConfigManager = Depends(get_config_manager)):
    """更新全局的弹幕输出设置。"""
    await crud.update_config_value(session, 'danmaku_output_limit_per_source', str(payload.limitPerSource)) # type: ignore
    await crud.update_config_value(session, 'danmaku_aggregation_enabled', str(payload.aggregationEnabled).lower()) # type: ignore
    config_manager.invalidate('danmaku_output_limit_per_source')
    config_manager.invalidate('danmaku_aggregation_enabled')
    return {"message": "弹幕输出设置已更新。"}

# --- 注册所有子路由 ---
router.include_router(library_router)
router.include_router(danmaku_router)
router.include_router(token_router)
router.include_router(settings_router)

# --- 任务管理 ---
tasks_router = APIRouter(prefix="/tasks", dependencies=[Depends(verify_api_key)])

@tasks_router.get("", response_model=List[models.TaskInfo], summary="获取后台任务列表")
async def get_tasks(
    search: Optional[str] = Query(None, description="按标题搜索"),
    status: str = Query("all", description="按状态过滤: all, in_progress, completed"),
    session: AsyncSession = Depends(get_db_session),
):
    """获取后台任务的列表和状态，支持按标题搜索和按状态过滤。"""
    tasks_from_db = await crud.get_tasks_from_history(session, search, status)
    return [models.TaskInfo.model_validate(t) for t in tasks_from_db]

@tasks_router.get("/{taskId}", response_model=models.TaskInfo, summary="获取单个任务状态")
async def get_task_status(
    taskId: str,
    session: AsyncSession = Depends(get_db_session),
    api_key: str = Depends(verify_api_key),
):
    """获取单个后台任务的详细状态。"""
    task_details = await crud.get_task_details_from_history(session, taskId)
    if not task_details:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="任务未找到。")
    return models.TaskInfo.model_validate(task_details)

@tasks_router.delete("/{taskId}", response_model=ControlActionResponse, summary="删除一个历史任务")
async def delete_task(
    taskId: str,
    session: AsyncSession = Depends(get_db_session),
    task_manager: TaskManager = Depends(get_task_manager),
):
    """
    ### 功能
    删除一个后台任务。
    - **排队中**: 从队列中移除。
    - **运行中/已暂停**: 尝试中止任务，然后删除。
    - **已完成/失败**: 从历史记录中删除。
    """
    task = await crud.get_task_from_history_by_id(session, taskId)
    if not task:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="任务未找到。")

    task_status = task['status']

    if task_status == TaskStatus.PENDING:
        if await task_manager.cancel_pending_task(taskId):
            logger.info(f"已从队列中取消待处理任务 {taskId}。")
    elif task_status in [TaskStatus.RUNNING, TaskStatus.PAUSED]:
        if await task_manager.abort_current_task(taskId):
            logger.info(f"已发送中止信号到任务 {taskId}。")

    if await crud.delete_task_from_history(session, taskId):
        return {"message": f"删除任务 {taskId} 的请求已处理。"}
    else:
        return {"message": "任务可能已被处理或不存在于历史记录中。"}

@tasks_router.post("/{taskId}/abort", response_model=ControlActionResponse, summary="中止正在运行的任务")
async def abort_task(taskId: str, task_manager: TaskManager = Depends(get_task_manager)):
    """尝试中止一个当前正在运行或已暂停的任务。此操作会向任务发送一个取消信号，任务将在下一个检查点安全退出。"""
    if not await task_manager.abort_current_task(taskId):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="中止任务失败，可能任务已完成或不是当前正在执行的任务。")
    return {"message": "中止任务的请求已发送。"}

@tasks_router.post("/{taskId}/pause", response_model=ControlActionResponse, summary="暂停正在运行的任务")
async def pause_task(taskId: str, task_manager: TaskManager = Depends(get_task_manager)):
    """暂停一个当前正在运行的任务。任务将在下一次进度更新时暂停。"""
    if not await task_manager.pause_task(taskId):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="暂停任务失败，可能任务未在运行。")
    return {"message": "任务已暂停。"}

@tasks_router.post("/{taskId}/resume", response_model=ControlActionResponse, summary="恢复已暂停的任务")
async def resume_task(taskId: str, task_manager: TaskManager = Depends(get_task_manager)):
    """恢复一个已暂停的任务。"""
    if not await task_manager.resume_task(taskId):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="恢复任务失败，可能任务未被暂停。")
    return {"message": "任务已恢复。"}

# --- 定时任务管理 ---
scheduler_router = APIRouter(prefix="/scheduler", dependencies=[Depends(verify_api_key)])

@scheduler_router.get("/tasks", response_model=List[Dict[str, Any]], summary="获取所有定时任务")
async def list_scheduled_tasks(
    scheduler_manager: SchedulerManager = Depends(get_scheduler_manager),
):
    """获取所有已配置的定时任务及其当前状态。"""
    tasks = await scheduler_manager.get_all_tasks()
    # 修正：将 'id' 键重命名为 'taskId' 以保持API一致性
    return tasks

@scheduler_router.get("/{taskId}/last_result", response_model=models.TaskInfo, summary="获取定时任务的最近一次运行结果")
async def get_scheduled_task_last_result(
    taskId: str = Path(..., description="定时任务的ID"),
    session: AsyncSession = Depends(get_db_session),
):
    """
    获取指定定时任务的最近一次运行结果。
    如果任务从未运行过，将返回 404 Not Found。
    """
    result = await crud.get_last_run_result_for_scheduled_task(session, taskId)
    if not result:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="未找到该定时任务的运行记录。")
    return models.TaskInfo.model_validate(result)


router.include_router(tasks_router)
router.include_router(scheduler_router)
