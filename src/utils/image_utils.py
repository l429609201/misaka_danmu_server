import hashlib
import logging
import uuid
from pathlib import Path
from typing import Optional, TYPE_CHECKING, Union
from urllib.parse import urlparse
import asyncio

from sqlalchemy.ext.asyncio import AsyncSession
import httpx
from src.db import crud
from src.core.env import is_docker_environment as _is_docker_environment

if TYPE_CHECKING:
    from src.services import ScraperManager

logger = logging.getLogger(__name__)

# ─── 自定义域名工具 ───────────────────────────────────────────────────────────

# 数据库配置键（统一使用下划线，与前端 /api/ui/config/custom_api_domain 保持一致）
_CUSTOM_DOMAIN_KEY = "custom_api_domain"


def validate_custom_domain_format(raw_domain: str) -> Optional[str]:
    """对自定义域名做格式校验：必须是 http(s):// 开头的无凭据地址。

    :param raw_domain: 原始域名字符串（可能含尾部斜杠、空白）
    :return: 规范化后的域名（去掉尾部斜杠），格式不合规时返回 None。

    why：自定义域名允许 http 和 https；公网可达性另由 probe_public_domain 负责检测。
    带认证信息的地址在对外分享场景下会出现安全问题，直接拒绝。
    """
    domain = str(raw_domain or "").strip().rstrip("/")
    if not domain:
        return None
    parsed = urlparse(domain)
    if parsed.scheme.lower() not in ("http", "https"):
        return None
    if not parsed.hostname:
        return None
    if parsed.username or parsed.password:
        return None
    return domain


async def get_custom_domain(config_manager) -> Optional[str]:
    """从配置中读取自定义域名并做格式校验（http/https 均接受）。

    :param config_manager: ConfigManager 实例
    :return: 合规的域名字符串（已去尾部斜杠），读取失败或格式不合规返回 None。

    why：所有需要拼接外联地址的业务（海报外联、通知外链、命令指令等）统一调此函数，
    避免在各处硬编码不同的 key 名称或重复做格式判断。
    配置 key 统一用 custom_api_domain（下划线，与前端保存路径一致）。
    """
    try:
        raw = await config_manager.get(_CUSTOM_DOMAIN_KEY, "")
    except Exception:
        return None
    return validate_custom_domain_format(raw)


async def probe_public_domain(domain: str) -> dict:
    """向已知存在的探针图片发起真实 HTTP 请求，验证自定义域名是否公网可达。

    探针文件路径为 /data/images/{_PUBLIC_URL_PROBE_NAME}，由调用方确保文件已存在。

    :param domain: 已经过 validate_custom_domain_format 校验的域名（无尾部斜杠）
    :return: {"ok": True, "probeUrl": ...} 或 {"ok": False, "detail": ...}

    why：与 notification_routes._probe_public_domain 共享同一逻辑，
    避免两处重复维护不同的超时/错误判断策略。
    外链模式要求外部能真实访问到本服务的图片，此处做实际网络探测。
    """
    probe_url = f"{domain}/data/images/{_PUBLIC_URL_PROBE_NAME}"
    try:
        async with httpx.AsyncClient(timeout=8.0, follow_redirects=True) as client:
            response = await client.get(probe_url, headers={"Accept": "image/*"})
    except httpx.TimeoutException:
        return {"ok": False, "detail": "自定义域名访问超时，请检查公网解析和反向代理"}
    except httpx.HTTPError as e:
        return {"ok": False, "detail": f"自定义域名无法访问：{type(e).__name__}"}

    content_type = response.headers.get("content-type", "").lower()
    if response.status_code != 200 or not content_type.startswith("image/"):
        return {
            "ok": False,
            "detail": f"图片静态路由不可用（HTTP {response.status_code}，Content-Type={content_type or '未知'}）",
        }
    return {"ok": True, "domain": domain, "probeUrl": probe_url}


# 探针图片常量（供 probe_public_domain 和 notification_routes 共用）
_PUBLIC_URL_PROBE_NAME = "notification_public_url_probe.png"

# 图片存储在 config/image/ 目录下
def _get_image_dir():
    """获取图片目录，根据运行环境自动调整"""
    if _is_docker_environment():
        return Path("/app/config/image")
    else:
        # 源码运行环境，使用当前工作目录
        return Path("config/image")

IMAGE_DIR = _get_image_dir()

def _ensure_image_dir():
    """确保图片目录存在"""
    try:
        IMAGE_DIR.mkdir(parents=True, exist_ok=True)
    except (OSError, PermissionError) as e:
        logger.warning(f"无法创建图片目录 {IMAGE_DIR}: {e}")

