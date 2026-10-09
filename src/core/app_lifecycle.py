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
from functools import partial
from pathlib import Path

from fastapi import FastAPI

from src.core import settings
from src.core.default_configs import get_default_configs
# C4 完成：缓存层统一为 CacheService，init_cache_backend 和 CacheManager 已废弃
from src.services.cache_service import init_cache_service, close_cache_service
# C5/C6：配置层统一为 ConfigService，旧配置管理器命名已完全移除
from src.services.config_service import init_config_service
# C7：数据访问层统一为 DatabaseService（Repository 模式）
from src.services.service_container import (
    init_database_service, close_database_service,
    init_scraper_manager, init_task_manager, init_scheduler_manager,
    init_webhook_service, init_media_server_service,
    init_metadata_service, init_rate_limiter,
    init_title_recognition_manager, init_title_recognition_service,
)
from src.webhook.emby import EmbyWebhook
from src.webhook.jellyfin import JellyfinWebhook
from src.webhook.plex import PlexWebhook
from src.metadata_sources.so360 import So360MetadataSource
from src.metadata_sources.anibt import AniBTMetadataSource
from src.metadata_sources.bangumi import BangumiMetadataSource
from src.metadata_sources.douban import DoubanMetadataSource
from src.metadata_sources.imdb import ImdbMetadataSource
from src.metadata_sources.tmdb import TmdbMetadataSource
from src.metadata_sources.trakt import TraktMetadataSource
from src.metadata_sources.tvdb import TvdbMetadataSource
from src.core.env import is_docker_environment
from src.db import init_db_tables, close_db_engine, get_db_type, DatabaseStartupError
from src.services.initial_admin_service import create_initial_admin_user
from src.utils.auth.passwords import get_password_hash
from src.services.service_container import get_database_service
from src.services.webhook_service import WebhookService
from src.services.task_manager import TaskManager
from src.services.metadata_service import MetadataService
from src.services.scraper_manager import ScraperManager
from src.services.scheduler import SchedulerManager
from src.services.title_recognition import TitleRecognitionService
from src.workflows.title_recognition import TitleRecognitionWorkflow
from src.services.media_server_service import MediaServerService
from src.utils.runtime.transport_manager import TransportManager
from src.services.tunnel_service import TunnelService
from src.workflows.notification_tunnel import apply_tunnel_from_notification_manager
from src.services.ai_service import init_ai_service
from src.services.notification_manager import NotificationManager
from src.services.notification_service import NotificationService
from src.notification.input_adapter import NotificationInputAdapter
from src.workflows.notification import NotificationWorkflow
from src.workflows.task_notification import TaskNotificationWorkflow
from src.workflows.tasks.recovery import TaskRecoveryResolver
from src.services.bangumi_data_service import BangumiDataService
from src.workflows.bangumi_data_sync import load_local_bangumi_data
from src.workflows.bangumi_data_platforms import resolve_sources_by_title
from src.api.ui.metadata_source import build_metadata_source_router
from src.notification.events import EventContext, NotificationEvent, SystemEventType
from src.utils.runtime.internal_polling import InternalPollingManager
from src.core.proxy import init_proxy_middleware
from src.utils.runtime.server_instance_id import generate_server_instance_id
from src.rate_limiter import RateLimiter
from src.ai.ai_prompts import (
    DEFAULT_AI_MATCH_PROMPT, DEFAULT_AI_RECOGNITION_PROMPT,
    DEFAULT_AI_ALIAS_VALIDATION_PROMPT, DEFAULT_AI_ALIAS_EXPANSION_PROMPT,
    DEFAULT_AI_SEASON_MAPPING_PROMPT,
)
from src._version import APP_VERSION
from src.frontend import mount_frontend
from src.tasks.registry import register_import_task_handlers
from src.workflows.bangumi_data_tasks import execute_bangumi_data_sync, execute_bangumi_data_clear
from src.workflows.scraper_resources.load_preparation import ScraperLoadPreparation
# 定时作业只在组装层显式接线，SchedulerService 不导入具体 Jobs。
from src.jobs.base import BaseJob
from src.jobs.auto_finish import AutoFinishJob
from src.jobs.bangumi_data_sync import BangumiDataSyncJob
from src.jobs.danmaku_cleanup import DanmakuCleanupJob
from src.jobs.database_backup import DatabaseBackupJob
from src.jobs.database_maintenance import DatabaseMaintenanceJob
from src.jobs.fill_missing_episodes import FillMissingEpisodesJob
from src.jobs.incremental_refresh import IncrementalRefreshJob
from src.jobs.refresh_latest_episode import RefreshLatestEpisodeJob
from src.jobs.schedule_sync import ScheduleSyncJob
from src.jobs.subscription_scan import SubscriptionScanJob, scan_and_import_target_task
from src.jobs.tmdb_auto_map import TmdbAutoMapJob
from src.jobs.watchlist_sync import WatchlistSyncJob
from src.jobs.webhook_processor import WebhookProcessorJob

