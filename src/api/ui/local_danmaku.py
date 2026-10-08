"""
UI API - 本地弹幕管理
🚀 薄层路由 - 委托给 workflows/local_danmaku
"""
import logging
from typing import Optional, List

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel

from src.utils.auth import security
from src.schemas.auth import User
from src.services.database_service import DatabaseService
from src.services.service_container import get_database_service
from src.services.task_manager import TaskManager
from src.services.service_container import get_task_manager
from src.api.dependencies import get_config_service
from src.services.config_service import ConfigService

# Workflow 导入
from src.workflows.local_danmaku import (
    browse_directory_flow,
    create_folder_flow,
    delete_folder_flow,
    scan_local_danmaku_flow,
    get_local_items_flow,
    get_local_works_flow,
    get_movie_files_flow,
    get_show_seasons_flow,
    get_season_episodes_flow,
    update_local_item_flow,
    delete_local_item_flow,
    batch_delete_local_items_flow,
    import_local_items_flow,
)

logger = logging.getLogger(__name__)
router = APIRouter()


# ==================== Pydantic 模型 ====================

class BatchDeleteRequest(BaseModel):
    """批量删除请求"""
    itemIds: List[int]
    deleteFiles: bool = False


class ImportRequest(BaseModel):
    """导入请求"""
    itemIds: List[int]
    importOptions: dict = {}


class UpdateItemRequest(BaseModel):
    """更新本地项请求"""
    title: Optional[str] = None
    season: Optional[int] = None
    episode: Optional[int] = None
    year: Optional[int] = None
    mediaType: Optional[str] = None


class CreateFolderRequest(BaseModel):
    """创建文件夹请求"""
    parentPath: str
    folderName: str


# ==================== 文件系统浏览 ====================

@router.get("/local-danmaku/browse", summary="浏览本地文件系统")
async def browse_directory(
    path: str = Query(..., description="目录路径"),
    sort: str = Query("name", description="排序方式: name/time"),
    current_user: User = Depends(security.get_current_user),
):
    """浏览本地目录 - 🚀 薄层路由"""
    try:
        items = await browse_directory_flow(path, sort)
        return {"items": items, "path": path}
    except ValueError as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=str(e))
    except PermissionError as e:
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail=str(e))


@router.post("/local-danmaku/folder", status_code=status.HTTP_201_CREATED, summary="创建文件夹")
async def create_folder(
    request: CreateFolderRequest,
    current_user: User = Depends(security.get_current_user),
):
    """创建新文件夹 - 🚀 薄层路由"""
    try:
        result = await create_folder_flow(request.parentPath, request.folderName)
        return result
    except ValueError as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=str(e))
    except PermissionError as e:
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail=str(e))


@router.delete("/local-danmaku/folder", summary="删除文件夹")
async def delete_folder(
    path: str = Query(..., description="文件夹路径"),
    current_user: User = Depends(security.get_current_user),
):
    """删除空文件夹 - 🚀 薄层路由"""
    try:
        result = await delete_folder_flow(path)
        return result
    except ValueError as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=str(e))
    except PermissionError as e:
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail=str(e))


# ==================== 配置管理 ====================

@router.get("/local-danmaku/last-path", summary="获取上次扫描路径")
async def get_last_scan_path(
    config_service: ConfigService = Depends(get_config_service),
    current_user: User = Depends(security.get_current_user),
):
    """获取上次扫描的路径 - 🚀 薄层路由"""
    last_path = await config_service.get("local_danmaku_last_scan_path", "")
    return {"lastPath": last_path}


@router.post("/local-danmaku/save-path", summary="保存扫描路径")
async def save_scan_path(
    path: str = Query(..., description="扫描路径"),
    config_service: ConfigService = Depends(get_config_service),
    current_user: User = Depends(security.get_current_user),
):
    """保存上次扫描路径。"""
    await config_service.set("local_danmaku_last_scan_path", path)
    return {"message": "路径已保存", "path": path}




# ==================== 扫描 ====================

@router.post("/local-danmaku/scan", status_code=status.HTTP_202_ACCEPTED, summary="扫描本地弹幕")
async def scan_local_danmaku(
    path: str = Query(..., description="扫描路径"),
    db: DatabaseService = Depends(get_database_service),
    current_user: User = Depends(security.get_current_user),
):
    """扫描指定目录下的本地弹幕文件 - 🚀 薄层路由"""
    try:
        # 复用注入的服务，事务由扫描编排自持。
        result = await scan_local_danmaku_flow(path, db)
        return result
    except ValueError as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=str(e))
    except Exception as e:
        logger.error(f"扫描失败: {e}", exc_info=True)
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"扫描失败: {str(e)}")


# ==================== 查询 ====================

