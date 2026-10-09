"""媒体海报鉴权准备：仅为已配置服务器同源路径提供下载凭据。"""
from typing import Optional
from urllib.parse import quote, unquote, urlsplit

from src.services.service_container import get_database_service


async def resolve_media_image_headers(image_url: str) -> Optional[dict[str, str]]:
    """匹配明确的服务器地址，拒绝向外站或服务器目录之外发送凭据。"""
    target = urlsplit(image_url)
    if target.scheme not in ("http", "https") or not target.hostname or target.username:
        return None
    decoded_path = unquote(target.path)
    if ".." in decoded_path.split("/") or "\\" in decoded_path:
        return None
    db = get_database_service()
    async with db.transaction():
        servers = await db.media_server.get_all_media_servers()
    matches = []
    for server in servers:
        base = urlsplit(server.get("url", ""))
        if (base.scheme, base.hostname, base.port) != (target.scheme, target.hostname, target.port):
            continue
        prefix = base.path.rstrip("/")
        if prefix and decoded_path != prefix and not decoded_path.startswith(prefix + "/"):
            continue
        matches.append((len(prefix), server))
    if not matches:
        return None
    server = max(matches, key=lambda item: item[0])[1]
    token = server.get("apiToken") or ""
    if not token:
        return None
    provider = server.get("providerName")
    if provider == "jellyfin":
        return {"Authorization": f'MediaBrowser Token="{quote(token, safe="")}"'}
    if provider == "emby":
        return {"X-Emby-Token": token}
    if provider == "plex":
        return {"X-Plex-Token": token}
    return None
