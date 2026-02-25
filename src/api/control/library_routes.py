"""
外部控制API - 媒体库管理路由
包含: /library/*, /metadata/search
"""

import logging
from typing import List, Callable

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import exc

from src.db import crud, models, get_db_session, ConfigManager
from src import tasks
from src.services import ScraperManager, TaskManager, TaskSuccess, MetadataSourceManager
from src.rate_limiter import RateLimiter

from .models import (
    AutoImportSearchType, AutoImportMediaType,
    ControlActionResponse, ControlTaskResponse,
    ControlAnimeCreateRequest, ControlAnimeDetailsResponse,
    ControlMetadataSearchResponse
)
from .dependencies import (
    get_scraper_manager, get_metadata_manager,
    get_task_manager, get_config_manager, get_rate_limiter,
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
    metadata_manager: MetadataSourceManager = Depends(get_metadata_manager)
):
    """
    ### 功能
    从指定的元数据源（如TMDB, Bangumi）中查找媒体信息。

    ### 工作流程
    1.  提供 `provider` 来指定要查询的源。
    2.  提供 `keyword` 或 `id` 中的一个来进行搜索。
    3.  对于某些源（如TMDB），可能需要提供 `mediaType` 来区分电视剧和电影。

    ### 返回
    返回一个包含元数据详情的列表。如果通过ID查找且成功，列表中将只有一个元素。
    """
    if not keyword and not id:
        raise HTTPException(status_code=400, detail="必须提供 'keyword' 或 'id' 参数之一。")
    if keyword and id:
        raise HTTPException(status_code=400, detail="不能同时提供 'keyword' 和 'id' 参数。")

    # --- 将通用媒体类型映射到特定于提供商的类型 ---
    provider_media_type: str | None = None
    if mediaType:
        if provider == 'tmdb':
            provider_media_type = 'tv' if mediaType == AutoImportMediaType.TV_SERIES else 'movie'
        elif provider == 'tvdb':
            provider_media_type = 'series' if mediaType == AutoImportMediaType.TV_SERIES else 'movies'
    # --- 映射结束 ---

    # 创建一个虚拟用户，因为元数据管理器的核心方法需要它
    user = models.User(id=0, username="control_api")
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

@router.get("/library", response_model=List[models.LibraryAnimeInfo], summary="获取媒体库列表")
async def get_library(session: AsyncSession = Depends(get_db_session)):
    """获取当前弹幕库中所有已收录的作品列表。"""
    paginated_results = await crud.get_library_anime(session)
    return [models.LibraryAnimeInfo.model_validate(item) for item in paginated_results["list"]]


@router.get("/library/search", response_model=List[models.LibraryAnimeInfo], summary="搜索媒体库")
async def search_library(
    keyword: str = Query(..., description="搜索关键词"),
    session: AsyncSession = Depends(get_db_session)
):
    """根据关键词搜索弹幕库中已收录的作品。"""
    paginated_results = await crud.get_library_anime(session, keyword=keyword)
    return [models.LibraryAnimeInfo.model_validate(item) for item in paginated_results["list"]]


@router.post("/library/anime", response_model=ControlAnimeDetailsResponse, status_code=status.HTTP_201_CREATED, summary="自定义创建影视条目")
async def create_anime_entry(
    payload: ControlAnimeCreateRequest,
    session: AsyncSession = Depends(get_db_session),
    title_recognition_manager = Depends(get_title_recognition_manager)
):
    """
    ### 功能
    在数据库中手动创建一个新的影视作品条目。
    ### 工作流程
    1.  接收作品的标题、类型、季度等基本信息。
    2.  （可选）接收TMDB、Bangumi等元数据ID和其他别名。
    3.  在数据库中创建对应的 `anime`, `anime_metadata`, `anime_aliases` 记录。
    4.  返回新创建的作品的完整信息。
    """
    # Check for duplicates first
    season_for_check = payload.season if payload.type == AutoImportMediaType.TV_SERIES else 1
    existing_anime = await crud.find_anime_by_title_season_year(
        session, payload.title, season_for_check, payload.year, title_recognition_manager
    )
    if existing_anime:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="已存在同名同季度的作品。"
        )

    season_for_create = payload.season if payload.type == AutoImportMediaType.TV_SERIES else 1
    new_anime_id = await crud.get_or_create_anime(
        session,
        title=payload.title,
        media_type=payload.type.value,
        season=season_for_create,
        year=payload.year,
        image_url=None,
        local_image_path=None,
        title_recognition_manager=title_recognition_manager
    )

    await crud.update_metadata_if_empty(
        session, new_anime_id,
        tmdb_id=payload.tmdbId, imdb_id=payload.imdbId, tvdb_id=payload.tvdbId,
        douban_id=payload.doubanId, bangumi_id=payload.bangumiId
    )

    await crud.update_anime_aliases(session, new_anime_id, payload)
    await session.commit()

    new_details = await crud.get_anime_full_details(session, new_anime_id)
    if not new_details:
        raise HTTPException(status_code=500, detail="创建作品后无法获取其详细信息。")

    return ControlAnimeDetailsResponse.model_validate(new_details)



