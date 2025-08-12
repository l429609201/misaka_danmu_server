import uvicorn
import asyncio
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request, Depends
import logging
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from fastapi.middleware.cors import CORSMiddleware  # 新增：处理跨域
import json
from .database import create_db_pool, close_db_pool, init_db_tables, create_initial_admin_user
from .api.ui import router as ui_router, auth_router
from .api.bangumi_api import router as bangumi_router
from .api.tmdb_api import router as tmdb_router
from .api.webhook_api import router as webhook_router
from .api.imdb_api import router as imdb_router
from .api.tvdb_api import router as tvdb_router
from .api.douban_api import router as douban_router
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


    pool = await create_db_pool(app)
    await init_db_tables(app)
    # 新增：在启动时清理任何未完成的任务
    interrupted_count = await crud.mark_interrupted_tasks_as_failed(pool)
    if interrupted_count > 0:
        logging.getLogger(__name__).info(f"已将 {interrupted_count} 个中断的任务标记为失败。")

    app.state.scraper_manager = ScraperManager(pool)
    await app.state.scraper_manager.load_and_sync_scrapers()
    # 新增：初始化元数据源管理器
    app.state.metadata_manager = MetadataSourceManager(pool)
    await app.state.metadata_manager.initialize()

    app.state.task_manager = TaskManager(pool)
    app.state.webhook_manager = WebhookManager(pool, app.state.task_manager, app.state.scraper_manager)
    app.state.task_manager.start()
    await create_initial_admin_user(app)
    app.state.cleanup_task = asyncio.create_task(cleanup_task(app))
    app.state.scheduler_manager = SchedulerManager(pool, app.state.task_manager)
    await app.state.scheduler_manager.start()
    
    yield
    
    # --- Shutdown Logic ---
    if hasattr(app.state, "cleanup_task"):
        app.state.cleanup_task.cancel()
        try:
            await app.state.cleanup_task
        except asyncio.CancelledError:
            pass
    await close_db_pool(app)
    if hasattr(app.state, "scraper_manager"):
        await app.state.scraper_manager.close_all()
    if hasattr(app.state, "task_manager"):
        await app.state.task_manager.stop()
    if hasattr(app.state, "scheduler_manager"):
        await app.state.scheduler_manager.stop()


app = FastAPI(title="Danmaku API", description="一个基于dandanplay API风格的弹幕服务", version="1.0.0", lifespan=lifespan)

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
    pool = app.state.db_pool
    while True:
        try:
            await asyncio.sleep(3600) # 每小时清理一次
            await crud.clear_expired_cache(pool)
            await crud.clear_expired_oauth_states(app.state.db_pool)
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
    app.mount("/static", StaticFiles(directory="dist/static"), name="static")
    app.mount("/assets", StaticFiles(directory="dist/assets"), name="assets")
    print("生产环境：已挂载静态文件")

# 包含 v2 版本的 API 路由
app.include_router(ui_router, prefix="/api/ui", tags=["Web UI API"])
app.include_router(auth_router, prefix="/api/ui/auth", tags=["Auth"])
app.include_router(dandan_router, prefix="/api", tags=["DanDanPlay Compatible"])
app.include_router(bangumi_router, prefix="/api/bgm", tags=["Bangumi"])
app.include_router(tmdb_router, prefix="/api/tmdb", tags=["TMDB"])
app.include_router(douban_router, prefix="/api/douban", tags=["Douban"])
app.include_router(imdb_router, prefix="/api/imdb", tags=["IMDb"])
app.include_router(tvdb_router, prefix="/api/tvdb", tags=["TVDB"])
app.include_router(webhook_router, prefix="/api/webhook", tags=["Webhook"])

# 前端入口路由（适配Vite+React SPA）
@app.get("/{full_path:path}", include_in_schema=False)
async def serve_react_app(request: Request, full_path: str):
    # 开发环境重定向到Vite服务器
    if settings.environment == "development":
        base_url = f"http://{settings.client.host}:{settings.client.port}"
        return RedirectResponse(url=f"{base_url}/{full_path}" if full_path else base_url)
    
    # 生产环境返回构建好的index.html
    return FileResponse("dist/index.html")

# 添加一个运行入口，以便直接从配置启动
# 这样就可以通过 `python -m src.main` 来运行，并自动使用 config.yml 中的端口和主机
if __name__ == "__main__":
    uvicorn.run(
        "src.main:app",
        host=settings.server.host,
        port=settings.server.port,
        reload=settings.environment == "development"  # 开发环境启用自动重载
    )
