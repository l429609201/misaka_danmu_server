"""
Source相关的API端点
"""
import hashlib
import logging
from datetime import datetime
from typing import Optional, List

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from src import security, tasks
from src.db import crud, models, get_db_session, ConfigManager, orm_models
from src.core.timezone import get_now
from src.services import TaskManager, ScraperManager, MetadataSourceManager, SchedulerManager
from src.rate_limiter import RateLimiter

from src.api.dependencies import (
    get_scraper_manager, get_task_manager, get_scheduler_manager,
    get_metadata_manager, get_config_manager, get_rate_limiter,
    get_title_recognition_manager
)
from .models import UITaskResponse, BulkDeleteRequest

logger = logging.getLogger(__name__)

router = APIRouter()

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



@router.delete("/library/source/{sourceId}", status_code=status.HTTP_202_ACCEPTED, summary="提交删除指定数据源的任务")
async def delete_source_from_anime(
    sourceId: int,
    deleteFiles: bool = Query(True, description="是否同时删除弹幕XML文件"),
    current_user: models.User = Depends(security.get_current_user),
    session: AsyncSession = Depends(get_db_session),
    task_manager: TaskManager = Depends(get_task_manager)
):
    """提交一个后台任务来删除一个数据源及其所有关联的分集和弹幕。"""
    source_info = await crud.get_anime_source_info(session, sourceId)
    if not source_info:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Source not found")

    task_title = f"删除源: {source_info['title']} ({source_info['providerName']})"
    if not deleteFiles:
        task_title += " (保留文件)"
    unique_key = f"delete-source-{sourceId}"
    task_coro = lambda session, callback: tasks.delete_source_task(sourceId, session, callback, delete_files=deleteFiles)
    task_id, _ = await task_manager.submit_task(task_coro, task_title, unique_key=unique_key, run_immediately=True)

    logger.info(f"用户 '{current_user.username}' 提交了删除源 ID: {sourceId} 的任务 (Task ID: {task_id})，deleteFiles={deleteFiles}。")
    return {"message": f"删除源 '{source_info['providerName']}' 的任务已提交。", "taskId": task_id}



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
    """切换指定数据源的定时增量更新的启用/禁用状态。同一番剧下只能有一个源开启追更。"""
    new_state = await crud.toggle_source_incremental_refresh(session, sourceId)
    if new_state is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Source not found")
    logger.info(f"用户 '{current_user.username}' 切换了源 ID {sourceId} 的追更状态为 {new_state}。")


@router.get("/library/source/{sourceId}/episodes", response_model=models.PaginatedEpisodesResponse, summary="获取数据源的所有分集")
async def get_source_episodes(
    sourceId: int,
    page: int = Query(1, ge=1, description="页码"),
    pageSize: int = Query(25, ge=1, description="每页数量"),
    current_user: models.User = Depends(security.get_current_user),
    session: AsyncSession = Depends(get_db_session)
):
    """获取指定数据源下的所有已收录分集列表。"""
    paginated_result = await crud.get_episodes_for_source(session, sourceId, page, pageSize)
    # 修正：返回完整的分页响应对象
    return models.PaginatedEpisodesResponse(
        total=paginated_result["total"],
        list=paginated_result.get("episodes", [])
    )



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
    task_id, _ = await task_manager.submit_task(
        task_coro, task_title, queue_type="management",
        task_type="reorder_episodes",
        task_parameters={"sourceId": sourceId}
    )

    logger.info(f"用户 '{current_user.username}' 提交了重整源 ID: {sourceId} 集数的任务 (Task ID: {task_id})。")
    return {"message": f"重整集数任务 '{task_title}' 已提交。", "taskId": task_id}



