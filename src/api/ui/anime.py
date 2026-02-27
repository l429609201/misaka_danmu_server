"""
Anime相关的API端点
"""
import logging
from typing import Optional, List

from fastapi import APIRouter, Body, Depends, HTTPException, Query, status
from sqlalchemy import update, exc
from sqlalchemy.ext.asyncio import AsyncSession

from src import security, tasks
from src.db import crud, models, orm_models, get_db_session
from src.services import TaskManager, MetadataSourceManager, ScraperManager
from src.utils import download_image

from src.api.dependencies import (
    get_scraper_manager, get_task_manager, get_metadata_manager
)
from .models import UITaskResponse, RefreshPosterRequest, ReassociationRequest, ScanDuplicatesResponse, BatchMergeRequest, BatchMergeResponse, MergeResultItem

logger = logging.getLogger(__name__)

router = APIRouter()

@router.post("/library/anime", response_model=models.LibraryAnimeInfo, status_code=201, summary="创建自定义作品条目")
async def create_anime_entry(
    payload: models.AnimeCreate,
    current_user: models.User = Depends(security.get_current_user),
    session: AsyncSession = Depends(get_db_session)
):
    """
    手动创建一个新的作品条目，不关联任何数据源。
    """
    try:
        new_anime = await crud.create_anime(session, payload)
        await session.commit()
        # We need to return a LibraryAnimeInfo, so we fetch the full details
        # with counts.
        details = await crud.get_library_anime_by_id(session, new_anime.id)
        if not details:
            # This should not happen, but as a fallback
            raise HTTPException(status_code=500, detail="创建作品后无法立即获取其信息。")
        return models.LibraryAnimeInfo.model_validate(details)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(e))



@router.get("/library", response_model=models.LibraryResponse, summary="获取媒体库内容")
async def get_library(
    keyword: Optional[str] = Query(None, description="按标题搜索"),
    type: Optional[str] = Query(None, description="类型过滤: movie=电影, tv=TV/OVA"),
    page: int = Query(1, ge=1, description="页码"),
    pageSize: int = Query(10, ge=1, description="每页数量"),
    current_user: models.User = Depends(security.get_current_user),
    session: AsyncSession = Depends(get_db_session)
):
    """获取数据库中所有已收录的番剧信息，支持搜索、类型过滤和分页。"""
    paginated_result = await crud.get_library_anime(session, keyword=keyword, anime_type=type, page=page, page_size=pageSize)
    return models.LibraryResponse(
        total=paginated_result["total"],
        list=[models.LibraryAnimeInfo.model_validate(item) for item in paginated_result["list"]]
    )



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
        group_id = update_data.tmdbEpisodeGroupId
        # 本地剧集组（local-*）已通过本地接口保存，无需从 TMDB API 拉取
        if group_id.startswith("local-"):
            logger.info(f"剧集组 {group_id} 为本地剧集组，跳过 TMDB API 映射更新。")
        else:
            logger.info(f"检测到TMDB ID和剧集组ID，开始更新映射表...")
            try:
                await metadata_manager.update_tmdb_mappings(
                    tmdb_tv_id=int(update_data.tmdbId),
                    group_id=group_id,
                    user=current_user
                )
            except Exception as e:
                # 仅记录错误，不中断主流程，因为核心信息已保存
                logger.error(f"更新TMDB映射失败: {e}", exc_info=True)
    return



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



