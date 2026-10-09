"""图片纯处理函数：仅处理内存数据与 URL，不访问网络、文件或业务服务。"""

import io
from urllib.parse import urlparse

from PIL import Image, ImageOps


PUBLIC_THUMBNAIL_WIDTH = 500


def encode_webp(raw: bytes, quality: int = 85) -> bytes:
    """将海报内存编码为静态 WebP，保留透明通道并校正 EXIF 方向。"""
    with Image.open(io.BytesIO(raw)) as source:
        image = ImageOps.exif_transpose(source).convert("RGBA")
        with io.BytesIO() as output:
            image.save(output, format="WEBP", quality=quality, method=4)
            return output.getvalue()


def encode_notification_image(raw: bytes, width: int | None = None) -> tuple[bytes, str, str]:
    """将通知海报编码为兼容 JPEG/PNG，并返回字节、后缀及 MIME。"""
    if width is not None and width <= 0:
        raise ValueError("图片宽度必须为正数")
    with Image.open(io.BytesIO(raw)) as source:
        image = ImageOps.exif_transpose(source)
        use_png = source.format == "PNG" and width is None
        if use_png:
            image = image.convert("RGBA")
        else:
            rgba = image.convert("RGBA")
            image = Image.new("RGB", rgba.size, "white")
            image.paste(rgba, mask=rgba.getchannel("A"))
        if width is not None and image.width > width:
            height = max(1, round(image.height * width / image.width))
            image = image.resize((width, height), Image.Resampling.LANCZOS)
        with io.BytesIO() as output:
            if use_png:
                image.save(output, format="PNG", optimize=True)
                return output.getvalue(), ".png", "image/png"
            image.save(output, format="JPEG", quality=85, optimize=True)
            return output.getvalue(), ".jpg", "image/jpeg"


def encode_thumbnail(raw: bytes, width: int = PUBLIC_THUMBNAIL_WIDTH) -> bytes:
    """将内存图片等比缩小为 JPEG；由调用方安排线程执行。"""
    return encode_notification_image(raw, width)[0]


def normalize_image_url(url: str) -> str:
    """补全协议，并为爱奇艺图片使用 HTTPS。"""
    if url.startswith("//"):
        url = "https:" + url
    host = (urlparse(url).hostname or "").lower()
    if host == "iqiyipic.com" or host.endswith(".iqiyipic.com"):
        url = url.replace("http://", "https://", 1)
    return url


def build_image_headers(url: str, referer: str | None = None) -> dict[str, str]:
    """构造图片请求头，按实际主机名匹配防盗链规则。"""
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Accept": "image/*",
    }
    if referer:
        headers["Referer"] = referer
    host = (urlparse(url).hostname or "").lower()
    if host == "iqiyipic.com" or host.endswith(".iqiyipic.com"):
        headers["Referer"] = "https://www.iqiyi.com/"
    elif host == "hdslb.com" or host.endswith(".hdslb.com"):
        headers["Referer"] = "https://www.bilibili.com/"
    return headers


def image_extension(content_type: str) -> str:
    """根据响应类型选择缓存后缀，保留既有 JPEG 默认值。"""
    mime = content_type.split(";", 1)[0].strip().lower()
    return {"image/png": ".png", "image/webp": ".webp"}.get(mime, ".jpg")
