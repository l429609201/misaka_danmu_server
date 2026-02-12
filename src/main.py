import time
import uvicorn
import asyncio
import secrets
import httpx
import logging
import json
from contextlib import asynccontextmanager
from pathlib import Path
from fastapi import FastAPI, Request, Depends, status
from fastapi.openapi.docs import get_swagger_ui_html
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse, JSONResponse, Response # noqa: F401
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text

# 内部模块导入 - 使用聚合式导入
from src.core import settings
from src.core.default_configs import get_default_configs
from src.db import crud, orm_models, init_db_tables, close_db_engine, create_initial_admin_user, get_db_type, DatabaseStartupError
from src.db import ConfigManager, CacheManager  # 管理器从 db 层导入
from src.services import (
    TaskManager, MetadataSourceManager, ScraperManager, WebhookManager,
    SchedulerManager, TitleRecognitionManager, MediaServerManager,
    TransportManager, setup_logging
)
from src.utils import InternalPollingManager, init_proxy_middleware
from src.api import api_router, control_router
from src.api.dandan import dandan_router
from src.ai import AIMatcherManager
from src.ai.ai_prompts import DEFAULT_AI_MATCH_PROMPT, DEFAULT_AI_RECOGNITION_PROMPT, DEFAULT_AI_ALIAS_VALIDATION_PROMPT, DEFAULT_AI_ALIAS_EXPANSION_PROMPT, DEFAULT_AI_SEASON_MAPPING_PROMPT
from src.rate_limiter import RateLimiter
from src._version import APP_VERSION
from src import security
    
print(f"当前环境: {settings.environment}")

logger = logging.getLogger(__name__)

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

    # 新增：在启动时清理任何未完成的任务
    async with session_factory() as session:
        interrupted_count = await crud.mark_interrupted_tasks_as_failed(session)
        if interrupted_count > 0:
            logging.getLogger(__name__).info(f"已将 {interrupted_count} 个中断的任务标记为失败。")


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

    # 初始化 CacheManager
    app.state.cache_manager = CacheManager(session_factory)
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

    async with session_factory() as session:
        existing_task = await session.get(orm_models.ScheduledTask, "system_token_reset")
        if not existing_task:
            await crud.create_scheduled_task(
                session,
                task_id="system_token_reset",
                name="系统内置：Token每日重置",
                job_type="tokenReset",
                cron="0 0 * * *",
                is_enabled=True
            )

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

    total_time = time.time() - startup_start
    logger.info(f"应用启动完成，总耗时 {total_time:.2f} 秒")

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
@app.get("/api/control/docs", include_in_schema=False)
async def custom_swagger_ui_html():
    """提供一个使用本地静态资源的 Swagger UI 页面。"""
    return get_swagger_ui_html(
        openapi_url=app.openapi_url,
        title=app.title + " - API Docs",
        swagger_js_url="/static/swagger-ui/swagger-ui-bundle.js",
        swagger_css_url="/static/swagger-ui/swagger-ui.css",
        swagger_favicon_url="/static/swagger-ui/favicon-32x32.png"
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
async def log_not_found_requests(request: Request, call_next):
    """
    中间件：捕获所有请求。
    - 如果是未找到的API路径 (404)，则返回 403 Forbidden，避免路径枚举。
    - 对其他 404 错误，记录详细信息以供调试。
    """
    response = await call_next(request)
    if response.status_code == 404:
        # 如果是 API 路径未找到，返回 403，同时记录原始响应内容
        if request.url.path.startswith("/api/"):
            original_body_text = None
            try:
                body_bytes = getattr(response, "body", b"")
                if isinstance(body_bytes, (bytes, bytearray)) and body_bytes:
                    try:
                        original_json = json.loads(body_bytes)
                        original_body_text = json.dumps(original_json, ensure_ascii=False)
                    except Exception:
                        original_body_text = body_bytes.decode("utf-8", "ignore")
            except Exception as e:
                logger.debug(f"读取原始404响应body失败: {e}")

            if original_body_text:
                logger.warning("API路径未找到原始响应内容: %s", original_body_text)

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


@app.middleware("http")
async def capture_control_api_response(request: Request, call_next):
    """
    中间件：为外部控制API捕获响应头和响应体，更新到访问日志中。
    仅对 /api/control/ 路径生效。
    """
    if not request.url.path.startswith("/api/control/"):
        return await call_next(request)

    response = await call_next(request)

    # 检查是否有日志ID需要更新
    log_id = getattr(request.state, 'external_log_id', None)
    if log_id is None:
        return response

    try:
        # 读取响应体（需要消费流并重建响应）
        from starlette.responses import Response as StarletteResponse
        response_body_bytes = b""
        async for chunk in response.body_iterator:
            response_body_bytes += chunk

        response_headers_str = json.dumps(dict(response.headers), ensure_ascii=False, indent=2)
        response_body_str = response_body_bytes.decode(errors='ignore') if response_body_bytes else None

        # 截断过长的响应体（避免数据库存储过大）
        max_body_len = 10000
        if response_body_str and len(response_body_str) > max_body_len:
            response_body_str = response_body_str[:max_body_len] + f"\n... (已截断，总长度: {len(response_body_bytes)} 字节)"

        # 使用独立的数据库会话更新日志
        session_factory = request.app.state.db_session_factory
        async with session_factory() as session:
            await crud.update_external_api_log_response(
                session,
                log_id=log_id,
                status_code=response.status_code,
                response_headers=response_headers_str,
                response_body=response_body_str,
            )

        # 重建响应
        return StarletteResponse(
            content=response_body_bytes,
            status_code=response.status_code,
            headers=dict(response.headers),
            media_type=response.media_type,
        )
    except Exception as e:
        logger.debug(f"捕获控制API响应信息失败: {e}")
        # 如果出错，尝试返回原始响应体
        try:
            return StarletteResponse(
                content=response_body_bytes,
                status_code=response.status_code,
                headers=dict(response.headers),
                media_type=response.media_type,
            )
        except Exception:
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





# 新增：显式地挂载外部控制API路由，以确保其优先级
app.include_router(control_router, prefix="/api/control", tags=["External Control API"])

app.include_router(dandan_router, prefix="/api/v1", tags=["DanDanPlay Compatible"], include_in_schema=False)

# 包含所有非 dandanplay 的 API 路由
app.include_router(api_router, prefix="/api")

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
    uvicorn.run(
        "src.main:app",
        host=settings.server.host,
        port=settings.server.port,
        reload=settings.environment == "development"  # 开发环境启用自动重载
    )
