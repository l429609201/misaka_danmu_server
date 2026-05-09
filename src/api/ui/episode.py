"""
Episode相关的API端点
"""
import hashlib
import logging
from typing import Optional, List

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from src import security, tasks
from src.db import crud, models, orm_models, get_db_session, ConfigManager
from src.services import TaskManager, ScraperManager
from src.rate_limiter import RateLimiter

from src.api.dependencies import (
    get_scraper_manager, get_task_manager, get_rate_limiter, get_config_manager
)
from .models import UITaskResponse, BulkDeleteEpisodesRequest

logger = logging.getLogger(__name__)

router = APIRouter()

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





@router.put("/library/episode/{episodeId}", status_code=status.HTTP_204_NO_CONTENT, summary="编辑分集信息")
async def edit_episode_info(
    episodeId: int,
    update_data: models.EpisodeInfoUpdate,
    current_user: models.User = Depends(security.get_current_user),
    session: AsyncSession = Depends(get_db_session)
):
    """更新指定分集的标题、集数和链接。对于自定义源，链接是可选的。"""
    # 1. 获取分集及其源提供商信息
    # 修正：在更新前先获取分集信息，以进行条件验证
    episode_info = await crud.get_episode_provider_info(session, episodeId)
    if not episode_info:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Episode not found")

    # 2. 根据源类型验证输入
    provider_name = episode_info.get("providerName")
    if provider_name != 'custom':
        # 对于非自定义源，链接是必需的
        if not update_data.sourceUrl:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="对于非自定义源，分集链接(sourceUrl)是必需的。"
            )

    # 3. 执行更新
    try:
        updated = await crud.update_episode_info(session, episodeId, update_data)
        if not updated:
            # This case might be redundant if get_episode_provider_info already confirmed existence, but it's safe.
            logger.warning(f"尝试更新一个不存在的分集 (ID: {episodeId})，操作被拒绝。")
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Episode not found")
        logger.info(f"用户 '{current_user.username}' 更新了分集 ID: {episodeId} 的信息。")
        return
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(e))



@router.delete("/library/episode/{episodeId}", status_code=status.HTTP_202_ACCEPTED, summary="提交删除指定分集的任务", response_model=UITaskResponse)
async def delete_episode_from_source(
    episodeId: int,
    deleteFiles: bool = Query(True, description="是否同时删除弹幕XML文件"),
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
    if not deleteFiles:
        task_title += " (保留文件)"
    unique_key = f"delete-episode-{episodeId}"
    task_coro = lambda session, callback: tasks.delete_episode_task(episodeId, session, callback, delete_files=deleteFiles)
    task_id, _ = await task_manager.submit_task(task_coro, task_title, unique_key=unique_key, run_immediately=True)

    logger.info(f"用户 '{current_user.username}' 提交了删除分集 ID: {episodeId} 的任务 (Task ID: {task_id})，deleteFiles={deleteFiles}。")
    return {"message": f"删除分集 '{episode_info['title']}' 的任务已提交。", "taskId": task_id}



@router.post("/library/episode/{episodeId}/refresh", status_code=status.HTTP_202_ACCEPTED, summary="刷新单个分集的弹幕", response_model=UITaskResponse)
async def refresh_single_episode(
    episodeId: int,
    current_user: models.User = Depends(security.get_current_user),
    session: AsyncSession = Depends(get_db_session),
    scraper_manager: ScraperManager = Depends(get_scraper_manager),
    task_manager: TaskManager = Depends(get_task_manager),
    rate_limiter: RateLimiter = Depends(get_rate_limiter),
    config_manager: ConfigManager = Depends(get_config_manager)
):
    """为指定分集启动一个后台任务，重新获取其弹幕。"""
    # 检查分集是否存在，以提供更友好的404错误
    episode = await crud.get_episode_for_refresh(session, episodeId)
    if not episode:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Episode not found")

    logger.info(f"用户 '{current_user.username}' 请求刷新分集 ID: {episodeId} ({episode['title']})")

    provider_name = episode.get('providerName', '未知源')
    media_id = episode.get('mediaId', '?')
    task_title = f"刷新分集: {episode['title']} - [{provider_name}] (mediaId={media_id})"
    task_coro = lambda session, callback: tasks.refresh_episode_task(episodeId, session, scraper_manager, rate_limiter, callback, config_manager)
    task_id, _ = await task_manager.submit_task(task_coro, task_title)

    return {"message": f"分集 '{episode['title']}' 的刷新任务已提交。", "taskId": task_id}



@router.post("/library/episodes/refresh-bulk", status_code=status.HTTP_202_ACCEPTED, summary="批量刷新分集弹幕", response_model=UITaskResponse)
async def refresh_episodes_bulk(
    request: Request,
    current_user: models.User = Depends(security.get_current_user),
    session: AsyncSession = Depends(get_db_session),
    scraper_manager: ScraperManager = Depends(get_scraper_manager),
    task_manager: TaskManager = Depends(get_task_manager),
    rate_limiter: RateLimiter = Depends(get_rate_limiter),
    config_manager: ConfigManager = Depends(get_config_manager)
):
    """批量刷新多个分集的弹幕（整合为一个任务）"""
    body = await request.json()
    episode_ids = body.get("episodeIds", [])

    if not episode_ids:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="未提供分集ID列表")

    logger.info(f"用户 '{current_user.username}' 请求批量刷新 {len(episode_ids)} 个分集")

    task_title = f"批量刷新 {len(episode_ids)} 个分集"
    task_coro = lambda s, cb: tasks.refresh_bulk_episodes_task(episode_ids, s, scraper_manager, rate_limiter, cb, config_manager)
    task_id, _ = await task_manager.submit_task(task_coro, task_title)

    return {"message": f"已提交批量刷新任务，共 {len(episode_ids)} 个分集", "taskId": task_id}



