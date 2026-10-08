"""媒体库扫描编排，远端请求与每库保存事务分离。"""
import logging
from typing import Callable, List, Optional

from src.services.service_container import get_database_service, get_media_server_service

logger = logging.getLogger(__name__)


async def scan_media_library(
    server_id: int, library_ids: Optional[List[str]], progress_callback: Callable,
) -> str:
    """扫描选定媒体库，只有成功提交的媒体项计入完成数量。"""
    await progress_callback(0, "开始扫描媒体库...")
    server = get_media_server_service().get_server(server_id)
    if server is None:
        raise ValueError(f"媒体服务器 {server_id} 不存在或未启用")
    db = get_database_service()
    async with db.transaction():
        config = await db.media_server.get_media_server_by_id(server_id)
    if config is None:
        raise ValueError(f"媒体服务器配置 {server_id} 不存在")
    libraries = library_ids or config.get("selectedLibraries")
    if not libraries:
        libraries = [library.id for library in await server.get_libraries()]
    total = 0
    for index, library_id in enumerate(libraries):
        base = int(index * 100 / len(libraries))
        await progress_callback(base, f"正在扫描媒体库 {index + 1}/{len(libraries)}...")
        try:
            items = await server.get_library_items(library_id)
            # 同一媒体库整体提交，失败不残留部分扫描结果。
            async with db.transaction():
                for item_index, item in enumerate(items):
                    await db.media_item.upsert_scanned_item(server_id, item.media_id, {
                        "libraryId": library_id, "mediaType": item.media_type,
                        "title": item.title, "seriesId": getattr(item, "series_id", None),
                        "seasonId": getattr(item, "season_id", None),
                        "episodeId": getattr(item, "episode_id", None),
                        "season": item.season, "episode": item.episode, "year": item.year,
                        "tmdbId": item.tmdb_id, "tvdbId": item.tvdb_id,
                        "imdbId": item.imdb_id, "posterUrl": item.poster_url,
                    })
                    if item_index % 10 == 0:
                        await progress_callback(base, f"正在保存媒体库项目 {item_index + 1}/{len(items)}...")
            total += len(items)
        except Exception:
            logger.exception("扫描媒体库 %s 失败", library_id)
    await progress_callback(100, f"扫描完成，共 {total} 个媒体项")
    return f"媒体库扫描完成，共扫描到 {total} 个媒体项"
