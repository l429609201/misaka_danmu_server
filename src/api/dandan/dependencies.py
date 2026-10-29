"""
弹弹Play 兼容 API 的依赖项函数

使用方式:
    from src.api.dandan.dependencies import (
        get_config_service, get_task_manager, get_metadata_service, get_rate_limiter
    )
"""

from fastapi import Request

from src.services.config_service import ConfigService
from src.services.task_manager import TaskManager
from src.services.metadata_service import MetadataService
from src.services.scraper_manager import ScraperManager
from src.rate_limiter import RateLimiter


async def get_config_service(request: Request) -> ConfigService:
    """依赖项：从应用状态获取配置服务。"""
    return request.app.state.config_service


async def get_task_manager(request: Request) -> TaskManager:
    """依赖项：从应用状态获取任务管理器"""
    return request.app.state.task_manager


async def get_metadata_service(request: Request) -> MetadataService:
    """从应用状态获取统一元数据服务。"""
    return request.app.state.metadata_service


async def get_rate_limiter(request: Request) -> RateLimiter:
    """依赖项：从应用状态获取速率限制器"""
    return request.app.state.rate_limiter


async def get_scraper_manager(request: Request) -> ScraperManager:
    """依赖项：从应用状态获取弹幕源管理器"""
    return request.app.state.scraper_manager

