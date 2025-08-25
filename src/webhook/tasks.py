import logging
import re
from typing import Any, Callable, Optional

from datetime import datetime
from sqlalchemy.ext.asyncio import AsyncSession
from thefuzz import fuzz

from .. import crud
from ..rate_limiter import RateLimiter
from ..scraper_manager import ScraperManager
from ..metadata_manager import MetadataSourceManager
from ..task_manager import TaskManager, TaskSuccess
from ..tasks import generic_import_task
from ..utils import parse_search_keyword

logger = logging.getLogger(__name__)


def _is_movie_by_title(title: str) -> bool:
    """
    通过标题中的关键词（如“剧场版”）判断是否为电影。
    """
    if not title:
        return False
    # 关键词列表，不区分大小写
    movie_keywords = ["剧场版", "劇場版", "movie", "映画"]
    title_lower = title.lower()
    return any(keyword in title_lower for keyword in movie_keywords)


async def webhook_search_and_dispatch_task(
    animeTitle: str,
    mediaType: str,
    season: int,
    currentEpisodeIndex: int,
    searchKeyword: str,
    doubanId: Optional[str],
    tmdbId: Optional[str],
    imdbId: Optional[str],
    tvdbId: Optional[str],
    bangumiId: Optional[str],
    webhookSource: str,
    year: Optional[int],
    progress_callback: Callable,
    session: AsyncSession,
    manager: ScraperManager,
    task_manager: TaskManager, # type: ignore
    metadata_manager: MetadataSourceManager,
    rate_limiter: RateLimiter
):
    """
    Webhook 触发的后台任务：搜索所有源，找到最佳匹配，并为该匹配分发一个新的、具体的导入任务。
    """
    try:
        logger.info(f"Webhook 任务: 开始为 '{animeTitle}' (S{season:02d}E{currentEpisodeIndex:02d}) 查找最佳源...")
        progress_callback(5, "正在检查已收藏的源...")

        # 1. 优先查找已收藏的源
        favorited_source = await crud.find_favorited_source_for_anime(session, animeTitle, season)
        if favorited_source:
            logger.info(f"Webhook 任务: 找到已收藏的源 '{favorited_source['providerName']}'，将直接使用此源。")
            progress_callback(10, f"找到已收藏的源: {favorited_source['providerName']}")

            # 直接使用这个源的信息创建导入任务
            task_title = f"Webhook自动导入: {favorited_source['animeTitle']} ({favorited_source['providerName']})"
            task_coro = lambda session, cb: generic_import_task(
                provider=favorited_source['providerName'], mediaId=favorited_source['mediaId'], animeTitle=favorited_source['animeTitle'], year=year,
                mediaType=favorited_source['mediaType'], season=season, currentEpisodeIndex=currentEpisodeIndex,
                imageUrl=favorited_source['imageUrl'], doubanId=doubanId, tmdbId=tmdbId, imdbId=imdbId, tvdbId=tvdbId, metadata_manager=metadata_manager,
                bangumiId=bangumiId, rate_limiter=rate_limiter,
                progress_callback=cb, session=session, manager=manager,
                task_manager=task_manager
            )
            await task_manager.submit_task(task_coro, task_title)
            raise TaskSuccess(f"Webhook: 已为收藏源 '{favorited_source['providerName']}' 创建导入任务。")

        # 2. 如果没有收藏源，则并发搜索所有启用的源
        logger.info(f"Webhook 任务: 未找到收藏源，开始并发搜索所有启用的源...")
        progress_callback(20, "并发搜索所有源...")

        # 关键修复：像UI一样，先解析搜索关键词，分离出纯标题
        parsed_keyword = parse_search_keyword(searchKeyword)
        search_title_only = parsed_keyword["title"]
        logger.info(f"Webhook 任务: 已将搜索词 '{searchKeyword}' 解析为标题 '{search_title_only}' 进行搜索。")

        all_search_results = await manager.search_all(
            [search_title_only], episode_info={"season": season, "episode": currentEpisodeIndex}
        )

        if not all_search_results:
            raise TaskSuccess(f"Webhook 任务失败: 未找到 '{animeTitle}' 的任何可用源。")

        # 3. 从所有源的返回结果中，根据类型、季度和标题相似度选择最佳匹配项
        ordered_settings = await crud.get_all_scraper_settings(session)
        provider_order = {s['providerName']: s['displayOrder'] for s in ordered_settings}

        valid_candidates = []
        for item in all_search_results:
            if item.type == 'tv_series' and _is_movie_by_title(item.title):
                item.type = 'movie'
                item.season = 1

            type_match = (item.type == mediaType)
            season_match = (item.season == season) if mediaType == 'tv_series' else True

            if type_match and season_match:
                valid_candidates.append(item)

        if not valid_candidates:
            raise TaskSuccess(f"Webhook 任务失败: 未找到 '{animeTitle}' 的精确匹配项。")

        valid_candidates.sort(
            key=lambda item: (fuzz.token_set_ratio(animeTitle, item.title), -provider_order.get(item.provider, 999)),
            reverse=True
        )
        best_match = valid_candidates[0]

        logger.info(f"Webhook 任务: 在所有源中找到最佳匹配项 '{best_match.title}' (来自: {best_match.provider})，将为其创建导入任务。")
        progress_callback(50, f"在 {best_match.provider} 中找到最佳匹配项")

        # 根据媒体类型格式化任务标题，以包含季集信息和时间戳
        current_time = datetime.now().strftime("%H:%M:%S")
        if mediaType == "tv_series":
            task_title = f"Webhook（{webhookSource}）自动导入：{best_match.title} - S{season:02d}E{currentEpisodeIndex:02d} ({best_match.provider}) [{current_time}]"
        else: # movie
            task_title = f"Webhook（{webhookSource}）自动导入：{best_match.title} ({best_match.provider}) [{current_time}]"
        task_coro = lambda session, cb: generic_import_task(
            provider=best_match.provider, mediaId=best_match.mediaId, year=year,
            animeTitle=best_match.title, mediaType=best_match.type,
            season=season, currentEpisodeIndex=best_match.currentEpisodeIndex, imageUrl=best_match.imageUrl, metadata_manager=metadata_manager,
            doubanId=doubanId, tmdbId=tmdbId, imdbId=imdbId, tvdbId=tvdbId, bangumiId=bangumiId, rate_limiter=rate_limiter,
            progress_callback=cb, session=session, manager=manager,  # 修正：使用由TaskManager提供的session和cb
            task_manager=task_manager
        )
        await task_manager.submit_task(task_coro, task_title)
        raise TaskSuccess(f"Webhook: 已为源 '{best_match.provider}' 创建导入任务。")
    except TaskSuccess:
        raise
    except Exception as e:
        logger.error(f"Webhook 搜索与分发任务发生严重错误: {e}", exc_info=True)
        raise