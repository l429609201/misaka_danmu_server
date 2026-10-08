"""
媒体服务器(Media Server)相关的API端点
"""

import logging
from typing import List, Dict, Any, Optional
from datetime import datetime
from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field

from src.schemas.auth import User
from src.workflows.media_server_binding import bind_media_server_to_anime as bind_media_server_to_anime_workflow
from src.workflows.media_import_preparation import build_media_import_title
from src.services.service_container import get_database_service
from src.utils.auth import security
from src.services.task_manager import TaskManager
from src.services.scraper_manager import ScraperManager
from src.services.metadata_service import MetadataService
from src.services.service_container import get_media_server_service
from src.services.config_service import ConfigService
from src.services.ai_service import AIService
from src.rate_limiter import RateLimiter
from src.api.dependencies import (
    get_task_manager,
    get_scraper_manager,
    get_metadata_service,
    get_config_service,
    get_ai_service,
    get_rate_limiter,
    get_title_recognition_manager
)

router = APIRouter()
logger = logging.getLogger(__name__)


# ==================== Pydantic Models ====================

class MediaServerCreate(BaseModel):
    name: str
    providerName: str
    url: str
    apiToken: str
    isEnabled: bool = True
    selectedLibraries: List[str] = []
    filterRules: Dict[str, Any] = {}


class MediaServerUpdate(BaseModel):
    name: Optional[str] = None
    providerName: Optional[str] = None
    url: Optional[str] = None
    apiToken: Optional[str] = None
    isEnabled: Optional[bool] = None
    selectedLibraries: Optional[List[str]] = None
    filterRules: Optional[Dict[str, Any]] = None


class MediaServerResponse(BaseModel):
    id: int
    name: str
    providerName: str
    url: str
    apiToken: str
    isEnabled: bool
    selectedLibraries: List[str]
    filterRules: Dict[str, Any]
    createdAt: datetime
    updatedAt: datetime


class MediaServerTestResponse(BaseModel):
    success: bool
    message: str
    serverInfo: Optional[Dict[str, Any]] = None


class MediaLibraryInfo(BaseModel):
    id: str
    name: str
    type: str


class MediaItemResponse(BaseModel):
    id: int
    serverId: int
    mediaId: str
    libraryId: Optional[str]
    seriesId: Optional[str]
    seasonId: Optional[str]
    episodeId: Optional[str]
    title: str
    mediaType: str
    season: Optional[int]
    episode: Optional[int]
    year: Optional[int]
    tmdbId: Optional[str]
    tvdbId: Optional[str]
    imdbId: Optional[str]
    posterUrl: Optional[str]
    isImported: bool
    createdAt: datetime


class MediaItemUpdate(BaseModel):
    title: Optional[str] = None
    mediaType: Optional[str] = None
    season: Optional[int] = None
    episode: Optional[int] = None
    year: Optional[int] = None
    tmdbId: Optional[str] = None
    tvdbId: Optional[str] = None
    imdbId: Optional[str] = None
    posterUrl: Optional[str] = None


class MediaItemsImportRequest(BaseModel):
    itemIds: List[int]


class MediaServerScanRequest(BaseModel):
    """媒体服务器扫描请求"""
    library_ids: Optional[List[str]] = None


# ==================== Media Server Endpoints ====================

@router.get("/media-servers", response_model=List[MediaServerResponse], summary="获取所有媒体服务器")
async def get_media_servers(
    current_user: User = Depends(security.get_current_user)
):
    """获取所有媒体服务器配置"""
    db = get_database_service()
    async with db.transaction():
        servers = await db.media_server.get_all_media_servers()
    return servers


@router.post("/media-servers", response_model=MediaServerResponse, status_code=201, summary="添加媒体服务器")
async def create_media_server(
    payload: MediaServerCreate,
    current_user: User = Depends(security.get_current_user)
):
    """创建新的媒体服务器配置"""
    db = get_database_service()
    async with db.transaction():
        server = await db.media_server_crud.create(
            name=payload.name,
            provider_name=payload.providerName,
            url=payload.url,
            api_token=payload.apiToken,
            is_enabled=payload.isEnabled,
            selected_libraries=payload.selectedLibraries,
            filter_rules=payload.filterRules
        )
        server_id = server.id

    # 如果服务器启用，加载到管理器中
    if payload.isEnabled:
        manager = get_media_server_service()
        await manager.reload_server(server_id)

    # 返回创建的服务器
    async with db.transaction():
        server_dict = await db.media_server.get_media_server_by_id(server_id)

    if not server_dict:
        raise HTTPException(status_code=500, detail="创建媒体服务器后无法获取")

    return server_dict


