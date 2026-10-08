"""无分集列表时的单集故障转移导入编排。"""
from typing import Any, Callable, Dict

from src.schemas.import_schemas import DanmakuEpisodeCreate
from src.services.service_container import get_database_service
from src.workflows.alias_workflow import fetch_and_save_aliases
from src.workflows.anime_identity import get_or_create_anime_with_deduplication
from src.workflows.danmaku_import import save_danmaku_for_episode
from src.workflows.image_download import download_image
from src.workflows.import_candidates import ImportCandidateRejected


async def import_failover_episode(
    parameters: Dict[str, Any], scraper: Any, config_service: Any,
    metadata_manager: Any, title_recognition_manager: Any,
    progress_callback: Callable,
) -> str:
    """先验证弹幕再建库，保存成功后补充别名并返回完成文案。"""
    index = parameters["currentEpisodeIndex"]
    if not index:
        raise ImportCandidateRejected("未找到任何分集信息。")
    await progress_callback(15, "未找到分集列表，尝试故障转移...")
    comments = await scraper.get_comments(
        parameters["mediaId"],
        pool=parameters.get("fallback_type") if parameters.get("is_fallback") else "global",
        progress_callback=lambda p, msg: progress_callback(15 + p * 0.05, msg),
    )
    if not comments:
        raise ImportCandidateRejected(f"未能找到第 {index} 集。")
    await progress_callback(20, f"故障转移成功，找到 {len(comments)} 条弹幕。")
    provider = parameters["provider"]
    image_url = parameters.get("imageUrl")
    local_image = await download_image(image_url, provider_name=provider)
    image_failed = bool(image_url and not local_image)
    # 保留故障转移的查重语义，不套用普通导入的预分配身份分支。
    anime_id = await get_or_create_anime_with_deduplication(
        provider=provider, mediaId=parameters["mediaId"],
        title=parameters["animeTitle"], mediaType=parameters["mediaType"],
        season=parameters["season"], imageUrl=image_url,
        local_image_path=local_image, year=parameters.get("year"),
        title_recognition_manager=title_recognition_manager,
        tmdb_id=parameters.get("tmdbId"), tvdb_id=parameters.get("tvdbId"),
        imdb_id=parameters.get("imdbId"),
    )
    db = get_database_service()
    # 源配置须先提交，弹幕保存随后在自持事务中建集。
    async with db.transaction():
        await db.anime.update_metadata_if_empty(
            anime_id, tmdb_id=parameters.get("tmdbId"), imdb_id=parameters.get("imdbId"),
            tvdb_id=parameters.get("tvdbId"), douban_id=parameters.get("doubanId"),
            bangumi_id=parameters.get("bangumiId"),
            media_server_type=parameters.get("mediaServerType"),
            media_server_series_id=parameters.get("mediaServerSeriesId"),
            media_server_season_id=parameters.get("mediaServerSeasonId"),
        )
        source_id = await db.source.link_source_to_anime(anime_id, provider, parameters["mediaId"])
        if parameters.get("enable_incremental_refresh"):
            await db.source.update(source_id, incrementalRefreshEnabled=True)
    domains = getattr(scraper, "handled_domains", None) or []
    added = await save_danmaku_for_episode(
        episode_id=None, comments=comments, config_service=config_service, force=True,
        chat_server=domains[0] if domains else None,
        create_episode=DanmakuEpisodeCreate(
            anime_id=anime_id, source_id=source_id, episode_index=index,
            title=f"第 {index} 集", provider_episode_id="failover",
            media_server_episode_id=parameters.get("mediaServerEpisodeId"),
        ),
    )
    await fetch_and_save_aliases(
        anime_id, parameters["animeTitle"], parameters["mediaType"], metadata_manager,
        tmdb_id=parameters.get("tmdbId"), year=parameters.get("year"),
    )
    message = (f"通过故障转移导入完成，共获取 {added} 条弹幕。"
               if added > 0 else "通过故障转移导入完成，暂无弹幕数据。")
    return message + (" (警告：海报图片下载失败)" if image_failed else "")