@router.get("/library/anime/{animeId}", response_model=ControlAnimeDetailsResponse, summary="获取作品详情")
async def get_anime_details(animeId: int, session: AsyncSession = Depends(get_db_session)):
    """获取弹幕库中单个作品的完整详细信息，包括所有元数据ID和别名。"""
    details = await crud.get_anime_full_details(session, animeId)
    if not details:
        raise HTTPException(404, "作品未找到")
    return ControlAnimeDetailsResponse.model_validate(details)


@router.get("/library/anime/{animeId}/sources", response_model=List[models.SourceInfo], summary="获取作品的所有数据源")
async def get_anime_sources(animeId: int, session: AsyncSession = Depends(get_db_session)):
    """获取指定作品已关联的所有弹幕源列表。"""
    anime_exists = await crud.get_anime_full_details(session, animeId)
    if not anime_exists:
        raise HTTPException(status_code=404, detail="作品未找到")
    return await crud.get_anime_sources(session, animeId)


@router.post("/library/anime/{animeId}/sources", response_model=models.SourceInfo, status_code=status.HTTP_201_CREATED, summary="为作品添加数据源")
async def add_source(
    animeId: int,
    payload: models.SourceCreate,
    session: AsyncSession = Depends(get_db_session)
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
    anime = await crud.get_anime_full_details(session, animeId)
    if not anime:
        raise HTTPException(status_code=404, detail="作品未找到")

    try:
        source_id = await crud.link_source_to_anime(session, animeId, payload.providerName, payload.mediaId)
        await session.commit()
        all_sources = await crud.get_anime_sources(session, animeId)
        newly_created_source = next((s for s in all_sources if s['sourceId'] == source_id), None)
        if not newly_created_source:
            raise HTTPException(status_code=500, detail="创建数据源后无法立即获取其信息。")
        return models.SourceInfo.model_validate(newly_created_source)
    except exc.IntegrityError:
        await session.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="该数据源已存在于此作品下，无法重复添加。")


@router.put("/library/anime/{animeId}", response_model=ControlActionResponse, summary="编辑作品信息")
async def edit_anime(animeId: int, payload: models.AnimeDetailUpdate, session: AsyncSession = Depends(get_db_session)):
    """更新弹幕库中单个作品的详细信息。"""
    if not await crud.update_anime_details(session, animeId, payload):
        raise HTTPException(404, "作品未找到")
    return {"message": "作品信息更新成功。"}


@router.delete("/library/anime/{animeId}", status_code=202, summary="删除作品", response_model=ControlTaskResponse)
async def delete_anime(
    animeId: int,
    session: AsyncSession = Depends(get_db_session),
    task_manager: TaskManager = Depends(get_task_manager)
):
    """提交一个后台任务，以删除弹幕库中的一个作品及其所有关联的数据源、分集和弹幕。"""
    details = await crud.get_anime_full_details(session, animeId)
    if not details:
        raise HTTPException(404, "作品未找到")
    try:
        unique_key = f"delete-anime-{animeId}"
        task_id, _ = await task_manager.submit_task(
            lambda s, cb: tasks.delete_anime_task(animeId, s, cb),
            f"外部API删除作品: {details['title']}",
            unique_key=unique_key, run_immediately=True
        )
        return {"message": "删除作品任务已提交", "taskId": task_id}
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(e))