@router.post("/library/source/{sourceId}/refresh", status_code=status.HTTP_202_ACCEPTED, summary="刷新指定数据源 (全量或增量)", response_model=UITaskResponse)
async def refresh_anime(
    sourceId: int,
    mode: str = Query("full", description="刷新模式: 'full' (全量) 或 'incremental' (增量)"),
    current_user: models.User = Depends(security.get_current_user),
    session: AsyncSession = Depends(get_db_session),
    scraper_manager: ScraperManager = Depends(get_scraper_manager),
    task_manager: TaskManager = Depends(get_task_manager),
    rate_limiter: RateLimiter = Depends(get_rate_limiter),
    metadata_manager: MetadataSourceManager = Depends(get_metadata_manager),
    config_manager: ConfigManager = Depends(get_config_manager),
    title_recognition_manager = Depends(get_title_recognition_manager)
):
    """
    为指定的数据源启动一个刷新任务。
    - full: 清空并重新抓取所有分集和弹幕。
    - incremental: 尝试抓取最新一集。
    """
    source_info = await crud.get_anime_source_info(session, sourceId)
    if not source_info or not source_info.get("providerName") or not source_info.get("mediaId"):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Anime not found or missing source information for refresh.")
    
    unique_key = ""
    if mode == "incremental":
        logger.info(f"用户 '{current_user.username}' 为番剧 '{source_info['title']}' (源ID: {sourceId}) 启动了增量刷新任务。")
        # 修正：crud.get_episodes_for_source 现在返回一个带分页的字典
        paginated_result = await crud.get_episodes_for_source(session, sourceId, page_size=9999) # 获取所有分集以找到最大集数
        latest_episode_index = max((ep['episodeIndex'] for ep in paginated_result.get("episodes", [])), default=0)
        next_episode_index = latest_episode_index + 1

        unique_key = f"import-{source_info['providerName']}-{source_info['mediaId']}-ep{next_episode_index}"
        task_title = f"增量刷新: {source_info['title']} ({source_info['providerName']}) - 尝试第{next_episode_index}集"
        task_coro = lambda s, cb: tasks.incremental_refresh_task(
            sourceId=sourceId, nextEpisodeIndex=next_episode_index, session=s, manager=scraper_manager,
            task_manager=task_manager, config_manager=config_manager, progress_callback=cb, animeTitle=source_info["title"],
            rate_limiter=rate_limiter, metadata_manager=metadata_manager,
            title_recognition_manager=title_recognition_manager
        )
        task_type = "incremental_refresh"
        task_parameters = {"sourceId": sourceId, "nextEpisodeIndex": next_episode_index, "animeTitle": source_info["title"]}
        message_to_return = f"番剧 '{source_info['title']}' 的增量刷新任务已提交。"
    elif mode == "full":
        logger.info(f"用户 '{current_user.username}' 为番剧 '{source_info['title']}' (源ID: {sourceId}) 启动了全量刷新任务。")
        unique_key = f"full-refresh-{sourceId}"
        task_title = f"全量刷新: {source_info['title']} ({source_info['providerName']})"
        task_coro = lambda s, cb: tasks.full_refresh_task(sourceId, s, scraper_manager, task_manager, rate_limiter, cb, metadata_manager, config_manager)
        task_type = "full_refresh"
        task_parameters = {"sourceId": sourceId}
        message_to_return = f"番剧 '{source_info['title']}' 的全量刷新任务已提交。"
    else:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="无效的刷新模式，必须是 'full' 或 'incremental'。")

    task_id, _ = await task_manager.submit_task(
        task_coro, task_title, unique_key=unique_key,
        task_type=task_type, task_parameters=task_parameters
    )
    return {"message": message_to_return, "taskId": task_id}



@router.post("/library/sources/delete-bulk", status_code=status.HTTP_202_ACCEPTED, summary="提交批量删除数据源的任务", response_model=UITaskResponse)
async def delete_bulk_sources(
    request_data: BulkDeleteRequest,
    current_user: models.User = Depends(security.get_current_user),
    task_manager: TaskManager = Depends(get_task_manager)
):
    """提交一个后台任务来批量删除多个数据源。"""
    if not request_data.sourceIds:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Source IDs list cannot be empty.")

    delete_files = getattr(request_data, 'deleteFiles', True)
    task_title = f"批量删除 {len(request_data.sourceIds)} 个数据源"
    if not delete_files:
        task_title += " (保留文件)"
    ids_str = ",".join(sorted([str(sid) for sid in request_data.sourceIds]))
    unique_key = f"delete-bulk-sources-{hashlib.md5(ids_str.encode('utf-8')).hexdigest()[:8]}"
    task_coro = lambda session, callback: tasks.delete_bulk_sources_task(request_data.sourceIds, session, callback, delete_files=delete_files)
    task_id, _ = await task_manager.submit_task(task_coro, task_title, unique_key=unique_key, run_immediately=True)

    logger.info(f"用户 '{current_user.username}' 提交了批量删除 {len(request_data.sourceIds)} 个源的任务 (Task ID: {task_id})，deleteFiles={delete_files}。")
    return {"message": task_title + "的任务已提交。", "taskId": task_id}







