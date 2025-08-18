import logging
import uuid
from pathlib import Path
from typing import Optional
import asyncio

import aiomysql
import httpx
from . import crud

logger = logging.getLogger(__name__)

# 图片存储在 config/image/ 目录下
IMAGE_DIR = Path(__file__).parent.parent / "config" / "image"
IMAGE_DIR.mkdir(parents=True, exist_ok=True)


async def download_image(image_url: Optional[str], pool: aiomysql.Pool, provider_name: Optional[str] = None) -> Optional[str]:
    """
    从给定的URL下载图片，保存到本地，并返回其相对Web路径。
    支持代理。

    :param image_url: 要下载的图片的URL。
    :param pool: 数据库连接池。
    :param provider_name: 触发下载的源提供方名称，用于确定是否使用代理。
    :return: 成功则返回图片的Web可访问路径 (e.g., /images/xxxx.jpg)，失败则返回 None。
    """
    if not image_url:
        return None

    # --- Start of new proxy logic ---
    proxy_url_task = crud.get_config_value(pool, "proxy_url", "")
    proxy_enabled_globally_task = crud.get_config_value(pool, "proxy_enabled", "false")
    
    tasks = [proxy_url_task, proxy_enabled_globally_task]
    
    proxy_url, proxy_enabled_str = await asyncio.gather(*tasks)
    proxy_enabled_globally = proxy_enabled_str.lower() == 'true'
    use_proxy_for_this_provider = False

    if provider_name and proxy_enabled_globally:
        # Check both scrapers and metadata sources for the provider's setting
        scraper_settings_task = crud.get_all_scraper_settings(pool)
        metadata_settings_task = crud.get_all_metadata_source_settings(pool)
        scraper_settings, metadata_settings = await asyncio.gather(scraper_settings_task, metadata_settings_task)
        
        provider_setting = next((s for s in scraper_settings if s['provider_name'] == provider_name), None)
        if not provider_setting:
            provider_setting = next((s for s in metadata_settings if s['provider_name'] == provider_name), None)
        
        if provider_setting:
            use_proxy_for_this_provider = provider_setting.get('use_proxy', False)

    proxy_to_use = proxy_url if proxy_enabled_globally and use_proxy_for_this_provider and proxy_url else None
    # --- End of new proxy logic ---

    # 修正：确保URL以http开头
    if image_url.startswith('//'):
        image_url = 'https:' + image_url

    try:
        async with httpx.AsyncClient(timeout=30.0, follow_redirects=True, proxy=proxy_to_use) as client:
            # 动态设置 Referer 以提高下载成功率
            referer_map = {
                "bilibili": "https://www.bilibili.com/",
                "iqiyi": "https://www.iqiyi.com/",
                "tencent": "https://v.qq.com/",
                "youku": "https://www.youku.com/",
                "mgtv": "https://www.mgtv.com/",
                "gamer": "https://ani.gamer.com.tw/",
                "renren": "https://rrsp.com.cn/",
            }
            # 如果提供了源名称，则使用对应的Referer，否则使用一个通用的默认值
            referer = referer_map.get(provider_name, "https://www.google.com/")
            
            response = await client.get(image_url, headers={"Referer": referer})
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
            return f"/images/{filename}"  # 返回Web可访问的相对路径
    except Exception as e:
        logger.error(f"下载图片失败 (URL: {image_url}): {e}", exc_info=True)
        return None
