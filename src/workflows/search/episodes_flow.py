"""
弹弹Play 兼容搜索分集业务流程
"""

import logging
from typing import Optional, TYPE_CHECKING

# 🚀 新架构：从 workflow 层导入 search_implementation
from src.workflows.search.fallback_search import search_implementation
from src.services.scraper_manager import ScraperManager
from src.services.config_service import ConfigService

if TYPE_CHECKING:
    from src.rate_limiter import RateLimiter

logger = logging.getLogger(__name__)


async def search_episodes_flow(
    anime: str,
    episode: Optional[str],
    db,
    scraper_manager: ScraperManager,
    config_service: ConfigService,
    rate_limiter: "RateLimiter",
):
    """
    搜索节目和分集的业务流程
    
    模拟 dandanplay 的 /api/v2/search/episodes 接口。
    它会搜索 **本地弹幕库** 中的番剧和分集信息。
    当启用并行搜索时，还会从源站补充缺失的分集。
    
    Args:
        anime: 节目名称
        episode: 分集标题（通常是数字）
        db: DatabaseService 数据访问服务
        scraper_manager: 弹幕源管理器
        config_service: 配置服务
        rate_limiter: 速率限制器
        
    Returns:
        DandanSearchEpisodesResponse 对象
    """
    search_term = anime.strip()
    return await search_implementation(
        search_term, episode, db,
        scraper_manager=scraper_manager,
        config_service=config_service,
        rate_limiter=rate_limiter,
    )