@router.post("/library/source/{source_id}/manual-import", status_code=status.HTTP_202_ACCEPTED, summary="手动导入单个分集弹幕")
async def manual_import_episode(
    source_id: int,
    request_data: models.ManualImportRequest,
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
    
    # 修正：使用 url 或 content 字段，优先使用 content
    content_to_use = request_data.content if request_data.content is not None else request_data.url

    # 仅对非自定义源验证URL
    if provider_name != 'custom':
        if not content_to_use: # Should be caught by validator, but for safety
             raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="URL is required for non-custom sources.")
        url_prefixes = {
            'bilibili': 'bilibili.com', 'tencent': 'v.qq.com', 'iqiyi': 'iqiyi.com', 'youku': 'youku.com',
            'mgtv': 'mgtv.com', 'acfun': 'acfun.cn', 'renren': 'rrsp.com.cn'
        }
        expected_prefix = url_prefixes.get(provider_name)
        if not expected_prefix or expected_prefix not in content_to_use:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"提供的URL与当前源 '{provider_name}' 不匹配。")

    task_title = f"手动导入: {source_info['title']} - {request_data.title or f'第 {request_data.episodeIndex} 集'} - [{provider_name}]"
    
    # 生成unique_key以防止重复任务
    unique_key = f"manual-import-{source_id}-{request_data.episodeIndex}-{provider_name}"
    
    task_coro = lambda session, callback: tasks.manual_import_task(
        sourceId=source_id, animeId=source_info['animeId'], title=request_data.title,
        episodeIndex=request_data.episodeIndex, content=content_to_use, providerName=provider_name,
        progress_callback=callback, session=session, manager=scraper_manager, rate_limiter=rate_limiter
    )
    task_id, _ = await task_manager.submit_task(
        task_coro, task_title, unique_key=unique_key,
        task_type="manual_import",
        task_parameters={"sourceId": source_id, "episodeIndex": request_data.episodeIndex, "providerName": provider_name}
    )
    return {"message": f"手动导入任务 '{task_title}' 已提交。", "taskId": task_id}



@router.post("/library/source/{sourceId}/batch-import", status_code=status.HTTP_202_ACCEPTED, summary="批量手动导入分集", response_model=UITaskResponse)
async def batch_manual_import(
    sourceId: int,
    payload: models.BatchManualImportRequest,
    current_user: models.User = Depends(security.get_current_user),
    session: AsyncSession = Depends(get_db_session),
    task_manager: TaskManager = Depends(get_task_manager),
    scraper_manager: ScraperManager = Depends(get_scraper_manager),
    rate_limiter: RateLimiter = Depends(get_rate_limiter),
):
    """
    为指定的数据源批量手动导入分集。
    - 对于普通数据源，请求体中的 'content' 应为视频URL。
    - 对于 'custom' 数据源，'content' 应为dandanplay格式的XML弹幕文件内容。
    """
    source_info = await crud.get_anime_source_info(session, sourceId)
    if not source_info:
        raise HTTPException(status_code=404, detail="数据源未找到")

    task_title = f"批量手动导入: {source_info['title']} ({source_info['providerName']})"
    unique_key = f"batch-manual-import-{sourceId}"
    try:
        task_coro = lambda s, cb: tasks.batch_manual_import_task(
            sourceId=sourceId,
            animeId=source_info['animeId'],
            providerName=source_info['providerName'],
            items=payload.items,
            progress_callback=cb,
            session=s,
            manager=scraper_manager,
            rate_limiter=rate_limiter
        )
        task_id, _ = await task_manager.submit_task(
            task_coro, task_title, unique_key=unique_key,
            task_type="batch_manual_import",
            task_parameters={"sourceId": sourceId, "providerName": source_info['providerName']}
        )
        return {"message": "批量手动导入任务已提交", "taskId": task_id}
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(e))


# --- 追更与标记管理 ---

class IncrementalRefreshSourceInfo(BaseModel):
    """单个源的追更信息"""
    sourceId: int
    providerName: str
    isFavorited: bool
    incrementalRefreshEnabled: bool
    incrementalRefreshFailures: int
    lastRefreshLatestEpisodeAt: Optional[datetime] = None
    episodeCount: int


class IncrementalRefreshAnimeGroup(BaseModel):
    """按番剧分组的追更信息"""
    animeId: int
    animeTitle: str
    animeType: str  # tv_series, movie, ova, other
    sources: List[IncrementalRefreshSourceInfo]


class IncrementalRefreshSourcesResponse(BaseModel):
    """追更源列表响应（分页）"""
    total: int  # 总番剧数
    totalSources: int  # 总源数
    refreshEnabled: int  # 追更中数量
    favorited: int  # 已标记数量
    maxFailures: int  # 最大失败次数配置
    list: List[IncrementalRefreshAnimeGroup]


