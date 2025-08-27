import re
from typing import Optional, List, Any, Dict, Callable, Union
import asyncio
import secrets
import importlib
import string
import time
from urllib.parse import urlparse, urlunparse, quote, unquote
import logging

from datetime import timedelta, datetime, timezone
from sqlalchemy import update, select, func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
import httpx
from ..rate_limiter import RateLimiter, RateLimitExceededError
from ..config_manager import ConfigManager
from pydantic import BaseModel, Field
from fastapi import APIRouter, Body, Depends, HTTPException, Query, Request, status, Response
from fastapi.security import OAuth2PasswordRequestForm

from .. import crud, models, orm_models, security, scraper_manager
from src import models as api_models
from ..log_manager import get_logs
from ..task_manager import TaskManager, TaskSuccess, TaskStatus
from ..metadata_manager import MetadataSourceManager
from ..scraper_manager import ScraperManager
from .. import tasks
from ..utils import parse_search_keyword
from ..webhook_manager import WebhookManager
from ..image_utils import download_image
from ..scheduler import SchedulerManager
from thefuzz import fuzz
from ..config import settings
from ..database import get_db_session

router = APIRouter()
auth_router = APIRouter()
logger = logging.getLogger(__name__)

class UITaskResponse(BaseModel):
    message: str
    taskId: str

async def get_scraper_manager(request: Request) -> ScraperManager:
    """依赖项：从应用状态获取 Scraper 管理器"""
    return request.app.state.scraper_manager

async def get_task_manager(request: Request) -> TaskManager:
    """依赖项：从应用状态获取任务管理器"""
    return request.app.state.task_manager

async def get_scheduler_manager(request: Request) -> SchedulerManager:
    """依赖项：从应用状态获取 Scheduler 管理器"""
    return request.app.state.scheduler_manager

async def get_webhook_manager(request: Request) -> WebhookManager:
    """依赖项：从应用状态获取 Webhook 管理器"""
    return request.app.state.webhook_manager

async def get_metadata_manager(request: Request) -> MetadataSourceManager:
    """依赖项：从应用状态获取元数据源管理器"""
    return request.app.state.metadata_manager

async def get_config_manager(request: Request) -> ConfigManager:
    """依赖项：从应用状态获取配置管理器"""
    return request.app.state.config_manager

async def get_rate_limiter(request: Request) -> RateLimiter:
    """依赖项：从应用状态获取速率限制器"""
    return request.app.state.rate_limiter

@router.get(
    "/search/anime",
    response_model=models.AnimeSearchResponse,
    summary="搜索本地数据库中的节目信息",
)
async def search_anime_local(
    keyword: str = Query(..., min_length=1, description="搜索关键词"),
    session: AsyncSession = Depends(get_db_session)
):
    db_results = await crud.search_anime(session, keyword)
    animes = [
        models.AnimeInfo(animeId=item["id"], animeTitle=item["title"], type=item["type"])
        for item in db_results
    ]
    return models.AnimeSearchResponse(animes=animes)

class UIProviderSearchResponse(models.ProviderSearchResponse):
    """扩展了 ProviderSearchResponse 以包含原始搜索的上下文。"""
    search_season: Optional[int] = None
    search_episode: Optional[int] = None
@router.get("/search/provider", response_model=UIProviderSearchResponse, summary="从外部数据源搜索节目")
async def search_anime_provider(
    keyword: str = Query(..., min_length=1, description="搜索关键词"),
    manager: ScraperManager = Depends(get_scraper_manager),
    current_user: models.User = Depends(security.get_current_user),
    session: AsyncSession = Depends(get_db_session),
    metadata_manager: MetadataSourceManager = Depends(get_metadata_manager)
):
    """
    从所有已配置的数据源（如腾讯、B站等）搜索节目信息。
    此接口实现了智能的按季缓存机制，并保留了原有的别名搜索、过滤和排序逻辑。
    """
    try:
        parsed_keyword = parse_search_keyword(keyword)
        search_title = parsed_keyword["title"]
        season_to_filter = parsed_keyword["season"]
        episode_to_filter = parsed_keyword["episode"]

        # --- 新增：按季缓存逻辑 ---
        # 缓存键基于核心标题和季度，允许在同一季的不同分集搜索中复用缓存
        cache_key = f"provider_search_{search_title}_{season_to_filter or 'all'}"
        cached_results_data = await crud.get_cache(session, cache_key)

        if cached_results_data is not None:
            logger.info(f"搜索缓存命中: '{cache_key}'")
            # 缓存数据已排序和过滤，只需更新当前请求的集数信息
            results = [models.ProviderSearchInfo.model_validate(item) for item in cached_results_data]
            for item in results:
                item.currentEpisodeIndex = episode_to_filter
            
            return UIProviderSearchResponse(
                results=results,
                search_season=season_to_filter,
                search_episode=episode_to_filter
            )
        
        logger.info(f"搜索缓存未命中: '{cache_key}'，正在执行完整搜索流程...")
        # --- 缓存逻辑结束 ---

        episode_info = {
            "season": season_to_filter,
            "episode": episode_to_filter
        } if episode_to_filter is not None else None

        logger.info(f"用户 '{current_user.username}' 正在搜索: '{keyword}' (解析为: title='{search_title}', season={season_to_filter}, episode={episode_to_filter})")
        if not manager.has_enabled_scrapers:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="没有启用的弹幕搜索源，请在“搜索源”页面中启用至少一个。"
            )

        # --- 原有的复杂搜索流程开始 ---
        tmdb_api_key = await crud.get_config_value(session, "tmdb_api_key", "")
        enabled_aux_sources = await crud.get_enabled_aux_metadata_sources(session)

        if not enabled_aux_sources or (len(enabled_aux_sources) == 1 and enabled_aux_sources[0]['providerName'] == 'tmdb' and not tmdb_api_key):
            logger.info("未配置或未启用任何有效的辅助搜索源，直接进行全网搜索。")
            results = await manager.search_all([search_title], episode_info=episode_info)
            logger.info(f"直接搜索完成，找到 {len(results)} 个原始结果。")
        else:
            logger.info("一个或多个元数据源已启用辅助搜索，开始执行...")
            # 修正：增加一个“防火墙”来验证从元数据源返回的别名，防止因模糊匹配导致的结果污染。
            # 1. 获取所有可能的别名
            all_possible_aliases = await metadata_manager.search_aliases_from_enabled_sources(search_title, current_user)
            
            # 2. 验证每个别名与原始搜索词的相似度
            validated_aliases = set()
            for alias in all_possible_aliases:
                # 使用 token_set_ratio 并设置一个合理的阈值（例如70），以允许小的差异但过滤掉完全不相关的结果。
                if fuzz.token_set_ratio(search_title, alias) > 70:
                    validated_aliases.add(alias)
                else:
                    logger.debug(f"别名验证：已丢弃低相似度的别名 '{alias}' (与 '{search_title}' 相比)")
            
            # 3. 使用经过验证的别名列表进行后续操作
            filter_aliases = validated_aliases
            filter_aliases.add(search_title) # 确保原始搜索词总是在列表中
            logger.info(f"所有辅助搜索完成，最终别名集大小: {len(filter_aliases)}")

            # 新增：根据您的要求，打印最终的别名列表以供调试
            logger.info(f"用于过滤的别名列表: {list(filter_aliases)}")

            logger.info(f"将使用解析后的标题 '{search_title}' 进行全网搜索...")
            all_results = await manager.search_all([search_title], episode_info=episode_info)

            def normalize_for_filtering(title: str) -> str:
                if not title: return ""
                title = re.sub(r'[\[【(（].*?[\]】)）]', '', title)
                return title.lower().replace(" ", "").replace("：", ":").strip()

            # 修正：采用更智能的两阶段过滤策略
            # 阶段1：基于原始搜索词进行初步、宽松的过滤，以确保所有相关系列（包括不同季度和剧场版）都被保留。
            # 只有当用户明确指定季度时，我们才进行更严格的过滤。
            normalized_filter_aliases = {normalize_for_filtering(alias) for alias in filter_aliases if alias}
            filtered_results = []
            for item in all_results:
                normalized_item_title = normalize_for_filtering(item.title)
                if not normalized_item_title: continue
                
                # 检查搜索结果是否与任何一个别名匹配
                # token_set_ratio 擅长处理单词顺序不同和部分单词匹配的情况。
                # 修正：使用 partial_ratio 来更好地匹配续作和外传 (e.g., "刀剑神域" vs "刀剑神域外传")
                # 85 的阈值可以在保留强相关的同时，过滤掉大部分无关结果。
                if any(fuzz.partial_ratio(normalized_item_title, alias) > 85 for alias in normalized_filter_aliases):
                    filtered_results.append(item)

            logger.info(f"别名过滤: 从 {len(all_results)} 个原始结果中，保留了 {len(filtered_results)} 个相关结果。")
            results = filtered_results
    except httpx.RequestError as e:
        error_message = f"搜索 '{keyword}' 时发生网络错误: {e}"
        logger.error(error_message, exc_info=True)
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=error_message)

    # 辅助函数，用于根据标题修正媒体类型
    def is_movie_by_title(title: str) -> bool:
        if not title:
            return False
        # 关键词列表，不区分大小写
        movie_keywords = ["剧场版", "劇場版", "movie", "映画"]
        title_lower = title.lower()
        return any(keyword in title_lower for keyword in movie_keywords)

    # 新增逻辑：根据标题关键词修正媒体类型
    for item in results:
        if item.type == 'tv_series' and is_movie_by_title(item.title):
            logger.info(f"标题 '{item.title}' 包含电影关键词，类型从 'tv_series' 修正为 'movie'。")
            item.type = 'movie'

    # 如果用户在搜索词中明确指定了季度，则对结果进行过滤
    if season_to_filter:
        original_count = len(results)
        # 当指定季度时，我们只关心电视剧类型
        filtered_by_type = [item for item in results if item.type == 'tv_series']
        
        # 然后在电视剧类型中，我们按季度号过滤
        filtered_by_season = []
        for item in filtered_by_type:
            # 使用模型中已解析好的 season 字段进行比较
            if item.season == season_to_filter:
                filtered_by_season.append(item)
        
        logger.info(f"根据指定的季度 ({season_to_filter}) 进行过滤，从 {original_count} 个结果中保留了 {len(filtered_by_season)} 个。")
        results = filtered_by_season

    # 修正：在返回结果前，确保 currentEpisodeIndex 与本次请求的 episode_info 一致。
    # 这可以防止因缓存或其他原因导致的状态泄露。
    current_episode_index_for_this_request = episode_info.get("episode") if episode_info else None
    for item in results:
        item.currentEpisodeIndex = current_episode_index_for_this_request

    # 新增：根据搜索源的显示顺序和标题相似度对结果进行排序
    source_settings = await crud.get_all_scraper_settings(session)
    source_order_map = {s['providerName']: s['displayOrder'] for s in source_settings}

    def sort_key(item: models.ProviderSearchInfo):
        provider_order = source_order_map.get(item.provider, 999)
        # 使用 token_set_ratio 来获得更鲁棒的标题相似度评分
        similarity_score = fuzz.token_set_ratio(search_title, item.title)
        # 主排序键：源顺序（升序）；次排序键：相似度（降序）
        return (provider_order, -similarity_score)

    sorted_results = sorted(results, key=sort_key)

    # --- 新增：在返回前缓存最终结果 ---
    # 我们缓存的是整季的结果，所以在存入前清除特定集数的信息
    results_to_cache = []
    for item in sorted_results:
        item_copy = item.model_copy(deep=True)
        item_copy.currentEpisodeIndex = None
        results_to_cache.append(item_copy.model_dump())

    if sorted_results:
        await crud.set_cache(session, cache_key, results_to_cache, ttl_seconds=10800) # 缓存3小时
    # --- 缓存逻辑结束 ---

    return UIProviderSearchResponse(
        results=sorted_results,
        search_season=season_to_filter,
        search_episode=episode_to_filter
    )

@router.get("/library/episodes-by-title", response_model=List[int], summary="根据作品标题获取已存在的分集序号")
async def get_existing_episode_indices(
    title: str = Query(..., description="要查询的作品标题"),
    season: Optional[int] = Query(None, description="要查询的季度号"),
    current_user: models.User = Depends(security.get_current_user),
    session: AsyncSession = Depends(get_db_session)
):
    """
    根据一个作品的标题和季度，查询弹幕库中该作品已存在的所有分集的序号列表。
    用于在“编辑导入”界面实现增量导入。
    """
    return await crud.get_episode_indices_by_anime_title(session, title, season=season)