# 延迟创建目录，避免在模块加载时就尝试创建

# 对外分享用缩略图的目标宽度，与各元数据站常见的 w500 规格保持一致
PUBLIC_THUMBNAIL_WIDTH = 500


def _encode_thumbnail(raw: bytes, width: int) -> bytes:
    """同步把图片等比缩放到指定宽度并编码为 JPEG。

    why：这是 CPU 密集操作，由调用方放进线程执行，避免阻塞事件循环。
    统一转 RGB 是因为 PNG 的透明通道与 P 模式无法直接存 JPEG。
    """
    import io
    from PIL import Image

    with Image.open(io.BytesIO(raw)) as im:
        im = im.convert("RGB")
        if im.width > width:
            height = max(1, round(im.height * width / im.width))
            im = im.resize((width, height), Image.LANCZOS)
        buf = io.BytesIO()
        im.save(buf, format="JPEG", quality=85, optimize=True)
        return buf.getvalue()


async def save_public_thumbnail(
    image_source: Optional[Union[str, bytes]],
    width: int = PUBLIC_THUMBNAIL_WIDTH,
) -> Optional[str]:
    """把图片处理成 W500 规格存入本地图片目录，返回可对外访问的相对路径。

    :param image_source: 远程 URL、本地 /data/images/ 路径，或已在内存中的图片字节。
    :param width: 目标宽度，图片窄于该值时保持原尺寸不放大。
    :return: 形如 /data/images/w500_<hash>.jpg 的相对路径；失败返回 None。

    why：文件名取图片内容的 sha256 前缀，同一张图重复通知时直接命中已生成的文件，
    既省去重复编码，也避免 config/image 被相同海报的副本堆满。
    """
    raw = image_source if isinstance(image_source, bytes) else await load_image_bytes(image_source)
    if not raw:
        return None

    filename = f"w{width}_{hashlib.sha256(raw).hexdigest()[:16]}.jpg"
    save_path = IMAGE_DIR / filename
    web_path = f"/data/images/{filename}"

    if save_path.is_file():
        return web_path

    try:
        data = await asyncio.to_thread(_encode_thumbnail, raw, width)
    except Exception as e:
        logger.warning(f"生成 W{width} 缩略图失败: {e}")
        return None

    try:
        _ensure_image_dir()
        save_path.write_bytes(data)
    except (OSError, PermissionError) as e:
        logger.warning(f"保存 W{width} 缩略图失败 ({save_path}): {e}")
        return None

    logger.info(f"已生成对外分享缩略图: {save_path} ({len(data)} 字节)")
    return web_path

async def load_image_bytes(image_source: Optional[str], max_bytes: int = 10 * 1024 * 1024) -> Optional[bytes]:
    """把远程 URL、本地图片 URL 或 file:// 地址统一读取为图片字节。

    why：通知渠道只应负责平台发送协议；图片路径解析、防盗链请求头、响应校验在此统一处理。
    """
    if not image_source:
        return None

    try:
        if image_source.startswith("/data/images/"):
            image_path = IMAGE_DIR / Path(image_source).name
            return image_path.read_bytes() if image_path.is_file() else None

        if image_source.startswith("file://"):
            from urllib.parse import unquote, urlparse
            image_path = Path(unquote(urlparse(image_source).path))
            return image_path.read_bytes() if image_path.is_file() else None

        if image_source.startswith("//"):
            image_source = f"https:{image_source}"
        if not image_source.startswith(("http://", "https://")):
            return None

        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Accept": "image/*",
        }
        if "iqiyipic.com" in image_source:
            headers["Referer"] = "https://www.iqiyi.com/"
        elif "hdslb.com" in image_source:
            headers["Referer"] = "https://www.bilibili.com/"

        async with httpx.AsyncClient(timeout=30.0, follow_redirects=True, headers=headers) as client:
            response = await client.get(image_source)
            response.raise_for_status()
            content_type = response.headers.get("content-type", "").lower()
            if content_type and not content_type.startswith("image/"):
                logger.warning(f"图片响应类型无效: {content_type}")
                return None
            if len(response.content) > max_bytes:
                logger.warning(f"图片超过大小限制: {len(response.content)} > {max_bytes}")
                return None
            return response.content
    except Exception as e:
        logger.warning(f"读取图片失败: {e}")
        return None


async def download_image(image_url: Optional[str], session: AsyncSession, scraper_manager: "ScraperManager", provider_name: Optional[str] = None) -> Optional[str]:
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

    # 确保图片目录存在
    _ensure_image_dir()

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
