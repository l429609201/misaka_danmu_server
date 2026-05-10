import time
import warnings
warnings.filterwarnings("ignore", message="urllib3.*doesn't match a supported version")
import uvicorn
import asyncio
import secrets
import httpx
import logging
import json
from contextlib import asynccontextmanager
from pathlib import Path
from fastapi import FastAPI, Request, Depends, status
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse, JSONResponse, Response # noqa: F401
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text

# 内部模块导入 - 使用聚合式导入
from src.core import settings
from src.core.default_configs import get_default_configs
from src.core.cache import init_cache_backend, close_cache_backend
from src.db import crud, orm_models, init_db_tables, close_db_engine, create_initial_admin_user, get_db_type, DatabaseStartupError
from src.db import ConfigManager, CacheManager  # 管理器从 db 层导入
from src.services import (
    TaskManager, MetadataSourceManager, ScraperManager, WebhookManager,
    SchedulerManager, TitleRecognitionManager, MediaServerManager,
    TransportManager, setup_logging,
    NotificationService, NotificationManager,
    TunnelService, apply_tunnel_from_notification_manager,
)
from src.utils import InternalPollingManager, init_proxy_middleware
from src.api import api_router, control_router
from src.api.dandan import dandan_router
from src.api.middleware import log_not_found_requests, capture_api_response
from src.api.mcp import setup_mcp
from src.ai import AIMatcherManager
from src.ai.ai_prompts import DEFAULT_AI_MATCH_PROMPT, DEFAULT_AI_RECOGNITION_PROMPT, DEFAULT_AI_ALIAS_VALIDATION_PROMPT, DEFAULT_AI_ALIAS_EXPANSION_PROMPT, DEFAULT_AI_SEASON_MAPPING_PROMPT
from src.rate_limiter import RateLimiter
from src._version import APP_VERSION
from src import security
    
logger = logging.getLogger(__name__)
logger.info(f"当前环境: {settings.environment}")

def _is_docker_environment():
    """检测是否在Docker容器中运行"""
    import os
    # 方法1: 检查 /.dockerenv 文件（Docker标准做法）
    if Path("/.dockerenv").exists():
        return True
    # 方法2: 检查环境变量
    if os.getenv("DOCKER_CONTAINER") == "true" or os.getenv("IN_DOCKER") == "true":
        return True
    # 方法3: 检查当前工作目录是否为 /app
    if Path.cwd() == Path("/app"):
        return True
    return False

def _ensure_required_directories():
    """确保应用运行所需的目录存在"""
    if _is_docker_environment():
        required_dirs = [
            Path("/app/config/image"),
        ]
    else:
        required_dirs = [
            Path("config/image"),
        ]

    for dir_path in required_dirs:
        try:
            dir_path.mkdir(parents=True, exist_ok=True)
            logger.info(f"确保目录存在: {dir_path}")
        except (OSError, PermissionError) as e:
            logger.warning(f"无法创建目录 {dir_path}: {e}")

def _get_default_danmaku_path_template():
    """根据运行环境获取默认弹幕路径模板"""

    if _is_docker_environment():
        return '/app/config/danmaku/${animeId}/${episodeId}'
    else:
        return 'config/danmaku/${animeId}/${episodeId}'


async def _apply_tunnel_from_channels(app):
    await apply_tunnel_from_notification_manager(
        tunnel_service=app.state.tunnel_service,
        notification_manager=app.state.notification_manager,
        config_manager=app.state.config_manager,
        local_port=settings.server.port,
    )