@router.get("/search/episodes", response_model=List[models.ProviderEpisodeInfo], summary="获取搜索结果的分集列表")
async def get_episodes_for_search_result(
    provider: str = Query(...),
    media_id: str = Query(...),
    media_type: Optional[str] = Query(None), # Pass media_type to help scraper
    manager: ScraperManager = Depends(get_scraper_manager),
    current_user: models.User = Depends(security.get_current_user)
):
    """为指定的搜索结果获取完整的分集列表。"""
    try:
        scraper = manager.get_scraper(provider)
        # 将 db_media_type 传递给 get_episodes 以帮助需要它的刮削器（如 mgtv）
        episodes = await scraper.get_episodes(media_id, db_media_type=media_type)
        return episodes
    except httpx.RequestError as e:
        # 新增：捕获网络错误
        error_message = f"从 {provider} 获取分集列表时发生网络错误: {e}"
        logger.error(f"获取分集列表失败 (provider={provider}, media_id={media_id}): {error_message}", exc_info=True)
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=error_message)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))
    except Exception as e:
        logger.error(f"获取分集列表失败 (provider={provider}, media_id={media_id}): {e}", exc_info=True)
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="获取分集列表失败。")


@router.get("/library", response_model=models.LibraryResponse, summary="获取媒体库内容")
async def get_library(
    current_user: models.User = Depends(security.get_current_user),
    session: AsyncSession = Depends(get_db_session)
):
    """获取数据库中所有已收录的番剧信息，用于“弹幕情况”展示。"""
    # 修正：直接在此处执行正确的查询，以确保 episodeCount 和 sourceCount 是实时计算的。
    stmt = (
        select(
            orm_models.Anime.id.label("animeId"),
            orm_models.Anime.title,
            orm_models.Anime.type,
            orm_models.Anime.season,
            orm_models.Anime.year,
            orm_models.Anime.imageUrl,
            orm_models.Anime.localImagePath,
            orm_models.Anime.createdAt,
            func.count(func.distinct(orm_models.AnimeSource.id)).label("sourceCount"),
            func.count(func.distinct(orm_models.Episode.id)).label("episodeCount")
        )
        .select_from(orm_models.Anime)
        .outerjoin(orm_models.Anime.sources)
        .outerjoin(orm_models.AnimeSource.episodes)
        .group_by(orm_models.Anime.id)
        .order_by(orm_models.Anime.createdAt.desc())
    )
    result = await session.execute(stmt)
    db_results = result.mappings().all()
    # Pydantic 会自动处理 datetime 到 ISO 8601 字符串的转换
    return models.LibraryResponse(animes=[models.LibraryAnimeInfo.model_validate(item) for item in db_results])

@router.get("/library/anime/{animeId}/details", response_model=models.AnimeFullDetails, summary="获取影视完整详情")
async def get_anime_full_details(
    animeId: int,
    current_user: models.User = Depends(security.get_current_user),
    session: AsyncSession = Depends(get_db_session)
):
    """获取指定番剧的完整信息，包括所有元数据ID。"""
    details = await crud.get_anime_full_details(session, animeId)
    if not details:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Anime not found")
    
    # 修正：如果 crud 函数没有返回年份，手动从 Anime 表中获取并添加到响应中
    # 这确保了即使在 `get_anime_full_details` 的实现中忘记包含年份，API也能正确返回。
    if 'year' not in details or details.get('year') is None:
        anime_record = await session.get(orm_models.Anime, animeId)
        if anime_record:
            details['year'] = anime_record.year

    return models.AnimeFullDetails.model_validate(details)

@router.put("/library/anime/{animeId}", status_code=status.HTTP_204_NO_CONTENT, summary="编辑影视信息")
async def edit_anime_info(
    animeId: int,
    update_data: models.AnimeDetailUpdate,
    current_user: models.User = Depends(security.get_current_user),
    session: AsyncSession = Depends(get_db_session),
    metadata_manager: MetadataSourceManager = Depends(get_metadata_manager)
):
    """更新指定番剧的标题、季度和元数据。"""
    updated = await crud.update_anime_details(session, animeId, update_data)
    if not updated:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="作品未找到或更新失败")
    logger.info(f"用户 '{current_user.username}' 更新了番剧 ID: {animeId} 的详细信息。")

    # 新增：如果提供了TMDB ID和剧集组ID，则更新映射表
    if update_data.tmdbId and update_data.tmdbEpisodeGroupId:
        logger.info(f"检测到TMDB ID和剧集组ID，开始更新映射表...")
        try:
            await metadata_manager.update_tmdb_mappings(
                tmdb_tv_id=int(update_data.tmdbId),
                group_id=update_data.tmdbEpisodeGroupId,
                user=current_user
            )
        except Exception as e:
            # 仅记录错误，不中断主流程，因为核心信息已保存
            logger.error(f"更新TMDB映射失败: {e}", exc_info=True)
    return

class RefreshPosterRequest(models.BaseModel):
    imageUrl: str

@router.post("/library/anime/{animeId}/refresh-poster", summary="刷新并缓存影视海报")
async def refresh_anime_poster(
    animeId: int,
    request_data: RefreshPosterRequest,
    current_user: models.User = Depends(security.get_current_user),
    session: AsyncSession = Depends(get_db_session),
    scraper_manager: ScraperManager = Depends(get_scraper_manager)
):
    """根据提供的URL，重新下载并缓存海报，更新数据库记录。"""
    new_local_path = await download_image(request_data.imageUrl, session, scraper_manager)
    if not new_local_path:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="图片下载失败，请检查URL或服务器日志。")

    stmt = update(orm_models.Anime).where(orm_models.Anime.id == animeId).values(imageUrl=request_data.imageUrl, localImagePath=new_local_path)
    result = await session.execute(stmt)
    await session.commit()
    affected_rows = result.rowcount

    if affected_rows == 0:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="作品未找到。")
    return {"new_path": new_local_path}

@router.get("/library/source/{sourceId}/details", response_model=models.SourceDetailsResponse, summary="获取单个数据源的详情")
async def get_source_details(
    sourceId: int,
    current_user: models.User = Depends(security.get_current_user),
    session: AsyncSession = Depends(get_db_session),
):
    """获取指定数据源的详细信息，包括其提供方名称。"""
    source_info = await crud.get_anime_source_info(session, sourceId)
    if not source_info:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Source not found")
    return models.SourceDetailsResponse.model_validate(source_info)

class ReassociationRequest(models.BaseModel):
    targetAnimeId: int

@router.post("/library/anime/{sourceAnimeId}/reassociate", status_code=status.HTTP_204_NO_CONTENT, summary="重新关联作品的数据源")
async def reassociate_anime_sources(
    sourceAnimeId: int,
    request_data: ReassociationRequest = Body(...),
    current_user: models.User = Depends(security.get_current_user),
    session: AsyncSession = Depends(get_db_session)
):
    """将一个作品的所有数据源移动到另一个作品，并删除原作品。"""
    if sourceAnimeId == request_data.targetAnimeId:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="源作品和目标作品不能相同。")

    success = await crud.reassociate_anime_sources(session, sourceAnimeId, request_data.targetAnimeId)
    if not success:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="源作品或目标作品未找到，或操作失败。")
    logger.info(f"用户 '{current_user.username}' 将作品 ID {sourceAnimeId} 的源关联到了 ID {request_data.targetAnimeId}。")
    return

@router.delete("/library/source/{sourceId}", status_code=status.HTTP_202_ACCEPTED, summary="提交删除指定数据源的任务")
async def delete_source_from_anime(
    sourceId: int,
    current_user: models.User = Depends(security.get_current_user),
    session: AsyncSession = Depends(get_db_session),
    task_manager: TaskManager = Depends(get_task_manager)
):
    """提交一个后台任务来删除一个数据源及其所有关联的分集和弹幕。"""
    source_info = await crud.get_anime_source_info(session, sourceId)
    if not source_info:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Source not found")

    task_title = f"删除源: {source_info['title']} ({source_info['providerName']})"
    task_coro = lambda session, callback: tasks.delete_source_task(sourceId, session, callback)
    task_id, _ = await task_manager.submit_task(task_coro, task_title)

    logger.info(f"用户 '{current_user.username}' 提交了删除源 ID: {sourceId} 的任务 (Task ID: {task_id})。")
    return {"message": f"删除源 '{source_info['providerName']}' 的任务已提交。", "taskId": task_id}

class BulkDeleteEpisodesRequest(models.BaseModel):
    episodeIds: List[int] = Field(..., alias="episode_ids")

    class Config:
        populate_by_name = True

@router.post("/library/episodes/delete-bulk", status_code=status.HTTP_202_ACCEPTED, summary="提交批量删除分集的任务", response_model=UITaskResponse)
async def delete_bulk_episodes(
    request_data: BulkDeleteEpisodesRequest,
    current_user: models.User = Depends(security.get_current_user),
    task_manager: TaskManager = Depends(get_task_manager)
):
    """提交一个后台任务来批量删除多个分集。"""
    if not request_data.episodeIds:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Episode IDs list cannot be empty.")

    task_title = f"批量删除 {len(request_data.episodeIds)} 个分集"
    
    # 注意：这里我们将整个列表传递给任务
    task_coro = lambda session, callback: tasks.delete_bulk_episodes_task(request_data.episodeIds, session, callback)
    
    task_id, _ = await task_manager.submit_task(task_coro, task_title)

    logger.info(f"用户 '{current_user.username}' 提交了批量删除 {len(request_data.episodeIds)} 个分集的任务 (Task ID: {task_id})。")
    return {"message": task_title + "的任务已提交。", "taskId": task_id}


@router.put("/library/source/{sourceId}/favorite", status_code=status.HTTP_204_NO_CONTENT, summary="切换数据源的精确标记状态")
async def toggle_source_favorite(
    sourceId: int,
    current_user: models.User = Depends(security.get_current_user),
    session: AsyncSession = Depends(get_db_session)
):
    """切换指定数据源的精确标记状态。一个作品只能有一个精确标记的源。"""
    new_status = await crud.toggle_source_favorite_status(session, sourceId)
    if new_status is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Source not found")
    return # 204 No Content, so no body is needed

@router.put("/library/source/{sourceId}/toggle-incremental-refresh", status_code=status.HTTP_204_NO_CONTENT, summary="切换数据源的定时增量更新状态")
async def toggle_source_incremental_refresh(
    sourceId: int,
    current_user: models.User = Depends(security.get_current_user),
    session: AsyncSession = Depends(get_db_session)
):
    """切换指定数据源的定时增量更新的启用/禁用状态。"""
    toggled = await crud.toggle_source_incremental_refresh(session, sourceId)
    if not toggled:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Source not found")
    logger.info(f"用户 '{current_user.username}' 切换了源 ID {sourceId} 的定时增量更新状态。")
@router.get("/library/anime/{animeId}/sources", response_model=List[models.SourceInfo], summary="获取作品的所有数据源")
async def get_anime_sources_for_anime(
    animeId: int,
    current_user: models.User = Depends(security.get_current_user),
    session: AsyncSession = Depends(get_db_session)
):
    """获取指定作品关联的所有数据源列表。"""
    return await crud.get_anime_sources(session, animeId)

@router.get("/library/source/{sourceId}/episodes", response_model=List[models.EpisodeDetail], summary="获取数据源的所有分集")
async def get_source_episodes(
    sourceId: int,
    current_user: models.User = Depends(security.get_current_user),
    session: AsyncSession = Depends(get_db_session)
):
    """获取指定数据源下的所有已收录分集列表。"""
    return await crud.get_episodes_for_source(session, sourceId)

@router.put("/library/episode/{episodeId}", status_code=status.HTTP_204_NO_CONTENT, summary="编辑分集信息")
async def edit_episode_info(
    episodeId: int,
    update_data: models.EpisodeInfoUpdate,
    current_user: models.User = Depends(security.get_current_user),
    session: AsyncSession = Depends(get_db_session)
):
    """更新指定分集的标题、集数和链接。"""
    try:
        updated = await crud.update_episode_info(session, episodeId, update_data)
        if not updated:
            logger.warning(f"尝试更新一个不存在的分集 (ID: {episodeId})，操作被拒绝。")
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Episode not found")
        logger.info(f"用户 '{current_user.username}' 更新了分集 ID: {episodeId} 的信息。")
        return
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(e))

