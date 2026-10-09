"""
外部URL弹幕获取业务流程

包含：
- 从外部URL获取弹幕的完整逻辑
- 缓存机制
- 速率限制处理
"""

import logging

from fastapi import HTTPException, status

from src.schemas.dandan import CommentResponse as CommentsResponse
from src.services.service_container import (
    get_database_service,
    get_scraper_manager,
    get_rate_limiter,
)
from src.services.config_service import get_config_service
from src.workflows.comments.output_flow import apply_output_config
from src.workflows.dandan.helpers import get_db_cache, set_db_cache

logger = logging.getLogger(__name__)


async def get_external_comments_from_url(url: str, token: str, chConvert: int = 0) -> CommentsResponse:
    """
    从外部URL获取弹幕

    Args:
        url: 外部视频链接
        token: 用户token（保留参数，用于未来扩展如播放历史记录）
        chConvert: 中文简繁转换

    Returns:
        CommentsResponse: 弹幕响应
    """
    # 使用全局单例
    db = get_database_service()
    scraper_manager = get_scraper_manager()
    config_service = get_config_service()

    scraper = scraper_manager.get_scraper_by_domain(url)
    async with db.transaction() as session:
        cache_key = f"ext_danmaku_v2_{url}"
        cached_comments = await get_db_cache(session, "", cache_key)
        if cached_comments is not None:
            logger.info(f"外部弹幕缓存命中: {url}")
            comments_data = cached_comments
        else:
            logger.info(f"外部弹幕缓存未命中，正在从网络获取: {url}")
            if not scraper:
                raise HTTPException(status_code=400, detail="不支持的URL或视频源。")

            try:
                provider_episode_id = await scraper.get_id_from_url(url)
                if not provider_episode_id:
                    raise ValueError(f"无法从URL '{url}' 中解析出有效的视频ID。")

                episode_id_for_comments = scraper.format_episode_id_for_comments(provider_episode_id)
                # 外部 URL 同样消耗普通池额度，不提供旁路下载。
                comments_data = await scraper.get_comments(episode_id_for_comments)

                # 修正：使用 scraper.provider_name 修复未定义的 'provider' 变量
                if not comments_data:
                    logger.warning(f"未能从 {scraper.provider_name} URL 获取任何弹幕: {url}")

            except Exception as e:
                logger.error(f"处理 {scraper.provider_name} 外部弹幕时出错: {e}", exc_info=True)
                raise HTTPException(status_code=500, detail=f"获取 {scraper.provider_name} 弹幕失败。")

            # 仅缓存源数据，显示配置在每次请求中重新应用。
            await set_db_cache(session, "", cache_key, comments_data, 18000)

        processed_comments = await apply_output_config(
            comments_data, config_service, chConvert,
            fire_threshold=scraper.likes_fire_threshold if scraper else 1000,
        )
        return CommentsResponse(count=len(processed_comments), comments=processed_comments)
