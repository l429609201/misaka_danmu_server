"""
应用生命周期（启动/关闭）逻辑

why：从 main.py 抽离约 230 行的 lifespan 启动/关闭流程，main.py 只保留一个薄壳 lifespan
调用 run_startup / run_shutdown。此处为 1:1 忠实迁移，初始化顺序与依赖注入保持完全一致。
"""

import os
import time
import asyncio
import secrets
import logging
from pathlib import Path

from fastapi import FastAPI
from sqlalchemy import text

from src.core import settings
from src.core.default_configs import get_default_configs
from src.core.cache import init_cache_backend, close_cache_backend
from src.core.env import is_docker_environment
from src.db import crud, orm_models, init_db_tables, close_db_engine, create_initial_admin_user, get_db_type, DatabaseStartupError
from src.db import ConfigManager, CacheManager
from src.services import (
    TaskManager, MetadataSourceManager, ScraperManager, WebhookManager,
    SchedulerManager, TitleRecognitionManager, MediaServerManager,
    TransportManager,
    NotificationService, NotificationManager,
    TunnelService, apply_tunnel_from_notification_manager,
    init_bangumi_data_manager,
)
from src.utils import InternalPollingManager, init_proxy_middleware
from src.utils.server_instance_id import generate_server_instance_id
from src.rate_limiter import RateLimiter
from src.ai import AIMatcherManager
from src.ai.ai_prompts import (
    DEFAULT_AI_MATCH_PROMPT, DEFAULT_AI_RECOGNITION_PROMPT,
    DEFAULT_AI_ALIAS_VALIDATION_PROMPT, DEFAULT_AI_ALIAS_EXPANSION_PROMPT,
    DEFAULT_AI_SEASON_MAPPING_PROMPT,
)
from src._version import APP_VERSION
from src.frontend import mount_frontend

logger = logging.getLogger(__name__)


def _ensure_required_directories():
    """确保应用运行所需的目录存在"""
    if is_docker_environment():
        required_dirs = [Path("/app/config/image")]
    else:
        required_dirs = [Path("config/image")]

    for dir_path in required_dirs:
        try:
            dir_path.mkdir(parents=True, exist_ok=True)
            logger.info(f"确保目录存在: {dir_path}")
        except (OSError, PermissionError) as e:
            logger.warning(f"无法创建目录 {dir_path}: {e}")


async def _apply_tunnel_from_channels(app: FastAPI):
    await apply_tunnel_from_notification_manager(
        tunnel_service=app.state.tunnel_service,
        notification_manager=app.state.notification_manager,
        config_manager=app.state.config_manager,
        local_port=settings.server.port,
    )


async def cleanup_task(app: FastAPI):
    """定期清理过期缓存和OAuth states的后台任务。"""
    session_factory = app.state.db_session_factory
    while True:
        try:
            await asyncio.sleep(3600)  # 每小时清理一次
            async with session_factory() as session:
                await crud.clear_expired_cache(session)
                await crud.clear_expired_oauth_states(session)
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error(f"缓存清理任务出错: {e}")