@router.post("/library/source/{sourceId}/reorder-episodes", status_code=status.HTTP_202_ACCEPTED, summary="重整指定源的分集顺序")
async def reorder_source_episodes(
    sourceId: int,
    current_user: models.User = Depends(security.get_current_user),
    session: AsyncSession = Depends(get_db_session),
    task_manager: TaskManager = Depends(get_task_manager)
):
    """提交一个后台任务，按当前顺序重新编号指定数据源的所有分集。"""
    source_info = await crud.get_anime_source_info(session, sourceId)
    if not source_info:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Source not found")

    task_title = f"重整集数: {source_info['title']} ({source_info['providerName']})"
    task_coro = lambda session, callback: tasks.reorder_episodes_task(sourceId, session, callback)
    task_id, _ = await task_manager.submit_task(task_coro, task_title)

    logger.info(f"用户 '{current_user.username}' 提交了重整源 ID: {sourceId} 集数的任务 (Task ID: {task_id})。")
    return {"message": f"重整集数任务 '{task_title}' 已提交。", "taskId": task_id}


@router.delete("/library/episode/{episodeId}", status_code=status.HTTP_202_ACCEPTED, summary="提交删除指定分集的任务", response_model=UITaskResponse)
async def delete_episode_from_source(
    episodeId: int,
    current_user: models.User = Depends(security.get_current_user),
    session: AsyncSession = Depends(get_db_session),
    task_manager: TaskManager = Depends(get_task_manager)
):
    """提交一个后台任务来删除一个分集及其所有关联的弹幕。"""
    episode_info = await crud.get_episode_for_refresh(session, episodeId)
    if not episode_info:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Episode not found")

    provider_name = episode_info.get('providerName', '未知源')
    task_title = f"删除分集: {episode_info['title']} - [{provider_name}]"
    task_coro = lambda session, callback: tasks.delete_episode_task(episodeId, session, callback)
    task_id, _ = await task_manager.submit_task(task_coro, task_title)

    logger.info(f"用户 '{current_user.username}' 提交了删除分集 ID: {episodeId} 的任务 (Task ID: {task_id})。")
    return {"message": f"删除分集 '{episode_info['title']}' 的任务已提交。", "taskId": task_id}

@router.post("/library/episode/{episodeId}/refresh", status_code=status.HTTP_202_ACCEPTED, summary="刷新单个分集的弹幕", response_model=UITaskResponse)
async def refresh_single_episode(
    episodeId: int,
    current_user: models.User = Depends(security.get_current_user),
    session: AsyncSession = Depends(get_db_session),
    scraper_manager: ScraperManager = Depends(get_scraper_manager),
    task_manager: TaskManager = Depends(get_task_manager),
    rate_limiter: RateLimiter = Depends(get_rate_limiter)
):
    """为指定分集启动一个后台任务，重新获取其弹幕。"""
    # 检查分集是否存在，以提供更友好的404错误
    episode = await crud.get_episode_for_refresh(session, episodeId)
    if not episode:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Episode not found")
    
    logger.info(f"用户 '{current_user.username}' 请求刷新分集 ID: {episodeId} ({episode['title']})")

    provider_name = episode.get('providerName', '未知源')
    task_title = f"刷新分集: {episode['title']} - [{provider_name}]"
    task_coro = lambda session, callback: tasks.refresh_episode_task(episodeId, session, scraper_manager, rate_limiter, callback)
    task_id, _ = await task_manager.submit_task(task_coro, task_title)

    return {"message": f"分集 '{episode['title']}' 的刷新任务已提交。", "taskId": task_id}

@router.post("/library/source/{sourceId}/refresh", status_code=status.HTTP_202_ACCEPTED, summary="刷新指定数据源 (全量或增量)", response_model=UITaskResponse)
async def refresh_anime(
    sourceId: int,
    mode: str = Query("full", description="刷新模式: 'full' (全量) 或 'incremental' (增量)"),
    current_user: models.User = Depends(security.get_current_user),
    session: AsyncSession = Depends(get_db_session),
    scraper_manager: ScraperManager = Depends(get_scraper_manager),
    task_manager: TaskManager = Depends(get_task_manager),
    rate_limiter: RateLimiter = Depends(get_rate_limiter),
    metadata_manager: MetadataSourceManager = Depends(get_metadata_manager)
):
    """
    为指定的数据源启动一个刷新任务。
    - full: 清空并重新抓取所有分集和弹幕。
    - incremental: 尝试抓取最新一集。
    """
    source_info = await crud.get_anime_source_info(session, sourceId)
    if not source_info or not source_info.get("providerName") or not source_info.get("mediaId"):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Anime not found or missing source information for refresh.")
    
    if mode == "incremental":
        logger.info(f"用户 '{current_user.username}' 为番剧 '{source_info['title']}' (源ID: {sourceId}) 启动了增量刷新任务。")
        eps = await crud.get_episodes_for_source(session, sourceId)
        latest_episode_index = max((ep['episodeIndex'] for ep in eps), default=0)
        next_episode_index = latest_episode_index + 1
        
        task_title = f"增量刷新: {source_info['title']} ({source_info['providerName']}) - 尝试第{next_episode_index}集"
        task_coro = lambda s, cb: tasks.incremental_refresh_task(
            sourceId=sourceId, nextEpisodeIndex=next_episode_index, session=s, manager=scraper_manager,
            task_manager=task_manager, progress_callback=cb, animeTitle=source_info["title"],
            rate_limiter=rate_limiter, metadata_manager=metadata_manager
        )
        message_to_return = f"番剧 '{source_info['title']}' 的增量刷新任务已提交。"
    elif mode == "full":
        logger.info(f"用户 '{current_user.username}' 为番剧 '{source_info['title']}' (源ID: {sourceId}) 启动了全量刷新任务。")
        task_title = f"全量刷新: {source_info['title']} ({source_info['providerName']})"
        task_coro = lambda s, cb: tasks.full_refresh_task(sourceId, s, scraper_manager, task_manager, rate_limiter, cb, metadata_manager)
        message_to_return = f"番剧 '{source_info['title']}' 的全量刷新任务已提交。"
    else:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="无效的刷新模式，必须是 'full' 或 'incremental'。")

    task_id, _ = await task_manager.submit_task(task_coro, task_title)
    return {"message": message_to_return, "taskId": task_id}

@router.delete("/library/anime/{animeId}", status_code=status.HTTP_202_ACCEPTED, summary="提交删除媒体库中番剧的任务", response_model=UITaskResponse)
async def delete_anime_from_library(
    animeId: int,
    current_user: models.User = Depends(security.get_current_user),
    session: AsyncSession = Depends(get_db_session),
    task_manager: TaskManager = Depends(get_task_manager)
):
    """提交一个后台任务来删除一个番剧及其所有关联数据。"""
    # Get title for task name
    anime_details = await crud.get_anime_full_details(session, animeId)
    if not anime_details:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Anime not found")
    
    task_title = f"删除作品: {anime_details['title']}"
    task_coro = lambda session, callback: tasks.delete_anime_task(animeId, session, callback)
    task_id, _ = await task_manager.submit_task(task_coro, task_title)

    logger.info(f"用户 '{current_user.username}' 提交了删除作品 ID: {animeId} 的任务 (Task ID: {task_id})。")
    return {"message": f"删除作品 '{anime_details['title']}' 的任务已提交。", "taskId": task_id}

class BulkDeleteRequest(models.BaseModel):
    sourceIds: List[int] = Field(..., alias="source_ids")

    class Config:
        populate_by_name = True

@router.post("/library/sources/delete-bulk", status_code=status.HTTP_202_ACCEPTED, summary="提交批量删除数据源的任务", response_model=UITaskResponse)
async def delete_bulk_sources(
    request_data: BulkDeleteRequest,
    current_user: models.User = Depends(security.get_current_user),
    task_manager: TaskManager = Depends(get_task_manager)
):
    """提交一个后台任务来批量删除多个数据源。"""
    if not request_data.sourceIds:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Source IDs list cannot be empty.")

    task_title = f"批量删除 {len(request_data.sourceIds)} 个数据源"
    task_coro = lambda session, callback: tasks.delete_bulk_sources_task(request_data.sourceIds, session, callback)
    task_id, _ = await task_manager.submit_task(task_coro, task_title)

    logger.info(f"用户 '{current_user.username}' 提交了批量删除 {len(request_data.sourceIds)} 个源的任务 (Task ID: {task_id})。")
    return {"message": task_title + "的任务已提交。", "taskId": task_id}

@router.get("/scrapers", response_model=List[models.ScraperSettingWithConfig], summary="获取所有搜索源的设置")
async def get_scraper_settings(
    current_user: models.User = Depends(security.get_current_user),
    session: AsyncSession = Depends(get_db_session),
    manager: ScraperManager = Depends(get_scraper_manager),
    config_manager: ConfigManager = Depends(get_config_manager)
):
    """获取所有可用搜索源的列表及其配置（启用状态、顺序、可配置字段）。"""
    settings = await crud.get_all_scraper_settings(session)
    
    # 获取验证开关的全局状态
    verification_enabled_str = await config_manager.get("scraper_verification_enabled", "false")
    verification_enabled = verification_enabled_str.lower() == 'true'

    full_settings = []
    for s in settings:
        provider_name = s['providerName']
        scraper_class = manager.get_scraper_class(provider_name)
        
        # Create a new dictionary with all required fields before validation
        full_setting_data = s.copy()
        
        # 如果验证被禁用，则所有源都应显示为已验证
        full_setting_data['isVerified'] = True if not verification_enabled else (provider_name in manager._verified_scrapers)
        
        if scraper_class:
            full_setting_data['isLoggable'] = getattr(scraper_class, "is_loggable", False)
            # 关键修复：复制类属性以避免修改共享的可变字典
            base_fields = getattr(scraper_class, "configurable_fields", None)
            configurable_fields = base_fields.copy() if base_fields is not None else {}

            # 为当前源动态添加其专属的黑名单配置字段
            blacklist_key = f"{provider_name}_episode_blacklist_regex"
            configurable_fields[blacklist_key] = "分集标题黑名单 (正则)"
            full_setting_data['configurableFields'] = configurable_fields
        else:
            # Provide defaults if scraper_class is not found to prevent validation errors
            full_setting_data['isLoggable'] = False
            full_setting_data['configurableFields'] = {}

        s_with_config = models.ScraperSettingWithConfig.model_validate(full_setting_data)
        full_settings.append(s_with_config)
            
    return full_settings

@router.get("/metadata-sources", response_model=List[models.MetadataSourceStatusResponse], summary="获取所有元数据源的设置")
async def get_metadata_source_settings(
    current_user: models.User = Depends(security.get_current_user),
    manager: MetadataSourceManager = Depends(get_metadata_manager)
):
    """获取所有元数据源及其当前状态（配置、连接性等）。"""
    return await manager.get_sources_with_status()

@router.put("/metadata-sources", status_code=status.HTTP_204_NO_CONTENT, summary="更新元数据源的设置")
async def update_metadata_source_settings(
    settings: List[models.MetadataSourceSettingUpdate],
    current_user: models.User = Depends(security.get_current_user),
    session: AsyncSession = Depends(get_db_session),
    manager: MetadataSourceManager = Depends(get_metadata_manager)
):
    """批量更新元数据源的启用状态、辅助搜索状态和显示顺序。"""
    # 修正：恢复使用专用的 `metadata_sources` 表来存储设置，
    # 这将调用 `crud.py` 中正确的函数，并解决之前遇到的状态保存问题。
    await crud.update_metadata_sources_settings(session, settings)
    # 新增：触发 MetadataSourceManager 重新加载设置，以使更改立即生效。
    await manager.load_and_sync_sources()
    logger.info(f"用户 '{current_user.username}' 更新了元数据源设置，已重新加载。")

@router.put("/scrapers", status_code=status.HTTP_204_NO_CONTENT, summary="更新搜索源的设置")
async def update_scraper_settings(
    settings: List[models.ScraperSetting],
    current_user: models.User = Depends(security.get_current_user),
    session: AsyncSession = Depends(get_db_session),
    manager: ScraperManager = Depends(get_scraper_manager)
):
    """批量更新搜索源的启用状态和显示顺序。"""
    await crud.update_scrapers_settings(session, settings)
    # 更新数据库后，触发 ScraperManager 重新加载搜索源
    await manager.load_and_sync_scrapers()
    logger.info(f"用户 '{current_user.username}' 更新了搜索源设置，已重新加载。")
    return

class ProxyTestResult(BaseModel):
    status: str  # 'success' or 'failure'
    latency: Optional[float] = None # in ms
    error: Optional[str] = None
@router.get("/config/proxy", response_model=models.ProxySettingsResponse, summary="获取代理配置")

