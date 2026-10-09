"""后备搜索详情：从搜索缓存恢复源坐标并建立弹幕读取映射。"""

import logging
from typing import Optional

from src.schemas.dandan import BangumiDetailsResponse, BangumiDetails, BangumiEpisode
from src.services.service_container import get_database_service, get_scraper_manager
from src.utils.dandan.constants import (
    FALLBACK_SEARCH_SESSION_PREFIX as FALLBACK_SEARCH_CACHE_PREFIX,
    FALLBACK_SEARCH_SESSION_TTL as FALLBACK_SEARCH_CACHE_TTL,
    DANDAN_TYPE_MAPPING, DANDAN_TYPE_DESC_MAPPING,
)
from src.utils.parsing.filename_parser import parse_search_keyword
from src.workflows.dandan.helpers import update_episode_mappings
from src.workflows.bangumi.helpers import generate_episode_id

from src.workflows.supplement_episodes import get_episodes_routed

logger = logging.getLogger(__name__)


async def get_fallback_bangumi_details(bangumi_id: str) -> BangumiDetailsResponse:
    """按搜索缓存中的 bangumiId 查找源，并返回可用于后备下载的分集 ID。"""
    db = get_database_service()
    mapping: Optional[dict] = None
    search_info: Optional[dict] = None
    matched_key: Optional[str] = None
    # 一次读取全部有效搜索映射，在内存中定位，避免切源时逐键查询。
    async with db.transaction():
        search_caches = await db.cache.get_json_by_prefix(f"{FALLBACK_SEARCH_CACHE_PREFIX}:search_")
        for key, info in search_caches.items():
            if not isinstance(info, dict):
                continue
            candidate = info.get("bangumi_mapping", {}).get(bangumi_id)
            if not isinstance(candidate, dict):
                continue
            if mapping is not None:
                # 旧编号曾跨搜索重复，无法确定来源时拒绝猜测，避免返回另一部作品。
                return BangumiDetailsResponse(
                    success=False, bangumi=None, errorMessage="旧搜索结果编号重复，请重新搜索"
                )
            mapping, search_info, matched_key = candidate, info, key

    if mapping is None:
        logger.warning("后备详情未命中搜索映射: bangumiId=%s, 搜索缓存数=%d", bangumi_id, len(search_caches))
        return BangumiDetailsResponse(
            success=False, bangumi=None, errorMessage="搜索结果不存在或已过期"
        )

    provider = mapping["provider"]
    media_id = mapping["media_id"]
    title = mapping.get("original_title") or "未知作品"
    parsed = parse_search_keyword(title)
    season = (search_info.get("parsed_info") or {}).get("season")
    if season is None:
        season = mapping.get("season")
    if season is None:
        season = parsed.get("season") or 1
    media_type = mapping.get("type") or "tv_series"
    scraper_manager = get_scraper_manager()
    logger.info("后备详情命中搜索映射: bangumiId=%s, provider=%s, mediaId=%s", bangumi_id, provider, media_id)
    try:
        actual_episodes = await get_episodes_routed(scraper_manager,
            provider, media_id, db_media_type=media_type
        )
    except Exception:
        logger.exception("后备搜索详情获取分集失败: %s", bangumi_id)
        return BangumiDetailsResponse(success=False, bangumi=None, errorMessage="获取源站分集失败，请稍后重试")
    logger.info("后备详情源站分集结果: bangumiId=%s, 分集数=%d", bangumi_id, len(actual_episodes or []))
    if not actual_episodes:
        return BangumiDetailsResponse(success=False, bangumi=None, errorMessage="源站未返回分集")

    # 用同一个持久化计数器锁串行化编号租约，重读映射以避免并发详情分配不同编号。
    async with db.transaction():
        reserved_id = await db.fallback.get_next_real_anime_id()
        latest_info = await db.cache.get_json(matched_key)
        latest_mapping = (latest_info or {}).get("bangumi_mapping", {}).get(bangumi_id)
        if not latest_mapping:
            return BangumiDetailsResponse(success=False, bangumi=None, errorMessage="搜索结果已过期，请重新搜索")
        real_id = latest_mapping.get("real_anime_id")
        if not real_id:
            existing_id = await db.source.get_anime_id_by_source_media_id(provider, media_id, season)
            real_id = existing_id or await db.fallback.find_anime_by_title_season(
                title, parsed.get("title") or title, season
            )
        real_id = real_id or reserved_id
        source_order = await db.fallback.get_or_predict_source_order(real_id, provider, media_id)
        latest_mapping["real_anime_id"] = real_id
        await db.cache.set_json(matched_key, latest_info, FALLBACK_SEARCH_CACHE_TTL)

    # 编号租约提交后再发布整批映射，避免缓存先可见而数据库事务尚未提交。
    series_base = 25000000000000 + real_id * 1000000 + source_order * 10000
    await update_episode_mappings(
        {generate_episode_id(real_id, source_order, ep.episodeIndex): ep.episodeIndex
         for ep in actual_episodes},
        provider, media_id, title, season=season,
        extra_entries={f"fallback_episode_{series_base}": ({
            "real_anime_id": real_id, "provider": provider, "mediaId": media_id,
            "final_title": parsed.get("title") or title, "original_title": title,
            "final_season": season, "media_type": media_type,
            "imageUrl": mapping.get("image_url"), "year": mapping.get("year"),
        }, 10800)},
    )

    return BangumiDetailsResponse(bangumi=BangumiDetails(
        animeId=real_id, bangumiId=bangumi_id,
        animeTitle=f"{title} （来源：{provider}）",
        imageUrl=mapping.get("image_url"), searchKeyword=title,
        type=DANDAN_TYPE_MAPPING.get(media_type, "other"),
        typeDescription=DANDAN_TYPE_DESC_MAPPING.get(media_type, "其他"),
        year=mapping.get("year"), summary=f"来自后备搜索的结果 (源: {provider})",
        episodes=[BangumiEpisode(
            episodeId=generate_episode_id(real_id, source_order, ep.episodeIndex),
            episodeTitle=ep.title, episodeNumber=str(ep.episodeIndex),
        ) for ep in actual_episodes],
    ))