@router.put("/media-servers/{server_id}", response_model=MediaServerResponse, summary="更新媒体服务器")
async def update_media_server(
    server_id: int,
    payload: MediaServerUpdate,
    current_user: User = Depends(security.get_current_user)
):
    """更新媒体服务器配置"""
    db = get_database_service()
    async with db.transaction():
        success = await db.media_server_crud.update(
            server_id,
            name=payload.name,
            provider_name=payload.providerName,
            url=payload.url,
            api_token=payload.apiToken,
            is_enabled=payload.isEnabled,
            selected_libraries=payload.selectedLibraries,
            filter_rules=payload.filterRules
        )

    if not success:
        raise HTTPException(status_code=404, detail="媒体服务器不存在")

    # 重新加载服务器实例，确保配置变更立即生效
    manager = get_media_server_service()
    await manager.reload_server(server_id)

    # 返回更新后的服务器
    async with db.transaction():
        server = await db.media_server.get_media_server_by_id(server_id)

    if not server:
        raise HTTPException(status_code=500, detail="更新媒体服务器后无法获取")

    return server


@router.delete("/media-servers/{server_id}", status_code=204, summary="删除媒体服务器")
async def delete_media_server(
    server_id: int,
    current_user: User = Depends(security.get_current_user)
):
    """删除媒体服务器配置(级联删除关联的媒体项)"""
    db = get_database_service()
    async with db.transaction():
        success = await db.media_server_crud.delete(server_id)

    if not success:
        raise HTTPException(status_code=404, detail="媒体服务器不存在")
    await get_media_server_service().remove_server(server_id)


@router.post("/media-servers/{server_id}/test", response_model=MediaServerTestResponse, summary="测试媒体服务器连接")
async def test_media_server_connection(
    server_id: int,
    current_user: User = Depends(security.get_current_user)
):
    """测试媒体服务器连接"""
    service = get_media_server_service()
    try:
        server_info = await service.test_connection(server_id)
        return MediaServerTestResponse(success=True, message="连接成功", serverInfo=server_info)
    except LookupError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except Exception as e:
        logger.error(f"测试媒体服务器连接失败: {e}", exc_info=True)
        return MediaServerTestResponse(success=False, message=str(e))


