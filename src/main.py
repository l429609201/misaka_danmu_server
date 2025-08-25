import uvicorn
import asyncio
import secrets
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request, Depends, status
import httpx
import logging
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse, JSONResponse, Response # noqa: F401
from fastapi.middleware.cors import CORSMiddleware  # 新增：处理跨域
import json
from .config_manager import ConfigManager
from .database import init_db_tables, close_db_engine, create_initial_admin_user
from .api import api_router
from .dandan_api import dandan_router
from .task_manager import TaskManager
from .metadata_manager import MetadataSourceManager
from .scraper_manager import ScraperManager
from .webhook_manager import WebhookManager
from .scheduler import SchedulerManager
from .config import settings
from . import crud, security
from .log_manager import setup_logging
from .rate_limiter import RateLimiter

print(f"当前环境: {settings.environment}") 

logger = logging.getLogger(__name__)

@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    应用生命周期管理器。
    - `yield` 之前的部分在应用启动时执行。 
    - `yield` 之后的部分在应用关闭时执行。
    """
    # --- Startup Logic ---
    setup_logging()

    # init_db_tables 现在处理数据库创建、引擎和会话工厂的创建
    await init_db_tables(app)
    session_factory = app.state.db_session_factory

    # 新增：在启动时清理任何未完成的任务
    async with session_factory() as session:
        interrupted_count = await crud.mark_interrupted_tasks_as_failed(session)
        if interrupted_count > 0:
            logging.getLogger(__name__).info(f"已将 {interrupted_count} 个中断的任务标记为失败。")
    
    # 新增：初始化配置管理器
    app.state.config_manager = ConfigManager(session_factory)
    # 新增：集中定义所有默认配置
    default_configs = {
        # 缓存 TTL
        'jwtSecretKey': (secrets.token_hex(32), '用于签名JWT令牌的密钥，在首次启动时自动生成。'),
        'searchTtlSeconds': (10800, '搜索结果的缓存时间（秒），最低3小时。'),
        'episodesTtlSeconds': (10800, '分集列表的缓存时间（秒），最低3小时。'),
        'baseInfoTtlSeconds': (10800, '基础媒体信息（如爱奇艺）的缓存时间（秒），最低3小时。'),
        'metadataSearchTtlSeconds': (10800, '元数据（如TMDB, Bangumi）搜索结果的缓存时间（秒），最低3小时。'),
        # API 和 Webhook
        'customApiDomain': ('', '用于拼接弹幕API地址的自定义域名。'),
        'webhookApiKey': ('', '用于Webhook调用的安全密钥。'),
        'externalApiKey': ('', '用于外部API调用的安全密钥。'),
        'webhookCustomDomain': ('', '用于拼接Webhook URL的自定义域名。'),
        # 认证
        # 代理
        'proxyUrl': ('', '全局HTTP/HTTPS/SOCKS5代理地址。'),
        'proxyEnabled': ('false', '是否全局启用代理。'),
        'proxySslVerify': ('true', '使用HTTPS代理时是否验证SSL证书。设为false可解决自签名证书问题。'),
        'jwtExpireMinutes': (settings.jwt.access_token_expire_minutes, 'JWT令牌的有效期（分钟）。-1 表示永不过期。'),
        # 元数据源
        'tmdbApiKey': ('', '用于访问 The Movie Database API 的密钥。'),
        'tmdbApiBaseUrl': ('https://api.themoviedb.org', 'TMDB API 的基础域名。'),
        'tmdbImageBaseUrl': ('https://image.tmdb.org', 'TMDB 图片服务的基础 URL。'),
        'tvdbApiKey': ('', '用于访问 TheTVDB API 的密钥。'),
        'bangumiClientId': ('', '用于Bangumi OAuth的App ID。'),
        'bangumiClientSecret': ('', '用于Bangumi OAuth的App Secret。'),
        'doubanCookie': ('', '用于访问豆瓣API的Cookie。'),
        # 弹幕源
        'danmakuOutputLimitPerSource': ('-1', '单源弹幕输出总数限制。-1为无限制。'),
        'danmakuAggregationEnabled': ('true', '是否启用跨源弹幕聚合功能。'),
        'scraperVerificationEnabled': ('false', '是否启用搜索源签名验证。'),
        'bilibiliCookie': ('', '用于访问B站API的Cookie，特别是buvid3。'),
        'gamerCookie': ('', '用于访问巴哈姆特动画疯的Cookie。'),
        'gamerUserAgent': ('', '用于访问巴哈姆特动画疯的User-Agent。'),
        "rate_limit_global_limit": ("50", ""),
        "rate_limit_global_period_seconds": ("3600", ""),
    }
    await app.state.config_manager.register_defaults(default_configs)

    app.state.scraper_manager = ScraperManager(session_factory, app.state.config_manager)
    await app.state.scraper_manager.initialize()
    app.state.rate_limiter = RateLimiter(session_factory, app.state.config_manager, app.state.scraper_manager)

    # 新增：初始化元数据源管理器
    app.state.metadata_manager = MetadataSourceManager(session_factory, app.state.config_manager, app.state.scraper_manager)
    await app.state.metadata_manager.initialize()
    # 新增：在主应用中显式挂载元数据管理器的路由
    # 这使得路由结构更清晰，并解决了潜在的冲突问题
    # 必须在管理器初始化之后挂载，以确保其内部路由已构建
    app.include_router(app.state.metadata_manager.router, prefix="/api/metadata")



    app.state.task_manager = TaskManager(session_factory)
    # 修正：将 ConfigManager 传递给 WebhookManager
    app.state.webhook_manager = WebhookManager(
        session_factory, app.state.task_manager, app.state.scraper_manager, app.state.config_manager, app.state.rate_limiter, app.state.metadata_manager
    )
    app.state.task_manager.start()
    await create_initial_admin_user(app)
    app.state.cleanup_task = asyncio.create_task(cleanup_task(app))
    app.state.scheduler_manager = SchedulerManager(session_factory, app.state.task_manager, app.state.scraper_manager, app.state.rate_limiter, app.state.metadata_manager)
    await app.state.scheduler_manager.start()
    
    # --- 前端服务 (生产环境) ---
    # 在所有API路由注册完毕后，再挂载前端服务，以确保API路由优先匹配。
    # 在生产环境中，我们需要挂载 Vite 构建后的静态资源目录
    # 并且需要一个“捕获所有”的路由来始终提供 index.html，以支持前端路由。
    if settings.environment == "development":
        # 开发环境：所有非API请求都重定向到Vite开发服务器
        @app.get("/{full_path:path}", include_in_schema=False)
        async def serve_react_app_dev(request: Request, full_path: str):
            base_url = f"http://{settings.client.host}:{settings.client.port}"
            return RedirectResponse(url=f"{base_url}/{full_path}" if full_path else base_url)
    else:
        # 生产环境：显式挂载静态资源目录
        app.mount("/assets", StaticFiles(directory="web/dist/assets"), name="assets")
        # 修正：挂载前端的静态图片 (如 logo)，使其指向正确的 'web/dist/images' 目录
        app.mount("/images", StaticFiles(directory="web/dist/images"), name="images")
        # 挂载用户缓存的图片 (如海报)
        app.mount("/data/images", StaticFiles(directory="config/image"), name="cached_images")
        # 然后，为所有其他路径提供 index.html 以支持前端路由
        @app.get("/{full_path:path}", include_in_schema=False)
        async def serve_spa(request: Request, full_path: str):
            return FileResponse("web/dist/index.html")

    yield
    
    # --- Shutdown Logic ---
    if hasattr(app.state, "cleanup_task"):
        app.state.cleanup_task.cancel()
        try:
            await app.state.cleanup_task
        except asyncio.CancelledError:
            pass
    await close_db_engine(app)
    if hasattr(app.state, "scraper_manager"):
        await app.state.scraper_manager.close_all()
    if hasattr(app.state, "task_manager"):
        await app.state.task_manager.stop()
    # 新增：在关闭时也关闭元数据管理器
    if hasattr(app.state, "metadata_manager"):
        await app.state.metadata_manager.close_all()
    if hasattr(app.state, "scheduler_manager"):
        await app.state.scheduler_manager.stop()

app = FastAPI(
    title="Misaka Danmaku External Control API",
    description="用于外部自动化和集成的API。所有端点都需要通过 `?api_key=` 进行鉴权。",
    version="1.0.0",
    lifespan=lifespan,
    docs_url="/api/control/docs",  # 为外部控制API设置专用的文档路径
    redoc_url=None         # 禁用ReDoc
)

# 新增：配置CORS，允许前端开发服务器访问API
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        f"http://{settings.client.host}:{settings.client.port}",  # 前端开发服务器
        "http://localhost:5173",  # 默认Vite开发端口
        "http://127.0.0.1:5173",
    ],
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




@app.middleware("http")
async def log_not_found_requests(request: Request, call_next):
    """
    中间件：捕获所有请求。
    - 如果是未找到的API路径 (404)，则返回 403 Forbidden，避免路径枚举。
    - 对其他 404 错误，记录详细信息以供调试。
    """
    response = await call_next(request)
    if response.status_code == 404:
        # 如果是 API 路径未找到，返回 403
        if request.url.path.startswith("/api/"):
            logger.warning(
                f"API路径未找到 (返回403): {request.method} {request.url.path} from {request.client.host}"
            )
            return JSONResponse(
                status_code=status.HTTP_403_FORBIDDEN,
                content={"detail": "Forbidden"}
            )
        
        # 对于非 API 路径的 404 (例如，如果静态文件服务被错误配置)，记录详细信息
        scope = request.scope
        serializable_scope = {
            "type": scope.get("type"),
            "http_version": scope.get("http_version"),
            "server": scope.get("server"),
            "client": scope.get("client"),
            "scheme": scope.get("scheme"),
            "method": scope.get("method"),
            "root_path": scope.get("root_path"),
            "path": scope.get("path"),
            "raw_path": scope.get("raw_path", b"").decode("utf-8", "ignore"),
            "query_string": scope.get("query_string", b"").decode("utf-8", "ignore"),
            "headers": {h[0].decode("utf-8", "ignore"): h[1].decode("utf-8", "ignore") for h in scope.get("headers", [])},
        }
        log_details = {
            "message": "HTTP 404 Not Found - 未找到匹配的路由或文件",
            "url": str(request.url),
            "raw_request_scope": serializable_scope
        }
        logging.getLogger(__name__).warning("未处理的请求详情 (原始请求范围):\n%s", json.dumps(log_details, indent=2, ensure_ascii=False))
    return response

async def cleanup_task(app: FastAPI):
    """定期清理过期缓存和OAuth states的后台任务。"""
    session_factory = app.state.db_session_factory
    while True:
        try:
            await asyncio.sleep(3600) # 每小时清理一次
            async with session_factory() as session:
                await crud.clear_expired_cache(session)
                await crud.clear_expired_oauth_states(session)
        except asyncio.CancelledError:
            break
        except Exception as e:
            logging.getLogger(__name__).error(f"缓存清理任务出错: {e}")

# 包含所有非 dandanplay 的 API 路由
app.include_router(api_router, prefix="/api")

app.include_router(dandan_router, prefix="/api/v1", tags=["DanDanPlay Compatible"], include_in_schema=False)

# 添加一个运行入口，以便直接从配置启动
# 这样就可以通过 `python -m src.main` 来运行，并自动使用 config.yml 中的端口和主机
if __name__ == "__main__":
    uvicorn.run(
        "src.main:app",
        host=settings.server.host,
        port=settings.server.port,
        reload=settings.environment == "development"  # 开发环境启用自动重载
    )