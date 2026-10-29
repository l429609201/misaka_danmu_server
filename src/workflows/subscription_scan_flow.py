"""订阅扫描产生的候选项导入流程。"""

from typing import Any, Dict

from src.services.service_container import get_database_service
from src.workflows.danmaku_import import save_danmaku_for_episode


async def persist_subscription_scan_result(
    target: Dict[str, Any], result: Dict[str, Any],
) -> int:
    """一次短事务落库扫描结果并推进目标的下次扫描时间。"""
    db = get_database_service()
    items = result.get("items") or []
    async with db.transaction():
        if result.get("mode") == "subscriptions":
            for item in items:
                await db.external_calendar.upsert_subscription_target(
                    provider=item["provider"], external_id=str(item["externalId"]),
                    title=item.get("title") or "",
                    subscription_type=item.get("subscriptionType") or "subject",
                    extra={**(item.get("extraData") or {}), "animeType": item.get("animeType", "tv_series")},
                    status=item.get("status") or "pending",
                )
            written = len(items)
        else:
            written = await db.subscription_candidate.upsert_candidates(
                target["id"], target["provider"], items,
            ) if items else 0
        await db.external_calendar.update_subscription_next_scan(
            target["provider"], target["externalId"],
        )
    return written


async def import_subscription_item(
    scraper: Any, item: Dict[str, Any],
    config_service: Any, title_recognition_manager: Any = None,
) -> None:
    """先拉取弹幕，再短事务建库，最后交由弹幕持久化流程写入文件。"""
    title = item.get("parentTitle") or item.get("animeTitle") or "未知订阅作品"
    media_type = item.get("mediaType") or "tv_series"
    season = int(item.get("season") or 1)
    episode_index = int(item.get("episodeIndex") or 1)
    provider = item.get("provider")
    parent_id = item.get("parentExternalId") or item.get("externalId")
    comments = await scraper.fetch_subscription_item_comments(item)
    if not comments:
        raise ValueError("未获取到弹幕")
    clean_media_id = parent_id.replace("collection:", "").replace("video:", "") if parent_id else parent_id
    episode_id = (item.get("externalId") or "").replace("video:", "").replace("collection:", "")
    db = get_database_service()
    # 标题识别可能调用外部能力，不在数据库事务中执行。
    if title_recognition_manager is not None:
        title, season, _, _, _ = await title_recognition_manager.apply_storage_postprocessing(
            title, season, None,
        )
    async with db.transaction():
        existing = await db.anime.find_by_title_season_year(title, season, item.get("year"))
        if existing:
            anime_id = existing["id"]
        else:
            anime = await db.anime.create_with_metadata(
                title, media_type, season, item.get("year"), item.get("imageUrl"),
            )
            anime_id = anime.id
        source_id = await db.source.link_source_to_anime(anime_id, provider, clean_media_id)
        episode_db_id = await db.episode.create_episode_if_not_exists(
            anime_id, source_id, episode_index,
            item.get("animeTitle") or f"第{episode_index}集", None, episode_id,
        )
    await save_danmaku_for_episode(episode_db_id, comments, config_service)
