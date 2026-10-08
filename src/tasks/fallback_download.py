"""可恢复的匹配后备下载任务入口，冷启动业务由 Workflow 承接。"""
from typing import Optional

from src.rate_limiter import RateLimitExceededError
from src.utils.diagnostics.task_exceptions import TaskSuccess, TaskPauseForRateLimit
from src.workflows.match_fallback_download import execute_match_fallback_download


async def match_fallback_download_task(
    session,
    progress_callback,
    *,
    # ── 核心参数（可序列化，供任务恢复）──
    episodeId: int,
    real_anime_id: int,
    provider: str,
    mediaId: str,
    episode_number: int,
    episode_title: str,
    episode_url: str,
    provider_episode_id: str,
    final_title: str,
    display_title: str,
    final_season: int,
    media_type: str,
    imageUrl: Optional[str] = None,
    year: Optional[int] = None,
    total_episodes: Optional[int] = None,
    fallback_episode_cache_key: Optional[str] = None,
    # ── 依赖注入（由提交入口 submit_match_fallback_download 注入）──
    scraper_manager=None,
    rate_limiter=None,
    config_service=None,
):
    """匹配后备弹幕下载任务（冷启动，可恢复）。

    严格对齐 comments.py:564-728 闭包逻辑。关键参数：
        episodeId: 14位真实 episodeId（25{animeId:06d}{sourceOrder:02d}{episode:04d}）
        real_anime_id: 真实 animeId（作品主键）
        display_title: 展示标题（建 Anime 用，如"碧蓝之海 第二季"）
        final_title: 最终标题（match_season 缓存键纯标题解析基准）
        total_episodes: 整部剧集数（有则写整季基准缓存，支持连续播放）
        fallback_episode_cache_key: 待清理的 fallback_search 旧缓存键（不含前缀）

    依赖 scraper_manager/rate_limiter/config_service 由提交入口通过 coro_factory 注入；
    恢复场景由 TaskManager 重建 coro_factory 时从 _recovery_dependencies 注入。

    Returns:
        str: 任务完成消息
    Raises:
        TaskSuccess: 未获取到弹幕（正常结束，非失败）
    """
    # 任务会话仅作为调度契约保留，业务流程自持短事务。
    try:
        message = await execute_match_fallback_download(
            {
                "episodeId": episodeId, "real_anime_id": real_anime_id,
                "provider": provider, "mediaId": mediaId,
                "episode_number": episode_number, "episode_title": episode_title,
                "episode_url": episode_url, "provider_episode_id": provider_episode_id,
                "final_title": final_title, "display_title": display_title,
                "final_season": final_season, "media_type": media_type,
                "imageUrl": imageUrl, "year": year, "total_episodes": total_episodes,
                "fallback_episode_cache_key": fallback_episode_cache_key,
            },
            progress_callback, scraper_manager, rate_limiter, config_service,
        )
    except RateLimitExceededError as exc:
        raise TaskPauseForRateLimit(
            retry_after_seconds=exc.retry_after_seconds,
            message=f"速率受限，将在 {exc.retry_after_seconds:.0f} 秒后自动重试...",
        ) from exc
    raise TaskSuccess(message)
