"""
API依赖注入函数
提供FastAPI端点所需的各种管理器和服务
"""

from fastapi import Request
from src.services.webhook_service import WebhookService
from src.services.scraper_manager import ScraperManager
from src.services.task_manager import TaskManager
from src.services.scheduler import SchedulerManager
from src.services.metadata_service import MetadataService
from src.services.config_service import ConfigService
from src.services.cache_service import CacheService
from src.services.ai_service import AIService
from src.rate_limiter import RateLimiter


async def get_scraper_manager(request: Request) -> ScraperManager:
    """依赖项：从应用状态获取 Scraper 管理器"""
    return request.app.state.scraper_manager


async def get_task_manager(request: Request) -> TaskManager:
    """依赖项：从应用状态获取任务管理器"""
    return request.app.state.task_manager


async def get_scheduler_manager(request: Request) -> SchedulerManager:
    """依赖项：从应用状态获取 Scheduler 管理器"""
    return request.app.state.scheduler_manager


async def get_webhook_service(request: Request) -> WebhookService:
    """从应用状态获取 Webhook 接入服务。"""
    return request.app.state.webhook_service


async def get_metadata_service(request: Request) -> MetadataService:
    """从应用状态获取统一元数据服务。"""
    return request.app.state.metadata_service


async def get_config_service(request: Request) -> ConfigService:
    """依赖项：从应用状态获取配置服务。"""
    return request.app.state.config_service


async def get_rate_limiter(request: Request) -> RateLimiter:
    """依赖项：从应用状态获取速率限制器"""
    return request.app.state.rate_limiter


async def get_ai_service(request: Request) -> AIService:
    """依赖项：从应用状态获取共享 AI 服务。"""
    return request.app.state.ai_service


async def get_title_recognition_manager(request: Request):
    """依赖项：从应用状态获取标题识别管理器"""
    return request.app.state.title_recognition_manager


async def get_cache_manager(request: Request) -> CacheService:
    """依赖项：从应用状态获取缓存管理器"""
    return request.app.state.cache_manager
