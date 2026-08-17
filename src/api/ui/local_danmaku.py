"""
本地弹幕扫描相关API端点
"""
import logging
import os
import stat
import shutil
from datetime import datetime
from pathlib import Path
from typing import Dict, Any, List, Optional
from fastapi import APIRouter, Depends, Query, HTTPException, status, Request
from sqlalchemy.ext.asyncio import AsyncSession
from pydantic import BaseModel

from src.db import models, get_db_session, ConfigManager
from src import security
from src.services import TaskManager, MetadataSourceManager
from src.db.crud import local_danmaku as crud, anime as anime_crud, episode as episode_crud, source as source_crud
from src.utils import download_image
from src.utils.local_danmaku_scanner import LocalDanmakuScanner, copy_local_poster
from src.utils.danmaku_parser import parse_dandan_xml_to_comments
from src.services.task_manager import TaskSuccess
from src.db.crud.danmaku import update_metadata_if_empty
from src.api.dependencies import get_config_manager, get_task_manager
from .models import FileItem
from src.db.crud.episode import update_episode_danmaku_info

router = APIRouter()
logger = logging.getLogger(__name__)


# ==================== Pydantic Models ====================

class LocalScanRequest(BaseModel):
    scanPath: str


class LocalItemUpdate(BaseModel):
    title: Optional[str] = None
    mediaType: Optional[str] = None
    season: Optional[int] = None
    episode: Optional[int] = None
    year: Optional[int] = None
    tmdbId: Optional[str] = None
    tvdbId: Optional[str] = None
    imdbId: Optional[str] = None
    posterUrl: Optional[str] = None
    filePath: Optional[str] = None  # 弹幕文件存储路径


class LocalItemImportConfig(BaseModel):
    """单个本地项的导入配置"""
    itemId: int
    provider: str = 'custom'  # 默认为custom
    mediaId: Optional[str] = None  # 如果为None,后端会自动生成


class LocalItemsImportRequest(BaseModel):
    itemIds: Optional[List[int]] = None  # 简单导入,使用默认custom源
    items: Optional[List[LocalItemImportConfig]] = None  # 高级导入,指定每个项的来源
    shows: Optional[List[Dict[str, Any]]] = None
    seasons: Optional[List[Dict[str, Any]]] = None


class CreateFolderRequest(BaseModel):
    parentPath: str
    folderName: str


class DeleteFolderRequest(BaseModel):
    folderPath: str


# ==================== API Endpoints ====================

@router.post("/local-scan/browse", summary="浏览本地目录")
async def browse_directory(
    fileitem: FileItem,
    sort: Optional[str] = "name",
    current_user: models.User = Depends(security.get_current_user)
) -> List[FileItem]:
    """
    浏览本地文件系统目录

    参数:
    - fileitem: 文件项,包含要浏览的路径
    - sort: 排序方式, name:按名称排序, time:按修改时间排序

    返回:
    - 目录下的子目录和文件列表
    """
    try:
        # 获取路径
        path = fileitem.path if fileitem.path else "/"

        # 安全检查:防止路径遍历攻击
        try:
            path_obj = Path(path).resolve()
        except Exception as e:
            logger.error(f"路径解析失败: {path}, 错误: {e}")
            raise HTTPException(status_code=400, detail="无效的路径")

        # 检查路径是否存在
        if not path_obj.exists():
            raise HTTPException(status_code=404, detail="路径不存在")

        # 检查是否为目录
        if not path_obj.is_dir():
            raise HTTPException(status_code=400, detail="路径不是目录")

        # 列出目录内容
        result = []
        try:
            for item in path_obj.iterdir():
                try:
                    item_stat = item.stat()
                    is_dir = item.is_dir()

                    file_item = FileItem(
                        storage="local",
                        type="dir" if is_dir else "file",
                        path=str(item),
                        name=item.name,
                        basename=item.stem if not is_dir else item.name,
                        extension=item.suffix[1:] if item.suffix else None,
                        size=item_stat.st_size if not is_dir else 0,
                        modify_time=datetime.fromtimestamp(item_stat.st_mtime)
                    )
                    result.append(file_item)
                except (PermissionError, OSError) as e:
                    # 跳过无权限访问的文件/目录
                    logger.debug(f"跳过无权限访问的项: {item}, 错误: {e}")
                    continue
        except PermissionError:
            raise HTTPException(status_code=403, detail="没有权限访问该目录")

        # 排序
        if sort == "name":
            # 目录在前,文件在后,同类型按名称排序
            result.sort(key=lambda x: (x.type != "dir", x.name.lower()))
        else:
            # 按修改时间排序,最新的在前
            result.sort(key=lambda x: x.modify_time or datetime.min, reverse=True)

        return result

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"浏览目录失败: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"浏览目录失败: {str(e)}")