async def get_proxy_settings(
    current_user: models.User = Depends(security.get_current_user),
    session: AsyncSession = Depends(get_db_session)
):
    """获取全局代理配置。"""
    proxy_url_task = crud.get_config_value(session, "proxy_url", "")
    proxy_enabled_task = crud.get_config_value(session, "proxy_enabled", "false")
    proxy_url, proxy_enabled_str = await asyncio.gather(proxy_url_task, proxy_enabled_task)

    proxy_enabled = proxy_enabled_str.lower() == 'true'
    
    # Parse the URL into components
    protocol, host, port, username, password = "http", None, None, None, None
    if proxy_url:
        try:
            p = urlparse(proxy_url)
            protocol = p.scheme or "http"
            host = p.hostname
            port = p.port
            username = unquote(p.username) if p.username else None
            password = unquote(p.password) if p.password else None
        except Exception as e:
            logger.error(f"解析存储的代理URL '{proxy_url}' 失败: {e}")

    return models.ProxySettingsResponse(
        proxyProtocol=protocol,
        proxyHost=host,
        proxyPort=port,
        proxyUsername=username,
        proxyPassword=password,
        proxyEnabled=proxy_enabled
    )

@router.put("/config/proxy", status_code=status.HTTP_204_NO_CONTENT, summary="更新代理配置")
async def update_proxy_settings(
    payload: models.ProxySettingsUpdate,
    current_user: models.User = Depends(security.get_current_user),
    session: AsyncSession = Depends(get_db_session),
    config_manager: ConfigManager = Depends(get_config_manager)
):
    """更新全局代理配置。"""
    proxy_url = ""
    if payload.proxyHost and payload.proxyPort:
        # URL-encode username and password to handle special characters like '@'
        userinfo = ""
        if payload.proxyUsername:
            userinfo = quote(payload.proxyUsername)
            if payload.proxyPassword:
                userinfo += ":" + quote(payload.proxyPassword)
            userinfo += "@"
        
        proxy_url = f"{payload.proxyProtocol}://{userinfo}{payload.proxyHost}:{payload.proxyPort}"

    await crud.update_config_value(session, "proxy_url", proxy_url)
    config_manager.invalidate("proxy_url")
    
    await crud.update_config_value(session, "proxy_enabled", str(payload.proxyEnabled).lower())
    config_manager.invalidate("proxy_enabled")
    logger.info(f"用户 '{current_user.username}' 更新了代理配置。")

class ProxyTestRequest(BaseModel):
    proxy_url: Optional[str] = None

class FullProxyTestResponse(BaseModel):
    proxy_connectivity: ProxyTestResult
    target_sites: Dict[str, ProxyTestResult]

@router.post("/proxy/test", response_model=FullProxyTestResponse, summary="测试代理连接和延迟")
async def test_proxy_latency(
    request: ProxyTestRequest,
    current_user: models.User = Depends(security.get_current_user)
):
    """测试代理到常用域名的连接延迟。如果未提供代理URL，则测试直接连接。"""
    proxy_url = request.proxy_url
    # 修正：使用旧的 'proxy' 参数以兼容旧版 httpx
    proxy_to_use = proxy_url if proxy_url else None

    # Test 1: Proxy Connectivity
    proxy_connectivity_result: ProxyTestResult
    # 使用一个已知的高可用、轻量级端点进行测试
    test_url_google = "http://www.google.com/generate_204"

    if not proxy_url:
        proxy_connectivity_result = ProxyTestResult(status="skipped", error="未配置代理，跳过测试")
    else:
        try:
            async with httpx.AsyncClient(proxy=proxy_to_use, timeout=10.0, follow_redirects=False) as client:
                start_time = time.time()
                response = await client.get(test_url_google)
                latency = (time.time() - start_time) * 1000
                # 204 No Content 是成功的标志
                if response.status_code == 204:
                    proxy_connectivity_result = ProxyTestResult(status="success", latency=latency)
                else:
                    proxy_connectivity_result = ProxyTestResult(
                        status="failure",
                        error=f"连接成功但状态码异常: {response.status_code}"
                    )
        except Exception as e:
            proxy_connectivity_result = ProxyTestResult(status="failure", error=str(e))

    # Test 2: Target Site Reachability
    target_sites_results: Dict[str, ProxyTestResult] = {}
    test_domains = [
        "https://api.bilibili.com", "https://www.iqiyi.com", "https://v.qq.com",
        "https://youku.com", "https://www.mgtv.com", "https://ani.gamer.com.tw",
        "https://api.themoviedb.org", "https://api.bgm.tv", "https://movie.douban.com"
    ]
    
    async def test_domain(domain: str, client: httpx.AsyncClient) -> tuple[str, ProxyTestResult]:
        try:
            start_time = time.time()
            # 使用 HEAD 请求以提高效率，我们只关心连通性
            await client.head(domain, timeout=10.0)
            latency = (time.time() - start_time) * 1000
            return domain, ProxyTestResult(status="success", latency=latency)
        except Exception as e:
            # 简化错误信息，避免在UI上显示过长的堆栈跟踪
            error_str = f"{type(e).__name__}"
            return domain, ProxyTestResult(status="failure", error=error_str)

    async with httpx.AsyncClient(proxy=proxy_to_use, timeout=10.0) as client:
        tasks = [test_domain(domain, client) for domain in test_domains]
        results = await asyncio.gather(*tasks)
        for domain, result in results:
            target_sites_results[domain] = result

    return FullProxyTestResponse(
        proxy_connectivity=proxy_connectivity_result,
        target_sites=target_sites_results
    )

@router.get("/scrapers/{providerName}/config", response_model=Dict[str, Any], summary="获取指定搜索源的配置")
async def get_scraper_config(
    providerName: str,
    current_user: models.User = Depends(security.get_current_user),
    session: AsyncSession = Depends(get_db_session),
    manager: ScraperManager = Depends(get_scraper_manager)
):
    """
    获取单个搜索源的详细配置，包括其在 `scrapers` 表中的设置（如 useProxy）
    和在 `config` 表中的键值对（如 cookie）。
    """
    scraper_class = manager.get_scraper_class(providerName)
    if not scraper_class:
        raise HTTPException(status_code=404, detail="该搜索源不存在。")

    response_data = {}

    # 1. 从 scrapers 表获取 useProxy 设置
    scraper_settings = await crud.get_scraper_setting_by_name(session, providerName)
    response_data['useProxy'] = scraper_settings.get('useProxy', False) if scraper_settings else False

    # 2. 从 config 表获取其他可配置字段
    config_keys_snake = []
    if hasattr(scraper_class, 'configurable_fields'):
        config_keys_snake.extend(scraper_class.configurable_fields.keys())
    if getattr(scraper_class, 'is_loggable', False):
        config_keys_snake.append(f"scraper_{providerName}_log_responses")
    config_keys_snake.append(f"{providerName}_episode_blacklist_regex")

    # 辅助函数，用于将 snake_case 转换为 camelCase
    def to_camel(snake_str):
        components = snake_str.split('_')
        return components[0] + ''.join(x.title() for x in components[1:])

    for db_key in config_keys_snake:
        value = await crud.get_config_value(session, db_key, "")
        camel_key = to_camel(db_key)
        # 对布尔值进行特殊处理，以匹配前端Switch组件的期望
        if "log_responses" in db_key:
            response_data[camel_key] = value.lower() == 'true'
        else:
            response_data[camel_key] = value

    return response_data

@router.put("/scrapers/{providerName}/config", status_code=status.HTTP_204_NO_CONTENT, summary="更新指定搜索源的配置")
async def update_scraper_config(
    providerName: str,
    payload: Dict[str, Any],
    current_user: models.User = Depends(security.get_current_user),
    session: AsyncSession = Depends(get_db_session),
    config_manager: ConfigManager = Depends(get_config_manager),
    manager: ScraperManager = Depends(get_scraper_manager)
):
    """更新指定搜索源的配置，包括代理设置和其他可配置字段。"""
    try:
        scraper_class = manager.get_scraper_class(providerName)
        if not scraper_class:
            raise HTTPException(status_code=404, detail="该搜索源不存在。")

        # 1. 单独处理 useProxy 字段，它更新的是 scrapers 表
        if 'useProxy' in payload:
            use_proxy_value = bool(payload.pop('useProxy', False))
            await crud.update_scraper_proxy(session, providerName, use_proxy_value)

        # 2. 构建所有期望处理的配置键 (camelCase)
        expected_camel_keys = set()
        # 修正：定义正确的 to_camel 辅助函数
        def to_camel(snake_str):
            components = snake_str.split('_')
            return components[0] + ''.join(x.title() for x in components[1:])

        if hasattr(scraper_class, 'configurable_fields'):
            for snake_key in scraper_class.configurable_fields.keys():
                expected_camel_keys.add(to_camel(snake_key))
        
        if getattr(scraper_class, 'is_loggable', False):
            expected_camel_keys.add(f"scraper{providerName.capitalize()}LogResponses")
        
        expected_camel_keys.add(f"{providerName}EpisodeBlacklistRegex")

        # 3. 遍历 payload，只处理期望的键
        def to_snake(camel_str):
            s1 = re.sub('(.)([A-Z][a-z]+)', r'\1_\2', camel_str)
            return re.sub('([a-z0-9])([A-Z])', r'\1_\2', s1).lower()

        for camel_key, value in payload.items():
            if camel_key in expected_camel_keys:
                db_key = to_snake(camel_key)
                value_to_save = str(value) if not isinstance(value, bool) else str(value).lower()
                await crud.update_config_value(session, db_key, value_to_save)
                config_manager.invalidate(db_key)
        
        # 4. 在所有数据库操作完成后，统一提交事务
        await session.commit()
        logger.info(f"用户 '{current_user.username}' 更新了搜索源 '{providerName}' 的配置。")
    except Exception as e:
        await session.rollback()
        logger.error(f"更新搜索源 '{providerName}' 配置时出错: {e}", exc_info=True)
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="更新配置时发生内部错误。")

@router.get("/logs", response_model=List[str], summary="获取最新的服务器日志")
async def get_server_logs(current_user: models.User = Depends(security.get_current_user)):
    """获取存储在内存中的最新日志条目。"""
    return get_logs()

@router.get("/metadata/{provider}/search", response_model=List[models.MetadataDetailsResponse], summary="从元数据源搜索")
async def search_metadata(
    provider: str,
    keyword: str,
    mediaType: Optional[str] = Query(None),
    current_user: models.User = Depends(security.get_current_user),
    manager: MetadataSourceManager = Depends(get_metadata_manager)
):
    return await manager.search(provider, keyword, current_user, mediaType=mediaType)

@router.get("/metadata/{provider}/details/{item_id}", response_model=models.MetadataDetailsResponse, summary="获取元数据详情")
async def get_metadata_details(
    provider: str,
    item_id: str,
    mediaType: Optional[str] = Query(None),
    current_user: models.User = Depends(security.get_current_user),
    manager: MetadataSourceManager = Depends(get_metadata_manager)
):
    details = await manager.get_details(provider, item_id, current_user, mediaType=mediaType)
    if not details:
        raise HTTPException(status_code=404, detail="未找到详情")
    return details

@router.get("/metadata/{provider}/details/{mediaType}/{item_id}", response_model=models.MetadataDetailsResponse, summary="获取元数据详情 (带媒体类型)", include_in_schema=False)
async def get_metadata_details_with_type(
    provider: str,
    mediaType: str,
    item_id: str,
    current_user: models.User = Depends(security.get_current_user),
    manager: MetadataSourceManager = Depends(get_metadata_manager)
):
    """
    一个兼容性路由，允许将 mediaType 作为路径的一部分。
    """
    details = await manager.get_details(provider, item_id, current_user, mediaType=mediaType)
    if not details:
        raise HTTPException(status_code=404, detail="未找到详情")
    return details

@router.post("/metadata/{provider}/actions/{action_name}", summary="执行元数据源的自定义操作")
async def execute_metadata_action(
    provider: str,
    action_name: str,
    request: Request,
    payload: Optional[Dict[str, Any]] = Body(None),
    current_user: models.User = Depends(security.get_current_user),
    manager: MetadataSourceManager = Depends(get_metadata_manager)
):
    try:
        return await manager.execute_action(provider, action_name, payload or {}, current_user, request=request)
    except Exception as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))

@router.post("/scrapers/{providerName}/actions/{actionName}", summary="执行搜索源的自定义操作")
async def execute_scraper_action(
    providerName: str,
    actionName: str,
    payload: Dict[str, Any] = None, # FastAPI will parse JSON body into a dict
    current_user: models.User = Depends(security.get_current_user),
    manager: ScraperManager = Depends(get_scraper_manager)
):
    """
    执行指定搜索源的特定操作。
    例如，Bilibili的登录流程可以通过调用 'get_login_info', 'generate_qrcode', 'poll_login' 等操作来驱动。
    """
    try:
        scraper = manager.get_scraper(providerName)
        result = await scraper.execute_action(actionName, payload or {})
        return result
    except httpx.RequestError as e:
        # 新增：捕获所有 httpx 网络错误 (连接超时, 读取超时等)
        error_message = f"与 {providerName} 通信时发生网络错误: {e}"
        logger.error(f"执行搜索源 '{providerName}' 的操作 '{actionName}' 时出错: {error_message}")
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=error_message)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))
    except NotImplementedError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    except Exception as e:
        logger.error(f"执行搜索源 '{providerName}' 的操作 '{actionName}' 时出错: {e}", exc_info=True)
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="执行操作时发生内部错误。")

