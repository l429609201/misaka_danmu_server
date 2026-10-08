"""
Anime相关的API端点
⚠️ 需要架构重构：剩余业务逻辑继续迁移到 workflow 层
"""
import logging
from typing import Optional, List

from fastapi import APIRouter, Body, Depends, HTTPException, Query, status
from sqlalchemy.exc import IntegrityError

from src.utils.auth import security
from src import tasks
from src.services.service_container import (
    get_database_service, get_task_manager, get_metadata_service,
)
from src.workflows.reassociation_flow import (
    reassociate_anime_sources_flow, reassociate_episodes_with_resolution_flow,
)
# 图片下载和作品更新统一交给编排层。
from src.workflows.image_download import refresh_anime_poster as refresh_poster_workflow
# 通用响应模型由 schemas 导出，ui 子包仅提供搜索模型。
import src.schemas as ui_models

from src.schemas import ReassociationRequest
from src.schemas.ui_models import (
    UITaskResponse, RefreshPosterRequest,
    ScanDuplicatesResponse, BatchMergeRequest, BatchMergeResponse, MergeResultItem
)

logger = logging.getLogger(__name__)

router = APIRouter()

@router.post("/library/anime", response_model=ui_models.LibraryAnimeInfo, status_code=201, summary="创建自定义作品条目")
async def create_anime_entry(
    payload: ui_models.AnimeCreate,
    current_user: ui_models.User = Depends(security.get_current_user),
) -> ui_models.LibraryAnimeInfo:
    """手动创建一个新的作品条目，不关联任何数据源。"""
    db = get_database_service()
    try:
        # 创建和响应校验共用事务，避免响应构建失败却已提交作品。
        async with db.transaction():
            new_anime = await db.anime.create_anime(payload)
            details = await db.anime.get_library_anime_by_id(new_anime.id)
            if not details:
                raise HTTPException(status_code=500, detail="创建作品后无法立即获取其信息。")
            response = ui_models.LibraryAnimeInfo.model_validate(details)
        return response
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(e)) from e


@router.get("/library", response_model=ui_models.LibraryResponse, summary="获取媒体库内容")
async def get_library(
    keyword: Optional[str] = Query(None, description="按标题搜索"),
    type: Optional[str] = Query(None, description="类型过滤: movie=电影, tv=TV/OVA"),
    page: int = Query(1, ge=1, description="页码"),
    pageSize: int = Query(10, ge=1, description="每页数量"),
    sortBy: str = Query("anime_created", description="排序字段: anime_created=媒体库入库时间, episode_fetched=分集入库时间"),
    sortOrder: str = Query("desc", description="排序方向: asc=升序, desc=降序"),
    current_user: ui_models.User = Depends(security.get_current_user),
) -> ui_models.LibraryResponse:
    """获取已收录的番剧信息，支持搜索、类型过滤、排序和分页。"""
    db = get_database_service()
    # DatabaseService 的 anime 代理统一提供读写仓储方法。
    async with db.transaction():
        paginated_result = await db.anime.get_library_list(
            keyword=keyword, anime_type=type, page=page, page_size=pageSize,
            sort_by=sortBy, sort_order=sortOrder,
        )
    return ui_models.LibraryResponse(
        total=paginated_result["total"],
        list=[ui_models.LibraryAnimeInfo.model_validate(item) for item in paginated_result["list"]]
    )


@router.get("/library/anime/{animeId}/details", response_model=ui_models.AnimeFullDetails, summary="获取影视完整详情")
async def get_anime_full_details(
    animeId: int,
    current_user: ui_models.User = Depends(security.get_current_user),
) -> ui_models.AnimeFullDetails:
    """获取指定番剧的完整信息，包括所有元数据ID。"""
    db = get_database_service()
    async with db.transaction():
        details = await db.anime.get_full_details(animeId)
    if not details:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="作品未找到")
    # 年份已由详情查询返回，不再在 API 层补查 ORM。
    return ui_models.AnimeFullDetails.model_validate(details)


