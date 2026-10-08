"""图片网络获取能力：只接受显式网络参数，不查询数据库或决定业务配置。"""

import logging
from typing import Optional

import httpx

from src.utils.misc.image_processing import build_image_headers, normalize_image_url

logger = logging.getLogger(__name__)


async def fetch_image(
    image_url: str, *, proxy: Optional[str] = None, verify: bool = True,
    referer: Optional[str] = None, max_bytes: int = 10 * 1024 * 1024,
) -> Optional[tuple[bytes, str]]:
    """流式获取图片及响应类型，在读取过程中限制内存占用。"""
    if max_bytes <= 0:
        raise ValueError("图片下载上限必须为正数")
    url = normalize_image_url(image_url)
    if not url.startswith(("http://", "https://")):
        return None
    try:
        async with httpx.AsyncClient(
            timeout=30.0, follow_redirects=True, proxy=proxy, verify=verify,
            headers=build_image_headers(url, referer),
        ) as client:
            async with client.stream("GET", url) as response:
                response.raise_for_status()
                content_type = response.headers.get("content-type", "").lower()
                if content_type and not content_type.startswith("image/"):
                    logger.warning("图片响应类型无效：%s", content_type)
                    return None
                data = bytearray()
                async for chunk in response.aiter_bytes():
                    # 不等待整个响应下载完成后才判断大小。
                    if len(data) + len(chunk) > max_bytes:
                        logger.warning("图片超过下载大小上限：%s", max_bytes)
                        return None
                    data.extend(chunk)
                return (bytes(data), content_type) if data else None
    except (httpx.HTTPError, ValueError):
        logger.warning("图片下载失败", exc_info=True)
        return None


async def probe_image_url(url: str) -> dict:
    """探测显式图片地址，只读取响应头，避免下载无界响应体。"""
    try:
        async with httpx.AsyncClient(timeout=8.0, follow_redirects=True) as client:
            async with client.stream("GET", url, headers={"Accept": "image/*"}) as response:
                content_type = response.headers.get("content-type", "").lower()
                if response.status_code != 200 or not content_type.startswith("image/"):
                    return {
                        "ok": False,
                        "detail": f"图片静态路由不可用（HTTP {response.status_code}，Content-Type={content_type or '未知'}）",
                    }
        return {"ok": True}
    except httpx.TimeoutException:
        return {"ok": False, "detail": "自定义域名访问超时，请检查公网解析和反向代理"}
    except httpx.HTTPError as exc:
        return {"ok": False, "detail": f"自定义域名无法访问：{type(exc).__name__}"}