from src.ai.assistant.skill_manager import set_skills_base_dir, get_skill_manager
from src.ai.assistant.builtin_skills import cleanup_legacy_builtin_files
from src.core.logging_setup import setup_logging
from src.services.performance_collector import PerformanceCollector
from src.internal_tasks.performance_collection import PerformanceCollectionTask
from src.ai.assistant.tools.db_tools import init_llm_db_tools
from src.ai.assistant.api_gateway import validate_whitelist

logger = logging.getLogger(__name__)

# 具体 Job 类型只在组装层列出，Service 层不解析文件名或动态加载模块。
METADATA_SOURCE_CLASSES = (
    So360MetadataSource, AniBTMetadataSource, BangumiMetadataSource,
    DoubanMetadataSource, ImdbMetadataSource, TmdbMetadataSource,
    TraktMetadataSource, TvdbMetadataSource,
)

SCHEDULED_JOB_CLASSES = (
    AutoFinishJob, BangumiDataSyncJob, DanmakuCleanupJob,
    DatabaseBackupJob, DatabaseMaintenanceJob, FillMissingEpisodesJob,
    IncrementalRefreshJob, RefreshLatestEpisodeJob, ScheduleSyncJob,
    SubscriptionScanJob, TmdbAutoMapJob, WatchlistSyncJob,
    WebhookProcessorJob,
)


def _ensure_required_directories():
    """确保应用运行所需的目录存在"""
    if is_docker_environment():
        required_dirs = [Path("/app/config/image"), Path("/app/config/skills")]
    else:
        required_dirs = [Path("config/image"), Path("config/skills")]

    for dir_path in required_dirs:
        try:
            dir_path.mkdir(parents=True, exist_ok=True)
            logger.info(f"确保目录存在: {dir_path}")
        except (OSError, PermissionError) as e:
            logger.warning(f"无法创建目录 {dir_path}: {e}")


def _init_skills():
    """初始化技能系统：设置用户技能目录 → 清理旧版残留 → 加载全部技能。

    内置技能随代码发布、常驻内存，不落盘；用户自建技能仍存 config/skills/。
    """
    try:

        base_dir = Path("/app") if is_docker_environment() else Path(".")
        set_skills_base_dir(base_dir)
        # 清理早期版本落盘的内置技能目录，避免与内存内置技能同名冲突
        cleanup_legacy_builtin_files()
        get_skill_manager().load_all()
    except Exception as e:  # noqa: BLE001
        # 技能系统属增强能力，失败不应阻塞应用启动
        logger.warning(f"技能系统初始化失败（不影响主流程）: {e}", exc_info=True)


async def _apply_tunnel_from_channels(app: FastAPI):
    await apply_tunnel_from_notification_manager(
        tunnel_service=app.state.tunnel_service,
        notification_manager=app.state.notification_manager,
        config_service=app.state.config_service,
        local_port=settings.server.port,
    )


async def cleanup_task(app: FastAPI):
    """定期清理过期缓存和 OAuth 状态。"""
    while True:
        try:
            await asyncio.sleep(3600)  # 每小时清理一次
            db = get_database_service()
            async with db.transaction():
                # Repository 使用事务上下文中的会话，不再传入已不存在的旧式 session 参数。
                await db.cache.delete_expired()
                await db.oauth.clear_expired_states()
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error(f"缓存清理任务出错: {e}")