@router.post("/local-scan/create-folder", summary="创建文件夹")
async def create_folder(
    payload: CreateFolderRequest,
    current_user: models.User = Depends(security.get_current_user)
):
    """
    在指定路径创建新文件夹

    支持Windows和Linux路径格式
    """
    try:
        parent_path = payload.parentPath
        folder_name = payload.folderName.strip()

        # 验证文件夹名称
        if not folder_name:
            raise HTTPException(status_code=400, detail="文件夹名称不能为空")

        # 检查文件夹名称是否包含非法字符
        invalid_chars = ['<', '>', ':', '"', '|', '?', '*']
        if any(char in folder_name for char in invalid_chars):
            raise HTTPException(status_code=400, detail="文件夹名称包含非法字符")

        # Windows下还有额外的非法字符
        if os.name == 'nt':  # Windows
            windows_invalid = ['\\', '/', ':', '*', '?', '"', '<', '>', '|']
            if any(char in folder_name for char in windows_invalid):
                raise HTTPException(status_code=400, detail="文件夹名称包含Windows非法字符")

        # 防止路径遍历攻击
        if '..' in folder_name or folder_name.startswith('/') or folder_name.startswith('\\'):
            raise HTTPException(status_code=400, detail="无效的文件夹名称")

        # 解析父路径
        try:
            parent_path_obj = Path(parent_path).resolve()
        except Exception as e:
            logger.error(f"父路径解析失败: {parent_path}, 错误: {e}")
            raise HTTPException(status_code=400, detail="无效的父路径")

        # 检查父路径是否存在且为目录
        if not parent_path_obj.exists():
            raise HTTPException(status_code=404, detail="父路径不存在")

        if not parent_path_obj.is_dir():
            raise HTTPException(status_code=400, detail="父路径不是目录")

        # 构造新文件夹路径
        new_folder_path = parent_path_obj / folder_name

        # 检查文件夹是否已存在
        if new_folder_path.exists():
            raise HTTPException(status_code=409, detail="文件夹已存在")

        # 创建文件夹
        try:
            new_folder_path.mkdir(parents=True, exist_ok=False)
            logger.info(f"用户 '{current_user.username}' 创建了文件夹: {new_folder_path}")
            return {
                "message": "文件夹创建成功",
                "path": str(new_folder_path),
                "name": folder_name
            }
        except PermissionError:
            raise HTTPException(status_code=403, detail="没有权限创建文件夹")
        except Exception as e:
            logger.error(f"创建文件夹失败: {new_folder_path}, 错误: {e}")
            raise HTTPException(status_code=500, detail=f"创建文件夹失败: {str(e)}")

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"创建文件夹失败: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"创建文件夹失败: {str(e)}")


