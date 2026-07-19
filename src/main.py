import warnings
warnings.filterwarnings("ignore", message="urllib3.*doesn't match a supported version")
import uvicorn
import httpx
import logging
from contextlib import asynccontextmanager
from pathlib import Path
from fastapi import FastAPI, Request, status
from fastapi.staticfiles import StaticFiles
from fastapi.responses import JSONResponse

from fastapi.middleware.cors import CORSMiddleware

# 启动预处理：检查配置文件、环境变量、打印来源日志
# 必须在 settings 导入之前调用，确保配置文件存在
from src.core.bootstrap import preload_config
preload_config()

# 内部模块导入
from src.core import settings
from src.api import api_router, control_router
from src.api.dandan import dandan_router
from src.api.mcp import setup_mcp
from src.frontend import register_pwa_routes
from src.utils.asgi_middleware import NotFoundGuardMiddleware, CaptureApiResponseMiddleware
from src.api.control.openapi_docs import register_control_api_docs
from src.core.env import is_docker_environment as _is_docker_environment
from src.core.app_lifecycle import run_startup, run_shutdown

logger = logging.getLogger(__name__)
logger.info(f"当前环境: {settings.environment}")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期：启动/关闭逻辑委托给 src.core.app_lifecycle（保持薄壳）。"""
    await run_startup(app)
    yield
    await run_shutdown(app)


app = FastAPI(
    title="Misaka Danmaku External Control API",
    description="用于外部自动化和集成的API。所有端点都需要通过 `?api_key=` 进行鉴权。",
    version="1.0.0",
    lifespan=lifespan,
    # 禁用默认的 docs_url，我们将使用自定义的本地化版本
    docs_url=None,
    redoc_url=None         # 禁用ReDoc
)

# --- 健康检查端点（供 Docker HEALTHCHECK / 群辉 Container Manager 使用）---
@app.get("/api/health", include_in_schema=False)
async def health_check():
    """轻量级健康检查，不需要认证，不查数据库"""
    return {"status": "ok"}


# --- 前端 PWA 路由（favicon / manifest / registerSW / sw / workbox）---
register_pwa_routes(app)

# --- 外部控制 API 的本地化 Swagger UI 文档路由（实现见 openapi_docs 模块）---
register_control_api_docs(app)

# CORS 配置 — 全放开，兼容反代、PWA、Service Worker 等各种场景
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 新增：全局异常处理器，以优雅地处理网络错误
@app.exception_handler(httpx.ConnectError)
async def httpx_connect_error_handler(request: Request, exc: httpx.ConnectError):
    """处理无法连接到外部服务的错误。"""
    logger.error(f"网络连接错误: 无法连接到 {exc.request.url}。错误: {exc}")
    return JSONResponse(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        content={"detail": f"无法连接到外部服务 ({exc.request.url.host})。请检查您的网络连接、代理设置，或确认目标服务未屏蔽您的服务器IP。"},
    )

@app.exception_handler(httpx.TimeoutException)
async def httpx_timeout_error_handler(request: Request, exc: httpx.TimeoutException):
    """处理外部服务请求超时的错误。"""
    logger.error(f"网络超时错误: 请求 {exc.request.url} 超时。错误: {exc}")
    return JSONResponse(
        status_code=status.HTTP_504_GATEWAY_TIMEOUT,
        content={"detail": f"连接外部服务 ({exc.request.url.host}) 超时。请稍后重试。"},
    )




# 纯 ASGI 中间件注册（替代原两个 @app.middleware("http")）。
# why：BaseHTTPMiddleware 在客户端断开时 cancel scope 会级联取消下游 DB 操作，
# 触发连接池 terminate 二次异常刷屏；纯 ASGI 中间件不套 task group，从源头规避。
# add_middleware 后注册者在外层，此顺序与原装饰器（_log_not_found 先、_capture 后）等价。
app.add_middleware(NotFoundGuardMiddleware)
app.add_middleware(CaptureApiResponseMiddleware)


# 显式地挂载外部控制API路由，以确保其优先级
app.include_router(control_router, prefix="/api/control", tags=["External Control API"])

app.include_router(dandan_router, prefix="/api/v1", tags=["DanDanPlay Compatible"], include_in_schema=False)

# 包含所有非 dandanplay 的 API 路由
app.include_router(api_router, prefix="/api")

# --- MCP Server 初始化 ---
# 必须在所有路由注册完毕后调用，这样 fastapi-mcp 才能扫描到所有外部控制 API
setup_mcp(app)

# --- 挂载 Swagger UI 的静态文件目录 ---
def _get_static_dir():
    """获取静态文件目录，根据运行环境自动调整"""
    if _is_docker_environment():
        # 容器环境
        return Path("/app/static/swagger-ui")
    else:
        # 源码运行环境
        return Path("static/swagger-ui")

STATIC_DIR = _get_static_dir()
app.mount("/static/swagger-ui", StaticFiles(directory=STATIC_DIR), name="swagger-ui-static")

# 添加一个运行入口，以便直接从配置启动
# 这样就可以通过 `python -m src.main` 来运行，并自动使用 config.yml 中的端口和主机
if __name__ == "__main__":
    import socket

    port = settings.server.port
    ipv6_enabled = getattr(settings.server, 'ipv6', True)
    is_reload = settings.environment == "development"

    if ipv6_enabled:
        # 双栈模式：监听 [::] 并 patch socket 使其同时接受 IPv4
        # 通过设置 IPV6_V6ONLY=0，让 [::] 同时监听 IPv4 和 IPv6
        _original_bind = socket.socket.bind

        def _dual_stack_bind(self, address):
            if self.family == socket.AF_INET6:
                try:
                    self.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 0)
                except (AttributeError, OSError):
                    pass
            return _original_bind(self, address)

        socket.socket.bind = _dual_stack_bind
        uvicorn.run(
            "src.main:app",
            host="::",
            port=port,
            reload=is_reload,
        )
    else:
        uvicorn.run(
            "src.main:app",
            host=settings.server.host,
            port=port,
            reload=is_reload,
        )