@router.post("/library/anime/bulk-set-finished", status_code=status.HTTP_204_NO_CONTENT, summary="批量标记番剧完结状态")
async def bulk_set_finished(
    animeIds: List[int] = Body(..., description="番剧ID列表"),
    isFinished: bool = Body(..., description="是否完结"),
    current_user: ui_models.User = Depends(security.get_current_user),
) -> None:
    """批量设置作品下所有源的完结状态；完结后预下载将跳过该源。"""
    db = get_database_service()
    async with db.transaction():
        count = await db.source.bulk_set_finished_by_anime_ids(animeIds, isFinished)
    action = "完结" if isFinished else "取消完结"
    logger.info(f"用户 '{current_user.username}' 批量{action}了 {len(animeIds)} 部番剧的 {count} 个数据源")


@router.put("/library/anime/{animeId}", status_code=status.HTTP_204_NO_CONTENT, summary="编辑影视信息")
async def edit_anime_info(
    animeId: int,
    update_data: ui_models.AnimeDetailUpdate,
    current_user: ui_models.User = Depends(security.get_current_user),
) -> None:
    """更新指定番剧的标题、季度和元数据。"""
    db = get_database_service()
    # 核心信息先提交，外部元数据请求不占用数据库事务。
    async with db.transaction():
        updated = await db.anime.update_anime_details(animeId, update_data)
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
                await get_metadata_service().update_tmdb_mappings(
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
    current_user: ui_models.User = Depends(security.get_current_user),
) -> dict:
    """委托编排层刷新海报，路由只映射业务异常为 HTTP 状态。"""
    try:
        new_local_path = await refresh_poster_workflow(animeId, request_data.imageUrl)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"new_path": new_local_path}



@router.post("/library/anime/{animeId}/sources", response_model=ui_models.SourceInfo, status_code=201, summary="为作品新增数据源")
async def add_source_to_anime(
    animeId: int,
    payload: ui_models.SourceCreate,
    current_user: ui_models.User = Depends(security.get_current_user),
) -> ui_models.SourceInfo:
    """在同一事务中关联数据源并构建响应。"""
    db = get_database_service()
    try:
        # 查询、写入和响应校验统一提交，失败由数据库服务回滚。
        async with db.transaction():
            if not await db.anime.get_by_id(animeId):
                raise HTTPException(status_code=404, detail="作品未找到")
            source_id = await db.source.link_source_to_anime(animeId, payload.providerName, payload.mediaId)
            # source 代理统一提供写入和复杂查询方法，无需独立的 source_query 属性。
            all_sources = await db.source.get_anime_sources(animeId)
            source = next((s for s in all_sources if s['sourceId'] == source_id), None)
            if not source:
                raise HTTPException(status_code=500, detail="创建数据源后无法立即获取其信息。")
            response = ui_models.SourceInfo.model_validate(source)
        return response
    except IntegrityError as error:
        raise HTTPException(status_code=409, detail="该数据源已存在于此作品下，无法重复添加。") from error


@router.get("/library/anime/{animeId}/sources", response_model=List[ui_models.SourceInfo], summary="获取作品的所有数据源")
async def get_anime_sources_for_anime(
    animeId: int,
    current_user: ui_models.User = Depends(security.get_current_user),
) -> List[ui_models.SourceInfo]:
    """获取指定作品关联的所有数据源列表。"""
    db = get_database_service()
    async with db.transaction():
        sources = await db.source.get_anime_sources(animeId)
        return [ui_models.SourceInfo.model_validate(source) for source in sources]


@router.post("/library/anime/{sourceAnimeId}/reassociate/check", response_model=ui_models.ReassociationConflictResponse, summary="检测关联冲突")
async def check_reassociation_conflicts(
    sourceAnimeId: int,
    request_data: ui_models.ReassociationRequest = Body(...),
    current_user: ui_models.User = Depends(security.get_current_user),
) -> ui_models.ReassociationConflictResponse:
    """检测关联操作是否存在冲突。"""
    if sourceAnimeId == request_data.targetAnimeId:
        raise HTTPException(status_code=400, detail="源作品和目标作品不能相同。")
    db = get_database_service()
    async with db.transaction():
        return await db.reassociation.check_reassociation_conflicts(sourceAnimeId, request_data.targetAnimeId)