@router.get("/media-servers/{server_id}/libraries", response_model=List[MediaLibraryInfo], summary="获取媒体库列表")
async def get_media_server_libraries(
    server_id: int,
    current_user: User = Depends(security.get_current_user)
):
    """获取媒体服务器的媒体库列表"""
    service = get_media_server_service()
    try:
        return await service.get_libraries(server_id)
    except LookupError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except Exception as e:
        logger.error(f"获取媒体库列表失败: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"获取媒体库列表失败: {str(e)}") from e


@router.post("/media-servers/{server_id}/scan", status_code=202, summary="扫描媒体库")
async def scan_media_server_library(
    server_id: int,
    payload: MediaServerScanRequest,
    current_user: User = Depends(security.get_current_user),
    task_manager: TaskManager = Depends(get_task_manager)
):
    """扫描媒体服务器的媒体库"""
    manager = get_media_server_service()

    server_instance = manager.get_server(server_id)
    if not server_instance:
        raise HTTPException(status_code=404, detail="媒体服务器不存在")

    # 从数据库获取服务器配置以获取名称
    db = get_database_service()
    async with db.transaction():
        server_config = await db.media_server.get_media_server_by_id(server_id)

    server_name = server_config["name"] if server_config else f"服务器{server_id}"

    task_coro = task_manager.build_task_coro_factory(
        "media_scan", server_id=server_id, library_ids=payload.library_ids,
    )

    unique_key = f"scan-media-server-{server_id}"
    task_id, _ = await task_manager.submit_task(
        task_coro,
        title=f"扫描媒体服务器: {server_name}",
        queue_type="management",
        unique_key=unique_key,
        task_type="media_scan",
        task_parameters={"serverId": server_id, "libraryIds": payload.library_ids},
    )

    return {"message": "扫描任务已提交", "taskId": task_id}


# ==================== Media Item Endpoints ====================

@router.get("/media-items", response_model=Dict[str, Any], summary="获取媒体项列表")
async def get_media_items(
    server_id: Optional[int] = Query(None),
    is_imported: Optional[bool] = Query(None),
    media_type: Optional[str] = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(100, ge=1, le=500),
    current_user: User = Depends(security.get_current_user)
):
    """获取媒体项列表,支持过滤和分页"""
    db = get_database_service()
    async with db.transaction():
        result = await db.media_server.get_media_items(
            server_id=server_id,
            is_imported=is_imported,
            media_type=media_type,
            page=page,
            page_size=page_size
        )
    return result


@router.get("/media-works", response_model=Dict[str, Any], summary="获取作品列表(按作品分组)")
async def get_media_works(
    server_id: Optional[int] = Query(None),
    is_imported: Optional[bool] = Query(None),
    media_type: Optional[str] = Query(None),
    search: Optional[str] = Query(None),
    year_from: Optional[int] = Query(None, description="起始年份，闭区间"),
    year_to: Optional[int] = Query(None, description="结束年份，闭区间"),
    page: int = Query(1, ge=1),
    page_size: int = Query(100, ge=1, le=500),
    current_user: User = Depends(security.get_current_user)
):
    """获取作品列表(电影+电视剧组),按作品计数"""
    db = get_database_service()
    async with db.transaction():
        result = await db.media_server.get_media_works(
            server_id=server_id,
            is_imported=is_imported,
            media_type=media_type,
            search=search,
            year_from=year_from,
            year_to=year_to,
            page=page,
            page_size=page_size
        )
    return result


@router.get("/shows/{title}/seasons", response_model=List[Dict[str, Any]], summary="获取剧集的季度信息")
async def get_show_seasons(
    title: str,
    server_id: int = Query(...),
    current_user: User = Depends(security.get_current_user)
):
    """获取某部剧集的所有季度信息"""
    db = get_database_service()
    async with db.transaction():
        result = await db.media_server.get_show_seasons(server_id, title)
    return result


@router.get("/shows/{title}/seasons/{season}/episodes", response_model=Dict[str, Any], summary="获取某一季的分集列表")
async def get_season_episodes(
    title: str,
    season: int,
    server_id: int = Query(...),
    page: int = Query(1, ge=1),
    page_size: int = Query(100, ge=1, le=500),
    current_user: User = Depends(security.get_current_user)
):
    """获取某一季的所有集"""
    db = get_database_service()
    async with db.transaction():
        result = await db.media_server.get_season_episodes(
            server_id,
            title,
            season,
            page,
            page_size
        )
    return result


@router.put("/media-items/{item_id}", response_model=MediaItemResponse, summary="更新媒体项")
async def update_media_item(
    item_id: int,
    payload: MediaItemUpdate,
    current_user: User = Depends(security.get_current_user)
):
    """更新媒体项信息"""
    db = get_database_service()
    async with db.transaction():
        updated_item = await db.media_item.update(
            item_id, **payload.model_dump(exclude_none=True)
        )

        if not updated_item:
            raise HTTPException(status_code=404, detail="媒体项不存在")

        # ORM 实体仍在事务内，将其转换为完整的响应值快照。
        return {
            "id": updated_item.id,
            "serverId": updated_item.serverId,
            "mediaId": updated_item.mediaId,
            "libraryId": updated_item.libraryId,
            "seriesId": updated_item.seriesId,
            "seasonId": updated_item.seasonId,
            "episodeId": updated_item.episodeId,
            "mediaType": updated_item.mediaType,
            "title": updated_item.title,
            "season": updated_item.season,
            "episode": updated_item.episode,
            "year": updated_item.year,
            "tmdbId": updated_item.tmdbId,
            "tvdbId": updated_item.tvdbId,
            "imdbId": updated_item.imdbId,
            "posterUrl": updated_item.posterUrl,
            "isImported": updated_item.isImported,
            "createdAt": updated_item.createdAt,
        }


@router.delete("/media-items/{item_id}", status_code=204, summary="删除媒体项")
async def delete_media_item(
    item_id: int,
    current_user: User = Depends(security.get_current_user)
):
    """删除单个媒体项"""
    db = get_database_service()
    async with db.transaction():
        success = await db.media_item.delete(item_id)
        if not success:
            raise HTTPException(status_code=404, detail="媒体项不存在")


@router.post("/media-items/batch-delete", status_code=200, summary="批量删除媒体项")
async def batch_delete_media_items(
    payload: Dict[str, Any],
    current_user: User = Depends(security.get_current_user)
):
    """批量删除媒体项

    支持三种删除方式:
    1. 直接传 itemIds: List[int]
    2. shows: [{"serverId": int, "title": str}]
    3. seasons: [{"serverId": int, "title": str, "season": int}]
    """
    all_item_ids: set[int] = set()

    # 收集直接指定的item IDs
    item_ids = payload.get("itemIds") or []
    if isinstance(item_ids, list):
        for v in item_ids:
            try:
                all_item_ids.add(int(v))
            except (TypeError, ValueError):
                continue

    db = get_database_service()

    # 收集剧集组的所有episode IDs
    shows = payload.get("shows") or []
    if isinstance(shows, list):
        for show in shows:
            if not isinstance(show, dict):
                continue
            server_id = show.get("serverId")
            title = show.get("title")
            if server_id is None or not title:
                continue
            async with db.transaction():
                episode_ids = await db.media_server.get_episode_ids_by_show(
                    int(server_id),
                    title
                )
            all_item_ids.update(episode_ids)

    # 收集季度的所有episode IDs
    seasons = payload.get("seasons") or []
    if isinstance(seasons, list):
        for season in seasons:
            if not isinstance(season, dict):
                continue
            server_id = season.get("serverId")
            title = season.get("title")
            season_no = season.get("season")
            if server_id is None or not title or season_no is None:
                continue
            async with db.transaction():
                episode_ids = await db.media_server.get_episode_ids_by_season(
                    int(server_id),
                    title,
                    int(season_no)
                )
            all_item_ids.update(episode_ids)

    if not all_item_ids:
        return {"message": "没有要删除的项目"}

    async with db.transaction():
        count = await db.media_item.delete_batch(list(all_item_ids))

    return {"message": f"成功删除 {count} 个媒体项"}


class MediaItemsImportRequest(BaseModel):
    itemIds: Optional[List[int]] = None
    shows: Optional[List[Dict[str, Any]]] = None
    seasons: Optional[List[Dict[str, Any]]] = None


@router.post("/media-items/import", status_code=202, summary="导入选中的媒体项")
async def import_media_items(
    payload: MediaItemsImportRequest,
    current_user: User = Depends(security.get_current_user),
    task_manager: TaskManager = Depends(get_task_manager),
    scraper_manager: ScraperManager = Depends(get_scraper_manager),
    metadata_manager: MetadataService = Depends(get_metadata_service),
    config_service: ConfigService = Depends(get_config_service),
    ai_service: AIService = Depends(get_ai_service),
    rate_limiter: RateLimiter = Depends(get_rate_limiter),
    title_recognition_manager = Depends(get_title_recognition_manager)
):
    """导入选中的媒体项(触发webhook式搜索和弹幕下载)"""
    all_item_ids = set()

    # 收集直接指定的item IDs
    if payload.itemIds:
        all_item_ids.update(payload.itemIds)

    db = get_database_service()

    # 收集剧集组的所有episode IDs
    if payload.shows:
        for show in payload.shows:
            async with db.transaction():
                episode_ids = await db.media_server.get_episode_ids_by_show(
                    show['serverId'],
                    show['title']
                )
            all_item_ids.update(episode_ids)

    # 收集季度的所有episode IDs
    if payload.seasons:
        for season in payload.seasons:
            async with db.transaction():
                episode_ids = await db.media_server.get_episode_ids_by_season(
                    season['serverId'],
                    season['title'],
                    season['season']
                )
            all_item_ids.update(episode_ids)

    if not all_item_ids:
        return {"message": "没有要导入的项目"}

    # 提交导入任务
    item_ids_list = list(all_item_ids)
    # 生成基于 item_ids 的 unique_key，以区分不同批次的导入任务
    sorted_ids = sorted(item_ids_list)
    unique_key = f"media-import-{hash(tuple(sorted_ids))}"

    task_title = await build_media_import_title(item_ids_list)

    task_coro = task_manager.build_task_coro_factory(
        "import_media_items",
        item_ids=item_ids_list, task_manager=task_manager,
        scraper_manager=scraper_manager, metadata_manager=metadata_manager,
        config_service=config_service, ai_service=ai_service,
        rate_limiter=rate_limiter,
        title_recognition_manager=title_recognition_manager,
    )
    task_id, _ = await task_manager.submit_task(
        task_coro,
        title=task_title,
        queue_type="download",
        unique_key=unique_key,
        task_type="import_media_items",
        task_parameters={"itemIds": item_ids_list}
    )

    return {"message": "媒体项导入任务已提交", "taskId": task_id}



@router.get("/media-items/unimported-count", summary="获取未导入媒体项数量")
async def get_unimported_media_count(
    server_id: int = Query(..., description="媒体服务器ID"),
    media_type: Optional[str] = Query(None, description="媒体类型过滤"),
    current_user: User = Depends(security.get_current_user)
):
    """获取指定服务器下未导入的媒体项数量"""
    db = get_database_service()
    async with db.transaction():
        count = await db.media_server.get_unimported_count(server_id, media_type)
    return {"count": count}


@router.post("/media-items/import-all-unimported", status_code=202, summary="一键导入全部未导入的媒体项")
async def import_all_unimported_media_items(
    payload: Dict[str, Any],
    current_user: User = Depends(security.get_current_user),
    task_manager: TaskManager = Depends(get_task_manager),
    scraper_manager: ScraperManager = Depends(get_scraper_manager),
    metadata_manager: MetadataService = Depends(get_metadata_service),
    config_service: ConfigService = Depends(get_config_service),
    ai_service: AIService = Depends(get_ai_service),
    rate_limiter: RateLimiter = Depends(get_rate_limiter),
    title_recognition_manager = Depends(get_title_recognition_manager)
):
    """一键导入指定服务器下所有未导入的媒体项。

    why：原实现在此处同步调用 crud.get_unimported_item_ids 统计未导入清单，
    该查询含三表 JOIN 关联子查询且标题比对无法命中索引，媒体库大时耗时数十秒，
    接口长时间不返回导致用户误判功能失效（issue #441）。
    现将统计过程下沉到任务内部，接口只做参数校验并立即返回 taskId。
    """
    server_id = payload.get("serverId")
    media_type = payload.get("mediaType")

    if not server_id:
        raise HTTPException(status_code=400, detail="serverId 是必填参数")

    # why：unique_key 不再包含未导入数量（原先为 f"...-{len(item_ids)}"）。
    # 数量此刻尚未统计，且以数量入 key 会导致"数量恰好相同的重复提交"被静默去重。
    # 改为按 服务器+类型 维度去重，重复提交会明确返回 409。
    unique_key = f"media-import-all-{server_id}-{media_type or 'all'}"

    task_coro = task_manager.build_task_coro_factory(
        "import_all_unimported",
        server_id=server_id, media_type=media_type, task_manager=task_manager,
        scraper_manager=scraper_manager, metadata_manager=metadata_manager,
        config_service=config_service, ai_service=ai_service,
        rate_limiter=rate_limiter,
        title_recognition_manager=title_recognition_manager,
    )
    task_id, _ = await task_manager.submit_task(
        task_coro,
        title="一键导入全部未导入",
        queue_type="download",
        unique_key=unique_key,
        task_type="import_all_unimported",
        task_parameters={"serverId": server_id, "mediaType": media_type}
    )

    return {
        "message": "导入任务已提交，请在任务管理器查看进度",
        "taskId": task_id
    }


# ==================== 手动反查并绑定媒体服务器条目 ====================

class MediaServerLookupRequest(BaseModel):
    """反查请求：按标题在指定媒体服务器中搜索候选条目"""
    keyword: str = Field(..., min_length=1, description="搜索关键词（作品标题）")
    mediaType: Optional[str] = Field(None, description="限定类型：movie / tv_series，留空则全部")


class MediaServerLookupItem(BaseModel):
    """反查结果中的单个候选条目（顶层 Series / Movie）"""
    itemId: str = Field(..., description="媒体服务器中的条目 ID")
    title: str
    mediaType: Optional[str] = None
    year: Optional[int] = None
    seriesId: Optional[str] = Field(None, description="Series 级 ID（剧集才有）")
    seasonId: Optional[str] = Field(None, description="Season 级 ID（分季才有）")
    season: Optional[int] = None
    tmdbId: Optional[str] = None
    imdbId: Optional[str] = None
    posterUrl: Optional[str] = None


class MediaServerBindRequest(BaseModel):
    """绑定请求：把选中的媒体服务器条目写入作品的绑定字段"""
    serverId: int = Field(..., description="媒体服务器配置 ID")
    seriesId: str = Field(..., min_length=1, description="Series/Movie 级 ID")
    seasonId: Optional[str] = Field(None, description="Season 级 ID，剧集分季时提供")


async def _resolve_server_instance(server_id: int):
    """取得媒体服务器客户端实例。优先复用 manager 中已加载的，否则按配置临时创建。"""
    service = get_media_server_service()
    try:
        server, temporary = await service.resolve_server(server_id)
    except LookupError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return server, server if temporary else None


@router.post("/media-server/{serverId}/lookup", summary="反查媒体服务器条目")
async def lookup_media_server_items(
    serverId: int,
    payload: MediaServerLookupRequest,
    current_user: User = Depends(security.get_current_user)
) -> List[MediaServerLookupItem]:
    """按关键词搜索媒体服务器中的条目，返回候选列表供用户选择绑定。

    走媒体服务器原生搜索接口（Emby/Jellyfin 的 SearchTerm、Plex 的 /search），
    单次请求即可返回顶层条目，不拉全库、不展开分集，媒体库规模再大也不影响速度。
    """
    server, temp_server = await _resolve_server_instance(serverId)
    try:
        # 1. 测试连接（失败会抛异常，统一转成 503）
        try:
            await server.test_connection()
        except Exception as e:
            raise HTTPException(status_code=503, detail=f"媒体服务器连接失败: {e}")

        # 2. 调用原生搜索（服务端完成匹配）
        keyword = payload.keyword.strip()
        try:
            found = await server.search_items(
                keyword=keyword,
                media_type=payload.mediaType,
                limit=50,
            )
        except Exception as e:
            logger.error(f"反查媒体服务器 {serverId} 失败: {e}", exc_info=True)
            raise HTTPException(status_code=502, detail=f"搜索失败: {e}")

        # 3. 转换为响应模型
        candidates = [
            MediaServerLookupItem(
                itemId=item.media_id,
                title=item.title,
                mediaType=item.media_type,
                year=item.year,
                seriesId=item.series_id,
                seasonId=item.season_id,
                season=item.season,
                tmdbId=item.tmdb_id,
                imdbId=item.imdb_id,
                posterUrl=item.poster_url,
            )
            for item in found
            if item.media_id and item.title
        ]

        # 4. 按标题匹配度排序：完全相同 > 前缀命中 > 其他
        keyword_lower = keyword.lower()

        def _match_score(item: MediaServerLookupItem) -> int:
            title_lower = (item.title or "").lower()
            if title_lower == keyword_lower:
                return 2
            if title_lower.startswith(keyword_lower):
                return 1
            return 0

        candidates.sort(key=_match_score, reverse=True)
        return candidates

    finally:
        if temp_server:
            await temp_server.close()


@router.put("/library/anime/{animeId}/bind-media-server", summary="绑定媒体服务器条目")
async def bind_media_server_to_anime(
    animeId: int,
    payload: MediaServerBindRequest,
    current_user: User = Depends(security.get_current_user)
):
    """将反查得到的媒体服务器条目绑定到指定作品，写入 anime_metadata 表。

    绑定后，webhook 删除事件可通过这些 ID 自动清理对应弹幕库条目。
    """
    try:
        server_type = await bind_media_server_to_anime_workflow(
            animeId, payload.serverId, payload.seriesId, payload.seasonId,
        )
    except LookupError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e

    logger.info(f"用户 '{current_user.username}' 将作品 {animeId} 绑定到媒体服务器 {server_type} (SeriesId={payload.seriesId})")
    return {"message": "绑定成功"}

