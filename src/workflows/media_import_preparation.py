"""媒体项导入准备与提交状态维护。"""
from typing import Any, Dict, List, Tuple

from src.services.service_container import get_database_service


async def build_media_import_title(item_ids: List[int]) -> str:
    """在短事务内统计媒体标题并形成可读任务名。"""
    db = get_database_service()
    async with db.transaction():
        groups = await db.media_server.get_import_title_groups(item_ids)
    if not groups:
        return "导入媒体项"
    if len(groups) == 1:
        title, count = groups[0]
        return f"导入媒体项: {title}" if count == 1 else f"导入媒体项: {title} ({count}集)"
    if len(groups) <= 3:
        return f"导入媒体项: {', '.join(title for title, _ in groups)}"
    return f"导入媒体项: {', '.join(title for title, _ in groups[:2])} 等{len(groups)}部"


async def prepare_media_import(item_ids: List[int]) -> Tuple[List[Dict[str, Any]], Dict, str]:
    """按电影和季度分组，拒绝混合服务器造成删除联动类型串用。"""
    db = get_database_service()
    async with db.transaction():
        items = await db.media_item.get_import_snapshots(item_ids)
    if not items:
        raise ValueError("未找到要导入的媒体项")
    if len({item["serverId"] for item in items}) != 1:
        raise ValueError("同一批导入的媒体项必须属于同一媒体服务器")
    movies = []
    shows = {}
    for item in items:
        if item["mediaType"] == "movie":
            movies.append(item)
        elif item["mediaType"] == "tv_series":
            shows.setdefault((item["title"], item["season"]), []).append(item)
    return movies, shows, items[0]["mediaServerType"]


async def mark_media_submitted(item_ids: List[int]) -> None:
    """独立提交排队标记，不借用任务管理器的长生命周期会话。"""
    db = get_database_service()
    async with db.transaction():
        await db.media_server.mark_media_items_imported(item_ids)


async def collect_unimported_media_items(server_id: int, media_type: str | None) -> List[int]:
    """根据实际弹幕入库状态获取待导入项，查询结束立即释放事务。"""
    db = get_database_service()
    async with db.transaction():
        return await db.media_server.get_unimported_item_ids(server_id, media_type)
