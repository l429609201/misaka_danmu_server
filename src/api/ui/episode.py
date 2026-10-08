"""
Episode相关的API端点
"""
import hashlib
import logging
from typing import Optional, List

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from src.utils.auth import security
from src import tasks
from src.schemas.auth import User
from src.schemas.anime import EpisodeInfoUpdate
from src.schemas.control.episode import EpisodeOffsetRequest
from src.schemas.ui_models import UITaskResponse, BulkDeleteEpisodesRequest
from src.services.service_container import (
    get_database_service, get_scraper_manager, get_task_manager, get_rate_limiter,
)
from src.services.config_service import get_config_service
from src.workflows.episode_edit import update_episode_info

logger = logging.getLogger(__name__)

router = APIRouter()

@router.get("/library/episodes-by-title", response_model=List[int], summary="根据作品标题获取已存在的分集序号")
async def get_existing_episode_indices(
    title: str = Query(..., description="要查询的作品标题"),
    season: Optional[int] = Query(None, description="要查询的季度号"),
    current_user: User = Depends(security.get_current_user),
) -> List[int]:
    """按作品标题及季度查询已存在的分集序号，供增量导入使用。"""
    db = get_database_service()
    async with db.transaction():
        return await db.episode.get_episode_indices_by_anime_title(title, season=season)


@router.put("/library/episode/{episodeId}", status_code=status.HTTP_204_NO_CONTENT, summary="编辑分集信息")
async def edit_episode_info(
    episodeId: int,
    update_data: EpisodeInfoUpdate,
    current_user: User = Depends(security.get_current_user),
) -> None:
    """验证源链接后通过编排更新分集信息，保持文件和数据库一致。"""
    db = get_database_service()
    async with db.transaction():
        episode_info = await db.episode.get_episode_provider_info(episodeId)
    if not episode_info:
        raise HTTPException(status_code=404, detail="Episode not found")
    if episode_info["providerName"] != "custom" and not update_data.sourceUrl:
        raise HTTPException(status_code=422, detail="对于非自定义源，分集链接(sourceUrl)是必需的。")
    # 编排独立管理写事务，避免嵌套事务提前执行文件清理。
    try:
        if not await update_episode_info(episodeId, update_data):
            raise HTTPException(status_code=404, detail="Episode not found")
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    logger.info(f"用户 '{current_user.username}' 更新了分集 ID: {episodeId} 的信息。")



@router.delete("/library/episode/{episodeId}", status_code=status.HTTP_202_ACCEPTED, summary="提交删除指定分集的任务", response_model=UITaskResponse)
async def delete_episode_from_source(
    episodeId: int,
    deleteFiles: bool = Query(True, description="是否同时删除弹幕XML文件"),
    current_user: User = Depends(security.get_current_user),
) -> dict:
    """提交一个后台任务来删除一个分集及其所有关联的弹幕。"""
    task_manager = get_task_manager()
    db = get_database_service()
    # 提交后台任务前释放查询事务，不让排队过程占用请求连接。
    async with db.transaction():
        episode_info = await db.episode.get_episode_for_refresh(episodeId)
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
    current_user: User = Depends(security.get_current_user),
) -> dict:
    """为指定分集启动一个后台任务，重新获取其弹幕。"""
    scraper_manager = get_scraper_manager()
    task_manager = get_task_manager()
    rate_limiter = get_rate_limiter()
    config_service = get_config_service()
    db = get_database_service()
    # 查询结束即释放事务，任务仍沿用原去重键与通知分类。
    async with db.transaction():
        episode = await db.episode.get_episode_for_refresh(episodeId)
    if not episode:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Episode not found")

    logger.info(f"用户 '{current_user.username}' 请求刷新分集 ID: {episodeId} ({episode['title']})")

    provider_name = episode.get('providerName', '未知源')
    media_id = episode.get('mediaId', '?')
    task_title = f"刷新分集: {episode['title']} - [{provider_name}] (mediaId={media_id})"
    task_coro = lambda session, callback: tasks.refresh_episode_task(episodeId, session, scraper_manager, rate_limiter, callback, config_service)
    # 传入 unique_key，使任务完成后 _determine_event_type 能正确归类为 refresh 通知。
    # 否则空 unique_key 会导致通知事件类型判定为 None，刷新完成后不发任何通知。
    task_id, _ = await task_manager.submit_task(
        task_coro, task_title, unique_key=f"refresh-episode-{episodeId}"
    )

    return {"message": f"分集 '{episode['title']}' 的刷新任务已提交。", "taskId": task_id}