class IncrementalRefreshTaskStatus(BaseModel):
    """增量追更定时任务状态"""
    exists: bool
    enabled: bool
    cronExpression: Optional[str] = None
    nextRunTime: Optional[datetime] = None
    taskId: Optional[str] = None


class BatchToggleIncrementalRequest(BaseModel):
    """批量开启/关闭追更请求"""
    sourceIds: List[int]
    enabled: bool


class BatchSetFavoriteRequest(BaseModel):
    """批量设置标记请求"""
    sourceIds: List[int]


@router.get("/library/incremental-refresh/sources", response_model=IncrementalRefreshSourcesResponse, summary="获取所有源（按番剧分组，支持分页和过滤）")
async def get_incremental_refresh_sources(
    page: int = Query(1, ge=1, description="页码"),
    pageSize: int = Query(20, ge=1, le=100, description="每页番剧数量"),
    keyword: str = Query("", description="搜索关键词（匹配番剧名称或源名称）"),
    favoriteFilter: str = Query("all", pattern="^(all|favorited|unfavorited)$", description="标记过滤"),
    refreshFilter: str = Query("all", pattern="^(all|enabled|disabled)$", description="追更过滤"),
    typeFilter: str = Query("all", pattern="^(all|movie|tv_series)$", description="类型过滤"),
    current_user: models.User = Depends(security.get_current_user),
    session: AsyncSession = Depends(get_db_session),
):
    """获取所有源（包括启用和未启用追更的），按番剧分组返回，支持分页和过滤。用于批量管理弹窗。"""
    result = await crud.get_incremental_refresh_sources_grouped(
        session,
        page=page,
        page_size=pageSize,
        keyword=keyword,
        favorite_filter=favoriteFilter,
        refresh_filter=refreshFilter,
        type_filter=typeFilter,
    )
    # 获取最大失败次数配置
    max_failures = int(await crud.get_config_value(session, "incrementalRefreshMaxFailures", "10"))
    result["maxFailures"] = max_failures
    return result


@router.get("/library/incremental-refresh/task-status", response_model=IncrementalRefreshTaskStatus, summary="获取增量追更定时任务状态")
async def get_incremental_refresh_task_status(
    current_user: models.User = Depends(security.get_current_user),
    scheduler: SchedulerManager = Depends(get_scheduler_manager),
):
    """检测增量追更定时任务是否存在及其状态。"""
    tasks_list = await scheduler.get_all_tasks()

    # 查找 job_type 为 "incrementalRefresh" 的任务（驼峰命名）
    for task in tasks_list:
        if task.get("jobType") == "incrementalRefresh":
            return IncrementalRefreshTaskStatus(
                exists=True,
                enabled=task.get("isEnabled", False),
                cronExpression=task.get("cronExpression"),
                nextRunTime=task.get("nextRunTime"),
                taskId=task.get("taskId")
            )

    return IncrementalRefreshTaskStatus(
        exists=False,
        enabled=False,
        cronExpression=None,
        nextRunTime=None,
        taskId=None
    )


@router.post("/library/incremental-refresh/batch-toggle", summary="批量开启/关闭追更")
async def batch_toggle_incremental_refresh(
    payload: BatchToggleIncrementalRequest,
    current_user: models.User = Depends(security.get_current_user),
    session: AsyncSession = Depends(get_db_session),
):
    """批量开启或关闭指定源的增量追更。"""
    count = await crud.batch_toggle_incremental_refresh(session, payload.sourceIds, payload.enabled)
    action = "开启" if payload.enabled else "关闭"
    return {"message": f"成功{action} {count} 个源的追更", "count": count}


@router.post("/library/incremental-refresh/batch-favorite", summary="批量设置标记")
async def batch_set_favorite(
    payload: BatchSetFavoriteRequest,
    current_user: models.User = Depends(security.get_current_user),
    session: AsyncSession = Depends(get_db_session),
):
    """批量设置标记。每个源会被设为标记，同一番剧下的其他源会被取消标记。"""
    count = await crud.batch_set_favorite(session, payload.sourceIds)
    return {"message": f"成功设置 {count} 个源为标记", "count": count}


@router.post("/library/incremental-refresh/batch-unfavorite", summary="批量取消标记")
async def batch_unset_favorite(
    payload: BatchSetFavoriteRequest,
    current_user: models.User = Depends(security.get_current_user),
    session: AsyncSession = Depends(get_db_session),
):
    """批量取消标记。"""
    count = await crud.batch_unset_favorite(session, payload.sourceIds)
    return {"message": f"成功取消 {count} 个源的标记", "count": count}


