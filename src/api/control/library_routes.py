"""
外部控制API - 媒体库管理路由
包含: /library/*, /metadata/search
"""

import logging
from types import SimpleNamespace
from typing import List, Callable

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import exc

from src.services.service_container import get_database_service
from src.services.config_service import get_config_service
from src.schemas import anime as anime_schemas
from src.schemas.ui_models import LibraryAnimeInfo, LibrarySourceBrief, EpisodeDetail
from src.schemas.anime import AnimeCreate, AnimeDetailUpdate, SourceCreate, EpisodeInfoUpdate
from src.workflows.episode_edit import update_episode_info
from src.services.scraper_manager import ScraperManager
from src.services.task_manager import TaskManager
from src.utils.diagnostics.task_exceptions import TaskSuccess
from src.services.metadata_service import MetadataService
from src.rate_limiter import RateLimiter

from src.schemas.control import (
    AutoImportSearchType, AutoImportMediaType,
    ControlActionResponse, ControlTaskResponse,
    ControlAnimeCreateRequest, ControlAnimeDetailsResponse,
    ControlMetadataSearchResponse
)
# 复用共用事务依赖，避免将依赖函数误当作数据库会话。
from .dependencies import (
    get_scraper_manager, get_metadata_service,
    get_task_manager, get_config_service, get_rate_limiter,
    get_title_recognition_manager
)

logger = logging.getLogger(__name__)

router = APIRouter()


# --- 元信息搜索 ---