@router.get("/local-danmaku/items", summary="获取本地弹幕项列表")
async def get_local_items(
    is_imported: Optional[bool] = Query(None, description="是否已导入"),
    media_type: Optional[str] = Query(None, description="媒体类型"),
    page: int = Query(1, ge=1, description="页码"),
    page_size: int = Query(100, ge=1, le=500, description="每页数量"),
    db: DatabaseService = Depends(get_database_service),
    current_user: User = Depends(security.get_current_user),
):
    """获取本地弹幕项列表 - 🚀 薄层路由"""
    result = await get_local_items_flow(db, is_imported, media_type, page, page_size)
    return result


@router.get("/local-danmaku/works", summary="获取本地作品列表")
async def get_local_works(
    is_imported: Optional[bool] = Query(None, description="是否已导入"),
    media_type: Optional[str] = Query(None, description="媒体类型"),
    year_from: Optional[int] = Query(None, description="起始年份"),
    year_to: Optional[int] = Query(None, description="结束年份"),
    page: int = Query(1, ge=1, description="页码"),
    page_size: int = Query(100, ge=1, le=500, description="每页数量"),
    db: DatabaseService = Depends(get_database_service),
    current_user: User = Depends(security.get_current_user),
):
    """获取本地作品列表（按作品分组） - 🚀 薄层路由"""
    result = await get_local_works_flow(db, is_imported, media_type, year_from, year_to, page, page_size)
    return result


@router.get("/local-danmaku/movies/{title}/files", summary="获取电影文件")
async def get_local_movie_files(
    title: str,
    year: Optional[int] = Query(None, description="年份"),
    page: int = Query(1, ge=1, description="页码"),
    page_size: int = Query(100, ge=1, le=500, description="每页数量"),
    db: DatabaseService = Depends(get_database_service),
    current_user: User = Depends(security.get_current_user),
):
    """获取电影的所有弹幕文件 - 🚀 薄层路由"""
    result = await get_movie_files_flow(db, title, year, page, page_size)
    return result


@router.get("/local-danmaku/shows/{title}/seasons", summary="获取剧集季度")
async def get_local_show_seasons(
    title: str,
    db: DatabaseService = Depends(get_database_service),
    current_user: User = Depends(security.get_current_user),
):
    """获取本地剧集的所有季度 - 🚀 薄层路由"""
    seasons = await get_show_seasons_flow(db, title)
    return {"seasons": seasons}


@router.get("/local-danmaku/shows/{title}/seasons/{season}/episodes", summary="获取季度分集")
async def get_local_season_episodes(
    title: str,
    season: int,
    page: int = Query(1, ge=1, description="页码"),
    page_size: int = Query(100, ge=1, le=500, description="每页数量"),
    db: DatabaseService = Depends(get_database_service),
    current_user: User = Depends(security.get_current_user),
):
    """获取本地某一季的所有集 - 🚀 薄层路由"""
    result = await get_season_episodes_flow(db, title, season, page, page_size)
    return result


# ==================== 管理 ====================

@router.put("/local-danmaku/items/{item_id}", summary="更新本地项")
async def update_local_item(
    item_id: int,
    request: UpdateItemRequest,
    db: DatabaseService = Depends(get_database_service),
    current_user: User = Depends(security.get_current_user),
):
    """更新本地弹幕项信息 - 🚀 薄层路由"""
    try:
        update_data = request.dict(exclude_none=True)
        result = await update_local_item_flow(db, item_id, update_data)
        return result
    except ValueError as e:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=str(e))


@router.delete("/local-danmaku/items/{item_id}", summary="删除本地项")
async def delete_local_item(
    item_id: int,
    delete_file: bool = Query(False, description="是否删除物理文件"),
    db: DatabaseService = Depends(get_database_service),
    current_user: User = Depends(security.get_current_user),
):
    """删除本地弹幕项 - 🚀 薄层路由"""
    try:
        result = await delete_local_item_flow(db, item_id, delete_file)
        return result
    except ValueError as e:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=str(e))


@router.post("/local-danmaku/items/batch-delete", summary="批量删除本地项")
async def batch_delete_local_items(
    request: BatchDeleteRequest,
    db: DatabaseService = Depends(get_database_service),
    current_user: User = Depends(security.get_current_user),
):
    """批量删除本地弹幕项 - 🚀 薄层路由"""
    result = await batch_delete_local_items_flow(db, request.itemIds, request.deleteFiles)
    return result


# ==================== 导入 ====================

@router.post("/local-danmaku/import", status_code=status.HTTP_202_ACCEPTED, summary="导入本地弹幕")
async def import_local_items(
    request: ImportRequest,
    db: DatabaseService = Depends(get_database_service),
    task_manager: TaskManager = Depends(get_task_manager),
    current_user: User = Depends(security.get_current_user),
):
    """将本地弹幕项导入到正式数据库 - 🚀 薄层路由"""
    try:
        result = await import_local_items_flow(db, task_manager, request.itemIds, request.importOptions)
        return result
    except ValueError as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=str(e))
