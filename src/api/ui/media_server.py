"""
媒体服务器(Media Server)相关的API端点
"""

import logging
from typing import List, Dict, Any, Optional, Callable
from datetime import datetime
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession
from pydantic import BaseModel

from src.db import crud, models, get_db_session, ConfigManager
from src import security, tasks
from src.services import TaskManager, ScraperManager, MetadataSourceManager, get_media_server_manager
from src.ai.ai_matcher_manager import AIMatcherManager
from src.rate_limiter import RateLimiter
from src.media_servers import EmbyMediaServer, JellyfinMediaServer, PlexMediaServer
from src.api.dependencies import (
    get_task_manager,
    get_scraper_manager,
    get_metadata_manager,
    get_config_manager,
    get_ai_matcher_manager,
    get_rate_limiter,
    get_title_recognition_manager
)

# 从 crud 导入需要的子模块
media_server_crud = crud.media_server

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
    session: AsyncSession = Depends(get_db_session),
    current_user: models.User = Depends(security.get_current_user)
):
    """获取所有媒体服务器配置"""
    servers = await crud.get_all_media_servers(session)
    return servers


@router.post("/media-servers", response_model=MediaServerResponse, status_code=201, summary="添加媒体服务器")
async def create_media_server(
    payload: MediaServerCreate,
    session: AsyncSession = Depends(get_db_session),
    current_user: models.User = Depends(security.get_current_user)
):
    """创建新的媒体服务器配置"""
    server_id = await crud.create_media_server(
        session,
        name=payload.name,
        provider_name=payload.providerName,
        url=payload.url,
        api_token=payload.apiToken,
        is_enabled=payload.isEnabled,
        selected_libraries=payload.selectedLibraries,
        filter_rules=payload.filterRules
    )
    await session.commit()

    # 如果服务器启用，加载到管理器中
    if payload.isEnabled:
        manager = get_media_server_manager()
        await manager.reload_server(server_id)

    # 返回创建的服务器
    server = await crud.get_media_server_by_id(session, server_id)
    if not server:
        raise HTTPException(status_code=500, detail="创建媒体服务器后无法获取")

    return server


@router.put("/media-servers/{server_id}", response_model=MediaServerResponse, summary="更新媒体服务器")
async def update_media_server(
    server_id: int,
    payload: MediaServerUpdate,
    session: AsyncSession = Depends(get_db_session),
    current_user: models.User = Depends(security.get_current_user)
):
    """更新媒体服务器配置"""
    success = await crud.update_media_server(
        session,
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

    await session.commit()

    # 重新加载服务器实例，确保配置变更立即生效
    manager = get_media_server_manager()
    await manager.reload_server(server_id)

    # 返回更新后的服务器
    server = await crud.get_media_server_by_id(session, server_id)
    if not server:
        raise HTTPException(status_code=500, detail="更新媒体服务器后无法获取")

    return server


@router.delete("/media-servers/{server_id}", status_code=204, summary="删除媒体服务器")
async def delete_media_server(
    server_id: int,
    session: AsyncSession = Depends(get_db_session),
    current_user: models.User = Depends(security.get_current_user)
):
    """删除媒体服务器配置(级联删除关联的媒体项)"""
    success = await crud.delete_media_server(session, server_id)
    if not success:
        raise HTTPException(status_code=404, detail="媒体服务器不存在")
    await session.commit()


@router.post("/media-servers/{server_id}/test", response_model=MediaServerTestResponse, summary="测试媒体服务器连接")
async def test_media_server_connection(
    server_id: int,
    session: AsyncSession = Depends(get_db_session),
    current_user: models.User = Depends(security.get_current_user)
):
    """测试媒体服务器连接"""
    manager = get_media_server_manager()

    try:
        # 先尝试从manager中获取已加载的服务器实例
        server = manager.servers.get(server_id)

        # 如果没有找到(可能是禁用的服务器),从数据库读取配置并临时创建实例
        if not server:
            config = await crud.get_media_server_by_id(session, server_id)
            if not config:
                raise HTTPException(status_code=404, detail="媒体服务器不存在")

            # 根据类型创建临时实例
            SERVER_CLASSES = {
                'emby': EmbyMediaServer,
                'jellyfin': JellyfinMediaServer,
                'plex': PlexMediaServer,
            }

            provider_name = config['providerName']
            if provider_name not in SERVER_CLASSES:
                raise HTTPException(status_code=400, detail=f"不支持的服务器类型: {provider_name}")

            server_class = SERVER_CLASSES[provider_name]
            server = server_class(
                url=config['url'],
                api_token=config['apiToken']
            )

        server_info = await server.test_connection()
        return MediaServerTestResponse(success=True, message="连接成功", serverInfo=server_info)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"测试媒体服务器连接失败: {e}")
        return MediaServerTestResponse(success=False, message=str(e))