@router.get("/metadata/search", response_model=ControlMetadataSearchResponse, summary="查找元数据信息")
async def search_metadata_source(
    provider: str = Query(..., description="要查询的元数据源，例如: 'tmdb', 'bangumi'。"),
    keyword: str | None = Query(None, description="按关键词搜索。'keyword' 和 'id' 必须提供一个。"),
    id: str | None = Query(None, description="按ID精确查找。'keyword' 和 'id' 必须提供一个。"),
    mediaType: AutoImportMediaType | None = Query(None, description="媒体类型。可选值: 'tv_series', 'movie'。"),
    metadata_manager: MetadataService = Depends(get_metadata_service)
):
    """
    ### 功能
    从指定的元数据源（如TMDB, Bangumi）中查找媒体信息。

    ### 工作流程
    1.  提供 `provider` 来指定要查询的源。
    2.  提供 `keyword` 或 `id` 中的一个来进行搜索。
    3.  `mediaType` 为可选参数。TMDB 不传时默认搜索全部类型(multi)，其他源可忽略。

    ### 返回
    返回一个包含元数据详情的列表。如果通过ID查找且成功，列表中将只有一个元素。
    """
    if not keyword and not id:
        raise HTTPException(status_code=400, detail="必须提供 'keyword' 或 'id' 参数之一。")
    if keyword and id:
        raise HTTPException(status_code=400, detail="不能同时提供 'keyword' 和 'id' 参数。")

    # --- 将通用媒体类型映射到特定于提供商的类型 ---
    provider_media_type: str | None = None
    if provider == 'tmdb':
        if mediaType:
            provider_media_type = 'tv' if mediaType == AutoImportMediaType.TV_SERIES else 'movie'
        else:
            # TMDB 必须指定 mediaType，未指定时默认使用 multi（同时搜索电视剧和电影）
            provider_media_type = 'multi'
    elif provider == 'tvdb':
        if mediaType:
            provider_media_type = 'series' if mediaType == AutoImportMediaType.TV_SERIES else 'movies'
    elif mediaType:
        provider_media_type = mediaType.value
    # --- 映射结束 ---

    # 创建一个虚拟用户对象，因为元数据管理器的核心方法需要它
    # 使用简单的命名空间对象替代 ORM 模型
    # 标准库类型在文件顶部导入，避免局部导入破坏统一依赖约定。
    user = SimpleNamespace(id=0, username="control_api")
    results = []

    try:
        if id:
            details = await metadata_manager.get_details(provider, id, user, mediaType=provider_media_type)
            if details:
                results.append(details)
        elif keyword:
            results = await metadata_manager.search(provider, keyword, user, mediaType=provider_media_type)
    except HTTPException as e:
        raise e
    except Exception as e:
        logger.error(f"从元数据源 '{provider}' 搜索时发生未知错误: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"从元数据源 '{provider}' 搜索时发生内部错误。")

    return ControlMetadataSearchResponse(results=results)


# --- 媒体库管理 ---

@router.get("/library", response_model=List[LibraryAnimeInfo], summary="获取媒体库列表")
async def get_library():
    """获取当前弹幕库中所有已收录的作品列表。"""
    db = get_database_service()
    async with db.transaction():
        result = await db.anime.get_library_list()
    return [LibraryAnimeInfo.model_validate(item) for item in result["list"]]


@router.get("/library/search", response_model=List[LibraryAnimeInfo], summary="搜索媒体库")
async def search_library(
    keyword: str = Query(..., description="搜索关键词")
):
    """根据关键词搜索弹幕库中已收录的作品。"""
    db = get_database_service()
    async with db.transaction():
        result = await db.anime.get_library_list(keyword=keyword)
    return [LibraryAnimeInfo.model_validate(item) for item in result["list"]]


@router.post("/library/anime", response_model=ControlActionResponse, status_code=status.HTTP_201_CREATED, summary="自定义创建影视条目")
async def create_anime_entry(
    payload: ControlAnimeCreateRequest,
    title_recognition_manager = Depends(get_title_recognition_manager)
):
    """
    ### 功能
    在数据库中手动创建一个新的影视作品条目。
    ### 工作流程
    1.  接收作品的标题、类型、季度等基本信息。
    2.  （可选）接收TMDB、Bangumi等元数据ID和其他别名。
    3.  在数据库中创建对应的 `anime`, `anime_metadata`, `anime_aliases` 记录。
    4.  返回创建状态和新作品ID。
    """
    db = get_database_service()

    # Check for duplicates first
    season_for_check = payload.season if payload.type == AutoImportMediaType.TV_SERIES else 1
    # Note: find_anime_by_title_season_year 方法已废弃，这里简化为基本检查
    # 如果需要完整的重复检查逻辑，需要在 AnimeRepository 中实现

    season_for_create = payload.season if payload.type == AutoImportMediaType.TV_SERIES else 1
    anime_data = AnimeCreate(
        title=payload.title,
        type=payload.type.value,
        season=season_for_create,
        year=payload.year if payload.year else None,
    )
    try:
        async with db.transaction():
            new_anime = await db.anime.create_anime(anime_data)
            new_anime_id = new_anime.id

            # 更新元数据
            await db.anime.update_metadata_if_empty(
                new_anime_id,
                tmdb_id=payload.tmdbId, imdb_id=payload.imdbId, tvdb_id=payload.tvdbId,
                douban_id=payload.doubanId, bangumi_id=payload.bangumiId
            )

            # 更新别名
            await db.anime.update_anime_aliases(new_anime_id, payload)

        return {"message": f"作品 '{payload.title}' 创建成功。", "animeId": new_anime_id}
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(e))



@router.get("/library/anime/{animeId}", response_model=ControlAnimeDetailsResponse, summary="获取作品详情")
async def get_anime_details(animeId: int):
    """获取弹幕库中单个作品的完整详细信息，包括所有元数据ID和别名。"""
    db = get_database_service()
    async with db.transaction():
        details = await db.anime.get_full_details(animeId)
    if not details:
        raise HTTPException(404, "作品未找到")
    return ControlAnimeDetailsResponse.model_validate(details)


@router.get("/library/anime/{animeId}/sources", response_model=List[LibrarySourceBrief], summary="获取作品的所有数据源")
async def get_anime_sources(animeId: int):
    """获取指定作品已关联的所有弹幕源列表。"""
    db = get_database_service()
    async with db.transaction():
        anime_exists = await db.anime.get_full_details(animeId)
        if not anime_exists:
            raise HTTPException(status_code=404, detail="作品未找到")
        sources = await db.source.get_anime_sources(animeId)
    return sources