@router.post("/cache/clear", status_code=status.HTTP_200_OK, summary="清除所有缓存")

async def clear_all_caches(
    current_user: models.User = Depends(security.get_current_user),
    session: AsyncSession = Depends(get_db_session)
): #noqa
    """清除数据库中存储的所有缓存数据（如搜索结果、分集列表）。"""
    deleted_count = await crud.clear_all_cache(session)
    logger.info(f"用户 '{current_user.username}' 清除了所有缓存，共 {deleted_count} 条。")
    return {"message": f"成功清除了 {deleted_count} 条缓存记录。"}

@router.get("/tasks", response_model=List[models.TaskInfo], summary="获取所有后台任务的状态")
async def get_all_tasks(
    current_user: models.User = Depends(security.get_current_user),
    session: AsyncSession = Depends(get_db_session),
    search: Optional[str] = Query(None, description="按标题搜索"),
    status: Optional[str] = Query("all", description="按状态过滤: all, in_progress, completed")
):
    """获取后台任务的列表和状态，支持搜索和过滤。"""
    tasks = await crud.get_tasks_from_history(session, search, status)
    return [models.TaskInfo.model_validate(t) for t in tasks]


@router.post("/tasks/{task_id}/pause", status_code=status.HTTP_204_NO_CONTENT, summary="暂停一个正在运行的任务")
async def pause_task_endpoint(
    task_id: str,
    current_user: models.User = Depends(security.get_current_user),
    task_manager: TaskManager = Depends(get_task_manager)
):
    """暂停一个正在运行的任务。"""
    paused = await task_manager.pause_task(task_id)
    if not paused:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="任务未找到或无法暂停。")
    return


@router.post("/tasks/{task_id}/resume", status_code=status.HTTP_204_NO_CONTENT, summary="恢复一个已暂停的任务")
async def resume_task_endpoint(
    task_id: str,
    current_user: models.User = Depends(security.get_current_user),
    task_manager: TaskManager = Depends(get_task_manager)
):
    """恢复一个已暂停的任务。"""
    resumed = await task_manager.resume_task(task_id)
    if not resumed:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="任务未找到或无法恢复。")
    return


@router.post("/tasks/{task_id}/abort", status_code=status.HTTP_204_NO_CONTENT, summary="中止一个正在运行的任务")
async def abort_task_endpoint(
    task_id: str,
    current_user: models.User = Depends(security.get_current_user),
    task_manager: TaskManager = Depends(get_task_manager)
):
    """中止一个正在运行或暂停的任务。"""
    aborted = await task_manager.abort_current_task(task_id)
    if not aborted:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="任务未找到或无法中止。")
    return

@router.delete("/tasks/{task_id}", status_code=status.HTTP_204_NO_CONTENT, summary="删除一个历史任务")
async def delete_task_from_history_endpoint(
    task_id: str,
    current_user: models.User = Depends(security.get_current_user),
    session: AsyncSession = Depends(get_db_session),
    task_manager: TaskManager = Depends(get_task_manager)
):
    """从历史记录中删除一个任务。如果任务正在运行或暂停，会先尝试中止它。"""
    task = await crud.get_task_from_history_by_id(session, task_id)
    if not task:
        # 如果任务不存在，直接返回成功，因为最终状态是一致的
        return

    status = task['status']

    if status == TaskStatus.PENDING:
        await task_manager.cancel_pending_task(task_id)
    elif status in [TaskStatus.RUNNING, TaskStatus.PAUSED]:
        aborted = await task_manager.abort_current_task(task_id)
        if not aborted:
            # 这可能是一个竞态条件：在我们检查和中止之间，任务可能已经完成。
            # 重新检查数据库中的状态以确认。
            task_after_check = await crud.get_task_from_history_by_id(session, task_id)
            if task_after_check and task_after_check['status'] in [TaskStatus.RUNNING, TaskStatus.PAUSED]:
                # 如果它仍然在运行/暂停，说明中止失败，可能因为它不是当前任务。
                raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="中止任务失败，可能它不是当前正在执行的任务。")
            logger.info(f"任务 {task_id} 在中止前已完成，将直接删除历史记录。")

    deleted = await crud.delete_task_from_history(session, task_id)
    if not deleted:
        # 这不是一个严重错误，可能意味着任务在处理过程中已被删除。
        logger.info(f"在尝试删除时，任务 {task_id} 已不存在于历史记录中。")
        return
    logger.info(f"用户 '{current_user.username}' 删除了任务 ID: {task_id} (原状态: {status})。")
    return

@router.get("/tokens", response_model=List[models.ApiTokenInfo], summary="获取所有弹幕API Token")
async def get_all_api_tokens(
    current_user: models.User = Depends(security.get_current_user),
    session: AsyncSession = Depends(get_db_session)
):
    """获取所有为第三方播放器创建的 API Token。"""
    tokens = await crud.get_all_api_tokens(session)
    return [models.ApiTokenInfo.model_validate(t) for t in tokens]

@router.post("/tokens", response_model=models.ApiTokenInfo, status_code=status.HTTP_201_CREATED, summary="创建一个新的API Token")
async def create_new_api_token(
    token_data: models.ApiTokenCreate,
    current_user: models.User = Depends(security.get_current_user),
    session: AsyncSession = Depends(get_db_session)
):
    """创建一个新的、随机的 API Token。"""
    # 生成一个由大小写字母和数字组成的20位随机字符串
    alphabet = string.ascii_letters + string.digits
    new_token_str = ''.join(secrets.choice(alphabet) for _ in range(20))
    try:
        token_id = await crud.create_api_token(session, token_data.name, new_token_str, token_data.validityPeriod)
        # 重新从数据库获取以包含所有字段
        new_token = await crud.get_api_token_by_id(session, token_id)
        return models.ApiTokenInfo.model_validate(new_token)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(e))

@router.delete("/tokens/{token_id}", status_code=status.HTTP_204_NO_CONTENT, summary="删除一个API Token")
async def delete_api_token(
    token_id: int,
    current_user: models.User = Depends(security.get_current_user),
    session: AsyncSession = Depends(get_db_session)
):
    """根据ID删除一个 API Token。"""
    deleted = await crud.delete_api_token(session, token_id)
    if not deleted:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Token not found")
    return

@router.put("/tokens/{token_id}/toggle", status_code=status.HTTP_204_NO_CONTENT, summary="切换API Token的启用状态")
async def toggle_api_token_status(
    token_id: int,
    current_user: models.User = Depends(security.get_current_user),
    session: AsyncSession = Depends(get_db_session)
):
    """切换指定 API Token 的启用/禁用状态。"""
    toggled = await crud.toggle_api_token(session, token_id)
    if not toggled:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Token not found")
    return

@router.get("/config/{config_key}", response_model=Dict[str, str], summary="获取指定配置项的值")
async def get_config_item(
    config_key: str,
    current_user: models.User = Depends(security.get_current_user),
    session: AsyncSession = Depends(get_db_session)
):
    """获取数据库中单个配置项的值。"""
    value = await crud.get_config_value(session, config_key, "") # 默认为空字符串
    return {"key": config_key, "value": value}

@router.put("/config/{config_key}", status_code=status.HTTP_204_NO_CONTENT, summary="更新指定配置项的值")
async def update_config_item(
    config_key: str,
    payload: Dict[str, str],
    current_user: models.User = Depends(security.get_current_user),
    session: AsyncSession = Depends(get_db_session),
    config_manager: ConfigManager = Depends(get_config_manager)
):
    """更新数据库中单个配置项的值。"""
    value = payload.get("value")
    if value is None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Missing 'value' in request body")
    
    await crud.update_config_value(session, config_key, value)
    config_manager.invalidate(config_key)
    logger.info(f"用户 '{current_user.username}' 更新了配置项 '{config_key}'。")

@router.post("/config/webhookApiKey/regenerate", response_model=Dict[str, str], summary="重新生成Webhook API Key")
async def regenerate_webhook_api_key(
    current_user: models.User = Depends(security.get_current_user),
    session: AsyncSession = Depends(get_db_session),
    config_manager: ConfigManager = Depends(get_config_manager)
):
    """生成一个新的、随机的Webhook API Key并保存到数据库。"""
    alphabet = string.ascii_letters + string.digits
    new_key = ''.join(secrets.choice(alphabet) for _ in range(20))
    await crud.update_config_value(session, "webhookApiKey", new_key)
    config_manager.invalidate("webhookApiKey")
    logger.info(f"用户 '{current_user.username}' 重新生成了 Webhook API Key。")
    return {"key": "webhookApiKey", "value": new_key}

@router.post("/config/externalApiKey/regenerate", response_model=Dict[str, str], summary="重新生成外部API Key")
async def regenerate_external_api_key(
    current_user: models.User = Depends(security.get_current_user),
    session: AsyncSession = Depends(get_db_session),
    config_manager: ConfigManager = Depends(get_config_manager)
):
    """生成一个新的、随机的外部API Key并保存到数据库。"""
    alphabet = string.ascii_letters + string.digits
    new_key = ''.join(secrets.choice(alphabet) for _ in range(32)) # 增加长度以提高安全性
    await crud.update_config_value(session, "externalApiKey", new_key)
    config_manager.invalidate("externalApiKey")
    logger.info(f"用户 '{current_user.username}' 重新生成了外部 API Key。")
    return {"key": "externalApiKey", "value": new_key}

@router.get("/external-logs", response_model=List[models.ExternalApiLogInfo], summary="获取最新的外部API访问日志")
async def get_external_api_logs(
    current_user: models.User = Depends(security.get_current_user),
    session: AsyncSession = Depends(get_db_session)
):
    logs = await crud.get_external_api_logs(session)
    return [models.ExternalApiLogInfo.model_validate(log) for log in logs]

@router.get("/ua-rules", response_model=List[models.UaRule], summary="获取所有UA规则")
async def get_ua_rules(
    current_user: models.User = Depends(security.get_current_user),
    session: AsyncSession = Depends(get_db_session)
):
    rules = await crud.get_ua_rules(session)
    return [models.UaRule.model_validate(r) for r in rules]

class UaRuleCreate(models.BaseModel):
    uaString: str = Field(..., alias="ua_string")

@router.post("/ua-rules", response_model=models.UaRule, status_code=201, summary="添加UA规则")
async def add_ua_rule(
    ruleData: UaRuleCreate,
    currentUser: models.User = Depends(security.get_current_user),
    session: AsyncSession = Depends(get_db_session)
):
    try:
        rule_id = await crud.add_ua_rule(session, ruleData.uaString)
        # This is a bit inefficient but ensures we return the full object
        rules = await crud.get_ua_rules(session)
        new_rule = next((r for r in rules if r['id'] == rule_id), None)
        return models.UaRule.model_validate(new_rule)
    except Exception:
        raise HTTPException(status_code=409, detail="该UA规则已存在。")

@router.delete("/ua-rules/{ruleId}", status_code=204, summary="删除UA规则")
async def delete_ua_rule(
    ruleId: str,
    currentUser: models.User = Depends(security.get_current_user),
    session: AsyncSession = Depends(get_db_session)
):
    deleted = await crud.delete_ua_rule(session, ruleId)
    if not deleted:
        raise HTTPException(status_code=404, detail="找不到指定的规则ID。")

@router.get("/config/provider/{providerName}", response_model=Dict[str, Any], summary="获取指定元数据源的配置")
async def get_provider_settings(
    providerName: str,
    current_user: models.User = Depends(security.get_current_user),
    metadata_manager: MetadataSourceManager = Depends(get_metadata_manager)
):
    """获取指定元数据源的所有相关配置。"""
    try:
        return await metadata_manager.getProviderConfig(providerName)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))