@router.delete("/local-scan/delete-folder", summary="删除文件夹")
async def delete_folder(
    folderPath: str = Query(..., description="要删除的文件夹路径"),
    current_user: models.User = Depends(security.get_current_user)
):
    """
    删除指定的文件夹

    注意：此操作不可逆，请谨慎使用
    """
    try:
        # folder_path = payload.folderPath

        # 防止路径遍历攻击
        if '..' in folderPath:
            raise HTTPException(status_code=400, detail="无效的文件夹路径")

        # 检查是否是绝对路径（Windows或Unix风格）
        if os.name == 'nt':  # Windows
            # Windows: 允许驱动器字母路径，如 C:\ 或 E:\test
            if not (len(folderPath) >= 3 and folderPath[1:3] == ':\\' and folderPath[0].isalpha()):
                # 如果不是Windows绝对路径，则不允许以 / 或 \ 开头（防止Unix风格路径遍历）
                if folderPath.startswith('/') or folderPath.startswith('\\'):
                    raise HTTPException(status_code=400, detail="无效的文件夹路径")
        else:  # Unix/Linux
            # Unix: 不允许以 / 开头的绝对路径（防止路径遍历）
            if folderPath.startswith('/'):
                raise HTTPException(status_code=400, detail="无效的文件夹路径")

        # 解析文件夹路径
        try:
            folder_path_obj = Path(folderPath).resolve()
        except Exception as e:
            logger.error(f"文件夹路径解析失败: {folderPath}, 错误: {e}")
            raise HTTPException(status_code=400, detail="无效的文件夹路径")

        # 检查文件夹是否存在
        if not folder_path_obj.exists():
            raise HTTPException(status_code=404, detail="文件夹不存在")

        # 检查是否为目录
        if not folder_path_obj.is_dir():
            raise HTTPException(status_code=400, detail="路径不是文件夹")

        # 检查文件夹是否为空（为了安全起见）
        try:
            folder_contents = list(folder_path_obj.iterdir())
            if folder_contents:
                raise HTTPException(status_code=400, detail="只能删除空文件夹")
        except PermissionError:
            raise HTTPException(status_code=403, detail="没有权限访问文件夹内容")

        # 删除文件夹
        try:
            folder_path_obj.rmdir()  # 只删除空文件夹
            logger.info(f"用户 '{current_user.username}' 删除了文件夹: {folder_path_obj}")
            return {
                "message": "文件夹删除成功",
                "path": str(folder_path_obj)
            }
        except PermissionError:
            raise HTTPException(status_code=403, detail="没有权限删除文件夹")
        except OSError as e:
            if "directory not empty" in str(e).lower():
                raise HTTPException(status_code=400, detail="文件夹不为空，无法删除")
            else:
                raise HTTPException(status_code=500, detail=f"删除文件夹失败: {str(e)}")
        except Exception as e:
            logger.error(f"删除文件夹失败: {folder_path_obj}, 错误: {e}")
            raise HTTPException(status_code=500, detail=f"删除文件夹失败: {str(e)}")

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"删除文件夹失败: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"删除文件夹失败: {str(e)}")


@router.get("/local-scan/last-path", summary="获取上次使用的扫描路径")
async def get_last_scan_path(
    config_manager: ConfigManager = Depends(get_config_manager),
    current_user: models.User = Depends(security.get_current_user)
):
    """获取上次使用的扫描路径"""
    try:
        last_path = await config_manager.get("local_scan_last_path", "")
        return {"path": last_path}
    except Exception as e:
        logger.error(f"获取上次扫描路径失败: {e}", exc_info=True)
        return {"path": ""}


