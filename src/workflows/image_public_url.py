"""自定义图片外链编排：读取配置并协调探针文件和网络服务。"""

import base64
from typing import Optional
from src.services.config_service import ConfigService, get_config_service
from src.services.file_storage_service import get_file_storage_service
from src.services.image_http_service import probe_image_url
from src.utils.misc.public_url import validate_custom_domain_format
from src.workflows.image_resources import IMAGE_DIR

PUBLIC_URL_PROBE_NAME = "notification_public_url_probe.png"
PUBLIC_URL_PROBE_BYTES = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
)


async def get_custom_domain(config_service: Optional[ConfigService] = None) -> Optional[str]:
    """读取统一外链配置，纯格式校验交给工具层。"""
    try:
        config = config_service if config_service is not None else get_config_service()
        raw = await config.get("custom_api_domain", "")
    except Exception:
        return None
    return validate_custom_domain_format(raw)


async def probe_public_domain(domain: str) -> dict:
    """确保探针存在后探测外链，文件与网络操作分别交给底层服务。"""
    storage = get_file_storage_service()
    path = IMAGE_DIR / PUBLIC_URL_PROBE_NAME
    if not storage.exists(path):
        if not await storage.write_bytes(path, PUBLIC_URL_PROBE_BYTES):
            raise OSError("无法创建图片探测文件，请检查 config/image 目录权限")
    url = f"{domain}/data/images/{PUBLIC_URL_PROBE_NAME}"
    result = await probe_image_url(url)
    if result["ok"]:
        result.update(domain=domain, probeUrl=url)
    return result