async def run_startup(app: FastAPI):
    """应用启动逻辑（原 main.py lifespan yield 之前部分，1:1 迁移）。"""
    from src.services import setup_logging

    setup_logging()
    logger.info(f"Misaka Danmaku API 版本 {APP_VERSION} 正在启动...")

    # 创建必要的目录
    _ensure_required_directories()

    # init_db_tables 处理数据库创建、引擎和会话工厂的创建
    try:
        await init_db_tables(app)
    except DatabaseStartupError:
        os._exit(1)
    session_factory = app.state.db_session_factory

    # PostgreSQL 序列自动修复(防止主键冲突)
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
    default_configs['jwtSecretKey'] = (secrets.token_hex(32), '用于签名JWT令牌的密钥，在首次启动时自动生成。')
    default_configs['serverInstanceId'] = (generate_server_instance_id(), '实例ID')
    await app.state.config_manager.register_defaults(default_configs)

    # 初始化 TransportManager
    app.state.transport_manager = TransportManager()

    # 初始化缓存后端（Memory/Redis/Database/Hybrid，Redis 不可用时自动降级）
    cache_backend = await init_cache_backend(
        session_factory=session_factory,
        cache_config=settings.cache,
    )
    app.state.cache_manager = CacheManager(session_factory, backend=cache_backend)
    logger.info("缓存管理器已初始化")

    # 初始化 ProxyMiddleware
    app.state.proxy_middleware = init_proxy_middleware(app.state.config_manager)
    logger.info("代理中间件已初始化")

    # 初始化 AIMatcherManager
    app.state.ai_matcher_manager = AIMatcherManager(app.state.config_manager, session_factory)
    logger.info("AI匹配管理器已初始化")

    startup_start = time.time()

    # 创建管理器实例
    app.state.metadata_manager = MetadataSourceManager(session_factory, app.state.config_manager, None, app.state.cache_manager)
    app.state.scraper_manager = ScraperManager(session_factory, app.state.config_manager, app.state.metadata_manager, app.state.transport_manager)
    app.state.metadata_manager.scraper_manager = app.state.scraper_manager

    # 【并行优化】同时初始化两个管理器
    logger.info("开始并行初始化...")
    init_start = time.time()
    await asyncio.gather(
        app.state.scraper_manager.initialize(),
        app.state.metadata_manager.initialize()
    )

    # 【优化】预加载配置到缓存
    logger.info("预加载配置缓存...")
    async with session_factory() as session:
        proxy_mode = await crud.get_config_value(session, "proxyMode", "none")
        proxy_url = await crud.get_config_value(session, "proxyUrl", "")
        proxy_enabled = await crud.get_config_value(session, "proxyEnabled", "false")
        accelerate_proxy_url = await crud.get_config_value(session, "accelerateProxyUrl", "")
        app.state.config_manager._cache["proxyMode"] = proxy_mode
        app.state.config_manager._cache["proxyUrl"] = proxy_url
        app.state.config_manager._cache["proxyEnabled"] = proxy_enabled
        app.state.config_manager._cache["accelerateProxyUrl"] = accelerate_proxy_url
        scraper_settings = await crud.get_all_scraper_settings(session)
        app.state.scraper_manager._cached_scraper_settings = {
            s['providerName']: s for s in scraper_settings
        }

    # 初始化关键组件
    app.state.rate_limiter = RateLimiter(session_factory, app.state.scraper_manager)
    app.include_router(app.state.metadata_manager.router, prefix="/api/metadata")

    # Bangumi 专属路由
    if 'bangumi' in app.state.metadata_manager.sources:
        bangumi_router = app.state.metadata_manager.sources['bangumi'].api_router
        app.include_router(bangumi_router, prefix="/api/bangumi", tags=["Bangumi"])

    app.state.task_manager = TaskManager(session_factory, app.state.config_manager)
    app.state.title_recognition_manager = TitleRecognitionManager(session_factory)
    app.state.media_server_manager = MediaServerManager(session_factory)
    await app.state.media_server_manager.initialize()

    # bangumi-data 离线数据层管理器
    app.state.bangumi_data_manager = init_bangumi_data_manager(session_factory, app.state.config_manager)

    async def _load_bangumi_local_data():
        try:
            await app.state.bangumi_data_manager.sync_from_local()
        except Exception as e:
            logger.warning(f"bangumi-data 本地离线数据加载失败（不影响启动）: {e}")
    asyncio.create_task(_load_bangumi_local_data())

    app.state.webhook_manager = WebhookManager(
        session_factory, app.state.task_manager, app.state.scraper_manager,
        app.state.rate_limiter, app.state.metadata_manager,
        app.state.config_manager, app.state.title_recognition_manager,
        app.state.ai_matcher_manager
    )

    init_time = time.time() - init_start
    logger.info(f"并行初始化完成，耗时 {init_time:.2f} 秒")

    await _run_startup_services(app, session_factory, startup_start)


async def _run_startup_services(app: FastAPI, session_factory, startup_start: float):
    """启动依赖 webhook/task 管理器之后的服务（1:1 迁移）。"""
    # 设置任务恢复所需的依赖
    app.state.task_manager.set_recovery_dependencies({
        "scraper_manager": app.state.scraper_manager,
        "rate_limiter": app.state.rate_limiter,
        "metadata_manager": app.state.metadata_manager,
        "ai_matcher_manager": app.state.ai_matcher_manager,
        "title_recognition_manager": app.state.title_recognition_manager,
    })

    # 启动服务
    app.state.task_manager.start()
    await create_initial_admin_user(app)

    # 一次性清理：删除旧的 system_token_reset 定时任务
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

    # 内置轮询任务管理器
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

    # 初始化 TunnelService
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

    # 前端服务：在所有 API 路由注册完毕后挂载，确保 API 路由优先匹配
    mount_frontend(app, settings)


async def run_shutdown(app: FastAPI):
    """应用关闭逻辑（原 main.py lifespan yield 之后部分，1:1 迁移）。"""
    logger.info("应用正在关闭...")

    if hasattr(app.state, "cleanup_task"):
        app.state.cleanup_task.cancel()
        try:
            await app.state.cleanup_task
        except asyncio.CancelledError:
            pass

    await close_cache_backend()
    await close_db_engine(app)
    if hasattr(app.state, "scraper_manager"):
        await app.state.scraper_manager.close_all()
    if hasattr(app.state, "transport_manager"):
        try:
            await app.state.transport_manager.close_all()
        except Exception as e:
            logger.exception(f"关闭 TransportManager 时发生错误: {e}")
    if hasattr(app.state, "task_manager"):
        await app.state.task_manager.stop()
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
