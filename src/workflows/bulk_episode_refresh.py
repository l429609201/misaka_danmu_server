"""批量刷新单条编排，数据库查询与下载保存不进入任务层。"""
import asyncio
import logging
from typing import Any, Callable, Set, Tuple

from src.rate_limiter import RateLimitExceededError
from src.schemas import ProviderEpisodeInfo
from src.services.service_container import get_database_service
from src.workflows.danmaku_import import save_danmaku_for_episode
from src.workflows.episode_download import download_episode_comments_concurrent

logger = logging.getLogger(__name__)


async def refresh_bulk_item(
    episode_id: int, manager: Any, rate_limiter: Any,
    progress_callback: Callable, config_service: Any,
    limited_providers: Set[str], progress: int, position: int, total: int,
) -> Tuple[int, int, bool]:
    """返回集号、写入数量及成功标记；单源限流跳过，全局限流原地等待。"""
    index = episode_id
    db = get_database_service()
    try:
        async with db.transaction():
            info = await db.episode.get_episode_provider_info(episode_id)
        if info:
            index = info.get("episodeIndex", episode_id)
        if not info or not info.get("providerName") or not info.get("providerEpisodeId"):
            return index, 0, False
        provider = info["providerName"]
        if provider in limited_providers:
            return index, 0, False
        scraper = manager.get_scraper(provider)
        while True:
            try:
                await rate_limiter.check(provider)
                break
            except RateLimitExceededError as exc:
                if "全局速率限制" not in str(exc) and "__global__" not in str(exc):
                    limited_providers.add(provider)
                    return index, 0, False
                await progress_callback(
                    progress, f"全局流控等待中，{exc.retry_after_seconds:.0f} 秒后继续 ({position}/{total})...",
                )
                await asyncio.sleep(exc.retry_after_seconds + 1)
        episode = ProviderEpisodeInfo(
            provider=provider, episodeIndex=1, title=f"批量刷新分集 {episode_id}",
            episodeId=info["providerEpisodeId"], url="",
        )
        results = await download_episode_comments_concurrent(
            scraper, [episode], rate_limiter, lambda _percent, _description: asyncio.sleep(0),
        )
        comments = results[0][1] if results else None
        if not comments:
            # 保持批量刷新既有空响应语义，不覆盖弹幕文件。
            async with db.transaction():
                await db.episode.update_fetch_time(episode_id)
            return index, 0, False
        added = await save_danmaku_for_episode(
            episode_id, comments, config_service, force=True, update_fetch_time_on_skip=True,
        )
        await asyncio.sleep(0.1)
        return index, added, True
    except Exception as exc:
        logger.error("刷新分集 ID %s 时发生错误: %s", episode_id, exc, exc_info=True)
        return index, 0, False