@router.put("/config/provider/{providerName}", status_code=status.HTTP_204_NO_CONTENT, summary="更新指定元数据源的配置")
async def set_provider_settings(
    providerName: str,
    settings: Dict[str, Any],
    current_user: models.User = Depends(security.get_current_user),
    config_manager: ConfigManager = Depends(get_config_manager)
):
    """批量更新指定元数据源的相关配置。"""
    config_keys_map = {
        "tmdb": ["tmdbApiKey", "tmdbApiBaseUrl", "tmdbImageBaseUrl"],
        "bangumi": ["bangumiClientId", "bangumiClientSecret", "bangumiToken"],
        "douban": "doubanCookie", # 单值配置
        "tvdb": "tvdbApiKey",   # 单值配置
    }

    if providerName in ["douban", "tvdb"]:
        key = config_keys_map.get(providerName)
        if key:
            await config_manager.setValue(configKey=key, configValue=settings.get("value", ""))
    else:
        keys_to_update = config_keys_map.get(providerName, [])
        tasks = [
            config_manager.setValue(configKey=key, configValue=settings[key])
            for key in keys_to_update if key in settings
        ]
        if tasks:
            await asyncio.gather(*tasks)
    logger.info(f"用户 '{current_user.username}' 更新了元数据源 '{providerName}' 的配置。")

@router.get("/tokens/{tokenId}/logs", response_model=List[models.TokenAccessLog], summary="获取Token的访问日志")
async def get_token_logs(
    tokenId: int,
    current_user: models.User = Depends(security.get_current_user),
    session: AsyncSession = Depends(get_db_session)
):
    logs = await crud.get_token_access_logs(session, tokenId)
    return [models.TokenAccessLog.model_validate(log) for log in logs]

@router.get(
    "/comment/{episodeId}",
    response_model=models.CommentResponse,
    summary="获取指定分集的弹幕",
)
async def get_comments(
    episodeId: int,
    session: AsyncSession = Depends(get_db_session)
):
    # 检查episode是否存在，如果不存在则返回404
    if not await crud.check_episode_exists(session, episodeId):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Episode not found")

    comments_data = await crud.fetch_comments(session, episodeId)
    
    comments = [models.Comment(cid=item["cid"], p=item["p"], m=item["m"]) for item in comments_data]
    return models.CommentResponse(count=len(comments), comments=comments)

@router.get("/webhooks/available", response_model=List[str], summary="获取所有可用的Webhook类型")
async def get_available_webhook_types(
    current_user: models.User = Depends(security.get_current_user),
    webhook_manager: WebhookManager = Depends(get_webhook_manager)
):
    """获取所有已成功加载的、可供用户选择的Webhook处理器类型。"""
    return webhook_manager.get_available_handlers()

async def delete_bulk_episodes_task(episode_ids: List[int], session: AsyncSession, progress_callback: Callable):
    """后台任务：批量删除多个分集。"""
    total = len(episode_ids)
    deleted_count = 0
    for i, episode_id in enumerate(episode_ids):
        progress = int((i / total) * 100)
        await progress_callback(progress, f"正在删除分集 {i+1}/{total} (ID: {episode_id})...")
        try:
            episode = await session.get(orm_models.Episode, episode_id)
            if episode:
                await session.delete(episode)
                await session.commit()
                deleted_count += 1
        except Exception as e:
            logger.error(f"批量删除分集任务中，删除分集 (ID: {episode_id}) 失败: {e}", exc_info=True)
    raise TaskSuccess(f"批量删除完成，共处理 {total} 个，成功删除 {deleted_count} 个。")

async def generic_import_task(
    provider: str, # noqa: F821
    media_id: str,
    anime_title: str,
    media_type: str,
    season: int,
    year: Optional[int],
    current_episode_index: Optional[int],
    image_url: Optional[str],
    douban_id: Optional[str],
    tmdb_id: Optional[str],
    imdb_id: Optional[str],
    tvdb_id: Optional[str],
    bangumi_id: Optional[str],
    progress_callback: Callable,
    session: AsyncSession,
    manager: ScraperManager, 
    task_manager: TaskManager
):
    """
    后台任务：执行从指定数据源导入弹幕的完整流程。
    """
    # 重构导入逻辑以避免创建空条目
    scraper = manager.get_scraper(provider)
    normalized_title = anime_title.replace(":", "：")

    await progress_callback(10, "正在获取分集列表...")
    episodes = await scraper.get_episodes(
        media_id,
        target_episode_index=current_episode_index,
        db_media_type=media_type
    )
    if not episodes:
        msg = f"未能找到第 {current_episode_index} 集。" if current_episode_index else "未能获取到任何分集。"
        logger.warning(f"任务终止: {msg} (provider='{provider}', media_id='{media_id}')")
        raise TaskSuccess(msg)

    if media_type == "movie" and episodes:
        logger.info(f"检测到媒体类型为电影，将只处理第一个分集 '{episodes[0].title}'。")
        episodes = episodes[:1]

    anime_id: Optional[int] = None
    source_id: Optional[int] = None
    total_comments_added = 0
    total_episodes = len(episodes)

    for i, episode in enumerate(episodes):
        logger.info(f"--- 开始处理分集 {i+1}/{total_episodes}: '{episode.title}' (ID: {episode.episodeId}) ---")
        base_progress = 10 + int((i / total_episodes) * 85)
        await progress_callback(base_progress, f"正在处理: {episode.title} ({i+1}/{total_episodes})")

        async def sub_progress_callback(danmaku_progress: int, danmaku_description: str):
            current_total_progress = base_progress + (danmaku_progress / 100) * (85 / total_episodes)
            await progress_callback(current_total_progress, f"处理: {episode.title} - {danmaku_description}")

        comments = await scraper.get_comments(episode.episodeId, progress_callback=sub_progress_callback)

        if comments and anime_id is None:
            logger.info("首次成功获取弹幕，正在创建数据库主条目...")
            await progress_callback(base_progress + 1, "正在创建数据库主条目...")
            local_image_path = await download_image(image_url, session, manager, provider)
            anime_id = await crud.get_or_create_anime(session, normalized_title, media_type, season, image_url, local_image_path, year)
            await crud.update_metadata_if_empty(session, anime_id, tmdb_id, imdb_id, tvdb_id, douban_id)
            source_id = await crud.link_source_to_anime(session, anime_id, provider, media_id)
            logger.info(f"主条目创建完成 (Anime ID: {anime_id}, Source ID: {source_id})。")

        if anime_id and source_id:
            episode_db_id = await crud.create_episode_if_not_exists(session, anime_id, source_id, episode.episodeIndex, episode.title, episode.url, episode.episodeId)
            if not comments:
                logger.info(f"分集 '{episode.title}' (DB ID: {episode_db_id}) 未找到弹幕，但已创建分集记录。")
                continue
            added_count = await crud.bulk_insert_comments(session, episode_db_id, comments)
            total_comments_added += added_count
            logger.info(f"分集 '{episode.title}' (DB ID: {episode_db_id}) 新增 {added_count} 条弹幕。")
        else:
            logger.info(f"分集 '{episode.title}' 未找到弹幕，跳过创建主条目。")

    if total_comments_added == 0:
        raise TaskSuccess("导入完成，但未找到任何新弹幕。")
    else:
        raise TaskSuccess(f"导入完成，共新增 {total_comments_added} 条弹幕。")
    
async def edited_import_task(
    request_data: "EditedImportRequest",
    progress_callback: Callable,
    session: AsyncSession,
    manager: ScraperManager
):
    """后台任务：处理编辑后的导入请求。"""
    scraper = manager.get_scraper(request_data.provider)
    normalized_title = request_data.anime_title.replace(":", "：")
    
    episodes = request_data.episodes
    if not episodes:
        raise TaskSuccess("没有提供任何分集，任务结束。")

    anime_id: Optional[int] = None
    source_id: Optional[int] = None
    total_comments_added = 0
    total_episodes = len(episodes)

    for i, episode in enumerate(episodes):
        await progress_callback(10 + int((i / total_episodes) * 85), f"正在处理: {episode.title} ({i+1}/{total_episodes})")
        comments = await scraper.get_comments(episode.episodeId)

        if comments and anime_id is None:
            local_image_path = await download_image(request_data.image_url, session, request_data.provider)
            anime_id = await crud.get_or_create_anime(session, normalized_title, request_data.media_type, request_data.season, request_data.image_url, local_image_path)
            await crud.update_metadata_if_empty(session, anime_id, request_data.tmdb_id, None, None, request_data.douban_id)
            source_id = await crud.link_source_to_anime(session, anime_id, request_data.provider, request_data.media_id)

        if anime_id and source_id:
            episode_db_id = await crud.create_episode_if_not_exists(session, anime_id, source_id, episode.episodeIndex, episode.title, episode.url, episode.episodeId)
            if comments:
                total_comments_added += await crud.bulk_insert_comments(session, episode_db_id, comments)

    if total_comments_added == 0: raise TaskSuccess("导入完成，但未找到任何新弹幕。")
    else: raise TaskSuccess(f"导入完成，共新增 {total_comments_added} 条弹幕。")

async def full_refresh_task(source_id: int, session: AsyncSession, manager: ScraperManager, task_manager: TaskManager, progress_callback: Callable):
    """后台任务：全量刷新一个已存在的番剧，采用先获取后删除的安全策略。"""
    logger.info(f"开始全量刷新源 ID: {source_id}")
    source_info = await crud.get_anime_source_info(session, source_id)
    if not source_info:
        logger.error(f"刷新失败：在数据库中找不到源 ID: {source_id}")
        raise TaskSuccess("刷新失败: 找不到源信息")

    scraper = manager.get_scraper(source_info["providerName"])

    # 步骤 1: 获取所有新数据，但不写入数据库
    await progress_callback(10, "正在获取新分集列表...")
    new_episodes = await scraper.get_episodes(source_info["mediaId"])
    if not new_episodes:
        raise TaskSuccess("刷新失败：未能从源获取任何分集信息。旧数据已保留。")

    await progress_callback(20, f"获取到 {len(new_episodes)} 个新分集，正在获取弹幕...")
    
    new_data_package = []
    total_comments_fetched = 0
    total_episodes = len(new_episodes)

    for i, episode in enumerate(new_episodes):
        base_progress = 20 + int((i / total_episodes) * 70) if total_episodes > 0 else 90
        
        async def sub_progress_callback(danmaku_progress: int, danmaku_description: str):
            current_sub_progress = (danmaku_progress / 100) * (70 / total_episodes)
            await progress_callback(base_progress + current_sub_progress, f"处理: {episode.title} - {danmaku_description}")

        comments = await scraper.get_comments(episode.episodeId, progress_callback=sub_progress_callback)
        new_data_package.append((episode, comments))
        if comments:
            total_comments_fetched += len(comments)

    if total_comments_fetched == 0:
        raise TaskSuccess("刷新完成，但未找到任何新弹幕。旧数据已保留。")

    # 步骤 2: 数据获取成功，现在在一个事务中执行清空和写入操作
    await progress_callback(95, "数据获取成功，正在清空旧数据并写入新数据...")
    try:
        await crud.clear_source_data(session, source_id)
        
        for episode_info, comments in new_data_package:
            episode_db_id = await crud.create_episode_if_not_exists(
                session, source_info["animeId"], source_id, 
                episode_info.episodeIndex, episode_info.title, 
                episode_info.url, episode_info.episodeId
            )
            if comments:
                await crud.bulk_insert_comments(session, episode_db_id, comments)
        
        await session.commit()
        raise TaskSuccess(f"全量刷新完成，共导入 {len(new_episodes)} 个分集，{total_comments_fetched} 条弹幕。")
    except Exception as e:
        await session.rollback()
        logger.error(f"全量刷新源 {source_id} 时数据库写入失败: {e}", exc_info=True)
        raise

async def delete_bulk_sources_task(source_ids: List[int], session: AsyncSession, progress_callback: Callable):
    """Background task to delete multiple sources."""
    total = len(source_ids)
    deleted_count = 0
    for i, source_id in enumerate(source_ids):
        progress = int((i / total) * 100)
        await progress_callback(progress, f"正在删除源 {i+1}/{total} (ID: {source_id})...")
        try:
            source = await session.get(orm_models.AnimeSource, source_id)
            if source:
                await session.delete(source)
                await session.commit()
                deleted_count += 1
        except Exception as e:
            logger.error(f"批量删除源任务中，删除源 (ID: {source_id}) 失败: {e}", exc_info=True)
            # Continue to the next one
    raise TaskSuccess(f"批量删除完成，共处理 {total} 个，成功删除 {deleted_count} 个。")

