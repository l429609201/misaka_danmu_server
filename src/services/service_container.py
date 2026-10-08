"""
服务容器：全局服务实例管理
统一管理所有服务的全局单例，类似 ConfigService 和 CacheService
"""

import logging
from typing import Optional
from sqlalchemy.ext.asyncio import async_sessionmaker, AsyncSession

from src.services.database_service import DatabaseService

logger = logging.getLogger(__name__)

# ========== 全局单例变量 ==========
_database_service: Optional[DatabaseService] = None
_scraper_manager = None
_task_manager = None
_scheduler_manager = None
_webhook_service = None
_media_server_service = None
_metadata_service = None
_rate_limiter = None

_title_recognition_manager = None
_file_storage_service = None


def init_database_service(session_factory: async_sessionmaker[AsyncSession]) -> DatabaseService:
    """
    初始化 DatabaseService（应用启动时调用）
    
    Args:
        session_factory: SQLAlchemy 的 async_sessionmaker 实例
        
    Returns:
        DatabaseService 实例
    """
    global _database_service
    
    if _database_service is not None:
        logger.warning("DatabaseService 已初始化，跳过重复初始化")
        return _database_service
    
    _database_service = DatabaseService(session_factory)
    logger.info("DatabaseService 已初始化（全局单例）")
    return _database_service


def get_database_service() -> DatabaseService:
    """
    获取 DatabaseService 全局单例
    
    用法：
    ```python
    # API 层
    from src.services.service_container import get_database_service
    
    @router.get("/anime/{animeId}")
    async def get_anime(animeId: int):
        db = get_database_service()
        async with db.transaction():
            anime = await db.anime.get_by_id(animeId)
            return anime
    ```
    
    Returns:
        DatabaseService 实例
        
    Raises:
        RuntimeError: 如果 DatabaseService 未初始化
    """
    if _database_service is None:
        raise RuntimeError(
            "DatabaseService 未初始化。"
            "请确保在应用启动时调用了 init_database_service()"
        )
    return _database_service


async def close_database_service():
    """
    关闭 DatabaseService（应用关闭时调用）

    目前 DatabaseService 只是提供 session_factory 的包装，无需特殊清理。
    预留此函数以便未来扩展（如连接池统计、资源清理等）。
    """
    global _database_service

    if _database_service is None:
        return

    logger.info("DatabaseService 已关闭")
    _database_service = None


# ========== 其他服务管理器的全局单例 ==========

def init_scraper_manager(instance):
    """初始化 ScraperManager"""
    global _scraper_manager
    _scraper_manager = instance
    logger.info("ScraperManager 已注册到服务容器")


def get_scraper_manager():
    """获取 ScraperManager"""
    if _scraper_manager is None:
        raise RuntimeError("ScraperManager 未初始化")
    return _scraper_manager


def init_task_manager(instance):
    """初始化 TaskManager"""
    global _task_manager
    _task_manager = instance
    logger.info("TaskManager 已注册到服务容器")


def get_task_manager():
    """获取 TaskManager"""
    if _task_manager is None:
        raise RuntimeError("TaskManager 未初始化")
    return _task_manager


def init_scheduler_manager(instance):
    """初始化 SchedulerManager"""
    global _scheduler_manager
    _scheduler_manager = instance
    logger.info("SchedulerManager 已注册到服务容器")


def get_scheduler_manager():
    """获取 SchedulerManager"""
    if _scheduler_manager is None:
        raise RuntimeError("SchedulerManager 未初始化")
    return _scheduler_manager


def init_webhook_service(instance):
    """注册已显式配置的 WebhookService。"""
    global _webhook_service
    _webhook_service = instance
    logger.info("WebhookService 已注册到服务容器")


def get_webhook_service():
    """获取共享 WebhookService 实例。"""
    if _webhook_service is None:
        raise RuntimeError("WebhookService 未初始化")
    return _webhook_service


def init_media_server_service(instance):
    """注册启动时创建的媒体服务器服务。"""
    global _media_server_service
    _media_server_service = instance
    logger.info("MediaServerService 已注册到服务容器")


def get_media_server_service():
    """获取共享媒体服务，未启动时立即报告明确错误。"""
    if _media_server_service is None:
        raise RuntimeError("MediaServerService 未初始化")
    return _media_server_service


def init_metadata_service(instance):
    """注册全局元数据服务实例。"""
    global _metadata_service
    _metadata_service = instance
    logger.info("MetadataService 已注册到服务容器")


def get_metadata_service():
    """获取已注册的元数据服务。"""
    if _metadata_service is None:
        raise RuntimeError("MetadataService 未初始化")
    return _metadata_service


def init_rate_limiter(instance):
    """初始化 RateLimiter"""
    global _rate_limiter
    _rate_limiter = instance
    logger.info("RateLimiter 已注册到服务容器")


def get_rate_limiter():
    """获取 RateLimiter"""
    if _rate_limiter is None:
        raise RuntimeError("RateLimiter 未初始化")
    return _rate_limiter


# AI 全局实例统一由 services.ai_service 提供，此处不保留第二套入口。


def init_title_recognition_manager(instance):
    """初始化 TitleRecognitionManager"""
    global _title_recognition_manager
    _title_recognition_manager = instance
    logger.info("TitleRecognitionManager 已注册到服务容器")


def get_title_recognition_manager():
    """获取 TitleRecognitionManager"""
    if _title_recognition_manager is None:
        raise RuntimeError("TitleRecognitionManager 未初始化")
    return _title_recognition_manager


def get_file_storage_service():
    """
    获取 FileStorageService 全局单例（懒加载）

    FileStorageService 是纯文件 I/O 能力层，不依赖数据库与 session，
    因此无需显式初始化。

    Returns:
        FileStorageService 实例
    """
    global _file_storage_service

    if _file_storage_service is None:
        from src.services.file_storage_service import FileStorageService
        _file_storage_service = FileStorageService()
        logger.info("FileStorageService 已初始化（懒加载）")

    return _file_storage_service