@router.post("/library/anime/{sourceAnimeId}/reassociate", status_code=status.HTTP_204_NO_CONTENT, summary="重新关联作品的数据源")
async def reassociate_anime_sources(
    sourceAnimeId: int,
    request_data: ReassociationRequest = Body(...),
    current_user: ui_models.User = Depends(security.get_current_user),
) -> None:
    """由编排层统一迁移文件和数据源，并删除已空的原作品。"""
    try:
        success = await reassociate_anime_sources_flow(sourceAnimeId, request_data.targetAnimeId)
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    if not success:
        raise HTTPException(status_code=404, detail="源作品或目标作品未找到。")
    logger.info(f"用户 '{current_user.username}' 将作品 ID {sourceAnimeId} 的源关联到了 ID {request_data.targetAnimeId}。")


@router.post("/library/anime/{sourceAnimeId}/reassociate/resolve", status_code=status.HTTP_204_NO_CONTENT, summary="执行关联并解决冲突")
async def reassociate_with_conflict_resolution(
    sourceAnimeId: int,
    request_data: ui_models.ReassociationResolveRequest = Body(...),
    current_user: ui_models.User = Depends(security.get_current_user),
) -> None:
    """根据用户选择执行关联，冲突决策缺失时拒绝操作。"""
    try:
        success = await reassociate_episodes_with_resolution_flow(sourceAnimeId, request_data)
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    if not success:
        raise HTTPException(status_code=404, detail="源作品或目标作品未找到。")
    logger.info(f"用户 '{current_user.username}' 将作品 ID {sourceAnimeId} 的源关联到了 ID {request_data.targetAnimeId}，并解决了冲突。")



@router.delete("/library/anime/{animeId}", status_code=status.HTTP_202_ACCEPTED, summary="提交删除媒体库中番剧的任务", response_model=UITaskResponse)
async def delete_anime_from_library(
    animeId: int,
    deleteFiles: bool = Query(True, description="是否同时删除弹幕XML文件"),
    current_user: ui_models.User = Depends(security.get_current_user),
) -> dict[str, str]:
    """提交一个后台任务来删除一个番剧及其所有关联数据。"""
    db = get_database_service()
    async with db.transaction():
        anime_details = await db.anime.get_full_details(animeId)
    if not anime_details:
        raise HTTPException(status_code=404, detail="作品未找到")
    # 提交任务前释放查询事务，任务工厂签名保持任务系统约定。
    task_manager = get_task_manager()
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
    current_user: ui_models.User = Depends(security.get_current_user),
) -> ScanDuplicatesResponse:
    """扫描弹幕库中基于TMDB ID的重复条目，返回重复组列表。"""
    db = get_database_service()
    async with db.transaction():
        # 复杂查询通过 anime 代理转发到查询仓储。
        groups = await db.anime.scan_duplicate_animes(strict=strict)
    return ScanDuplicatesResponse(
        groups=groups,
        totalGroups=len(groups),
        totalItems=sum(len(g["items"]) for g in groups),
    )


@router.post("/library/batch-merge", response_model=BatchMergeResponse, summary="批量合并重复条目")
async def batch_merge_animes(
    request: BatchMergeRequest,
    current_user: ui_models.User = Depends(security.get_current_user),
) -> BatchMergeResponse:
    """逐个源条目独立合并，单项回滚不影响其他已完成的合并。"""
    results = []
    success_count = 0
    fail_count = 0
    for op in request.operations:
        for source_id in op.sourceAnimeIds:
            try:
                # 编排入口自行管理数据库事务与文件补偿，不共享批量会话。
                ok = await reassociate_anime_sources_flow(source_id, op.targetAnimeId)
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