async def refresh_episode_task(episode_id: int, session: AsyncSession, manager: ScraperManager, progress_callback: Callable):
    """后台任务：刷新单个分集的弹幕"""
    logger.info(f"开始刷新分集 ID: {episode_id}")
    try:
        await progress_callback(0, "正在获取分集信息...")
        # 1. 获取分集的源信息
        info = await crud.get_episode_provider_info(session, episode_id)
        if not info or not info.get("provider_name") or not info.get("provider_episode_id"):
            logger.error(f"刷新失败：在数据库中找不到分集 ID: {episode_id} 的源信息")
            progress_callback(100, "失败: 找不到源信息")
            return

        provider_name = info["provider_name"]
        provider_episode_id = info["provider_episode_id"]
        scraper = manager.get_scraper(provider_name)

        # 3. 获取新弹幕并插入
        await progress_callback(30, "正在从源获取新弹幕...")
        
        async def sub_progress_callback(danmaku_progress: int, danmaku_description: str):
            # 30% for setup, 65% for download, 5% for db write
            current_total_progress = 30 + (danmaku_progress / 100) * 65
            await progress_callback(current_total_progress, danmaku_description)

        all_comments_from_source = await scraper.get_comments(provider_episode_id, progress_callback=sub_progress_callback)

        if not all_comments_from_source:
            await crud.update_episode_fetch_time(session, episode_id)
            raise TaskSuccess("未找到任何弹幕。")

        # 新增：在插入前，先筛选出数据库中不存在的新弹幕，以避免产生大量的“重复条目”警告。
        await progress_callback(95, "正在比对新旧弹幕...")
        existing_cids = await crud.get_existing_comment_cids(session, episode_id)
        new_comments = [c for c in all_comments_from_source if str(c.get('cid')) not in existing_cids]

        if not new_comments:
            await crud.update_episode_fetch_time(session, episode_id)
            raise TaskSuccess("刷新完成，没有新增弹幕。")

        await progress_callback(96, f"正在写入 {len(new_comments)} 条新弹幕...")
        added_count = await crud.bulk_insert_comments(session, episode_id, new_comments)
        await crud.update_episode_fetch_time(session, episode_id)
        logger.info(f"分集 ID: {episode_id} 刷新完成，新增 {added_count} 条弹幕。")
        raise TaskSuccess(f"刷新完成，新增 {added_count} 条弹幕。")
    except TaskSuccess:
        # 任务成功完成，直接重新抛出，由 TaskManager 处理
        raise
    except Exception as e:
        logger.error(f"刷新分集 ID: {episode_id} 时发生严重错误: {e}", exc_info=True)
        raise # Re-raise so the task manager catches it and marks as FAILED

async def reorder_episodes_task(source_id: int, session: AsyncSession, progress_callback: Callable):
    """后台任务：重新编号一个源的所有分集。"""
    logger.info(f"开始重整源 ID: {source_id} 的分集顺序。")
    await progress_callback(0, "正在获取分集列表...")
    
    try:
        # 获取所有分集，按现有顺序排序
        episodes = await crud.get_episodes_for_source(session, source_id)
        if not episodes:
            raise TaskSuccess("没有找到分集，无需重整。")

        total_episodes = len(episodes)
        updated_count = 0
        
        # 开始事务
        try:
            for i, episode_data in enumerate(episodes):
                new_index = i + 1
                if episode_data['episode_index'] != new_index:
                    await session.execute(update(orm_models.Episode).where(orm_models.Episode.id == episode_data['id']).values(episode_index=new_index))
                    updated_count += 1
                await progress_callback(int(((i + 1) / total_episodes) * 100), f"正在处理分集 {i+1}/{total_episodes}...")
            await session.commit()
        except Exception as e:
            await session.rollback()
            logger.error(f"重整源 ID {source_id} 时数据库事务失败: {e}", exc_info=True)
            raise
        raise TaskSuccess(f"重整完成，共更新了 {updated_count} 个分集的集数。")
    except Exception as e:
        logger.error(f"重整分集任务 (源ID: {source_id}) 失败: {e}", exc_info=True)
        raise

async def incremental_refresh_task(sourceId: int, nextEpisodeIndex: int, session: AsyncSession, manager: ScraperManager, task_manager: TaskManager, progress_callback: Callable, animeTitle: str):
    """后台任务：增量刷新一个已存在的番剧。"""
    logger.info(f"开始增量刷新源 ID: {sourceId}，尝试获取第{nextEpisodeIndex}集")
    source_info = await crud.get_anime_source_info(session, sourceId)
    if not source_info:
        progress_callback(100, "失败: 找不到源信息")
        logger.error(f"刷新失败：在数据库中找不到源 ID: {sourceId}")
        return
    try:
        # 重新执行通用导入逻辑, 只导入指定的一集
        await generic_import_task(
            provider=source_info["providerName"], media_id=source_info["mediaId"],
            anime_title=animeTitle, media_type=source_info["type"],
            season=source_info.get("season", 1), year=source_info.get("year"),
            current_episode_index=nextEpisodeIndex, image_url=None,
            douban_id=None, tmdb_id=source_info.get("tmdbId"),
            imdb_id=None, tvdb_id=None, bangumi_id=source_info.get("bangumiId"),
            progress_callback=progress_callback,
            session=session, manager=manager, task_manager=task_manager)
    except Exception as e:
        logger.error(f"增量刷新源任务 (ID: {sourceId}) 失败: {e}", exc_info=True)
        raise

class ManualImportRequest(models.BaseModel):
    title: str
    episode_index: int = Field(..., alias="episodeIndex")
    url: str

    class Config:
        populate_by_name = True

@router.post("/library/source/{source_id}/manual-import", status_code=status.HTTP_202_ACCEPTED, summary="手动导入单个分集弹幕")
async def manual_import_episode(
    source_id: int,
    request_data: ManualImportRequest,
    current_user: models.User = Depends(security.get_current_user),
    session: AsyncSession = Depends(get_db_session),
    scraper_manager: ScraperManager = Depends(get_scraper_manager),
    task_manager: TaskManager = Depends(get_task_manager),
    rate_limiter: RateLimiter = Depends(get_rate_limiter)
):
    """提交一个后台任务，从给定的URL手动导入弹幕。"""
    source_info = await crud.get_anime_source_info(session, source_id)
    if not source_info:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Source not found")

    provider_name = source_info['providerName']
    
    url_prefixes = {
        'bilibili': 'bilibili.com', 'tencent': 'v.qq.com', 'iqiyi': 'iqiyi.com',
        'youku': 'youku.com', 'mgtv': 'mgtv.com', 'acfun': 'acfun.cn', 'renren': 'rrsp.com.cn'
    }
    expected_prefix = url_prefixes.get(provider_name)
    if not expected_prefix or expected_prefix not in request_data.url:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"提供的URL与当前源 '{provider_name}' 不匹配。")

    task_title = f"手动导入: {source_info['title']} - {request_data.title} - [{provider_name}]"
    task_coro = lambda session, callback: tasks.manual_import_task(
        sourceId=source_id, animeId=source_info['animeId'], title=request_data.title, episodeIndex=request_data.episode_index,
        url=request_data.url, providerName=provider_name,
        progress_callback=callback, session=session, manager=scraper_manager, rate_limiter=rate_limiter
    )
    task_id, _ = await task_manager.submit_task(task_coro, task_title)
    return {"message": f"手动导入任务 '{task_title}' 已提交。", "taskId": task_id}

async def manual_import_task(
    source_id: int, title: str, episode_index: int, url: str, provider_name: str,
    progress_callback: Callable, session: AsyncSession, manager: ScraperManager
):
    """后台任务：从URL手动导入弹幕。"""
    logger.info(f"开始手动导入任务: source_id={source_id}, title='{title}', url='{url}'")
    await progress_callback(10, "正在准备导入...")
    
    try:
        scraper = manager.get_scraper(provider_name)
        
        provider_episode_id = None
        if hasattr(scraper, 'get_ids_from_url'): provider_episode_id = await scraper.get_ids_from_url(url)
        elif hasattr(scraper, 'get_danmaku_id_from_url'): provider_episode_id = await scraper.get_danmaku_id_from_url(url)
        elif hasattr(scraper, 'get_tvid_from_url'): provider_episode_id = await scraper.get_tvid_from_url(url)
        elif hasattr(scraper, 'get_vid_from_url'): provider_episode_id = await scraper.get_vid_from_url(url)
        
        if not provider_episode_id: raise ValueError(f"无法从URL '{url}' 中解析出有效的视频ID。")

        # 修正：处理 Bilibili 和 MGTV 返回的字典ID，并将其格式化为 get_comments 期望的字符串格式。
        episode_id_for_comments = provider_episode_id
        if isinstance(provider_episode_id, dict):
            if provider_name == 'bilibili':
                episode_id_for_comments = f"{provider_episode_id.get('aid')},{provider_episode_id.get('cid')}"
            elif provider_name == 'mgtv':
                # MGTV 的 get_comments 期望 "cid,vid"
                episode_id_for_comments = f"{provider_episode_id.get('cid')},{provider_episode_id.get('vid')}"
            else:
                # 对于其他可能的字典返回，将其字符串化
                episode_id_for_comments = str(provider_episode_id)

        await progress_callback(20, f"已解析视频ID: {episode_id_for_comments}")
        comments = await scraper.get_comments(episode_id_for_comments, progress_callback=progress_callback)
        if not comments: raise TaskSuccess("未找到任何弹幕。")

        await progress_callback(90, "正在写入数据库...")
        episode_db_id = await crud.get_or_create_episode(session, source_id, episode_index, title, url, episode_id_for_comments)
        added_count = await crud.bulk_insert_comments(session, episode_db_id, comments)
        raise TaskSuccess(f"手动导入完成，新增 {added_count} 条弹幕。")
    except TaskSuccess:
        raise
    except Exception as e:
        logger.error(f"手动导入任务失败: {e}", exc_info=True)
        raise

@router.post("/import", status_code=status.HTTP_202_ACCEPTED, summary="从指定数据源导入弹幕", response_model=UITaskResponse)
async def import_from_provider(
    request_data: models.ImportRequest,
    current_user: models.User = Depends(security.get_current_user),
    session: AsyncSession = Depends(get_db_session),
    scraper_manager: ScraperManager = Depends(get_scraper_manager),
    task_manager: TaskManager = Depends(get_task_manager),
    rate_limiter: RateLimiter = Depends(get_rate_limiter),
    metadata_manager: MetadataSourceManager = Depends(get_metadata_manager)
):
    try:
        # 在启动任务前检查provider是否存在
        scraper_manager.get_scraper(request_data.provider)
        logger.info(f"用户 '{current_user.username}' 正在从 '{request_data.provider}' 导入 '{request_data.animeTitle}' (media_id={request_data.mediaId})")
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))

    # 只有在全量导入（非单集导入）时才执行此检查
    if request_data.currentEpisodeIndex is None:
        source_exists = await crud.check_source_exists_by_media_id(session, request_data.provider, request_data.mediaId)
        if source_exists:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="该数据源已存在于弹幕库中，无需重复导入。"
            )

    # 创建一个将传递给任务管理器的协程工厂 (lambda)
    task_coro = lambda session, callback: tasks.generic_import_task(
        provider=request_data.provider,
        mediaId=request_data.mediaId,
        animeTitle=request_data.animeTitle,
        mediaType=request_data.type,
        season=request_data.season,
        year=request_data.year,
        currentEpisodeIndex=request_data.currentEpisodeIndex,
        imageUrl=request_data.imageUrl,
        doubanId=request_data.doubanId,
        tmdbId=request_data.tmdbId,
        imdbId=None, 
        tvdbId=None, # 手动导入时这些ID为空,
        bangumiId=request_data.bangumiId,
        metadata_manager=metadata_manager,
        task_manager=task_manager, # 传递 task_manager
        progress_callback=callback,
        session=session,
        manager=scraper_manager,
        rate_limiter=rate_limiter
    )
    
    # 构造任务标题
    task_title = f"导入: {request_data.animeTitle} ({request_data.provider})"
    # 如果是电视剧且指定了单集导入，则在标题中追加季和集信息
    if request_data.type == "tv_series" and request_data.currentEpisodeIndex is not None and request_data.season is not None:
        task_title += f" - S{request_data.season:02d}E{request_data.currentEpisodeIndex:02d}"

    # 提交任务并获取任务ID
    task_id, _ = await task_manager.submit_task(task_coro, task_title)

    return {"message": f"'{request_data.animeTitle}' 的导入任务已提交。请在任务管理器中查看进度。", "taskId": task_id}

@router.post("/import/edited", status_code=status.HTTP_202_ACCEPTED, summary="导入编辑后的分集列表", response_model=UITaskResponse)
async def import_edited_episodes(
    request_data: models.EditedImportRequest,
    current_user: models.User = Depends(security.get_current_user),
    task_manager: TaskManager = Depends(get_task_manager),
    scraper_manager: ScraperManager = Depends(get_scraper_manager),
    rate_limiter: RateLimiter = Depends(get_rate_limiter),
    metadata_manager: MetadataSourceManager = Depends(get_metadata_manager)
):
    """提交一个后台任务，使用用户在前端编辑过的分集列表进行导入。"""
    task_title = f"编辑后导入: {request_data.animeTitle} ({request_data.provider})"
    task_coro = lambda session, callback: tasks.edited_import_task(
        request_data=request_data,
        progress_callback=callback,
        session=session,
        manager=scraper_manager,
        rate_limiter=rate_limiter,
        metadata_manager=metadata_manager
    )
    task_id, _ = await task_manager.submit_task(task_coro, task_title)
    return {"message": f"'{request_data.animeTitle}' 的编辑导入任务已提交。", "taskId": task_id}