@router.post("/local-scan/save-path", summary="保存扫描路径")
async def save_scan_path(
    payload: LocalScanRequest,
    config_manager: ConfigManager = Depends(get_config_manager),
    current_user: models.User = Depends(security.get_current_user)
):
    """保存扫描路径以便下次使用"""
    try:
        await config_manager.setValue("local_scan_last_path", payload.scanPath)
        return {"message": "路径已保存"}
    except Exception as e:
        logger.error(f"保存扫描路径失败: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"保存路径失败: {str(e)}")


@router.post("/local-scan", status_code=202, summary="扫描本地弹幕文件")
async def scan_local_danmaku(
    payload: LocalScanRequest,
    request: Request,
    config_manager: ConfigManager = Depends(get_config_manager),
    current_user: models.User = Depends(security.get_current_user)
):
    """
    扫描指定目录下的所有.xml弹幕文件

    扫描逻辑:
    1. 递归查找所有.xml文件
    2. 尝试从nfo文件读取元数据(TMDB ID等)
    3. 从文件名和目录结构推断标题、季集信息
    4. 存入local_danmaku_items表
    """
    try:
        # 保存路径以便下次使用
        await config_manager.setValue("local_scan_last_path", payload.scanPath)

        # 从app.state获取session_factory
        session_factory = request.app.state.db_session_factory
        scanner = LocalDanmakuScanner(session_factory)

        # 执行扫描(同步执行,因为通常不会太慢)
        result = await scanner.scan_directory(payload.scanPath)

        logger.info(f"用户 '{current_user.username}' 扫描了本地目录: {payload.scanPath}, 结果: {result}")

        return {
            "message": f"扫描完成: 找到 {result['total']} 个文件, 成功 {result['success']} 个",
            "result": result
        }
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"扫描本地弹幕失败: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"扫描失败: {str(e)}")


@router.get("/local-items", response_model=Dict[str, Any], summary="获取本地弹幕项列表")
async def get_local_items(
    is_imported: Optional[bool] = Query(None),
    media_type: Optional[str] = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(100, ge=1, le=500),
    session: AsyncSession = Depends(get_db_session),
    current_user: models.User = Depends(security.get_current_user)
):
    """获取本地弹幕项列表,支持过滤和分页"""
    result = await crud.get_local_items(
        session,
        is_imported=is_imported,
        media_type=media_type,
        page=page,
        page_size=page_size
    )
    return result


@router.get("/local-works", response_model=Dict[str, Any], summary="获取本地作品列表(按作品分组)")
async def get_local_works(
    is_imported: Optional[bool] = Query(None),
    media_type: Optional[str] = Query(None),
    year_from: Optional[int] = Query(None, description="起始年份，闭区间"),
    year_to: Optional[int] = Query(None, description="结束年份，闭区间"),
    page: int = Query(1, ge=1),
    page_size: int = Query(100, ge=1, le=500),
    session: AsyncSession = Depends(get_db_session),
    current_user: models.User = Depends(security.get_current_user)
):
    """获取本地作品列表(电影+电视剧组),按作品计数"""
    result = await crud.get_local_works(
        session,
        is_imported=is_imported,
        media_type=media_type,
        year_from=year_from,
        year_to=year_to,
        page=page,
        page_size=page_size
    )
    return result


@router.get("/local-movies/{title}/files", response_model=Dict[str, Any], summary="获取电影的弹幕文件列表")
async def get_local_movie_files(
    title: str,
    year: Optional[int] = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(100, ge=1, le=500),
    session: AsyncSession = Depends(get_db_session),
    current_user: models.User = Depends(security.get_current_user)
):
    """获取电影的所有弹幕文件"""
    files = await crud.get_movie_files(session, title, year, page, page_size)
    return files


@router.get("/local-shows/{title}/seasons", response_model=List[Dict[str, Any]], summary="获取本地剧集的季度信息")
async def get_local_show_seasons(
    title: str,
    session: AsyncSession = Depends(get_db_session),
    current_user: models.User = Depends(security.get_current_user)
):
    """获取本地剧集的所有季度"""
    seasons = await crud.get_show_seasons(session, title)
    return seasons


@router.get("/local-shows/{title}/seasons/{season}/episodes", response_model=Dict[str, Any], summary="获取本地某一季的分集列表")
async def get_local_season_episodes(
    title: str,
    season: int,
    page: int = Query(1, ge=1),
    page_size: int = Query(100, ge=1, le=500),
    session: AsyncSession = Depends(get_db_session),
    current_user: models.User = Depends(security.get_current_user)
):
    """获取本地某一季的所有集"""
    result = await crud.get_season_episodes(
        session,
        title,
        season,
        page,
        page_size
    )
    return result


@router.put("/local-items/{item_id}", status_code=status.HTTP_204_NO_CONTENT, summary="更新本地弹幕项")
async def update_local_item(
    item_id: int,
    payload: LocalItemUpdate,
    session: AsyncSession = Depends(get_db_session),
    current_user: models.User = Depends(security.get_current_user)
):
    """更新本地弹幕项的元数据"""
    update_data = payload.model_dump(exclude_unset=True)
    if not update_data:
        raise HTTPException(status_code=400, detail="没有要更新的字段")

    success = await crud.update_local_item(session, item_id, **update_data)
    if not success:
        raise HTTPException(status_code=404, detail="本地弹幕项不存在")

    logger.info(f"用户 '{current_user.username}' 更新了本地弹幕项 {item_id}")
    return


@router.delete("/local-items/{item_id}", status_code=status.HTTP_204_NO_CONTENT, summary="删除本地弹幕项")
async def delete_local_item(
    item_id: int,
    session: AsyncSession = Depends(get_db_session),
    current_user: models.User = Depends(security.get_current_user)
):
    """删除单个本地弹幕项"""
    success = await crud.delete_local_item(session, item_id)
    if not success:
        raise HTTPException(status_code=404, detail="本地弹幕项不存在")

    logger.info(f"用户 '{current_user.username}' 删除了本地弹幕项 {item_id}")
    return


@router.post("/local-items/batch-delete", status_code=status.HTTP_204_NO_CONTENT, summary="批量删除本地弹幕项")
async def batch_delete_local_items(
    payload: Dict[str, Any],
    session: AsyncSession = Depends(get_db_session),
    current_user: models.User = Depends(security.get_current_user)
):
    """
    批量删除本地弹幕项

    支持两种格式:
    1. itemIds: List[int] - 直接传递数据库ID列表
    2. itemIds: List[List[int]] - 传递ID数组的数组(前端从ids字段获取)
    """
    item_ids_raw = payload.get("itemIds", [])
    if not item_ids_raw:
        raise HTTPException(status_code=400, detail="没有要删除的项目")

    # 展平ID列表
    item_ids = []
    for item in item_ids_raw:
        if isinstance(item, list):
            # 如果是数组,展开它
            item_ids.extend(item)
        else:
            # 如果是单个ID,直接添加
            item_ids.append(item)

    if not item_ids:
        raise HTTPException(status_code=400, detail="没有要删除的项目")

    deleted_count = await crud.batch_delete_local_items(session, item_ids)
    logger.info(f"用户 '{current_user.username}' 批量删除了 {deleted_count} 个本地弹幕项")
    return


@router.post("/local-items/import", status_code=202, summary="导入本地弹幕")
async def import_local_items(
    payload: LocalItemsImportRequest,
    request: Request,
    session: AsyncSession = Depends(get_db_session),
    task_manager: TaskManager = Depends(get_task_manager),
    current_user: models.User = Depends(security.get_current_user)
):
    """
    导入选中的本地弹幕项到弹幕库

    实现逻辑:
    1. 读取.xml文件内容
    2. 解析弹幕数据
    3. 创建anime/episode记录
    4. 复制海报文件(如果有)
    5. 批量插入comment记录
    6. 标记isImported=true
    """
    # 收集所有要导入的item_ids和配置
    # item_configs: { item_id: { 'provider': 'xxx', 'mediaId': 'xxx' } }
    item_configs = {}

    # 处理简单导入(使用默认custom源)
    if payload.itemIds:
        for item_id in payload.itemIds:
            item_configs[item_id] = {'provider': 'custom', 'mediaId': None}

    # 处理高级导入(指定每个项的来源)
    if payload.items:
        for item_config in payload.items:
            item_configs[item_config.itemId] = {
                'provider': item_config.provider,
                'mediaId': item_config.mediaId
            }

    # 处理整部剧集导入
    if payload.shows:
        for show in payload.shows:
            episode_ids = await crud.get_episode_ids_by_show(session, show['title'])
            for episode_id in episode_ids:
                if episode_id not in item_configs:
                    item_configs[episode_id] = {'provider': 'custom', 'mediaId': None}

    # 处理季度导入
    if payload.seasons:
        for season in payload.seasons:
            episode_ids = await crud.get_episode_ids_by_season(
                session,
                season['title'],
                season['season']
            )
            for episode_id in episode_ids:
                if episode_id not in item_configs:
                    item_configs[episode_id] = {'provider': 'custom', 'mediaId': None}

    if not item_configs:
        return {"message": "没有要导入的项目"}

    # 创建后台任务执行导入
    async def import_task(task_session: AsyncSession, progress_callback):
        success_count = 0
        error_count = 0
        total = len(item_configs)
        current = 0

        # 用于缓存已处理的作品海报,避免重复下载
        # key: (title, season, year), value: local_image_path
        poster_cache = {}

        for item_id, config in item_configs.items():
            try:
                current += 1
                await progress_callback(
                    int(current / total * 90),
                    f"正在导入 {current}/{total}..."
                )

                # 获取本地项信息
                item = await crud.get_local_item_by_id(task_session, item_id)
                if not item:
                    logger.warning(f"本地项不存在: {item_id}")
                    error_count += 1
                    continue

                # 读取XML文件
                xml_path = Path(item.filePath)
                if not xml_path.exists():
                    logger.error(f"弹幕文件不存在: {xml_path}")
                    error_count += 1
                    continue

                # 校验XML文件是否可读取
                try:
                    xml_content = xml_path.read_text(encoding='utf-8')
                except Exception as e:
                    logger.error(f"无法读取弹幕文件 {xml_path}: {e}")
                    error_count += 1
                    continue

                # 校验XML文件是否可解析
                comments = parse_dandan_xml_to_comments(xml_content)
                if not comments:
                    logger.warning(f"弹幕文件为空或解析失败: {xml_path}")
                    error_count += 1
                    continue

                # 处理海报 - 使用缓存避免重复下载
                cache_key = (item.title, item.season or 1, item.year)
                local_image_path = poster_cache.get(cache_key)

                # 如果缓存中没有,则处理海报
                if local_image_path is None:
                    # 优先使用本地海报
                    if item.posterUrl:
                        # 如果posterUrl是本地路径,复制到海报目录
                        poster_path = Path(item.posterUrl)
                        if not poster_path.is_absolute():
                            # 相对路径,相对于xml文件所在目录
                            poster_path = xml_path.parent / item.posterUrl

                        if poster_path.exists():
                            local_image_path = copy_local_poster(str(poster_path))
                            if local_image_path:
                                logger.info(f"海报已复制: {poster_path} -> {local_image_path}")

                    # 如果没有本地海报但有 TMDB ID,尝试从 TMDB 获取
                    if not local_image_path and item.tmdbId:
                        try:
                            # 获取元数据管理器和scraper管理器
                            metadata_manager: MetadataSourceManager = request.app.state.metadata_manager
                            scraper_manager = request.app.state.scraper_manager

                            # 确定媒体类型
                            media_type = "movie" if item.mediaType == "movie" else "tv"

                            # 使用元数据管理器获取 TMDB 详情
                            details = await metadata_manager.get_details("tmdb", item.tmdbId, current_user, mediaType=media_type)
                            if details and details.imageUrl:
                                # 下载并缓存图片
                                local_image_path = await download_image(details.imageUrl, task_session, scraper_manager, "tmdb")
                                if local_image_path:
                                    logger.info(f"从 TMDB 获取海报成功: {details.imageUrl} -> {local_image_path}")
                        except Exception as e:
                            logger.warning(f"从 TMDB 获取海报失败 (TMDB ID: {item.tmdbId}): {e}")

                    # 缓存结果(即使是None也缓存,避免重复尝试)
                    poster_cache[cache_key] = local_image_path
                else:
                    logger.info(f"使用缓存的海报: {item.title} -> {local_image_path}")

                # 创建或查找anime记录
                anime_id = await anime_crud.get_or_create_anime(
                    task_session,
                    title=item.title,
                    media_type=item.mediaType,
                    season=item.season or 1,
                    image_url=None,  # 不使用URL
                    local_image_path=local_image_path,  # 使用本地路径
                    year=item.year
                )

                # 更新元数据（如果有）
                await update_metadata_if_empty(
                    task_session,
                    anime_id,
                    tmdb_id=item.tmdbId,
                    imdb_id=item.imdbId,
                    tvdb_id=item.tvdbId
                )

                # 获取配置的provider和mediaId
                provider = config.get('provider', 'custom')
                media_id = config.get('mediaId')

                # 如果没有指定mediaId,从文件名自动识别来源标签
                if not media_id:
                    # 从文件路径提取文件名
                    filename = xml_path.name.lower()

                    # 识别来源标签
                    source_label = None
                    if 'bilibili' in filename or '哔哩' in filename:
                        source_label = 'bilibili'
                    elif 'iqiyi' in filename or '爱奇艺' in filename:
                        source_label = 'iqiyi'
                    elif 'tencent' in filename or '腾讯' in filename:
                        source_label = 'tencent'
                    elif 'youku' in filename or '优酷' in filename:
                        source_label = 'youku'
                    elif 'mgtv' in filename or '芒果' in filename:
                        source_label = 'mgtv'
                    elif 'renren' in filename or '人人' in filename:
                        source_label = 'renren'

                    # 生成mediaId
                    if source_label:
                        media_id = f"custom_{source_label}"
                        logger.info(f"从文件名识别到来源: {source_label}, mediaId: {media_id}")
                    else:
                        # 如果无法识别,使用唯一ID避免冲突
                        media_id = f"custom_{anime_id}_{item_id}"
                        logger.info(f"未识别到来源标签,使用唯一mediaId: {media_id}")

                # 创建或获取指定的源
                source_id = await source_crud.link_source_to_anime(task_session, anime_id, provider, media_id)
                await task_session.flush()

                # 创建或查找episode记录
                episode_index = item.episode or 1
                episode_id = await episode_crud.create_episode_if_not_exists(
                    task_session,
                    anime_id=anime_id,
                    source_id=source_id,
                    episode_index=episode_index,
                    title=f"第{episode_index}集",
                    url=None,
                    provider_episode_id=f"local_{item_id}_{episode_index}"
                )

                # 直接使用本地弹幕文件路径，不创建新文件
                # 更新 episode 的弹幕信息，指向本地文件
                await update_episode_danmaku_info(
                    task_session,
                    episode_id,
                    str(xml_path),  # 使用本地文件的绝对路径
                    len(comments)
                )

                # 标记为已导入
                await crud.update_local_item(task_session, item_id, isImported=True)
                await task_session.commit()

                success_count += 1
                logger.info(f"成功导入: {item.title} - 第{episode_index}集, 弹幕数: {len(comments)}, 路径: {xml_path}")

            except Exception as e:
                logger.error(f"导入本地项失败 (ID: {item_id}): {e}", exc_info=True)
                error_count += 1
                await task_session.rollback()
                continue

        await progress_callback(100, "导入完成")
        raise TaskSuccess(f"导入完成: 成功 {success_count} 个, 失败 {error_count} 个")

    # 提交任务到 management 队列（本地操作，不需要下载队列）
    task_id, _ = await task_manager.submit_task(
        import_task,
        f"导入本地弹幕 ({len(item_configs)} 个项目)",
        queue_type="management"
    )

    logger.info(f"用户 '{current_user.username}' 提交了本地弹幕导入任务: {task_id}")

    return {
        "message": f"已提交导入任务,共 {len(item_configs)} 个项目",
        "taskId": task_id,
        "count": len(item_configs)
    }

