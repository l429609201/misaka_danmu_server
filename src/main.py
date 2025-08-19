import uvicorn
import asyncio
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request, Depends
import logging
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse, JSONResponse # noqa: F401
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

print(f"当前环境: {settings.environment}") 

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
        'search_ttl_seconds': (10800, '搜索结果的缓存时间（秒），最低3小时。'),
        'episodes_ttl_seconds': (10800, '分集列表的缓存时间（秒），最低3小时。'),
        'base_info_ttl_seconds': (10800, '基础媒体信息（如爱奇艺）的缓存时间（秒），最低3小时。'),
        'metadata_search_ttl_seconds': (10800, '元数据（如TMDB, Bangumi）搜索结果的缓存时间（秒），最低3小时。'),
        # API 和 Webhook
        'custom_api_domain': ('', '用于拼接弹幕API地址的自定义域名。'),
        'webhook_api_key': ('', '用于Webhook调用的安全密钥。'),
        'external_api_key': ('', '用于外部API调用的安全密钥。'),
        'webhook_custom_domain': ('', '用于拼接Webhook URL的自定义域名。'),
        # 认证
        # 代理
        'proxy_url': ('', '全局HTTP/HTTPS/SOCKS5代理地址。'),
        'proxy_enabled': ('false', '是否全局启用代理。'),
        'jwt_expire_minutes': (settings.jwt.access_token_expire_minutes, 'JWT令牌的有效期（分钟）。-1 表示永不过期。'),
        # 元数据源
        'tmdb_api_key': ('', '用于访问 The Movie Database API 的密钥。'),
        'tmdb_api_base_url': ('https://api.themoviedb.org', 'TMDB API 的基础域名。'),
        'tmdb_image_base_url': ('https://image.tmdb.org', 'TMDB 图片服务的基础 URL。'),
        'tvdb_api_key': ('', '用于访问 TheTVDB API 的密钥。'),
        'bangumi_client_id': ('', '用于Bangumi OAuth的App ID。'),
        'bangumi_client_secret': ('', '用于Bangumi OAuth的App Secret。'),
        'douban_cookie': ('', '用于访问豆瓣API的Cookie。'),
        # 弹幕源
        'danmaku_output_limit_per_source': ('-1', '单源弹幕输出总数限制。-1为无限制。'),
        'danmaku_aggregation_enabled': ('true', '是否启用跨源弹幕聚合功能。'),
        'scraper_verification_enabled': ('false', '是否启用搜索源签名验证。'),
        'bilibili_cookie': ('', '用于访问B站API的Cookie，特别是buvid3。'),
        'gamer_cookie': ('', '用于访问巴哈姆特动画疯的Cookie。'),
        'gamer_user_agent': ('', '用于访问巴哈姆特动画疯的User-Agent。'),
    }
    await app.state.config_manager.register_defaults(default_configs)

    app.state.scraper_manager = ScraperManager(session_factory, app.state.config_manager)
    await app.state.scraper_manager.initialize()
    # 新增：初始化元数据源管理器
    app.state.metadata_manager = MetadataSourceManager(session_factory)
    await app.state.metadata_manager.initialize()

    app.state.task_manager = TaskManager(session_factory)
    # 修正：将 ConfigManager 传递给 WebhookManager
    app.state.webhook_manager = WebhookManager(
        session_factory, app.state.task_manager, app.state.scraper_manager, app.state.config_manager
    )
    app.state.task_manager.start()
    await create_initial_admin_user(app)
    app.state.cleanup_task = asyncio.create_task(cleanup_task(app))
    app.state.scheduler_manager = SchedulerManager(session_factory, app.state.task_manager, app.state.scraper_manager)
    await app.state.scheduler_manager.start()
    
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

@app.middleware("http")
async def log_not_found_requests(request: Request, call_next):
    """
    中间件：捕获所有请求，如果响应是 404 Not Found，
    则以JSON格式记录详细的请求入参，方便调试。
    """
    response = await call_next(request)
    if response.status_code == 404:
        # 创建一个可序列化的 ASGI scope 副本以进行详细日志记录
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
            "message": "HTTP 404 Not Found - 未找到匹配的API路由",
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

# 挂载静态文件目录（适配Vite构建产物）
if settings.environment == "development":
    # 开发环境：不挂载静态文件（由 Vite 开发服务器提供）
    print("开发环境：跳过静态文件挂载")
else:
    # 生产环境：挂载构建后的静态文件
    app.mount("/assets", StaticFiles(directory="web/dist/assets"), name="assets")
    print("生产环境：已挂载静态文件")

# 包含所有非 dandanplay 的 API 路由
app.include_router(api_router, prefix="/api")

app.include_router(dandan_router, prefix="/api/v1", tags=["DanDanPlay Compatible"], include_in_schema=False)

# 将最通用的 dandan_router 挂载在最后，以避免路径冲突。
# 这样可以确保像 /api/ui 这样的静态路径会优先于 /api/{token} 被匹配。
app.include_router(dandan_router, prefix="/api", tags=["DanDanPlay Compatible"])

# 前端入口路由（适配Vite+React SPA）
@app.get("/{full_path:path}", include_in_schema=False)
async def serve_react_app(request: Request, full_path: str):
    # 开发环境重定向到Vite服务器
    if settings.environment == "development":
        base_url = f"http://{settings.client.host}:{settings.client.port}"
        return RedirectResponse(url=f"{base_url}/{full_path}" if full_path else base_url)
    
    # 生产环境返回构建好的index.html
    return FileResponse("web/dist/index.html")

# 添加一个运行入口，以便直接从配置启动
# 这样就可以通过 `python -m src.main` 来运行，并自动使用 config.yml 中的端口和主机
if __name__ == "__main__":
    uvicorn.run(
        "src.main:app",
        host=settings.server.host,
        port=settings.server.port,
        reload=settings.environment == "development"  # 开发环境启用自动重载
    )