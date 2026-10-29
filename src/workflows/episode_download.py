"""分集并发下载编排，不依赖任务入口。"""
import asyncio
import logging
from typing import Any, Callable, List, Optional, Tuple

from src.rate_limiter import RateLimiter

logger = logging.getLogger(__name__)


async def download_episode_comments_concurrent(
    scraper: Any,
    episodes: List,
    rate_limiter: RateLimiter,
    progress_callback: Callable,
    first_episode_comments: Optional[List] = None,
    is_fallback: bool = False,
    fallback_type: Optional[str] = None,
) -> List[Tuple[int, Optional[List]]]:
    """并发下载分集并复用首集验证结果，保留现有结果与失败语义。"""
    logger.info(f"开始并发下载 {len(episodes)} 个分集的弹幕（三线程模式）{'[后备任务]' if is_fallback else ''}")

    async def download_single_episode(episode_info: Tuple[int, Any]) -> Tuple[int, Optional[List]]:
        episode_index, episode = episode_info
        try:
            if episode_index == 0 and first_episode_comments is not None:
                logger.info(f"使用预获取的第一集弹幕: {len(first_episode_comments)} 条")
                return episode.episodeIndex, first_episode_comments
            if is_fallback:
                if not fallback_type:
                    raise ValueError("后备任务必须指定fallback_type参数")
                await rate_limiter.check_fallback(fallback_type, scraper.provider_name)
            else:
                await rate_limiter.check(scraper.provider_name)

            async def sub_progress_callback(p: float, msg: str) -> None:
                await progress_callback(
                    30 + int((episode_index + p / 100) * 60 / len(episodes)),
                    f"[线程{episode_index + 1}] {msg}",
                )

            # 下载前统一占额，首集缓存复用不经过此入口。
            comments = await scraper.get_comments(
                episode.episodeId, progress_callback=sub_progress_callback,
                pool=fallback_type if is_fallback else "global",
            )
            if comments is not None:
                logger.info(f"[并发下载] 分集 '{episode.title}' 获取到 {len(comments)} 条弹幕")
            else:
                logger.warning(f"[并发下载] 分集 '{episode.title}' 获取弹幕失败")
            return episode.episodeIndex, comments
        except Exception as e:
            logger.error(f"[并发下载] 分集 '{episode.title}' 下载失败: {e}")
            return episode.episodeIndex, None

    semaphore = asyncio.Semaphore(3)

    async def download_with_semaphore(episode_info: Tuple[int, Any]) -> Tuple[int, Optional[List]]:
        async with semaphore:
            return await download_single_episode(episode_info)

    download_tasks = [
        download_with_semaphore((i, episode)) for i, episode in enumerate(episodes)
    ]
    results = await asyncio.gather(*download_tasks, return_exceptions=True)
    valid_results = []
    for result in results:
        if isinstance(result, Exception):
            logger.error(f"[并发下载] 任务执行异常: {result}")
            continue
        valid_results.append(result)
    logger.info(f"并发下载完成，成功下载 {len([r for r in valid_results if r[1] is not None])}/{len(episodes)} 个分集")
    return valid_results
