"""媒体服务删除联动编排：自持事务并保护弹幕文件共享引用。"""

import logging
from pathlib import Path
from typing import Any, Optional

from src.services.config_service import ConfigService
from src.services.file_storage_service import get_file_storage_service, wait_for_settlement
from src.services.service_container import get_database_service
from src.workflows.danmaku_paths import cleanup_unreferenced_paths

logger = logging.getLogger(__name__)


async def handle_webhook_delete_flow(
    config_service: ConfigService,
    server_type: str,
    item_type: str,
    item_id: str,
    series_id: Optional[str] = None,
    season_id: Optional[str] = None,
    season_number: Optional[int] = None,
    title: Optional[str] = None,
) -> None:
    """处理媒体服务删除事件，数据库事务与文件收尾均由本流程自持。"""
    del season_number

    enabled = (await config_service.get("webhookDeleteSyncEnabled", "false")).lower() == "true"
    if not enabled:
        logger.info("Webhook 删除联动已禁用，忽略 %s 的 %s 删除事件（ItemId=%s）", server_type, item_type, item_id)
        return

    server_type = (server_type or "").strip().lower()
    display_title = title or item_id
    fs = get_file_storage_service()
    db = get_database_service()
    outcome = db.TransactionOutcome()
    paths: set[Path] = set()
    deleted_episode_ids: list[int] = []

    async with fs.danmaku_mutation():
        try:
            async with db.transaction(outcome=outcome):
                if item_type == "Episode":
                    episode = await db.episode.find_by_media_server_episode(server_type, item_id)
                    if episode is None:
                        logger.info("Webhook 删除联动：未找到分集 %s，跳过", item_id)
                    else:
                        _collect_path(fs, episode.danmakuFilePath, paths)
                        deleted_episode_ids.append(episode.id)
                        await db.episode.delete(episode.id)
                elif item_type == "Season":
                    anime_ids = await db.anime.find_anime_ids_by_media_server(
                        server_type, season_id=season_id or item_id,
                    )
                    await _delete_animes(db, fs, anime_ids, paths, deleted_episode_ids)
                elif item_type in ("Series", "Movie"):
                    anime_ids = await db.anime.find_anime_ids_by_media_server(
                        server_type, series_id=series_id or item_id,
                    )
                    await _delete_animes(db, fs, anime_ids, paths, deleted_episode_ids)
                else:
                    logger.info("Webhook 删除联动：忽略未知 Item 类型 %s", item_type)
        finally:
            if outcome.status == "committed":
                await wait_for_settlement(cleanup_unreferenced_paths(paths))

    logger.info(
        "Webhook 删除联动已完成：%s %s（%s），删除分集 %s 个",
        server_type, item_type, display_title, len(deleted_episode_ids),
    )


async def _delete_animes(
    db: Any,
    fs: Any,
    anime_ids: list[int],
    paths: set[Path],
    deleted_episode_ids: list[int],
) -> None:
    """收集作品下的分集路径后删除作品记录，级联关系由 ORM 配置处理。"""
    for anime_id in anime_ids:
        anime = await db.anime.get_by_id(anime_id)
        if anime is None:
            continue
        for source_ref in anime.sources:
            # 作品查询仅预加载源，显式加载分集以避免异步 ORM 隐式 I/O。
            source = await db.source.get_by_id(source_ref.id)
            if source is None:
                continue
            for episode in source.episodes:
                _collect_path(fs, episode.danmakuFilePath, paths)
                deleted_episode_ids.append(episode.id)
        await db.anime.delete(anime_id)


def _collect_path(fs: Any, web_path: Optional[str], paths: set[Path]) -> None:
    """解析可识别的弹幕路径；无法解析时不冒险删除物理文件。"""
    if not web_path:
        return
    path = fs.resolve_fs_path(web_path)
    if path is None:
        logger.warning("Webhook 弹幕路径无法解析，保留文件：%s", web_path)
        return
    paths.add(path)
