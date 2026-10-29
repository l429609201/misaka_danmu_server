"""
前端静态资源挂载与 PWA 路由。

将所有与前端宿主相关的逻辑集中在此，保持 main.py 简洁。
"""

import logging
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles

logger = logging.getLogger(__name__)

# 构建产物属于源码包的只读资源，不随独立数据库/弹幕落盘的工作目录变化。
_WEB_DIST = Path(__file__).resolve().parent.parent / "web" / "dist"

# 静态文件后缀白名单：请求带这些后缀时，缺失就返回 404，绝不回退到 index.html
_STATIC_FILE_SUFFIXES = (
    ".js", ".css", ".map", ".json", ".ico", ".png", ".jpg", ".jpeg",
    ".gif", ".svg", ".webp", ".woff", ".woff2", ".ttf", ".eot",
)


def _no_store_headers() -> dict[str, str]:
    """返回禁止缓存的响应头，用于 PWA 注册脚本等需要即时获取最新版本的文件。"""
    return {
        "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
        "Pragma": "no-cache",
        "Expires": "0",
    }


def mount_frontend(app: FastAPI, app_settings: Any) -> None:
    """
    挂载前端静态资源和 SPA fallback。

    必须在所有 API 路由注册完毕后调用，以确保 API 路由优先匹配。
    """
    # 用户缓存图片仍属于当前应用工作目录，不能指向生产实例的数据目录。
    app.mount("/data/images", StaticFiles(directory="config/image"), name="cached_images")

    if app_settings.environment == "development":
        @app.get("/{full_path:path}", include_in_schema=False)
        async def serve_react_app_dev(request: Request, full_path: str) -> RedirectResponse:
            base_url = f"http://{app_settings.client.host}:{app_settings.client.port}"
            return RedirectResponse(url=f"{base_url}/{full_path}" if full_path else base_url)
    else:
        # 生产环境：构建资源固定跟随源码，只有图片缓存/弹幕使用隔离工作目录。
        app.mount("/assets", StaticFiles(directory=_WEB_DIST / "assets"), name="assets")
        app.mount("/images", StaticFiles(directory=_WEB_DIST / "images"), name="images")
        app.mount("/dist", StaticFiles(directory=_WEB_DIST), name="dist")

        # SPA fallback：仅对前端路由返回 index.html
        @app.get("/{full_path:path}", include_in_schema=False)
        async def serve_spa(request: Request, full_path: str) -> Response:
            # 静态资源缺失时直接 404，防止浏览器把 HTML 当 JS/CSS 执行
            if Path(full_path).suffix.lower() in _STATIC_FILE_SUFFIXES:
                return Response(status_code=404)
            return FileResponse(
                _WEB_DIST / "index.html",
                headers=_no_store_headers(),
            )


def register_pwa_routes(app: FastAPI) -> None:
    """
    注册 PWA 相关的根路径路由（favicon / manifest / registerSW / sw / workbox）。

    这些路由必须在 app 创建后、API 路由注册前就挂好，
    这样才能优先于 SPA catch-all 被匹配到。
    """
    @app.get("/favicon.ico", include_in_schema=False)
    async def favicon() -> FileResponse:
        """返回源码包内的站点图标。"""
        return FileResponse(_WEB_DIST / "images" / "favicon.ico", media_type="image/x-icon")

    @app.get("/manifest.json", include_in_schema=False)
    async def pwa_manifest() -> FileResponse:
        """返回源码包内的 PWA 清单并禁止旧内容缓存。"""
        return FileResponse(
            _WEB_DIST / "manifest.json",
            media_type="application/manifest+json",
            headers=_no_store_headers(),
        )

    @app.get("/registerSW.js", include_in_schema=False)
    async def pwa_register_sw() -> Response:
        """vite-plugin-pwa 注入的 Service Worker 注册脚本。"""
        register_sw_path = _WEB_DIST / "registerSW.js"
        if register_sw_path.exists():
            return FileResponse(
                register_sw_path,
                media_type="application/javascript",
                headers=_no_store_headers(),
            )
        # 文件不存在时返回安全注销脚本，避免旧 SW 继续控制页面
        return Response(
            content="if('serviceWorker' in navigator){navigator.serviceWorker.getRegistrations().then(rs=>rs.forEach(r=>r.unregister()))}",
            media_type="application/javascript",
            headers=_no_store_headers(),
        )

    @app.get("/sw.js", include_in_schema=False)
    async def pwa_service_worker() -> Response:
        """返回源码包内的 Service Worker，缺失时不返回 HTML。"""
        sw_path = _WEB_DIST / "sw.js"
        if sw_path.exists():
            return FileResponse(
                sw_path,
                media_type="application/javascript",
                headers=_no_store_headers(),
            )
        return Response(status_code=404)

    @app.get("/workbox-{rest:path}", include_in_schema=False)
    async def pwa_workbox(rest: str) -> Response:
        """只返回构建目录内的 Workbox 文件，拒绝穿越或目录响应。"""
        wb_path = (_WEB_DIST / f"workbox-{rest}").resolve()
        if wb_path.is_relative_to(_WEB_DIST.resolve()) and wb_path.is_file():
            return FileResponse(
                wb_path,
                media_type="application/javascript",
                headers=_no_store_headers(),
            )
        return Response(status_code=404)
