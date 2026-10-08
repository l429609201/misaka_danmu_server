"""
本地弹幕 - 查询 Workflow
处理本地弹幕项的查询、列表、分组等业务逻辑
"""
import logging
from typing import Dict, Any, List, Optional

from src.services.database_service import DatabaseService

logger = logging.getLogger(__name__)


async def get_local_items_flow(
    db: DatabaseService,
    is_imported: Optional[bool] = None,
    media_type: Optional[str] = None,
    page: int = 1,
    page_size: int = 100
) -> Dict[str, Any]:
    """
    获取本地弹幕项列表，支持过滤和分页
    
    Args:
        db: 数据库服务
        is_imported: 是否已导入（None=全部）
        media_type: 媒体类型（None=全部）
        page: 页码
        page_size: 每页数量
        
    Returns:
        本地弹幕项列表及分页信息
    """
    async with db.transaction():
        result = await db.local_danmaku.get_local_items(
            is_imported=is_imported,
            media_type=media_type,
            page=page,
            page_size=page_size
        )
    return result


async def get_local_works_flow(
    db: DatabaseService,
    is_imported: Optional[bool] = None,
    media_type: Optional[str] = None,
    year_from: Optional[int] = None,
    year_to: Optional[int] = None,
    page: int = 1,
    page_size: int = 100
) -> Dict[str, Any]:
    """
    获取本地作品列表（按作品分组）
    
    Args:
        db: 数据库服务
        is_imported: 是否已导入（None=全部）
        media_type: 媒体类型（None=全部）
        year_from: 起始年份
        year_to: 结束年份
        page: 页码
        page_size: 每页数量
        
    Returns:
        本地作品列表及分页信息
    """
    async with db.transaction():
        result = await db.local_danmaku.get_local_works(
            is_imported=is_imported,
            media_type=media_type,
            year_from=year_from,
            year_to=year_to,
            page=page,
            page_size=page_size
        )
    return result


async def get_movie_files_flow(
    db: DatabaseService,
    title: str,
    year: Optional[int] = None,
    page: int = 1,
    page_size: int = 100
) -> Dict[str, Any]:
    """
    获取电影的所有弹幕文件
    
    Args:
        db: 数据库服务
        title: 电影标题
        year: 年份
        page: 页码
        page_size: 每页数量
        
    Returns:
        电影弹幕文件列表及分页信息
    """
    async with db.transaction():
        result = await db.local_danmaku.get_movie_files(
            title=title,
            year=year,
            page=page,
            page_size=page_size
        )
    return result


async def get_show_seasons_flow(
    db: DatabaseService,
    title: str
) -> List[Dict[str, Any]]:
    """
    获取本地剧集的所有季度
    
    Args:
        db: 数据库服务
        title: 剧集标题
        
    Returns:
        季度信息列表
    """
    async with db.transaction():
        seasons = await db.local_danmaku.get_show_seasons(title=title)
    return seasons


async def get_season_episodes_flow(
    db: DatabaseService,
    title: str,
    season: int,
    page: int = 1,
    page_size: int = 100
) -> Dict[str, Any]:
    """
    获取本地某一季的所有集
    
    Args:
        db: 数据库服务
        title: 剧集标题
        season: 季度号
        page: 页码
        page_size: 每页数量
        
    Returns:
        分集列表及分页信息
    """
    async with db.transaction():
        result = await db.local_danmaku.get_season_episodes(
            title=title,
            season=season,
            page=page,
            page_size=page_size
        )
    return result
