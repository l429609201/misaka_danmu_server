"""媒体服务器与作品元数据绑定的跨域编排。"""

from typing import Optional

from src.services.service_container import get_database_service


async def bind_media_server_to_anime(
    anime_id: int, server_id: int, series_id: str, season_id: Optional[str],
) -> str:
    """在单个事务中校验媒体服务器并仅更新作品的绑定字段。"""
    db = get_database_service()
    async with db.transaction():
        config = await db.media_server.get_media_server_by_id(server_id)
        if config is None:
            raise LookupError("媒体服务器不存在")
        server_type = config["providerName"]
        if not await db.anime.bind_media_server(anime_id, server_type, series_id, season_id):
            raise LookupError("作品不存在")
    return server_type