@router.get("/media-servers/{server_id}/libraries", response_model=List[MediaLibraryInfo], summary="获取媒体库列表")
async def get_media_server_libraries(
    server_id: int,
    session: AsyncSession = Depends(get_db_session),
    current_user: models.User = Depends(security.get_current_user)
):
    """获取媒体服务器的媒体库列表"""
    manager = get_media_server_manager()

    # 先尝试从manager中获取已加载的服务器实例
    server = manager.servers.get(server_id)

    # 如果没有找到,从数据库读取配置并临时创建实例
    if not server:
        config = await crud.get_media_server_by_id(session, server_id)
        if not config:
            raise HTTPException(status_code=404, detail="媒体服务器不存在")

        # 根据类型创建临时实例
        SERVER_CLASSES = {
            'emby': EmbyMediaServer,
            'jellyfin': JellyfinMediaServer,
            'plex': PlexMediaServer,
        }

        provider_name = config['providerName']
        if provider_name not in SERVER_CLASSES:
            raise HTTPException(status_code=400, detail=f"不支持的服务器类型: {provider_name}")

        server_class = SERVER_CLASSES[provider_name]
        server = server_class(
            url=config['url'],
            api_token=config['apiToken']
        )

    try:
        libraries = await server.get_libraries()
        return libraries
    except Exception as e:
        logger.error(f"获取媒体库列表失败: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"获取媒体库列表失败: {str(e)}")


@router.post("/media-servers/{server_id}/scan", status_code=202, summary="扫描媒体库")
async def scan_media_server_library(
    server_id: int,
    payload: MediaServerScanRequest,
    session: AsyncSession = Depends(get_db_session),
    current_user: models.User = Depends(security.get_current_user),
    task_manager: TaskManager = Depends(get_task_manager)
):
    """扫描媒体服务器的媒体库"""
    manager = get_media_server_manager()

    server_instance = manager.servers.get(server_id)
    if not server_instance:
        raise HTTPException(status_code=404, detail="媒体服务器不存在")

    # 从数据库获取服务器配置以获取名称
    server_config = await crud.get_media_server_by_id(session, server_id)
    server_name = server_config.get('name', f'服务器{server_id}') if server_config else f'服务器{server_id}'

    # 创建协程工厂函数
    def create_scan_task(s: AsyncSession, cb: Callable):
        return tasks.scan_media_server_library(
            server_id=server_id,
            library_ids=payload.library_ids,
            session=s,
            progress_callback=cb
        )

    # 提交扫描任务到管理队列,使用unique_key确保同一时间只能执行一个扫描任务
    unique_key = f"scan-media-server-{server_id}"
    task_id, _ = await task_manager.submit_task(
        create_scan_task,
        title=f"扫描媒体服务器: {server_name}",
        queue_type="management",
        unique_key=unique_key
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
    session: AsyncSession = Depends(get_db_session),
    current_user: models.User = Depends(security.get_current_user)
):
    """获取媒体项列表,支持过滤和分页"""
    result = await crud.get_media_items(
        session,
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
    session: AsyncSession = Depends(get_db_session),
    current_user: models.User = Depends(security.get_current_user)
):
    """获取作品列表(电影+电视剧组),按作品计数"""
    result = await crud.get_media_works(
        session,
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
    session: AsyncSession = Depends(get_db_session),
    current_user: models.User = Depends(security.get_current_user)
):
    """获取某部剧集的所有季度信息"""
    result = await crud.get_show_seasons(session, server_id, title)
    return result


@router.get("/shows/{title}/seasons/{season}/episodes", response_model=Dict[str, Any], summary="获取某一季的分集列表")
async def get_season_episodes(
    title: str,
    season: int,
    server_id: int = Query(...),
    page: int = Query(1, ge=1),
    page_size: int = Query(100, ge=1, le=500),
    session: AsyncSession = Depends(get_db_session),
    current_user: models.User = Depends(security.get_current_user)
):
    """获取某一季的所有集"""
    result = await crud.get_season_episodes(
        session,
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
    session: AsyncSession = Depends(get_db_session),
    current_user: models.User = Depends(security.get_current_user)
):
    """更新媒体项信息"""
    success = await crud.update_media_item(
        session,
        item_id,
        title=payload.title,
        media_type=payload.mediaType,
        season=payload.season,
        episode=payload.episode,
        year=payload.year,
        tmdb_id=payload.tmdbId,
        tvdb_id=payload.tvdbId,
        imdb_id=payload.imdbId,
        poster_url=payload.posterUrl
    )

    if not success:
        raise HTTPException(status_code=404, detail="媒体项不存在")

    await session.commit()

    # 返回更新后的媒体项
    result = await crud.get_media_items(session, page=1, page_size=1)
    items = result.get('items', [])
    if not items:
        raise HTTPException(status_code=500, detail="更新媒体项后无法获取")

    return items[0]


@router.delete("/media-items/{item_id}", status_code=204, summary="删除媒体项")
async def delete_media_item(
    item_id: int,
    session: AsyncSession = Depends(get_db_session),
    current_user: models.User = Depends(security.get_current_user)
):
    """删除单个媒体项"""
    success = await crud.delete_media_item(session, item_id)
    if not success:
        raise HTTPException(status_code=404, detail="媒体项不存在")
    await session.commit()


@router.post("/media-items/batch-delete", status_code=200, summary="批量删除媒体项")
async def batch_delete_media_items(
    payload: Dict[str, Any],
    session: AsyncSession = Depends(get_db_session),
    current_user: models.User = Depends(security.get_current_user)
):
    """批量删除媒体项

    支持三种删除方式:
    1. 直接传 itemIds: List[int]
    2. shows: [{"serverId": int, "title": str}]
    3. seasons: [{"serverId": int, "title": str, "season": int}]
    """
    from src.db.crud.media_server import get_episode_ids_by_show, get_episode_ids_by_season

    all_item_ids: set[int] = set()

    # 收集直接指定的item IDs
    item_ids = payload.get("itemIds") or []
    if isinstance(item_ids, list):
        for v in item_ids:
            try:
                all_item_ids.add(int(v))
            except (TypeError, ValueError):
                continue

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
            episode_ids = await get_episode_ids_by_show(
                session,
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
            episode_ids = await get_episode_ids_by_season(
                session,
                int(server_id),
                title,
                int(season_no)
            )
            all_item_ids.update(episode_ids)

    if not all_item_ids:
        return {"message": "没有要删除的项目"}

    count = await crud.delete_media_items_batch(session, list(all_item_ids))
    await session.commit()
    return {"message": f"成功删除 {count} 个媒体项"}


class MediaItemsImportRequest(BaseModel):
    itemIds: Optional[List[int]] = None
    shows: Optional[List[Dict[str, Any]]] = None
    seasons: Optional[List[Dict[str, Any]]] = None


@router.post("/media-items/import", status_code=202, summary="导入选中的媒体项")
async def import_media_items(
    payload: MediaItemsImportRequest,
    session: AsyncSession = Depends(get_db_session),
    current_user: models.User = Depends(security.get_current_user),
    task_manager: TaskManager = Depends(get_task_manager),
    scraper_manager: ScraperManager = Depends(get_scraper_manager),
    metadata_manager: MetadataSourceManager = Depends(get_metadata_manager),
    config_manager: ConfigManager = Depends(get_config_manager),
    ai_matcher_manager: AIMatcherManager = Depends(get_ai_matcher_manager),
    rate_limiter: RateLimiter = Depends(get_rate_limiter),
    title_recognition_manager = Depends(get_title_recognition_manager)
):
    """导入选中的媒体项(触发webhook式搜索和弹幕下载)"""
    from src.db.crud.media_server import get_episode_ids_by_show, get_episode_ids_by_season

    all_item_ids = set()

    # 收集直接指定的item IDs
    if payload.itemIds:
        all_item_ids.update(payload.itemIds)

    # 收集剧集组的所有episode IDs
    if payload.shows:
        for show in payload.shows:
            episode_ids = await get_episode_ids_by_show(
                session,
                show['serverId'],
                show['title']
            )
            all_item_ids.update(episode_ids)

    # 收集季度的所有episode IDs
    if payload.seasons:
        for season in payload.seasons:
            episode_ids = await get_episode_ids_by_season(
                session,
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

    # 查询媒体项标题，用于生成可读的任务标题
    from src.db.orm_models import MediaItem
    from sqlalchemy import select, func
    title_stmt = select(MediaItem.title, func.count(MediaItem.id).label('cnt')).where(
        MediaItem.id.in_(item_ids_list)
    ).group_by(MediaItem.title)
    title_result = await session.execute(title_stmt)
    title_groups = title_result.all()  # [(title, count), ...]

    if len(title_groups) == 1:
        name, cnt = title_groups[0]
        task_title = f"导入媒体项: {name}" if cnt == 1 else f"导入媒体项: {name} ({cnt}集)"
    elif len(title_groups) <= 3:
        task_title = f"导入媒体项: {', '.join(t.title for t in title_groups)}"
    else:
        first_titles = ', '.join(t.title for t in title_groups[:2])
        task_title = f"导入媒体项: {first_titles} 等{len(title_groups)}部"

    task_id, _ = await task_manager.submit_task(
        lambda session, progress_callback: tasks.import_media_items(
            item_ids_list,
            session,
            task_manager,
            progress_callback,
            scraper_manager=scraper_manager,
            metadata_manager=metadata_manager,
            config_manager=config_manager,
            ai_matcher_manager=ai_matcher_manager,
            rate_limiter=rate_limiter,
            title_recognition_manager=title_recognition_manager
        ),
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
    session: AsyncSession = Depends(get_db_session),
    current_user: models.User = Depends(security.get_current_user)
):
    """获取指定服务器下未导入的媒体项数量"""
    count = await crud.get_unimported_count(session, server_id, media_type)
    return {"count": count}


@router.post("/media-items/import-all-unimported", status_code=202, summary="一键导入全部未导入的媒体项")
async def import_all_unimported_media_items(
    payload: Dict[str, Any],
    session: AsyncSession = Depends(get_db_session),
    current_user: models.User = Depends(security.get_current_user),
    task_manager: TaskManager = Depends(get_task_manager),
    scraper_manager: ScraperManager = Depends(get_scraper_manager),
    metadata_manager: MetadataSourceManager = Depends(get_metadata_manager),
    config_manager: ConfigManager = Depends(get_config_manager),
    ai_matcher_manager: AIMatcherManager = Depends(get_ai_matcher_manager),
    rate_limiter: RateLimiter = Depends(get_rate_limiter),
    title_recognition_manager = Depends(get_title_recognition_manager)
):
    """一键导入指定服务器下所有未导入的媒体项"""
    server_id = payload.get("serverId")
    media_type = payload.get("mediaType")

    if not server_id:
        raise HTTPException(status_code=400, detail="serverId 是必填参数")

    # 获取所有未导入的 item IDs
    item_ids_list = await crud.get_unimported_item_ids(session, server_id, media_type)

    if not item_ids_list:
        return {"message": "没有未导入的媒体项", "count": 0}

    # 生成 unique_key
    unique_key = f"media-import-all-{server_id}-{len(item_ids_list)}"

    task_id, _ = await task_manager.submit_task(
        lambda session, progress_callback: tasks.import_media_items(
            item_ids_list,
            session,
            task_manager,
            progress_callback,
            scraper_manager=scraper_manager,
            metadata_manager=metadata_manager,
            config_manager=config_manager,
            ai_matcher_manager=ai_matcher_manager,
            rate_limiter=rate_limiter,
            title_recognition_manager=title_recognition_manager
        ),
        title=f"一键导入全部未导入: 共{len(item_ids_list)}个",
        queue_type="download",
        unique_key=unique_key,
        task_type="import_media_items",
        task_parameters={"itemIds": item_ids_list}
    )

    return {
        "message": f"已提交导入任务，共 {len(item_ids_list)} 个未导入媒体项",
        "taskId": task_id,
        "count": len(item_ids_list)
    }