@router.post("/library/episodes/delete-bulk", status_code=status.HTTP_202_ACCEPTED, summary="提交批量删除分集的任务", response_model=UITaskResponse)
async def delete_bulk_episodes(
    request_data: BulkDeleteEpisodesRequest,
    current_user: models.User = Depends(security.get_current_user),
    task_manager: TaskManager = Depends(get_task_manager)
):
    """提交一个后台任务来批量删除多个分集。"""
    if not request_data.episodeIds:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Episode IDs list cannot be empty.")

    delete_files = getattr(request_data, 'deleteFiles', True)
    task_title = f"批量删除 {len(request_data.episodeIds)} 个分集"
    if not delete_files:
        task_title += " (保留文件)"
    ids_str = ",".join(sorted([str(eid) for eid in request_data.episodeIds]))
    unique_key = f"delete-bulk-episodes-{hashlib.md5(ids_str.encode('utf-8')).hexdigest()[:8]}"

    # 注意：这里我们将整个列表传递给任务
    task_coro = lambda session, callback: tasks.delete_bulk_episodes_task(request_data.episodeIds, session, callback, delete_files=delete_files)

    task_id, _ = await task_manager.submit_task(task_coro, task_title, unique_key=unique_key, run_immediately=True)

    logger.info(f"用户 '{current_user.username}' 提交了批量删除 {len(request_data.episodeIds)} 个分集的任务 (Task ID: {task_id})，deleteFiles={delete_files}。")
    return {"message": task_title + "的任务已提交。", "taskId": task_id}




@router.post("/library/episodes/offset", status_code=status.HTTP_202_ACCEPTED, summary="偏移选中分集的集数", response_model=UITaskResponse)
async def offset_episodes(
    request_data: models.EpisodeOffsetRequest,
    current_user: models.User = Depends(security.get_current_user),
    session: AsyncSession = Depends(get_db_session),
    task_manager: TaskManager = Depends(get_task_manager)
):
    """提交一个后台任务，对选中的分集进行集数偏移。"""
    if not request_data.episodeIds:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="episodeIds 列表不能为空。")
    
    # 1. 在提交任务前，先进行快速验证，以提供即时反馈
    min_index_res = await session.execute(
        select(func.min(orm_models.Episode.episodeIndex))
        .where(orm_models.Episode.id.in_(request_data.episodeIds))
    )
    min_index = min_index_res.scalar_one_or_none()

    if min_index is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="找不到任何一个选中的分集。")

    # 2. 检查偏移后的集数是否会小于1
    if (min_index + request_data.offset) < 1:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"操作无效：偏移后的最小集数将为 {min_index + request_data.offset}，集数必须大于0。"
        )


    # 获取一个代表性的标题用于任务日志
    first_episode = await session.get(
        orm_models.Episode, 
        request_data.episodeIds[0], 
        options=[selectinload(orm_models.Episode.source).selectinload(orm_models.AnimeSource.anime)]
    )
    # 此检查现在是多余的，因为上面的 min_index 检查已经覆盖了，但保留它以防万一
    if not first_episode:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="找不到任何一个选中的分集。")

    anime_title = first_episode.source.anime.title
    provider_name = first_episode.source.providerName
    offset_str = f"+{request_data.offset}" if request_data.offset >= 0 else str(request_data.offset)

    task_title = f"集数偏移 ({offset_str}): {anime_title} ({provider_name})"
    task_coro = lambda session, callback: tasks.offset_episodes_task(
        request_data.episodeIds, request_data.offset, session, callback
    )

    unique_key = f"modify-episodes-{first_episode.sourceId}"
    try:
        task_id, _ = await task_manager.submit_task(task_coro, task_title, unique_key=unique_key, queue_type="management")
    except HTTPException as e:
        # 重新抛出由 task_manager 引发的异常 (例如，任务已在运行)
        raise e

    logger.info(f"用户 '{current_user.username}' 提交了集数偏移任务 (Task ID: {task_id})。")
    return {"message": f"集数偏移任务 '{task_title}' 已提交。", "taskId": task_id}




