"""编辑导入准备流程：过滤已有分集、验证首集并提交作品身份。"""
import logging
from typing import Any, Callable, List, Tuple

from src.rate_limiter import RateLimitExceededError
from src.schemas import EditImportRequest
from src.services.service_container import get_database_service
from src.utils.diagnostics.error_message import extract_short_error_message
from src.utils.diagnostics.task_exceptions import TaskFailed, TaskSuccess
from src.workflows.anime_identity import get_or_create_anime_with_deduplication
from src.workflows.image_download import download_image

logger = logging.getLogger(__name__)


async def prepare_edited_import(
    request_data: EditImportRequest,
    scraper: Any,
    rate_limiter: Any,
    title_recognition_manager: Any,
    progress_callback: Callable,
) -> Tuple[List[Any], int, int, List[Any]]:
    """验证通过后才创建身份；数据库异常不伪装成建库前验证失败。"""
    episodes = request_data.episodes
    db = get_database_service()
    async with db.transaction():
        identity = await db.anime.find_import_identity(
            provider=request_data.provider, media_id=request_data.mediaId,
            season=request_data.season,
        )
        source_id = identity["source_id"] if identity else None
        existing_episodes = []
        if source_id:
            for episode in episodes:
                if await db.episode.check_episode_exists_with_danmaku(
                    request_data.provider, request_data.mediaId, episode.episodeIndex,
                ):
                    existing_episodes.append(episode.episodeIndex)

    if existing_episodes:
        episode_list = ", ".join(map(str, existing_episodes))
        episodes = [ep for ep in episodes if ep.episodeIndex not in existing_episodes]
        if not episodes:
            raise TaskSuccess(f"所有要导入的分集 ({episode_list}) 都已存在弹幕，无需重复导入。")
        remaining_list = ", ".join(str(ep.episodeIndex) for ep in episodes)
        logger.info(f"将跳过已存在的分集 ({episode_list})，继续导入分集: {remaining_list}")

    first_episode = episodes[0]
    await progress_callback(10, f"正在验证数据源有效性: {first_episode.title}")
    # 仅捕获网络验证错误，已提交身份后的异常必须保持真实失败语义。
    try:
        await rate_limiter.check(scraper.provider_name)
        first_comments = await scraper.get_comments(
            first_episode.episodeId,
            progress_callback=lambda p, msg: progress_callback(10 + p * 0.1, msg),
        )
    except RateLimitExceededError:
        raise
    except Exception as exc:
        short_error = extract_short_error_message(exc)
        logger.error("编辑导入首集验证失败: %s", exc, exc_info=True)
        raise TaskFailed(
            f"数据源验证失败：获取 '{first_episode.title}' 弹幕时发生错误 - "
            f"{short_error}。未创建数据库条目。"
        ) from exc

    if not first_comments:
        raise TaskFailed(
            f"数据源验证失败：'{first_episode.title}' 未获取到任何弹幕数据。"
            f"请到 {request_data.provider} 源验证该视频是否有弹幕。未创建数据库条目。"
        )
    await progress_callback(20, "数据源验证成功，正在创建数据库条目...")
    local_image_path = None
    if request_data.imageUrl:
        try:
            local_image_path = await download_image(
                request_data.imageUrl, provider_name=request_data.provider,
            )
        except Exception as exc:
            logger.warning("海报下载失败: %s", exc)

    anime_id = await get_or_create_anime_with_deduplication(
        provider=request_data.provider, mediaId=request_data.mediaId,
        title=request_data.animeTitle, mediaType=request_data.mediaType,
        season=request_data.season, imageUrl=request_data.imageUrl,
        local_image_path=local_image_path, year=request_data.year,
        title_recognition_manager=title_recognition_manager,
        tmdb_id=request_data.tmdbId, tvdb_id=request_data.tvdbId,
        imdb_id=request_data.imdbId,
    )
    async with db.transaction():
        await db.anime.update_metadata_if_empty(
            anime_id, tmdb_id=request_data.tmdbId, imdb_id=request_data.imdbId,
            tvdb_id=request_data.tvdbId, douban_id=request_data.doubanId,
            bangumi_id=request_data.bangumiId,
            tmdb_episode_group_id=request_data.tmdbEpisodeGroupId,
        )
        source_id = await db.source.link_source_to_anime(
            anime_id, request_data.provider, request_data.mediaId,
        )
    return episodes, anime_id, source_id, first_comments