@router.post("/library/anime/{animeId}/sources", response_model=ControlActionResponse, status_code=status.HTTP_201_CREATED, summary="为作品添加数据源")
async def add_source(
    animeId: int,
    payload: SourceCreate,
):
    """
    ### 功能
    为一个已存在的作品手动关联一个新的数据源。

    ### 工作流程
    1.  提供一个已存在于弹幕库中的 `animeId`。
    2.  在请求体中提供 `providerName` 和 `mediaId`。
    3.  系统会将此数据源关联到指定的作品。

    ### 使用场景
    -   **添加自定义源**: 您可以为任何作品添加一个 `custom` 类型的源，以便后续通过 `/import/xml` 接口为其上传弹幕文件。
        -   `providerName`: "custom"
        -   `mediaId`: 任意唯一的字符串，例如 `custom_123`。
    -   **手动关联刮削源**: 如果自动搜索未能找到正确的结果，您可以通过此接口手动将一个已知的 `providerName` 和 `mediaId` 关联到作品上。
    """
    db = get_database_service()
    async with db.transaction():
        anime = await db.anime.get_full_details(animeId)
        if not anime:
            raise HTTPException(status_code=404, detail="作品未找到")

        try:
            source_id = await db.source.link_source_to_anime(animeId, payload.providerName, payload.mediaId)
            return {"message": f"数据源 '{payload.providerName}:{payload.mediaId}' 添加成功。", "sourceId": source_id}
        except exc.IntegrityError:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="该数据源已存在于此作品下，无法重复添加。")


@router.put("/library/anime/{animeId}", response_model=ControlActionResponse, summary="编辑作品信息")
async def edit_anime(animeId: int, payload: AnimeDetailUpdate):
    """更新弹幕库中单个作品的详细信息。"""
    db = get_database_service()
    async with db.transaction():
        success = await db.anime.update_anime_details(animeId, payload)
    if not success:
        raise HTTPException(404, "作品未找到")
    return {"message": "作品信息更新成功。"}


@router.delete("/library/anime/{animeId}", status_code=202, summary="删除作品", response_model=ControlTaskResponse)
async def delete_anime(
    animeId: int,
    task_manager: TaskManager = Depends(get_task_manager)
):
    """提交一个后台任务，以删除弹幕库中的一个作品及其所有关联的数据源、分集和弹幕。"""
    db = get_database_service()
    async with db.transaction():
        details = await db.anime.get_full_details(animeId)
    if not details:
        raise HTTPException(404, "作品未找到")
    try:
        unique_key = f"delete-anime-{animeId}"
        task_id, _ = await task_manager.submit_task(
            task_manager.build_task_coro_factory("delete_anime", animeId=animeId),
            f"外部API删除作品: {details['title']}",
            unique_key=unique_key, run_immediately=True
        )
        return {"message": "删除作品任务已提交", "taskId": task_id}
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(e))


@router.delete("/library/source/{sourceId}", status_code=202, summary="删除数据源", response_model=ControlTaskResponse)
async def delete_source(
    sourceId: int,
    task_manager: TaskManager = Depends(get_task_manager)
):
    """提交一个后台任务，以删除一个已关联的数据源及其所有分集和弹幕。"""
    db = get_database_service()
    async with db.transaction():
        info = await db.source.get_anime_source_info(sourceId)
    if not info:
        raise HTTPException(404, "数据源未找到")
    try:
        unique_key = f"delete-source-{sourceId}"
        task_id, _ = await task_manager.submit_task(
            task_manager.build_task_coro_factory("delete_source", sourceId=sourceId),
            f"外部API删除源: {info['animeTitle']} ({info['providerName']})",
            unique_key=unique_key, run_immediately=True
        )
        return {"message": "删除源任务已提交", "taskId": task_id}
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(e))


