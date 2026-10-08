"""图片纯处理函数：仅处理内存数据与 URL，不访问网络、文件或业务服务。"""

import io
from urllib.parse import urlparse

from PIL import Image


PUBLIC_THUMBNAIL_WIDTH = 500


def encode_thumbnail(raw: bytes, width: int = PUBLIC_THUMBNAIL_WIDTH) -> bytes:
    """将内存图片等比缩小为 JPEG；由调用方安排线程执行。"""
    if width <= 0:
        raise ValueError("缩略图宽度必须为正数")
    with Image.open(io.BytesIO(raw)) as source:
        # 透明通道和调色板模式无法直接保存为 JPEG，统一转为 RGB。
        image = source.convert("RGB")
        if image.width > width:
            height = max(1, round(image.height * width / image.width))
            image = image.resize((width, height), Image.Resampling.LANCZOS)
        with io.BytesIO() as output:
            image.save(output, format="JPEG", quality=85, optimize=True)
            return output.getvalue()


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
