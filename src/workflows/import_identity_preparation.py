"""通用导入身份准备：海报、作品、来源和元数据关联。"""
import logging
from typing import Any, Dict, Tuple

from src.services.service_container import get_database_service
from src.workflows.alias_workflow import fetch_and_save_aliases
from src.workflows.anime_identity import create_preassigned_anime, get_or_create_anime_with_deduplication
from src.workflows.image_download import download_image

logger = logging.getLogger(__name__)


async def prepare_generic_identity(
    parameters: Dict[str, Any], metadata_manager: Any,
    title_recognition_manager: Any,
) -> Tuple[int, int, bool]:
    """验证通过后提交作品及来源；数据库异常原样传播，不触发候选顺延。"""
    provider = parameters["provider"]
    media_id = parameters["mediaId"]
    title = parameters["animeTitle"]
    media_type = parameters["mediaType"]
    season = parameters["season"]
    year = parameters.get("year")
    image_url = parameters.get("imageUrl")
    local_image_path = None
    image_failed = False
    if image_url:
        try:
            local_image_path = await download_image(image_url, provider_name=provider)
        except Exception as exc:
            logger.warning("海报下载失败: %s", exc)
            image_failed = True
    anime_id = parameters.get("preassignedAnimeId")
    if anime_id:
        await create_preassigned_anime(
            anime_id, title, media_type, season, image_url, local_image_path, year,
        )
    else:
        anime_id = await get_or_create_anime_with_deduplication(
            provider=provider, mediaId=media_id, title=title,
            mediaType=media_type, season=season, imageUrl=image_url,
            local_image_path=local_image_path, year=year,
            title_recognition_manager=title_recognition_manager,
            tmdb_id=parameters.get("tmdbId"), tvdb_id=parameters.get("tvdbId"),
            imdb_id=parameters.get("imdbId"),
        )
    # 源配置先提交，别名和弹幕保存流程才能通过独立事务访问。
    db = get_database_service()
    async with db.transaction():
        await db.anime.update_metadata_if_empty(
            anime_id, tmdb_id=parameters.get("tmdbId"), imdb_id=parameters.get("imdbId"),
            tvdb_id=parameters.get("tvdbId"), douban_id=parameters.get("doubanId"),
            bangumi_id=parameters.get("bangumiId"),
            media_server_type=parameters.get("mediaServerType"),
            media_server_series_id=parameters.get("mediaServerSeriesId"),
            media_server_season_id=parameters.get("mediaServerSeasonId"),
        )
        source_id = await db.source.link_source_to_anime(anime_id, provider, media_id)
        if parameters.get("enable_incremental_refresh"):
            await db.source.update(source_id, incrementalRefreshEnabled=True)
    await fetch_and_save_aliases(
        anime_id, title, media_type, metadata_manager,
        tmdb_id=parameters.get("tmdbId"), year=year,
    )
    return anime_id, source_id, image_failed