@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    应用生命周期管理器。
    - `yield` 之前的部分在应用启动时执行。
            try:
                await app.state.metadata_manager.close_all()
            except Exception as e:
                logger.exception(f"关闭 MetadataManager 时发生错误: {e}")
    """
    # --- Startup Logic ---
    setup_logging()

    # 新增：在日志系统初始化后立即打印版本号
    logger.info(f"Misaka Danmaku API 版本 {APP_VERSION} 正在启动...")

    # 创建必要的目录
    _ensure_required_directories()

    # init_db_tables 现在处理数据库创建、引擎和会话工厂的创建
    try:
        await init_db_tables(app)
    except DatabaseStartupError:
        import os
        os._exit(1)
    session_factory = app.state.db_session_factory

    # 注意：中断任务的处理已移至 TaskManager._handle_interrupted_tasks()
    # 该方法会在 task_manager.start() 时自动执行，尝试恢复可恢复的任务，
    # 并将无法恢复的任务标记为失败。不要在此处提前标记，否则会导致任务无法恢复。

    # 新增:PostgreSQL序列自动修复(防止主键冲突)
    if get_db_type() == "postgresql":
        async with session_factory() as session:
            try:
                await session.execute(text(
                    "SELECT setval('anime_id_seq', (SELECT COALESCE(MAX(id), 0) FROM anime))"
                ))
                await session.commit()
                logger.info("已自动同步PostgreSQL的anime_id_seq序列")
            except Exception as e:
                logger.warning(f"同步PostgreSQL序列时出错(可忽略): {e}")

    # 初始化配置管理器
    app.state.config_manager = ConfigManager(session_factory)

    # 注册默认配置(从default_configs.py导入)
    ai_prompts = {
        'DEFAULT_AI_MATCH_PROMPT': DEFAULT_AI_MATCH_PROMPT,
        'DEFAULT_AI_RECOGNITION_PROMPT': DEFAULT_AI_RECOGNITION_PROMPT,
        'DEFAULT_AI_ALIAS_VALIDATION_PROMPT': DEFAULT_AI_ALIAS_VALIDATION_PROMPT,
        'DEFAULT_AI_ALIAS_EXPANSION_PROMPT': DEFAULT_AI_ALIAS_EXPANSION_PROMPT,
        'DEFAULT_AI_SEASON_MAPPING_PROMPT': DEFAULT_AI_SEASON_MAPPING_PROMPT,
    }
    default_configs = get_default_configs(settings=settings, ai_prompts=ai_prompts)
    # 添加运行时生成的配置
    default_configs['jwtSecretKey'] = (secrets.token_hex(32), '用于签名JWT令牌的密钥，在首次启动时自动生成。')

    await app.state.config_manager.register_defaults(default_configs)

    # 初始化 TransportManager
    app.state.transport_manager = TransportManager()

    # 初始化缓存后端（Memory/Redis/Database/Hybrid，Redis 不可用时自动降级）
    cache_backend = await init_cache_backend(
        session_factory=session_factory,
        cache_config=settings.cache,
    )

    # 初始化 CacheManager（使用新的缓存后端）
    app.state.cache_manager = CacheManager(session_factory, backend=cache_backend)
    logger.info("缓存管理器已初始化")

    # 初始化 ProxyMiddleware
    app.state.proxy_middleware = init_proxy_middleware(app.state.config_manager)
    logger.info("代理中间件已初始化")

    # 初始化 AIMatcherManager（传入 session_factory 用于 AI 调用统计持久化）
    app.state.ai_matcher_manager = AIMatcherManager(app.state.config_manager, session_factory)
    logger.info("AI匹配管理器已初始化")

    # --- 并行优化的初始化顺序 ---
    startup_start = time.time()

    # 1-3. 创建管理器实例（不阻塞）
    app.state.metadata_manager = MetadataSourceManager(session_factory, app.state.config_manager, None, app.state.cache_manager)
    app.state.scraper_manager = ScraperManager(session_factory, app.state.config_manager, app.state.metadata_manager, app.state.transport_manager)
    app.state.metadata_manager.scraper_manager = app.state.scraper_manager

    # 4. 【并行优化】同时初始化 + 预热
    logger.info("开始并行初始化...")
    init_start = time.time()

    # 先并行初始化两个管理器
    await asyncio.gather(
        app.state.scraper_manager.initialize(),
        app.state.metadata_manager.initialize()
    )

    # 【优化】预加载所有配置到缓存
    logger.info("预加载配置缓存...")
    async with session_factory() as session:
        # 预加载代理相关配置
        proxy_mode = await crud.get_config_value(session, "proxyMode", "none")
        proxy_url = await crud.get_config_value(session, "proxyUrl", "")
        proxy_enabled = await crud.get_config_value(session, "proxyEnabled", "false")
        accelerate_proxy_url = await crud.get_config_value(session, "accelerateProxyUrl", "")
        app.state.config_manager._cache["proxyMode"] = proxy_mode
        app.state.config_manager._cache["proxyUrl"] = proxy_url
        app.state.config_manager._cache["proxyEnabled"] = proxy_enabled
        app.state.config_manager._cache["accelerateProxyUrl"] = accelerate_proxy_url

        # 一次性查询所有 scraper 设置并缓存
        scraper_settings = await crud.get_all_scraper_settings(session)
        # 存储到 scraper_manager 中供后续使用,避免重复查询
        app.state.scraper_manager._cached_scraper_settings = {
            s['providerName']: s for s in scraper_settings
        }

    # 初始化关键组件（同步执行，确保启动正常）
    app.state.rate_limiter = RateLimiter(session_factory, app.state.scraper_manager)
    app.include_router(app.state.metadata_manager.router, prefix="/api/metadata")

    # Add bangumi specific routes with /bangumi prefix
    if 'bangumi' in app.state.metadata_manager.sources:
        bangumi_router = app.state.metadata_manager.sources['bangumi'].api_router
        app.include_router(bangumi_router, prefix="/api/bangumi", tags=["Bangumi"])



    app.state.task_manager = TaskManager(session_factory, app.state.config_manager)

    # 初始化识别词管理器
    app.state.title_recognition_manager = TitleRecognitionManager(session_factory)

    # 初始化媒体服务器管理器
    app.state.media_server_manager = MediaServerManager(session_factory)
    await app.state.media_server_manager.initialize()

    app.state.webhook_manager = WebhookManager(
        session_factory, app.state.task_manager, app.state.scraper_manager,
        app.state.rate_limiter, app.state.metadata_manager,
        app.state.config_manager, app.state.title_recognition_manager,
        app.state.ai_matcher_manager
    )

    init_time = time.time() - init_start
    logger.info(f"并行初始化完成，耗时 {init_time:.2f} 秒")

    # 设置任务恢复所需的依赖，用于重启后恢复排队中的任务
    app.state.task_manager.set_recovery_dependencies({
        "scraper_manager": app.state.scraper_manager,
        "rate_limiter": app.state.rate_limiter,
        "metadata_manager": app.state.metadata_manager,
        "ai_matcher_manager": app.state.ai_matcher_manager,
        "title_recognition_manager": app.state.title_recognition_manager,
    })

    # 5. 启动服务（必须在上面完成后）
    app.state.task_manager.start()
    await create_initial_admin_user(app)

    # 一次性清理：删除旧的 system_token_reset 定时任务（已迁移到内部轮询任务）
    async with session_factory() as session:
        old_task = await session.get(orm_models.ScheduledTask, "system_token_reset")
        if old_task:
            await session.delete(old_task)
            await session.commit()
            logger.info("已清理旧的 system_token_reset 定时任务（已迁移到内部轮询任务）")

    app.state.cleanup_task = asyncio.create_task(cleanup_task(app))
    app.state.scheduler_manager = SchedulerManager(
        session_factory, app.state.task_manager, app.state.scraper_manager,
        app.state.rate_limiter, app.state.metadata_manager,
        app.state.config_manager, app.state.ai_matcher_manager,
        app.state.title_recognition_manager
    )
    await app.state.scheduler_manager.start()

    # 内置轮询任务管理器（任务在 start() 中自动注册）
    app.state.internal_polling = InternalPollingManager(app)
    await app.state.internal_polling.start()

    # 初始化通知服务
    app.state.notification_service = NotificationService(session_factory)
    app.state.notification_service.set_dependencies(
        scraper_manager=app.state.scraper_manager,
        metadata_manager=app.state.metadata_manager,
        task_manager=app.state.task_manager,
        scheduler_manager=app.state.scheduler_manager,
        config_manager=app.state.config_manager,
        rate_limiter=app.state.rate_limiter,
        title_recognition_manager=app.state.title_recognition_manager,
        ai_matcher_manager=app.state.ai_matcher_manager,
    )
    app.state.notification_manager = NotificationManager(session_factory, app.state.notification_service)
    await app.state.notification_manager.initialize()
    app.state.notification_service.notification_manager = app.state.notification_manager
    await app.state.notification_manager.start_channels()

    # 初始化 TunnelService，并根据渠道配置决定是否启动隧道
    app.state.tunnel_service = TunnelService()
    await _apply_tunnel_from_channels(app)
    logger.info("隧道服务已初始化")

    # 将通知服务注入 TaskManager 和 WebhookManager
    app.state.task_manager.set_notification_service(app.state.notification_service)
    app.state.webhook_manager.notification_service = app.state.notification_service

    total_time = time.time() - startup_start
    logger.info(f"应用启动完成，总耗时 {total_time:.2f} 秒")

    # 发射系统启动通知
    try:
        await app.state.notification_service.emit_event("system_start", {})
    except Exception as e:
        logger.error(f"发射 system_start 事件失败: {e}")

    # --- 前端服务 ---
    # 在所有API路由注册完毕后，再挂载前端服务，以确保API路由优先匹配。

    # 无论开发还是生产环境，都需要挂载用户缓存的图片
    # 这样开发环境下前端通过代理也能访问到这些资源
    app.mount("/data/images", StaticFiles(directory="config/image"), name="cached_images")

    # 在生产环境中，我们需要挂载 Vite 构建后的静态资源目录
    # 并且需要一个"捕获所有"的路由来始终提供 index.html，以支持前端路由。
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
        # dist挂载
        app.mount("/dist", StaticFiles(directory="web/dist"), name="dist")
        # 然后，为所有其他路径提供 index.html 以支持前端路由
        @app.get("/{full_path:path}", include_in_schema=False)
        async def serve_spa(request: Request, full_path: str):
            return FileResponse("web/dist/index.html")

    yield

    # --- Shutdown Logic ---
    logger.info("应用正在关闭...")

    if hasattr(app.state, "cleanup_task"):
        app.state.cleanup_task.cancel()
        try:
            await app.state.cleanup_task
        except asyncio.CancelledError:
            pass

    # 关闭缓存后端
    await close_cache_backend()

    await close_db_engine(app)
    if hasattr(app.state, "scraper_manager"):
        await app.state.scraper_manager.close_all()
    # 关闭 TransportManager
    if hasattr(app.state, "transport_manager"):
        try:
            await app.state.transport_manager.close_all()
        except Exception as e:
            logger.exception(f"关闭 TransportManager 时发生错误: {e}")
    if hasattr(app.state, "task_manager"):
        await app.state.task_manager.stop()
    # 新增：在关闭时也关闭元数据管理器
    if hasattr(app.state, "metadata_manager"):
        await app.state.metadata_manager.close_all()
    if hasattr(app.state, "notification_manager"):
        await app.state.notification_manager.stop_channels()
    if hasattr(app.state, "tunnel_service"):
        await app.state.tunnel_service.stop()
    if hasattr(app.state, "media_server_manager"):
        await app.state.media_server_manager.close_all()
    if hasattr(app.state, "scheduler_manager"):
        await app.state.scheduler_manager.stop()
    if hasattr(app.state, "internal_polling"):
        await app.state.internal_polling.stop()

    logger.info("应用已完全关闭")

app = FastAPI(
    title="Misaka Danmaku External Control API",
    description="用于外部自动化和集成的API。所有端点都需要通过 `?api_key=` 进行鉴权。",
    version="1.0.0",
    lifespan=lifespan,
    # 禁用默认的 docs_url，我们将使用自定义的本地化版本
    docs_url=None,
    redoc_url=None         # 禁用ReDoc
)

# --- favicon.ico 路由 ---
@app.get("/favicon.ico", include_in_schema=False)
async def favicon():
    """提供网站图标"""
    return FileResponse("web/dist/images/favicon.ico", media_type="image/x-icon")

# --- 新增：自定义本地化的 Swagger UI 文档路由 ---
# 为外部控制API生成独立的 OpenAPI 文档，只包含 API Key 安全方案
def _control_api_openapi():
    """生成仅包含外部控制API路由和API Key认证的独立 OpenAPI schema"""
    from fastapi.openapi.utils import get_openapi
    if hasattr(app, "_control_openapi_schema") and app._control_openapi_schema:
        return app._control_openapi_schema

    # 只收集 /api/control 前缀的路由
    control_routes = [
        route for route in app.routes
        if hasattr(route, 'path') and route.path.startswith('/api/control')
        and getattr(route, 'include_in_schema', True)
    ]

    schema = get_openapi(
        title="Misaka Danmaku External Control API",
        version="1.0.0",
        description="用于外部自动化和集成的API。所有端点都需要通过 `?api_key=` 进行鉴权。",
        routes=control_routes,
    )

    # 替换安全方案：只保留 API Key，移除 OAuth2
    schema["components"] = schema.get("components", {})
    schema["components"]["securitySchemes"] = {
        "APIKeyQuery": {
            "type": "apiKey",
            "in": "query",
            "name": "api_key",
            "description": "通过 URL 查询参数传递 API Key"
        }
    }

    # 给所有路径添加 API Key 安全要求
    for path_item in schema.get("paths", {}).values():
        for operation in path_item.values():
            if isinstance(operation, dict):
                operation["security"] = [{"APIKeyQuery": []}]

    app._control_openapi_schema = schema
    return schema


@app.get("/api/control/openapi.json", include_in_schema=False)
async def control_api_openapi_json():
    """外部控制API的独立 OpenAPI JSON"""
    return JSONResponse(content=_control_api_openapi())


@app.get("/api/control/docs", include_in_schema=False)
async def custom_swagger_ui_html():
    """提供一个使用本地静态资源、部分汉化的 Swagger UI 页面。"""
    from src.utils.swagger_cn import get_swagger_ui_html_cn
    return get_swagger_ui_html_cn(
        openapi_url="/api/control/openapi.json",
        title="Misaka Danmaku 外部控制 API 文档",
    )

# 新增：配置CORS，允许前端开发服务器访问API
app.add_middleware(
    CORSMiddleware,
    # 允许所有来源。对于生产环境，建议替换为您的前端域名列表。
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




@app.middleware("http")
async def _log_not_found(request: Request, call_next):
    return await log_not_found_requests(request, call_next)


@app.middleware("http")
async def _capture_api_response(request: Request, call_next):
    return await capture_api_response(request, call_next)


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





# 新增：显式地挂载外部控制API路由，以确保其优先级
app.include_router(control_router, prefix="/api/control", tags=["External Control API"])

app.include_router(dandan_router, prefix="/api/v1", tags=["DanDanPlay Compatible"], include_in_schema=False)

# 包含所有非 dandanplay 的 API 路由
app.include_router(api_router, prefix="/api")

# --- MCP Server 初始化 ---
# 必须在所有路由注册完毕后调用，这样 fastapi-mcp 才能扫描到所有外部控制 API
setup_mcp(app)

# --- 新增：挂载 Swagger UI 的静态文件目录 ---
def _is_docker_environment():
    """检测是否在Docker容器中运行"""
    import os
    # 方法1: 检查 /.dockerenv 文件（Docker标准做法）
    if Path("/.dockerenv").exists():
        return True
    # 方法2: 检查环境变量
    if os.getenv("DOCKER_CONTAINER") == "true" or os.getenv("IN_DOCKER") == "true":
        return True
    # 方法3: 检查当前工作目录是否为 /app
    if Path.cwd() == Path("/app"):
        return True
    return False

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
