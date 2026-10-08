"""
弹弹Play 兼容 API 的预下载功能

此模块仅作为 API 层的薄包装，实际业务逻辑在 workflows.danmaku.predownload_flow 中。
"""

from typing import Optional

from src.services.scraper_manager import ScraperManager
from src.services.task_manager import TaskManager
from src.services.config_service import ConfigService
from src.rate_limiter import RateLimiter
from src.workflows.danmaku.predownload_flow import (
    wait_for_refresh_task,
    predownload_next_episode_flow,
)


async def try_predownload_next_episode(
    current_episode_id: int,
    config_service: ConfigService,
    task_manager: TaskManager,
    scraper_manager: ScraperManager,
    rate_limiter: RateLimiter,
    title_recognition_manager = None,
) -> Optional[int]:
    """
    尝试预下载下一集弹幕（API 层包装）

    此函数仅作为向后兼容的 API 层接口，实际业务逻辑已迁移到
    src.workflows.danmaku.predownload_flow 中。

    Args:
        current_episode_id: 当前分集 ID
        config_service: 配置服务
        task_manager: 任务管理器
        scraper_manager: 弹幕源管理器
        rate_limiter: 流控管理器
        title_recognition_manager: 标题识别管理器（可选）

    Returns:
        当前分集 ID，如果成功提交预下载任务；否则 None
    """
    return await predownload_next_episode_flow(
        current_episode_id=current_episode_id,
        config_service=config_service,
        task_manager=task_manager,
        scraper_manager=scraper_manager,
        rate_limiter=rate_limiter,
        title_recognition_manager=title_recognition_manager,
    )


__all__ = [
    "wait_for_refresh_task",
    "try_predownload_next_episode",
]