@router.post("/library/anime/{animeId}/sources", response_model=models.SourceInfo, status_code=201, summary="为作品新增数据源")
async def add_source_to_anime(
    animeId: int,
    payload: models.SourceCreate,
    current_user: models.User = Depends(security.get_current_user),
    session: AsyncSession = Depends(get_db_session)
):
    """
    为一个已存在的作品手动关联一个新的数据源。
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



@router.get("/library/anime/{animeId}/sources", response_model=List[models.SourceInfo], summary="获取作品的所有数据源")
async def get_anime_sources_for_anime(
    animeId: int,
    current_user: models.User = Depends(security.get_current_user),
    session: AsyncSession = Depends(get_db_session)
):
    """获取指定作品关联的所有数据源列表。"""
    return await crud.get_anime_sources(session, animeId)



@router.post("/library/anime/{sourceAnimeId}/reassociate/check", response_model=models.ReassociationConflictResponse, summary="检测关联冲突")
async def check_reassociation_conflicts(
    sourceAnimeId: int,
    request_data: models.ReassociationRequest = Body(...),
    current_user: models.User = Depends(security.get_current_user),
    session: AsyncSession = Depends(get_db_session)
):
    """检测关联操作是否存在冲突"""
    if sourceAnimeId == request_data.targetAnimeId:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="源作品和目标作品不能相同。")

    conflicts = await crud.check_reassociation_conflicts(session, sourceAnimeId, request_data.targetAnimeId)
    return conflicts



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



@router.post("/library/anime/{sourceAnimeId}/reassociate/resolve", status_code=status.HTTP_204_NO_CONTENT, summary="执行关联并解决冲突")
async def reassociate_with_conflict_resolution(
    sourceAnimeId: int,
    request_data: models.ReassociationResolveRequest = Body(...),
    current_user: models.User = Depends(security.get_current_user),
    session: AsyncSession = Depends(get_db_session)
):
    """根据用户选择执行关联操作,解决冲突"""
    if sourceAnimeId == request_data.targetAnimeId:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="源作品和目标作品不能相同。")

    success = await crud.reassociate_anime_sources_with_resolution(session, sourceAnimeId, request_data)
    if not success:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="源作品或目标作品未找到，或操作失败。")
    logger.info(f"用户 '{current_user.username}' 将作品 ID {sourceAnimeId} 的源关联到了 ID {request_data.targetAnimeId}，并解决了冲突。")
    return



@router.delete("/library/anime/{animeId}", status_code=status.HTTP_202_ACCEPTED, summary="提交删除媒体库中番剧的任务", response_model=UITaskResponse)
async def delete_anime_from_library(
    animeId: int,
    deleteFiles: bool = Query(True, description="是否同时删除弹幕XML文件"),
    current_user: models.User = Depends(security.get_current_user),
    session: AsyncSession = Depends(get_db_session),
    task_manager: TaskManager = Depends(get_task_manager)
):
    """提交一个后台任务来删除一个番剧及其所有关联数据。"""
    # Get title for task name
    anime_details = await crud.get_anime_full_details(session, animeId)
    if not anime_details:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Anime not found")

    # 修正：为删除任务添加基于 animeId 的唯一键，以防止因作品重名导致的任务冲突。
    task_title = f"删除作品: {anime_details['title']}"
    if not deleteFiles:
        task_title += " (保留文件)"
    unique_key = f"delete-anime-{animeId}"
    task_coro = lambda session, callback: tasks.delete_anime_task(animeId, session, callback, delete_files=deleteFiles)
    task_id, _ = await task_manager.submit_task(task_coro, task_title, unique_key=unique_key, run_immediately=True)

    logger.info(f"用户 '{current_user.username}' 提交了删除作品 ID: {animeId} 的任务 (Task ID: {task_id})，deleteFiles={deleteFiles}。")
    return {"message": f"删除作品 '{anime_details['title']}' 的任务已提交。", "taskId": task_id}





@router.get("/library/scan-duplicates", response_model=ScanDuplicatesResponse, summary="扫描弹幕库中的重复条目")
async def scan_duplicates(
    strict: bool = Query(True, description="严格模式(tmdbId+season)或宽松模式(仅tmdbId)"),
    current_user: models.User = Depends(security.get_current_user),
    session: AsyncSession = Depends(get_db_session)
):
    """扫描弹幕库中基于TMDB ID的重复条目，返回重复组列表。"""
    groups = await crud.scan_duplicate_animes(session, strict=strict)
    total_items = sum(len(g["items"]) for g in groups)
    return ScanDuplicatesResponse(
        groups=groups,
        totalGroups=len(groups),
        totalItems=total_items,
    )


@router.post("/library/batch-merge", response_model=BatchMergeResponse, summary="批量合并重复条目")
async def batch_merge_animes(
    request: BatchMergeRequest,
    current_user: models.User = Depends(security.get_current_user),
    session: AsyncSession = Depends(get_db_session)
):
    """批量执行合并操作，将源条目的数据源移动到目标条目。"""
    results = []
    success_count = 0
    fail_count = 0

    for op in request.operations:
        for source_id in op.sourceAnimeIds:
            try:
                ok = await crud.reassociate_anime_sources(session, source_id, op.targetAnimeId)
                if ok:
                    success_count += 1
                    results.append(MergeResultItem(targetAnimeId=op.targetAnimeId, success=True))
                    logger.info(f"用户 '{current_user.username}' 合并: {source_id} → {op.targetAnimeId}")
                else:
                    fail_count += 1
                    results.append(MergeResultItem(targetAnimeId=op.targetAnimeId, success=False, error=f"合并 {source_id} 失败"))
            except Exception as e:
                fail_count += 1
                results.append(MergeResultItem(targetAnimeId=op.targetAnimeId, success=False, error=str(e)))
                logger.error(f"合并 {source_id} → {op.targetAnimeId} 失败: {e}", exc_info=True)

    return BatchMergeResponse(results=results, successCount=success_count, failCount=fail_count)
