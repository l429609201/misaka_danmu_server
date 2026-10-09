"""图片下载编排：读取业务配置，调用网络与文件服务，不借用外部会话。"""

import asyncio
import hashlib
import logging
from typing import Optional

from src.services.config_service import get_config_service
from src.services.file_storage_service import CONFIG_DIR, get_file_storage_service
from src.services.image_http_service import fetch_image
from src.services.service_container import get_database_service, get_scraper_manager
from src.utils.misc.image_processing import encode_webp
from src.workflows.media_poster import resolve_media_image_headers

logger = logging.getLogger(__name__)


async def download_image(
    image_url: Optional[str], provider_name: Optional[str] = None,
) -> Optional[str]:
    """按源代理配置缓存图片；网络和文件 I/O 均在配置查询事务结束后执行。"""
    if not image_url:
        return None
    # 已有本地海报只在文件确实存在时复用，避免把失效路径写回业务记录。
    if image_url.startswith("/data/images/"):
        path = CONFIG_DIR / "image" / image_url.rsplit("/", 1)[-1]
        return image_url if get_file_storage_service().exists(path) else None
    config = get_config_service()
    proxy_url = await config.get("proxyUrl", "")
    proxy_enabled = str(await config.get("proxyEnabled", "false")).lower() == "true"
    verify = str(await config.get("proxySslVerify", "true")).lower() == "true"
    use_proxy = False
    referer = None
    if provider_name:
        if proxy_enabled:
            db = get_database_service()
            async with db.transaction():
                # 使用查询仓储实际提供的设置接口。
                scrapers = await db.scraper.get_all_scraper_settings()
                metadata = await db.metadata_source.get_all_metadata_source_settings()
            setting = next(
                (item for item in scrapers + metadata if item["providerName"] == provider_name),
                None,
            )
            if setting:
                use_proxy = bool(setting.get("useProxy", False))
        try:
            referer = get_scraper_manager().get_scraper(provider_name).referer
        except ValueError:
            logger.debug("图片提供方不是搜索源，不附加源 Referer：%s", provider_name)
    media_headers = None
    if image_url.startswith(("http://", "https://")):
        try:
            media_headers = await resolve_media_image_headers(image_url)
        except RuntimeError:
            logger.debug("未匹配媒体服务器鉴权，按普通图片下载", exc_info=True)
    image = await fetch_image(
        image_url, proxy=proxy_url if proxy_enabled and use_proxy and proxy_url else None,
        verify=verify, referer=referer, headers=media_headers,
    )
    if image is None:
        return None
    content, _ = image
    try:
        content = await asyncio.to_thread(encode_webp, content)
    except Exception:
        logger.warning("海报 WebP 编码失败", exc_info=True)
        return None
    filename = f"{hashlib.sha256(content).hexdigest()}.webp"
    # 文件服务只负责落盘；缓存路径的业务约定由编排层决定。
    storage = get_file_storage_service()
    path = CONFIG_DIR / "image" / filename
    if not storage.exists(path) and not await storage.write_bytes(path, content):
        return None
    return f"/data/images/{filename}"


async def refresh_anime_poster(anime_id: int, image_url: str) -> str:
    """校验作品后下载海报，并在独立短事务中更新作品记录。"""
    db = get_database_service()
    async with db.transaction():
        if await db.anime.get_by_id(anime_id) is None:
            raise LookupError("作品未找到。")
    local_path = await download_image(image_url)
    if not local_path:
        raise ValueError("图片下载失败，请检查URL或服务器日志。")
    async with db.transaction():
        updated = await db.anime.update(
            anime_id, imageUrl=image_url, localImagePath=local_path,
        )
        if updated is None:
            raise LookupError("作品未找到。")
    # 不删除旧文件：其他作品可能共享它，需统一引用检查后才能清理。
    return local_path


async def download_poster_to_local(
    image_url: str, title: str, season: int, year: Optional[int] = None,
) -> dict:
    """下载海报后按作品信息关联；无匹配作品时仍返回已缓存路径。"""
    local_path = await download_image(image_url)
    if not local_path:
        raise ValueError("图片下载失败，请检查URL或服务器日志。")
    db = get_database_service()
    anime_id = None
    # 网络下载结束后才开启短事务，避免下载期间占用数据库连接。
    async with db.transaction():
        # anime 代理统一提供 CRUD 和复杂查询，不存在独立 anime_query 入口。
        anime = await db.anime.find_by_title_season_year(title, season, year)
        if anime:
            anime_id = anime["id"]
            updated = await db.anime.update(
                anime_id, imageUrl=image_url, localImagePath=local_path,
            )
            if updated is None:
                anime_id = None
    return {"localImagePath": local_path, "animeId": anime_id}

