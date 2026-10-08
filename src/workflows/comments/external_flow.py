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
from src.workflows.comments.helpers import (
    process_comments_for_dandanplay,
)
from src.utils.misc.common import handle_danmaku_likes
from src.utils.misc.converter import convert_comments, get_effective_convert_mode

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

    async with db.transaction() as session:
        cache_key = f"ext_danmaku_v2_{url}"
        cached_comments = await session.get_cache("", cache_key)
        if cached_comments is not None:
            logger.info(f"外部弹幕缓存命中: {url}")
            comments_data = cached_comments
        else:
            logger.info(f"外部弹幕缓存未命中，正在从网络获取: {url}")
            scraper = scraper_manager.get_scraper_by_domain(url)
            if not scraper:
                raise HTTPException(status_code=400, detail="不支持的URL或视频源。")

            try:
                provider_episode_id = await scraper.get_id_from_url(url)
                if not provider_episode_id:
                    raise ValueError(f"无法从URL '{url}' 中解析出有效的视频ID。")

                episode_id_for_comments = scraper.format_episode_id_for_comments(provider_episode_id)
                # 外部 URL 同样消耗普通池额度，不提供旁路下载。
                comments_data = await scraper.get_comments(episode_id_for_comments)
                likes_enabled = (await config_service.get('danmakuLikesOutputEnabled', 'true')).lower() == 'true'
                likes_style = await config_service.get('danmakuLikesStyle', 'heart_white')
                # likes_style='off' 等价于 enabled=False
                comments_data = handle_danmaku_likes(
                    comments_data, scraper.likes_fire_threshold,
                    enabled=likes_enabled and likes_style != 'off',
                    style=likes_style
                )

                # 修正：使用 scraper.provider_name 修复未定义的 'provider' 变量
                if not comments_data:
                    logger.warning(f"未能从 {scraper.provider_name} URL 获取任何弹幕: {url}")

            except Exception as e:
                logger.error(f"处理 {scraper.provider_name} 外部弹幕时出错: {e}", exc_info=True)
                raise HTTPException(status_code=500, detail=f"获取 {scraper.provider_name} 弹幕失败。")

            # 缓存结果5小时 (18000秒)
            await session.set_cache("", cache_key, comments_data, 18000)

        # 处理简繁转换（使用统一工具）
        try:
            final_convert_mode = await get_effective_convert_mode(
                chConvert, config_service
            )
            if final_convert_mode != 0 and comments_data:
                convert_comments(comments_data, final_convert_mode)
                logger.debug(f"外部弹幕简繁转换完成: url={url}, mode={final_convert_mode}, count={len(comments_data)}")
        except Exception as e:
            logger.error(f"应用简繁转换失败: {e}", exc_info=True)

        # 修正：使用统一的弹幕处理函数，以确保输出格式符合 dandanplay 客户端规范
        processed_comments = process_comments_for_dandanplay(comments_data)
        return CommentsResponse(count=len(processed_comments), comments=processed_comments)
