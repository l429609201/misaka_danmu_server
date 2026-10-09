"""图片资源流程：加载来源、编码缓存并生成公开路径。"""

import asyncio
import hashlib
import logging
from pathlib import Path
from typing import Optional, Union
from urllib.parse import unquote, urlparse

from src.services.file_storage_service import CONFIG_DIR, get_file_storage_service
from src.services.image_http_service import fetch_image
from src.utils.misc.image_processing import PUBLIC_THUMBNAIL_WIDTH, encode_thumbnail

logger = logging.getLogger(__name__)
IMAGE_DIR = CONFIG_DIR / "image"


async def load_image_bytes(
    image_source: Optional[str], max_bytes: int = 10 * 1024 * 1024,
) -> Optional[bytes]:
    """按图片来源协调网络或文件服务，限量读取图片内容。"""
    if not image_source:
        return None
    try:
        storage = get_file_storage_service()
        if image_source.startswith("/data/images/"):
            path = IMAGE_DIR / Path(urlparse(image_source).path).name
            return await storage.read_bytes(path, max_bytes=max_bytes)
        if image_source.startswith("file://"):
            path = Path(unquote(urlparse(image_source).path))
            return await storage.read_bytes(path, max_bytes=max_bytes)
        result = await fetch_image(image_source, max_bytes=max_bytes)
        return result[0] if result else None
    except (OSError, ValueError):
        logger.warning("读取图片失败", exc_info=True)
        return None


async def save_public_thumbnail(
    image_source: Optional[Union[str, bytes]], width: int = PUBLIC_THUMBNAIL_WIDTH,
) -> Optional[str]:
    """加载图片、复用内容哈希缓存并返回可公开访问的缩略图路径。"""
    raw = image_source if isinstance(image_source, bytes) else await load_image_bytes(image_source)
    if not raw:
        return None
    # 通知与检索共用内容缓存；实际原子落盘仍由文件服务负责。
    filename = f"w{width}_{hashlib.sha256(raw).hexdigest()[:16]}.jpg"
    path = IMAGE_DIR / filename
    storage = get_file_storage_service()
    if not storage.exists(path):
        try:
            data = await asyncio.to_thread(encode_thumbnail, raw, width)
        except Exception:
            logger.warning("生成缩略图失败", exc_info=True)
            return None
        if not await storage.write_bytes(path, data):
            return None
    return f"/data/images/{filename}"
