"""
弹弹Play 兼容 API 的搜索功能

包含搜索节目、搜索分集等功能。
🚀 新架构：API 层只负责路由和参数验证，业务逻辑委托给 workflow 层
"""

import logging
from typing import Optional

from fastapi import APIRouter, Depends, Query

# 🚀 导入 workflow 层实现
from src.workflows.search.anime_flow import search_anime_flow
from src.workflows.search.episodes_flow import search_episodes_flow

# 导入数据模型和路由处理器
from src.schemas.dandan import (
    DandanSearchEpisodesResponse,
    DandanSearchAnimeResponse,
)
from .route_handler import get_token_from_path, DandanApiRoute
from .dependencies import (
    get_task_manager,
    get_rate_limiter,
    get_scraper_manager,
    get_metadata_service,
)
from src.services.service_container import get_database_service
from src.services.scraper_manager import ScraperManager
from src.services.task_manager import TaskManager
from src.services.metadata_service import MetadataService
from src.services.config_service import ConfigService
from src.rate_limiter import RateLimiter
from src.api.control.dependencies import get_title_recognition_manager
from .dependencies import get_config_service

logger = logging.getLogger(__name__)

# ═══════════════════════════════════════════════════════════════
# 路由定义 - API 层薄层，委托给 workflow 层
# ═══════════════════════════════════════════════════════════════

search_router = APIRouter(route_class=DandanApiRoute)


@search_router.get(
    "/search/episodes",
    response_model=DandanSearchEpisodesResponse,
    summary="[dandanplay兼容] 搜索节目和分集"
)
async def search_episodes_for_dandan(
    anime: str = Query(..., description="节目名称"),
    episode: Optional[str] = Query(None, description="分集标题 (通常是数字)"),
    token: str = Depends(get_token_from_path),
    db = Depends(get_database_service),
    scraper_manager: ScraperManager = Depends(get_scraper_manager),
    config_service: ConfigService = Depends(get_config_service),
    rate_limiter: RateLimiter = Depends(get_rate_limiter),
):
    """
    模拟 dandanplay 的 /api/v2/search/episodes 接口 - 🚀 委托给 workflow 层处理
    """
    logger.debug("dandan 分集搜索使用 token: %s", token[:8])

    async with db.transaction():
        return await search_episodes_flow(
            anime, episode, db,
            scraper_manager, config_service, rate_limiter
        )


@search_router.get(
    "/search/anime",
    response_model=DandanSearchAnimeResponse,
    summary="[dandanplay兼容] 搜索作品"
)
async def search_anime_for_dandan(
    keyword: Optional[str] = Query(None, description="节目名称 (兼容 keyword)"),
    anime: Optional[str] = Query(None, description="节目名称 (兼容 anime)"),
    episode: Optional[str] = Query(None, description="分集标题 (此接口中未使用)"),
    token: str = Depends(get_token_from_path),
    db = Depends(get_database_service),
    scraper_manager: ScraperManager = Depends(get_scraper_manager),
    metadata_manager: MetadataService = Depends(get_metadata_service),
    config_service: ConfigService = Depends(get_config_service),
    rate_limiter: RateLimiter = Depends(get_rate_limiter),
    title_recognition_manager = Depends(get_title_recognition_manager),
    task_manager: TaskManager = Depends(get_task_manager)
):
    """
    模拟 dandanplay 的 /api/v2/search/anime 接口 - 🚀 委托给 workflow 层处理
    """
    async with db.transaction():
        return await search_anime_flow(
            keyword, anime, episode, token, db,
            scraper_manager, config_service, metadata_manager,
            rate_limiter, title_recognition_manager, task_manager
        )