async def run_startup(app: FastAPI):
    """应用启动逻辑（原 main.py lifespan yield 之前部分，1:1 迁移）。"""

    setup_logging()
    logger.info(f"Misaka Danmaku API 版本 {APP_VERSION} 正在启动...")

    # 创建必要的目录
    _ensure_required_directories()

    # 初始化技能目录并加载用户自制技能（config/skills/*/SKILL.md）
    _init_skills()

    # init_db_tables 处理数据库创建、引擎和会话工厂的创建
    try:
        await init_db_tables(app)
    except DatabaseStartupError:
        os._exit(1)
    session_factory = app.state.db_session_factory

    # 组合根先初始化统一数据库入口，启动业务 SQL 也经服务事务。
    database_service = init_database_service(session_factory)
    if get_db_type() == "postgresql":
        try:
            async with database_service.transaction():
                await database_service.anime.sync_postgres_sequence()
            logger.info("已自动同步PostgreSQL的anime_id_seq序列")
        except Exception as e:
            logger.warning(f"同步PostgreSQL序列时出错(可忽略): {e}")

    # C5：初始化 ConfigService（新架构，单例模式）
    config_service = init_config_service(session_factory, database_service=database_service)
    app.state.config_service = config_service

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

    # 使用 ConfigService 注册默认配置
    await config_service.register_defaults(default_configs)
    logger.info("配置服务层（ConfigService）已初始化")

    # 初始化 TransportManager
    app.state.transport_manager = TransportManager()

    # C4 完成：缓存层统一为 CacheService，旧的 cache_backend/CacheManager 已全部废弃
    await init_cache_service(
        session_factory=session_factory,
        cache_config=settings.cache,
    )
    logger.info("缓存服务层（CacheService）已初始化")

    # 初始化 ProxyMiddleware
    app.state.proxy_middleware = init_proxy_middleware(app.state.config_service)
    logger.info("代理中间件已初始化")

    # AIService 是唯一实例入口，后续业务依赖直接引用该服务。
    app.state.ai_service = init_ai_service(app.state.config_service, session_factory)

    startup_start = time.time()

    # 搜索源仍按原有插件机制加载；元数据适配器由组装层显式注册。
    offline_index = BangumiDataService(app.state.config_service)
    app.state.metadata_service = MetadataService(
        session_factory, app.state.config_service, None,
        source_classes=METADATA_SOURCE_CLASSES,
        offline_bangumi_service=offline_index,
        resolve_offline_sources=partial(resolve_sources_by_title, offline_index),
    )
    # 资源复合流程由组合根接线，管理器只消费准备完成的 manifest 快照。
    scraper_load_preparation = ScraperLoadPreparation()
    app.state.scraper_manager = ScraperManager(
        session_factory, app.state.config_service, app.state.metadata_service,
        app.state.transport_manager, prepare_load=scraper_load_preparation.prepare,
    )
    app.state.metadata_service.scraper_manager = app.state.scraper_manager
    app.state.scraper_manager.offline_bangumi_service = offline_index

    init_metadata_service(app.state.metadata_service)
    init_scraper_manager(app.state.scraper_manager)

    # 串行初始化（避免 DatabaseService 并发冲突）
    logger.info("开始初始化搜索源管理器与 MetadataService...")
    init_start = time.time()
    await app.state.scraper_manager.initialize()
    await app.state.metadata_service.initialize()

    # 【优化】预加载配置到缓存
    logger.info("预加载配置缓存...")
    db = get_database_service()
    async with db.transaction() as session:
        proxy_mode = await db.config.get_value("proxyMode", "none")
        proxy_url = await db.config.get_value("proxyUrl", "")
        proxy_enabled = await db.config.get_value("proxyEnabled", "false")
        accelerate_proxy_url = await db.config.get_value("accelerateProxyUrl", "")
        app.state.config_service._cache["proxyMode"] = proxy_mode
        app.state.config_service._cache["proxyUrl"] = proxy_url
        app.state.config_service._cache["proxyEnabled"] = proxy_enabled
        app.state.config_service._cache["accelerateProxyUrl"] = accelerate_proxy_url
        scraper_settings = await db.scraper.get_all_scraper_settings()
        app.state.scraper_manager._cached_scraper_settings = {
            s['providerName']: s for s in scraper_settings
        }

    # 初始化关键组件
    app.state.rate_limiter = RateLimiter(database_service, app.state.scraper_manager)
    init_rate_limiter(app.state.rate_limiter)  # C7：注册到服务容器
    app.include_router(build_metadata_source_router(app.state.metadata_service), prefix="/api/metadata")

    # Bangumi 专属路由
    if 'bangumi' in app.state.metadata_service.sources:
        bangumi_router = app.state.metadata_service.sources['bangumi'].api_router
        app.include_router(bangumi_router, prefix="/api/bangumi", tags=["Bangumi"])

    app.state.task_manager = TaskManager(
        session_factory,
        app.state.config_service,
        max_concurrent_tasks=settings.task_manager.max_concurrent_tasks,
        max_search_workers=settings.task_manager.max_search_workers
    )
    init_task_manager(app.state.task_manager)  # C7：注册到服务容器
    register_import_task_handlers(app.state.task_manager)
    app.state.task_manager.register_task_handler('scan_and_import_target', scan_and_import_target_task)
    app.state.task_manager.register_task_handler('bangumiDataSync', execute_bangumi_data_sync)
    app.state.task_manager.register_task_handler('bangumiDataClear', execute_bangumi_data_clear)

    app.state.title_recognition_service = TitleRecognitionService(database_service)
    app.state.title_recognition_manager = TitleRecognitionWorkflow(app.state.title_recognition_service)
    init_title_recognition_service(app.state.title_recognition_service)
    init_title_recognition_manager(app.state.title_recognition_manager)  # C7：注册到服务容器
    app.state.media_server_service = MediaServerService(session_factory, database_service=database_service)
    init_media_server_service(app.state.media_server_service)
    await app.state.media_server_service.initialize()

    async def _load_bangumi_local_data():
        try:
            await load_local_bangumi_data(offline_index)
        except Exception as e:
            logger.warning(f"bangumi-data 本地离线数据加载失败（不影响启动）: {e}")
    asyncio.create_task(_load_bangumi_local_data())

    app.state.webhook_service = WebhookService({
        "emby": EmbyWebhook,
        "jellyfin": JellyfinWebhook,
        "plex": PlexWebhook,
    })
    init_webhook_service(app.state.webhook_service)

    # 初始化性能监测采集器（C3.3：已删除 cache_manager 参数）
    app.state.performance_collector = PerformanceCollector(
        db_engine=app.state.db_engine, task_manager=app.state.task_manager,
    )
    app.state.performance_collection_task = PerformanceCollectionTask(
        app.state.performance_collector, database_service=database_service,
    )
    await app.state.performance_collection_task.start()
    logger.info("性能监测采集器已启动")

    # 初始化 LLM 数据库检索工具
    init_llm_db_tools()
    # 日志已在 init_llm_db_tools() 内部输出，此处不重复

    # 御坂助手 API 网关白名单自检：
    # 校验助手白名单登记的路径是否真实存在于路由表。后端调路径而白名单未同步时，
    # 在启动阶段就以 ERROR 暴露，避免等到用户对话时才发现助手调用 404。
    # 自检仅做核对，不阻断启动。
    try:
        gateway_check = validate_whitelist(app)
        if gateway_check["ok"]:
            logger.info(
                "御坂助手 API 网关白名单自检通过，共 %d 个操作",
                gateway_check["checked"],
            )
        else:
            logger.error(
                "御坂助手 API 网关白名单自检发现 %d 处路径失效，详见上方日志",
                len(gateway_check["missing"]),
            )
    except Exception as e:  # noqa: BLE001
        logger.warning(f"御坂助手 API 网关白名单自检未执行: {e}")

    init_time = time.time() - init_start
    logger.info(f"并行初始化完成，耗时 {init_time:.2f} 秒")

    await _run_startup_services(app, session_factory, startup_start)