# --- 拆分数据源 ---

@router.get("/library/source/{sourceId}/episodes-for-split", summary="获取数据源的分集列表（用于拆分选择）")
async def get_source_episodes_for_split(
    sourceId: int,
    current_user: models.User = Depends(security.get_current_user),
    session: AsyncSession = Depends(get_db_session),
):
    """获取指定数据源的分集列表，用于拆分数据源时选择分集。"""
    source_info = await crud.get_anime_source_info(session, sourceId)
    if not source_info:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Source not found")

    episodes = await crud.get_source_episode_list(session, sourceId)
    return {"episodes": episodes, "sourceInfo": source_info}


@router.post("/library/anime/{animeId}/split-source", response_model=models.SplitSourceResponse, summary="拆分数据源")
async def split_source(
    animeId: int,
    payload: models.SplitSourceRequest,
    current_user: models.User = Depends(security.get_current_user),
    session: AsyncSession = Depends(get_db_session),
):
    """
    将一个数据源中的部分分集拆分到新的或已有的媒体条目。

    - targetType='new': 创建新的媒体条目，并将选中的分集移动到新条目
    - targetType='existing': 将选中的分集移动到已有的媒体条目
    """
    # 验证源数据源属于当前媒体
    source_info = await crud.get_anime_source_info(session, payload.sourceId)
    if not source_info:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="数据源未找到")
    if source_info['animeId'] != animeId:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="数据源不属于当前媒体")

    # 验证分集ID列表不为空
    if not payload.episodeIds:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="请选择要拆分的分集")

    target_anime_id: int

    if payload.targetType == "new":
        # 创建新的媒体条目
        if not payload.newMediaInfo:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="创建新条目时必须提供媒体信息")

        # 检查是否已存在同名同季度的作品
        existing = await crud.find_anime_by_title_season_year(
            session,
            payload.newMediaInfo.title,
            payload.newMediaInfo.season,
            payload.newMediaInfo.year
        )
        if existing:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"已存在同名同季度的作品: {payload.newMediaInfo.title} (第 {payload.newMediaInfo.season} 季)"
            )

        # 创建新的 Anime 记录
        new_anime = orm_models.Anime(
            title=payload.newMediaInfo.title,
            type="tv_series",
            season=payload.newMediaInfo.season,
            year=payload.newMediaInfo.year,
            imageUrl=payload.newMediaInfo.imageUrl,
            createdAt=get_now()
        )
        session.add(new_anime)
        await session.flush()

        # 创建关联的元数据和别名记录
        new_metadata = orm_models.AnimeMetadata(animeId=new_anime.id)
        new_alias = orm_models.AnimeAlias(animeId=new_anime.id)
        session.add_all([new_metadata, new_alias])
        await session.flush()

        target_anime_id = new_anime.id
        logger.info(f"用户 '{current_user.username}' 创建了新媒体条目: {payload.newMediaInfo.title} (ID: {target_anime_id})")

    elif payload.targetType == "existing":
        # 使用已有的媒体条目
        if not payload.existingMediaId:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="合并到已有条目时必须提供目标媒体ID")

        # 验证目标媒体存在
        target_anime = await crud.get_anime_full_details(session, payload.existingMediaId)
        if not target_anime:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="目标媒体条目未找到")

        target_anime_id = payload.existingMediaId
    else:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="无效的目标类型，必须是 'new' 或 'existing'")

    # 执行分集移动
    try:
        moved_count = await crud.split_source_episodes(
            session,
            source_id=payload.sourceId,
            episode_ids=payload.episodeIds,
            target_anime_id=target_anime_id,
            reindex_episodes=payload.reindexEpisodes
        )
        await session.commit()

        logger.info(f"用户 '{current_user.username}' 将 {moved_count} 个分集从媒体 {animeId} 拆分到媒体 {target_anime_id}")

        return models.SplitSourceResponse(
            success=True,
            targetMediaId=target_anime_id,
            movedEpisodeCount=moved_count,
            message=f"成功拆分 {moved_count} 个分集到{'新' if payload.targetType == 'new' else '已有'}条目"
        )
    except ValueError as e:
        await session.rollback()
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    except Exception as e:
        await session.rollback()
        logger.error(f"拆分数据源失败: {e}", exc_info=True)
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"拆分失败: {str(e)}")



