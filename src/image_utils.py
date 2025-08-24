import logging
import uuid
from pathlib import Path
from typing import Optional
import asyncio

from sqlalchemy.ext.asyncio import AsyncSession
import httpx
from . import crud
from .scraper_manager import ScraperManager

logger = logging.getLogger(__name__)

# 图片存储在 config/image/ 目录下
IMAGE_DIR = Path(__file__).parent.parent / "config" / "image"
IMAGE_DIR.mkdir(parents=True, exist_ok=True)

async def download_image(image_url: Optional[str], session: AsyncSession, scraper_manager: ScraperManager, provider_name: Optional[str] = None) -> Optional[str]:
    """
    从给定的URL下载图片，保存到本地，并返回其相对Web路径。
    支持代理。

    :param image_url: 要下载的图片的URL。
    :param session: SQLAlchemy 异步会话。
    :param scraper_manager: ScraperManager 实例，用于获取源特定的配置。
    :param provider_name: 触发下载的源提供方名称，用于确定是否使用代理。
    :return: 成功则返回图片的Web可访问路径 (e.g., /images/xxxx.jpg)，失败则返回 None。
    """
    if not image_url:
        return None

    # --- Start of new proxy logic ---
    proxy_url = await crud.get_config_value(session, "proxyUrl", "")
    proxy_enabled_str = await crud.get_config_value(session, "proxyEnabled", "false")
    ssl_verify_str = await crud.get_config_value(session, "proxySslVerify", "true")
    ssl_verify = ssl_verify_str.lower() == 'true'
    proxy_enabled_globally = proxy_enabled_str.lower() == 'true'
    use_proxy_for_this_provider = False

    if provider_name and proxy_enabled_globally:
        # Check both scrapers and metadata sources for the provider's setting
        # 修正：将并发的 gather 调用改为顺序的 await，以避免 SQLAlchemy 会话错误
        scraper_settings = await crud.get_all_scraper_settings(session)
        metadata_settings = await crud.get_all_metadata_source_settings(session)
        
        provider_setting = next((s for s in scraper_settings if s['providerName'] == provider_name), None)
        if not provider_setting:
            provider_setting = next((s for s in metadata_settings if s['providerName'] == provider_name), None)
        
        if provider_setting:
            use_proxy_for_this_provider = provider_setting.get('useProxy', False)

    proxy_to_use = proxy_url if proxy_enabled_globally and use_proxy_for_this_provider and proxy_url else None
    # --- End of new proxy logic ---

    # 修正：确保URL以http开头
    if image_url.startswith('//'):
        image_url = 'https:' + image_url

    # 新增：对于爱奇艺的图片，总是尝试使用 HTTPS
    if 'iqiyipic.com' in image_url:
        image_url = image_url.replace('http://', 'https://', 1)

    try:
        # 修正：为下载客户端设置一个通用的浏览器User-Agent，以提高成功率
        client_headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        }
        
        # 修正：简化并修正Referer逻辑
        # 默认不发送Referer，但如果提供了provider_name，则使用该源的Referer
        if provider_name:
            try:
                scraper = scraper_manager.get_scraper(provider_name)
                if scraper.referer:
                    client_headers["Referer"] = scraper.referer
            except ValueError:
                logger.warning(f"下载图片时未找到提供方为 '{provider_name}' 的搜索源，将不发送 Referer。")
        
        # 针对特定源的特殊处理：确保Referer是正确的，即使provider_name不是该源（例如从TMDB获取的图片链接）
        if 'iqiyipic.com' in image_url:
            client_headers["Referer"] = "https://www.iqiyi.com/"
        elif 'hdslb.com' in image_url:
            client_headers["Referer"] = "https://www.bilibili.com/"

        async with httpx.AsyncClient(timeout=30.0, follow_redirects=True, proxy=proxy_to_use, headers=client_headers, verify=ssl_verify) as client:
            response = await client.get(image_url)
            response.raise_for_status()

            content_type = response.headers.get("content-type", "")
            extension = ".jpg"  # 默认扩展名
            if "jpeg" in content_type: extension = ".jpg"
            elif "png" in content_type: extension = ".png"
            elif "webp" in content_type: extension = ".webp"

            filename = f"{uuid.uuid4()}{extension}"
            save_path = IMAGE_DIR / filename
            save_path.write_bytes(response.content)
            logger.info(f"图片已成功缓存到: {save_path}")
            return f"/data/images/{filename}"  # 返回Web可访问的相对路径
    except Exception as e:
        logger.error(f"下载图片失败 (URL: {image_url}): {e}", exc_info=True)
        return None