async def _run_startup_services(app: FastAPI, session_factory, startup_start: float):
    """启动依赖 webhook/task 管理器之后的服务（1:1 迁移）。"""
    # 设置任务恢复所需的依赖
    execution_dependencies = {
        "scraper_manager": app.state.scraper_manager,
        "rate_limiter": app.state.rate_limiter,
        "metadata_manager": app.state.metadata_service,
        "ai_service": app.state.ai_service,
        "title_recognition_manager": app.state.title_recognition_manager,
    }
    app.state.task_manager.set_execution_dependencies(execution_dependencies)
    recovery = TaskRecoveryResolver(app.state.task_manager, execution_dependencies)
    app.state.task_manager.set_recovery_callback(recovery.rebuild, recovery.resolve_queue)

    # 启动服务（使用异步方法，确保任务恢复完成后再启动 worker）
    await app.state.task_manager.start_async()
    await create_initial_admin_user(
        database_service=get_database_service(),
        username=settings.admin.initial_user,
        password=settings.admin.initial_password,
        hash_password=get_password_hash,
    )

    # 一次性清理：删除旧的 system_token_reset 定时任务
    db = get_database_service()
    async with db.transaction():
        removed = await db.scheduled_task.remove_obsolete_token_reset()
    if removed:
        logger.info("已清理旧的 system_token_reset 定时任务（已迁移到内部轮询任务）")

    app.state.cleanup_task = asyncio.create_task(cleanup_task(app))
    app.state.scheduler_manager = SchedulerManager(
        session_factory, app.state.task_manager, app.state.scraper_manager,
        app.state.rate_limiter, app.state.metadata_service,
        app.state.config_service, app.state.ai_service,
        app.state.title_recognition_manager,
        job_base_class=BaseJob,
        job_classes=SCHEDULED_JOB_CLASSES,
        database_service=db,
    )
    init_scheduler_manager(app.state.scheduler_manager)  # C7：注册到服务容器
    await app.state.scheduler_manager.start()

    # 内置轮询任务管理器
    app.state.internal_polling = InternalPollingManager(app)
    await app.state.internal_polling.start()

    # 初始化通知服务
    app.state.notification_state = NotificationService()
    app.state.notification_service = NotificationInputAdapter(app.state.notification_state, session_factory)
    app.state.notification_service.set_dependencies(
        scraper_manager=app.state.scraper_manager,
        metadata_manager=app.state.metadata_service,
        task_manager=app.state.task_manager,
        scheduler_manager=app.state.scheduler_manager,
        config_service=app.state.config_service,
        rate_limiter=app.state.rate_limiter,
        title_recognition_manager=app.state.title_recognition_manager,
        ai_service=app.state.ai_service,
        app=app,
    )
    app.state.notification_manager = NotificationManager(session_factory, app.state.notification_service)
    await app.state.notification_manager.initialize()
    app.state.notification_service.notification_manager = app.state.notification_manager
    app.state.notification_workflow = NotificationWorkflow(
        app.state.notification_manager, app.state.notification_state, db,
    )
    await app.state.notification_workflow.start()
    await app.state.notification_manager.start_channels()

    # 初始化通知模板（订阅配置重置已迁移到 migrations.py 统一管理）
    logger.info("初始化通知模板...")
    # ✅ 改为通过 DatabaseService 访问 Repository
    try:
        db = get_database_service()
        async with db.transaction():
            await db.notification_template.ensure_defaults()
        logger.info("通知模板初始化完成")
    except Exception as e:
        logger.error(f"通知模板初始化失败: {e}", exc_info=True)

    # 初始化 TunnelService
    app.state.tunnel_service = TunnelService()
    await _apply_tunnel_from_channels(app)
    logger.info("隧道服务已初始化")

    # TaskManager 保留通知订阅；Webhook 接收通知由 Workflow 执行。
    task_notifications = TaskNotificationWorkflow(db, app.state.notification_workflow)
    app.state.task_manager.set_task_event_callbacks(
        task_notifications.emit_task_event, task_notifications.emit_progress,
    )

    total_time = time.time() - startup_start
    logger.info(f"应用启动完成，总耗时 {total_time:.2f} 秒")

    # 发射系统启动通知（使用 V2 事件入口）
    try:
        await app.state.notification_workflow.notify_event_v2(
            EventContext(
                event_type=NotificationEvent.SYSTEM_EVENT,
                system_type=SystemEventType.STARTUP,
            )
        )
    except Exception as e:
        logger.error(f"发射 system_start 事件失败: {e}", exc_info=True)

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

    # 关闭性能监测采集器
    if hasattr(app.state, "performance_collection_task"):
        await app.state.performance_collection_task.stop()
        logger.info("性能监测采集器已关闭")

    # 先停止产生业务任务的调度与轮询，再关闭其依赖的基础设施。
    if hasattr(app.state, "scheduler_manager"):
        await app.state.scheduler_manager.stop()
    if hasattr(app.state, "internal_polling"):
        await app.state.internal_polling.stop()
    if hasattr(app.state, "task_manager"):
        await app.state.task_manager.stop()
    if hasattr(app.state, "notification_workflow"):
        await app.state.notification_workflow.stop()
    if hasattr(app.state, "notification_manager"):
        await app.state.notification_manager.stop_channels()
    if hasattr(app.state, "tunnel_service"):
        await app.state.tunnel_service.stop()
    if hasattr(app.state, "metadata_service"):
        await app.state.metadata_service.close_all()
    if hasattr(app.state, "media_server_service"):
        await app.state.media_server_service.close_all()
    if hasattr(app.state, "scraper_manager"):
        await app.state.scraper_manager.close_all()
    if hasattr(app.state, "transport_manager"):
        try:
            await app.state.transport_manager.close_all()
        except Exception:
            logger.exception("关闭 TransportManager 时发生错误")
    await close_cache_service()
    await close_database_service()
    await close_db_engine(app)

    logger.info("应用已完全关闭")