@router.post("/library/episodes/refresh-bulk", status_code=status.HTTP_202_ACCEPTED, summary="批量刷新分集弹幕", response_model=UITaskResponse)
async def refresh_episodes_bulk(
    request: Request,
    current_user: User = Depends(security.get_current_user),
) -> dict:
    """批量刷新多个分集的弹幕（整合为一个任务）。"""
    # 管理器按请求取全局实例，不再创建未使用的请求级数据库会话。
    scraper_manager = get_scraper_manager()
    task_manager = get_task_manager()
    rate_limiter = get_rate_limiter()
    config_service = get_config_service()
    body = await request.json()
    episode_ids = body.get("episodeIds", [])

    if not episode_ids:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="未提供分集ID列表")

    logger.info(f"用户 '{current_user.username}' 请求批量刷新 {len(episode_ids)} 个分集")

    task_title = f"批量刷新 {len(episode_ids)} 个分集"
    task_coro = lambda s, cb: tasks.refresh_bulk_episodes_task(episode_ids, s, scraper_manager, rate_limiter, cb, config_service)
    # 传入 unique_key（bulk-refresh- 前缀），使任务完成后 _determine_event_type 正确归类为 refresh 通知。
    # 否则空 unique_key 会导致通知事件类型判定为 None，批量刷新完成后不发通知。
    ids_str = ",".join(sorted(str(eid) for eid in episode_ids))
    unique_key = f"bulk-refresh-{hashlib.md5(ids_str.encode('utf-8')).hexdigest()[:8]}"
    task_id, _ = await task_manager.submit_task(task_coro, task_title, unique_key=unique_key)

    return {"message": f"已提交批量刷新任务，共 {len(episode_ids)} 个分集", "taskId": task_id}



@router.post("/library/episodes/delete-bulk", status_code=status.HTTP_202_ACCEPTED, summary="提交批量删除分集的任务", response_model=UITaskResponse)
async def delete_bulk_episodes(
    request_data: BulkDeleteEpisodesRequest,
    current_user: User = Depends(security.get_current_user),
) -> dict:
    """提交一个后台任务来批量删除多个分集。"""
    task_manager = get_task_manager()
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
    request_data: EpisodeOffsetRequest,
    current_user: User = Depends(security.get_current_user),
) -> dict:
    """提交一个后台任务，对选中的分集进行集数偏移。"""
    if not request_data.episodeIds:
        raise HTTPException(status_code=400, detail="episodeIds 列表不能为空。")
    task_manager = get_task_manager()
    db = get_database_service()
    # 复用批量查询，事务内提取所需标量，避免离开会话后访问关联对象。
    async with db.transaction():
        episodes = await db.episode.get_by_ids(request_data.episodeIds)
        first_episode = next((ep for ep in episodes if ep.id == request_data.episodeIds[0]), None)
        if first_episode is None:
            raise HTTPException(status_code=404, detail="找不到任何一个选中的分集。")
        min_index = min(ep.episodeIndex for ep in episodes)
        anime_title = first_episode.source.anime.title
        provider_name = first_episode.source.providerName
        source_id = first_episode.sourceId
    if min_index + request_data.offset < 1:
        raise HTTPException(
            status_code=400,
            detail=f"操作无效：偏移后的最小集数将为 {min_index + request_data.offset}，集数必须大于0。",
        )
    offset_str = f"+{request_data.offset}" if request_data.offset >= 0 else str(request_data.offset)

    task_title = f"集数偏移 ({offset_str}): {anime_title} ({provider_name})"
    task_coro = lambda session, callback: tasks.offset_episodes_task(
        request_data.episodeIds, request_data.offset, session, callback
    )

    unique_key = f"modify-episodes-{source_id}"
    try:
        task_id, _ = await task_manager.submit_task(task_coro, task_title, unique_key=unique_key, queue_type="management")
    except HTTPException as e:
        # 重新抛出由 task_manager 引发的异常 (例如，任务已在运行)
        raise e

    logger.info(f"用户 '{current_user.username}' 提交了集数偏移任务 (Task ID: {task_id})。")
    return {"message": f"集数偏移任务 '{task_title}' 已提交。", "taskId": task_id}