@router.put("/library/source/{sourceId}/favorite", response_model=ControlActionResponse, summary="精确标记数据源")
async def favorite_source(sourceId: int):
    """切换数据源的"精确标记"状态。一个作品只能有一个精确标记的源，它将在自动匹配时被优先使用。"""
    try:
        db = get_database_service()
        async with db.transaction():
            new_status = await db.source.toggle_source_favorite_status(sourceId)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail="数据源未找到") from exc
    if new_status is None:
        raise HTTPException(404, "数据源未找到")
    message = "数据源已标记为精确。" if new_status else "数据源已取消精确标记。"
    return {"message": message}


@router.get("/library/source/{sourceId}/episodes", response_model=List[EpisodeDetail], summary="获取源的分集列表")
async def get_source_episodes(sourceId: int):
    """获取指定数据源下所有已收录的分集列表。"""
    db = get_database_service()
    async with db.transaction():
        episodes = await db.source.get_episodes_for_source(sourceId)
        # ORM 主键为 id，响应约定为 episodeId；在会话内显式构造响应。
        return [EpisodeDetail(
            episodeId=episode.id, title=episode.title, episodeIndex=episode.episodeIndex,
            sourceUrl=episode.sourceUrl, fetchedAt=episode.fetchedAt,
            commentCount=episode.commentCount, danmakuFilePath=episode.danmakuFilePath,
        ) for episode in episodes]



@router.put("/library/episode/{episodeid}", response_model=ControlActionResponse, summary="编辑分集信息")
async def edit_episode(episodeid: int, payload: EpisodeInfoUpdate) -> dict:
    """经共享编排更新分集信息，文件补偿必须覆盖真实的事务提交。"""
    try:
        success = await update_episode_info(episodeid, payload)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    if not success:
        raise HTTPException(404, "分集未找到")
    return {"message": "分集信息更新成功。"}


@router.post("/library/episode/{episodeId}/refresh", status_code=202, summary="刷新分集弹幕", response_model=ControlTaskResponse)
async def refresh_episode(
    episodeId: int,
    task_manager: TaskManager = Depends(get_task_manager),
    manager: ScraperManager = Depends(get_scraper_manager),
    rate_limiter: RateLimiter = Depends(get_rate_limiter),
    config_service = Depends(get_config_service),
):
    """提交一个后台任务，为单个分集重新从其源网站获取最新的弹幕。"""
    db = get_database_service()
    async with db.transaction():
        info = await db.episode.get_episode_for_refresh(episodeId)
    if not info:
        raise HTTPException(404, "分集未找到")

    unique_key = f"refresh-episode-{episodeId}"

    task_id, _ = await task_manager.submit_task(
        task_manager.build_task_coro_factory(
            "refresh_episode", episodeId=episodeId, manager=manager,
            rate_limiter=rate_limiter, config_service=config_service,
        ),
        f"外部API刷新分集: {info['episodeTitle']} [{info.get('providerName', '?')}] (mediaId={info.get('mediaId', '?')})",
        unique_key=unique_key,
        task_type="refresh_episode",
        task_parameters={"episodeId": episodeId}
    )
    return {"message": "刷新分集任务已提交", "taskId": task_id}


@router.delete("/library/episode/{episodeId}", status_code=202, summary="删除分集", response_model=ControlTaskResponse)
async def delete_episode(
    episodeId: int,
    task_manager: TaskManager = Depends(get_task_manager)
):
    """提交一个后台任务，以删除单个分集及其所有弹幕。"""
    db = get_database_service()
    async with db.transaction():
        info = await db.episode.get_episode_for_refresh(episodeId)
    if not info:
        raise HTTPException(404, "分集未找到")
    try:
        unique_key = f"delete-episode-{episodeId}"
        task_id, _ = await task_manager.submit_task(
            task_manager.build_task_coro_factory("delete_episode", episodeId=episodeId),
            f"外部API删除分集: {info['episodeTitle']}",
            unique_key=unique_key, run_immediately=True
        )
        return {"message": "删除分集任务已提交", "taskId": task_id}
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(e))