@router.delete("/library/source/{sourceId}", status_code=202, summary="删除数据源", response_model=ControlTaskResponse)
async def delete_source(
    sourceId: int,
    session: AsyncSession = Depends(get_db_session),
    task_manager: TaskManager = Depends(get_task_manager)
):
    """提交一个后台任务，以删除一个已关联的数据源及其所有分集和弹幕。"""
    info = await crud.get_anime_source_info(session, sourceId)
    if not info:
        raise HTTPException(404, "数据源未找到")
    try:
        unique_key = f"delete-source-{sourceId}"
        task_id, _ = await task_manager.submit_task(
            lambda s, cb: tasks.delete_source_task(sourceId, s, cb),
            f"外部API删除源: {info['title']} ({info['providerName']})",
            unique_key=unique_key, run_immediately=True
        )
        return {"message": "删除源任务已提交", "taskId": task_id}
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(e))


@router.put("/library/source/{sourceId}/favorite", response_model=ControlActionResponse, summary="精确标记数据源")
async def favorite_source(sourceId: int, session: AsyncSession = Depends(get_db_session)):
    """切换数据源的"精确标记"状态。一个作品只能有一个精确标记的源，它将在自动匹配时被优先使用。"""
    new_status = await crud.toggle_source_favorite_status(session, sourceId)
    if new_status is None:
        raise HTTPException(404, "数据源未找到")
    message = "数据源已标记为精确。" if new_status else "数据源已取消精确标记。"
    return {"message": message}


@router.get("/library/source/{sourceId}/episodes", response_model=List[models.EpisodeDetail], summary="获取源的分集列表")
async def get_source_episodes(sourceId: int, session: AsyncSession = Depends(get_db_session)):
    """获取指定数据源下所有已收录的分集列表。"""
    paginated_result = await crud.get_episodes_for_source(session, sourceId)
    return paginated_result.get("episodes", [])



@router.put("/library/episode/{episodeid}", response_model=ControlActionResponse, summary="编辑分集信息")
async def edit_episode(episodeid: int, payload: models.EpisodeInfoUpdate, session: AsyncSession = Depends(get_db_session)):
    """更新单个分集的标题、集数和官方链接。"""
    if not await crud.update_episode_info(session, episodeid, payload):
        raise HTTPException(404, "分集未找到")
    return {"message": "分集信息更新成功。"}


@router.post("/library/episode/{episodeId}/refresh", status_code=202, summary="刷新分集弹幕", response_model=ControlTaskResponse)
async def refresh_episode(
    episodeId: int,
    session: AsyncSession = Depends(get_db_session),
    task_manager: TaskManager = Depends(get_task_manager),
    manager: ScraperManager = Depends(get_scraper_manager),
    rate_limiter: RateLimiter = Depends(get_rate_limiter),
    config_manager: ConfigManager = Depends(get_config_manager),
):
    """提交一个后台任务，为单个分集重新从其源网站获取最新的弹幕。"""
    info = await crud.get_episode_for_refresh(session, episodeId)
    if not info:
        raise HTTPException(404, "分集未找到")

    unique_key = f"refresh-episode-{episodeId}"

    task_id, _ = await task_manager.submit_task(
        lambda s, cb: tasks.refresh_episode_task(episodeId, s, manager, rate_limiter, cb, config_manager),
        f"外部API刷新分集: {info['title']}",
        unique_key=unique_key,
        task_type="refresh_episode",
        task_parameters={"episodeId": episodeId}
    )
    return {"message": "刷新分集任务已提交", "taskId": task_id}


@router.delete("/library/episode/{episodeId}", status_code=202, summary="删除分集", response_model=ControlTaskResponse)
async def delete_episode(
    episodeId: int,
    session: AsyncSession = Depends(get_db_session),
    task_manager: TaskManager = Depends(get_task_manager)
):
    """提交一个后台任务，以删除单个分集及其所有弹幕。"""
    info = await crud.get_episode_for_refresh(session, episodeId)
    if not info:
        raise HTTPException(404, "分集未找到")
    try:
        unique_key = f"delete-episode-{episodeId}"
        task_id, _ = await task_manager.submit_task(
            lambda s, cb: tasks.delete_episode_task(episodeId, s, cb),
            f"外部API删除分集: {info['title']}",
            unique_key=unique_key, run_immediately=True
        )
        return {"message": "删除分集任务已提交", "taskId": task_id}
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(e))