@auth_router.post("/token", response_model=models.Token, summary="用户登录获取令牌")
async def login_for_access_token(
    form_data: OAuth2PasswordRequestForm = Depends(),
    session: AsyncSession = Depends(get_db_session)
):
    user = await crud.get_user_by_username(session, form_data.username)
    if not user or not security.verify_password(form_data.password, user["hashedPassword"]):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect username or password",
            headers={"WWW-Authenticate": "Bearer"},
        )

    access_token = await security.create_access_token(
        data={"sub": user["username"]}, session=session
    )
    # 更新用户的登录信息
    await crud.update_user_login_info(session, user["username"], access_token)

    return {"accessToken": access_token, "tokenType": "bearer"}


@auth_router.get("/users/me", response_model=models.User, summary="获取当前用户信息")
async def read_users_me(current_user: models.User = Depends(security.get_current_user)):
    return current_user

@auth_router.post("/logout", status_code=status.HTTP_204_NO_CONTENT, summary="用户登出")
async def logout():
    """
    用户登出。前端应清除本地存储的token。
    """
    return

@router.get("/scheduled-tasks/available-jobs", response_model=List[api_models.AvailableJobInfo])
async def get_available_jobs(request: Request):
    """获取所有已加载的可用任务类型及其名称。"""
    scheduler_manager = request.app.state.scheduler_manager
    jobs = scheduler_manager.get_available_jobs()
    logger.info(f"可用的任务类型: {jobs}")
    return jobs
@router.get("/scheduled-tasks", response_model=List[models.ScheduledTaskInfo], summary="获取所有定时任务")
async def get_all_scheduled_tasks(
    session: AsyncSession = Depends(get_db_session),
    current_user: models.User = Depends(security.get_current_user)
):
    """
    获取所有已配置的定时任务列表。
    此实现确保即使没有任务，也总是返回一个空的JSON数组 `[]`，而不是 `null` 或空白响应。
    """
    tasks = await crud.get_scheduled_tasks(session)
    # 关键修复：确保在没有任务时返回一个空列表，而不是 None
    return tasks or []


# --- Scheduled Tasks API ---

@router.get("/scheduled-tasks", response_model=List[models.ScheduledTaskInfo], summary="获取所有定时任务")
async def get_scheduled_tasks(
    current_user: models.User = Depends(security.get_current_user),
    scheduler: SchedulerManager = Depends(get_scheduler_manager)
):
    tasks = await scheduler.get_all_tasks()
    return [models.ScheduledTaskInfo.model_validate(t) for t in tasks]

@router.post("/scheduled-tasks", response_model=models.ScheduledTaskInfo, status_code=201, summary="创建定时任务")
async def create_scheduled_task(
    task_data: models.ScheduledTaskCreate,
    current_user: models.User = Depends(security.get_current_user),
    scheduler: SchedulerManager = Depends(get_scheduler_manager)
):
    try:
        new_task = await scheduler.add_task(task_data.name, task_data.jobType, task_data.cronExpression, task_data.isEnabled)
        return models.ScheduledTaskInfo.model_validate(new_task)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"创建定时任务失败: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="创建定时任务时发生内部错误")

class ImportFromUrlRequest(models.BaseModel):
    provider: str
    url: str
    title: str
    media_type: str
    season: int

@router.post("/import-from-url", status_code=status.HTTP_202_ACCEPTED, summary="从URL导入弹幕", response_model=UITaskResponse)
async def import_from_url(
    request_data: ImportFromUrlRequest,
    current_user: models.User = Depends(security.get_current_user),
    session: AsyncSession = Depends(get_db_session),
    scraper_manager: ScraperManager = Depends(get_scraper_manager),
    task_manager: TaskManager = Depends(get_task_manager),
    rate_limiter: RateLimiter = Depends(get_rate_limiter),
    metadata_manager: MetadataSourceManager = Depends(get_metadata_manager)
):
    provider = request_data.provider
    url = request_data.url
    title = request_data.title
    
    try:
        scraper = scraper_manager.get_scraper(provider)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))

    media_id_for_scraper = None

    try:
        if provider == 'bilibili':
            bvid_match = re.search(r'video/(BV[a-zA-Z0-9]+)', url)
            ssid_match = re.search(r'bangumi/play/ss(\d+)', url)
            epid_match = re.search(r'bangumi/play/ep(\d+)', url)
            if ssid_match:
                media_id_for_scraper = f"ss{ssid_match.group(1)}"
            elif bvid_match:
                media_id_for_scraper = f"bv{bvid_match.group(1)}"
            elif epid_match:
                async with httpx.AsyncClient() as client:
                    resp = await client.get(url, headers={"User-Agent": "Mozilla/5.0"}, follow_redirects=True)
                    resp.raise_for_status()
                    html_text = resp.text
                    ssid_match_from_page = re.search(r'"season_id":(\d+)', html_text)
                    if ssid_match_from_page:
                        media_id_for_scraper = f"ss{ssid_match_from_page.group(1)}"
        elif provider == 'tencent':
            cid_match = re.search(r'/cover/([^/]+)', url)
            if cid_match:
                media_id_for_scraper = cid_match.group(1)
        elif provider == 'iqiyi':
            linkid_match = re.search(r'v_(\w+)\.html', url)
            if linkid_match:
                media_id_for_scraper = linkid_match.group(1)
        elif provider == 'youku':
            async with httpx.AsyncClient() as client:
                resp = await client.get(url, headers={"User-Agent": "Mozilla/5.0"}, follow_redirects=True)
                resp.raise_for_status()
                html_text = resp.text
                showid_match = re.search(r'showid:"(\d+)"', html_text)
                if showid_match:
                    media_id_for_scraper = showid_match.group(1)
        elif provider == 'mgtv':
            cid_match = re.search(r'/b/(\d+)/', url)
            if cid_match:
                media_id_for_scraper = cid_match.group(1)
    except Exception as e:
        logger.error(f"从URL解析媒体ID时出错: {e}", exc_info=True)
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="从URL解析媒体ID时出错")

    if not media_id_for_scraper:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"无法从URL '{url}' 中为提供商 '{provider}' 解析出媒体ID。")

    task_coro = lambda session, callback: generic_import_task(
        provider=provider, media_id=media_id_for_scraper, anime_title=title, # type: ignore
        media_type=request_data.media_type, season=request_data.season,
        current_episode_index=None, image_url=None, douban_id=None, tmdb_id=None, imdb_id=None, tvdb_id=None, bangumi_id=None,
        metadata_manager=metadata_manager,
        progress_callback=callback, session=session, manager=scraper_manager, task_manager=task_manager,
        rate_limiter=rate_limiter
    )
    
    task_title = f"URL导入: {title} ({provider})"
    task_id, _ = await task_manager.submit_task(task_coro, task_title)

    return {"message": f"'{title}' 的URL导入任务已提交。", "taskId": task_id}

@router.put("/scheduled-tasks/{taskId}", response_model=models.ScheduledTaskInfo, summary="更新定时任务")
async def update_scheduled_task(
    taskId: str,
    task_data: models.ScheduledTaskUpdate,
    current_user: models.User = Depends(security.get_current_user),
    scheduler: SchedulerManager = Depends(get_scheduler_manager)
):
    updated_task = await scheduler.update_task(taskId, task_data.name, task_data.cronExpression, task_data.isEnabled)
    if not updated_task:
        raise HTTPException(status_code=404, detail="找不到指定的任务ID")
    return models.ScheduledTaskInfo.model_validate(updated_task)

@router.delete("/scheduled-tasks/{taskId}", status_code=204, summary="删除定时任务")
async def delete_scheduled_task(taskId: str, current_user: models.User = Depends(security.get_current_user), scheduler: SchedulerManager = Depends(get_scheduler_manager)):
    await scheduler.delete_task(taskId)

@router.post("/scheduled-tasks/{taskId}/run", status_code=202, summary="立即运行一次定时任务")
async def run_scheduled_task_now(taskId: str, current_user: models.User = Depends(security.get_current_user), scheduler: SchedulerManager = Depends(get_scheduler_manager)):
    try:
        await scheduler.run_task_now(taskId)
        return {"message": "任务已触发运行"}
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))

@auth_router.put("/users/me/password", status_code=status.HTTP_204_NO_CONTENT, summary="修改当前用户密码")
async def change_current_user_password(
    password_data: models.PasswordChange,
    current_user: models.User = Depends(security.get_current_user),
    session: AsyncSession = Depends(get_db_session)
):
    # 1. 从数据库获取完整的用户信息，包括哈希密码
    user_in_db = await crud.get_user_by_username(session, current_user.username)
    if not user_in_db:
        # 理论上不会发生，因为 get_current_user 已经验证过
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

    # 2. 验证旧密码是否正确
    if not security.verify_password(password_data.oldPassword, user_in_db["hashedPassword"]):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Incorrect old password")

    # 3. 更新密码
    new_hashed_password = security.get_password_hash(password_data.newPassword)
    await crud.update_user_password(session, current_user.username, new_hashed_password)

# --- Rate Limiter API ---

class RateLimitProviderStatus(BaseModel):
    providerName: str
    requestCount: int
    quota: Union[int, str]  # Can be a number or "∞"

class RateLimitStatusResponse(BaseModel):
    globalEnabled: bool
    globalRequestCount: int
    globalLimit: int
    globalPeriod: str
    secondsUntilReset: int
    providers: List[RateLimitProviderStatus]

@router.get("/rate-limit/status", response_model=RateLimitStatusResponse, summary="获取所有流控规则的状态")
async def get_rate_limit_status(
    session: AsyncSession = Depends(get_db_session),
    config_manager: ConfigManager = Depends(get_config_manager),
    scraper_manager: ScraperManager = Depends(get_scraper_manager),
    rate_limiter: RateLimiter = Depends(get_rate_limiter)
):
    """获取所有流控规则的当前状态，包括全局和各源的配额使用情况。"""
    # 在获取状态前，先触发一次全局流控的检查，这会强制重置过期的计数器
    try:
        await rate_limiter.check("__global__")
    except RateLimitExceededError:
        # 我们只关心检查和重置的副作用，不关心它是否真的超限，所以忽略此错误
        pass
    except Exception as e:
        # 记录其他潜在错误，但不中断状态获取
        logger.error(f"在获取流控状态时，检查全局流控失败: {e}")

    global_enabled_str = await config_manager.get("globalRateLimitEnabled", "true")
    global_enabled = global_enabled_str.lower() == 'true'
    global_limit_str = await config_manager.get("globalRateLimitCount", "50")
    global_limit = int(global_limit_str) if global_limit_str.isdigit() else 50
    global_period = await config_manager.get("globalRateLimitPeriod", "hour")
    period_seconds = {"second": 1, "minute": 60, "hour": 3600, "day": 86400}.get(global_period, 3600)

    all_states = await crud.get_all_rate_limit_states(session)
    states_map = {s.providerName: s for s in all_states}

    global_state = states_map.get("__global__")
    seconds_until_reset = 0
    if global_state:
        # crud.py 中已确保 lastResetTime 是 aware datetime
        time_since_reset = datetime.now(timezone.utc) - global_state.lastResetTime
        seconds_until_reset = max(0, int(period_seconds - time_since_reset.total_seconds()))

    provider_items = []
    # 修正：从数据库获取所有已配置的搜索源，而不是调用一个不存在的方法
    all_scrapers = await crud.get_all_scraper_settings(session)
    for scraper_setting in all_scrapers:
        provider_name = scraper_setting['providerName']
        provider_state = states_map.get(provider_name)
        
        quota: Union[int, str] = "∞"
        try:
            scraper_instance = scraper_manager.get_scraper(provider_name)
            provider_quota = getattr(scraper_instance, 'rate_limit_quota', None)
            if provider_quota is not None and provider_quota > 0:
                quota = provider_quota
        except ValueError:
            pass

        provider_items.append(RateLimitProviderStatus(
            providerName=provider_name,
            requestCount=provider_state.requestCount if provider_state else 0,
            quota=quota
        ))

    return RateLimitStatusResponse(
        globalEnabled=global_enabled,
        globalRequestCount=global_state.requestCount if global_state else 0,
        globalLimit=global_limit,
        globalPeriod=global_period,
        secondsUntilReset=seconds_until_reset,
        providers=provider_items
    )
