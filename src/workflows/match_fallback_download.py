"""匹配后备冷启动编排：下载、身份创建、保存与连续播放缓存。"""
import logging
from typing import Any, Callable, Dict

from src.core import get_now
from src.schemas.import_schemas import DanmakuEpisodeCreate
from src.services.service_container import get_database_service
from src.utils import parse_search_keyword
from src.utils.fallback.cache import (
    write_episode_comment_cache, write_series_fallback_cache, write_match_season_cache,
)
from src.workflows.anime_identity import create_preassigned_anime
from src.workflows.danmaku_import import save_danmaku_for_episode

logger = logging.getLogger(__name__)


async def execute_match_fallback_download(
    parameters: Dict[str, Any], progress_callback: Callable,
    scraper_manager: Any, rate_limiter: Any, config_service: Any,
) -> str:
    """下载成功才建库；身份事务提交后使用统一持锁弹幕保存流程。"""
    if scraper_manager is None or rate_limiter is None:
        raise ValueError("依赖未注入：scraper_manager / rate_limiter 不能为空")
    provider = parameters["provider"]
    scraper = scraper_manager.get_scraper(provider)
    if not scraper:
        raise ValueError(f"未找到弹幕源: {provider}")
    await progress_callback(10, "开始下载弹幕...")
    await rate_limiter.check_fallback("match", provider)
    actual_id = parameters["provider_episode_id"]
    if actual_id and actual_id.startswith("http"):
        try:
            parsed_id = await scraper.get_id_from_url(actual_id)
            if parsed_id:
                actual_id = scraper.format_episode_id_for_comments(parsed_id)
        except Exception as exc:
            logger.warning("URL 解析失败，尝试直接使用: %s", exc)
    comments = await scraper.get_comments(actual_id, progress_callback=progress_callback, pool="match")
    if not comments:
        return "未获取到弹幕，源站可能暂时不可用"
    await write_episode_comment_cache(parameters["episodeId"], comments)
    await progress_callback(60, "创建数据库条目...")
    anime_id = parameters["real_anime_id"]
    await create_preassigned_anime(
        anime_id, parameters["display_title"], parameters["media_type"],
        parameters["final_season"], parameters.get("imageUrl"), None, parameters.get("year"),
    )
    db = get_database_service()
    async with db.transaction():
        source_id = await db.source.link_source_to_anime(anime_id, provider, parameters["mediaId"])
        source_order = await db.fallback.get_or_predict_source_order(anime_id, provider, parameters["mediaId"])
    await progress_callback(80, "保存弹幕...")
    # 新建分集与文件保存原子协调，不再直接操作任务 session 或旧保存服务。
    added = await save_danmaku_for_episode(
        None, comments, config_service, fire_threshold=scraper.likes_fire_threshold, force=True,
        create_episode=DanmakuEpisodeCreate(
            anime_id=anime_id, source_id=source_id,
            episode_index=parameters["episode_number"], title=parameters["episode_title"],
            url=parameters["episode_url"], provider_episode_id=parameters["provider_episode_id"],
        ),
    )
    series_info = {
        "real_anime_id": anime_id, "provider": provider, "mediaId": parameters["mediaId"],
        "final_title": parameters["final_title"], "original_title": parameters["display_title"],
        "final_season": parameters["final_season"], "media_type": parameters["media_type"],
        "imageUrl": parameters.get("imageUrl"), "year": parameters.get("year"),
    }
    if parameters.get("total_episodes"):
        try:
            await write_series_fallback_cache(
                anime_id, source_order, {**series_info, "total_episodes": parameters["total_episodes"]},
            )
        except Exception as exc:
            logger.warning("写入整季基准缓存失败: %s", exc)
    cache_key = parameters.get("fallback_episode_cache_key")
    if cache_key:
        try:
            async with db.transaction():
                await db.cache.delete(f"fallback_search_{cache_key}")
        except Exception as exc:
            logger.warning("清理 fallback_search 缓存失败: %s", exc)
    if parameters["media_type"] != "movie":
        try:
            pure_title = parse_search_keyword(parameters["final_title"])["title"]
            await write_match_season_cache(
                pure_title, parameters["final_season"],
                {**series_info, "source_order": source_order, "timestamp": get_now().timestamp()},
            )
        except Exception as exc:
            logger.warning("写入整季匹配缓存失败: %s", exc)
    await progress_callback(100, "完成")
    return f"后备下载完成，共获取 {added} 条弹幕"
