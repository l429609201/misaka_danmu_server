"""本地弹幕导入执行编排，验证文件后复用统一建库与弹幕保存流程。"""
import asyncio
from pathlib import Path
from typing import Any, Dict

from src.services.service_container import get_database_service
from src.utils.danmaku.custom_xml import parse_xml_content
from src.workflows.anime_identity import get_or_create_anime_with_deduplication
from src.workflows.danmaku_import import save_danmaku_for_episode


async def import_local_item(item_id: int, options: Dict[str, Any]) -> int:
    """导入一个本地文件，仅在弹幕保存成功后标记本地项。"""
    db = get_database_service()
    async with db.transaction():
        item = await db.local_danmaku.get_by_id(item_id)
        if item is None:
            raise ValueError(f"本地弹幕项不存在: {item_id}")
        snapshot = {name: getattr(item, name) for name in (
            "filePath", "title", "mediaType", "season", "episode", "year",
            "tmdbId", "tvdbId", "imdbId",
        )}
    path = Path(snapshot["filePath"])
    content = await asyncio.to_thread(path.read_text, encoding="utf-8-sig")
    comments = await asyncio.to_thread(parse_xml_content, content)
    if not comments:
        raise ValueError(f"弹幕文件为空或解析失败: {path}")
    provider = options.get("provider", "custom")
    media_id = options.get("mediaId") or f"local_{item_id}"
    anime_id = await get_or_create_anime_with_deduplication(
        provider=provider, mediaId=media_id, title=snapshot["title"],
        mediaType=snapshot["mediaType"], season=snapshot["season"] or 1,
        imageUrl=None, local_image_path=None, year=snapshot["year"],
        title_recognition_manager=None, tmdb_id=snapshot["tmdbId"],
        tvdb_id=snapshot["tvdbId"], imdb_id=snapshot["imdbId"],
    )
    index = snapshot["episode"] or 1
    async with db.transaction():
        source_id = await db.source.link_source_to_anime(anime_id, provider, media_id)
        episode_id = await db.episode.create_episode_if_not_exists(
            anime_id=anime_id, source_id=source_id, episode_index=index,
            title=f"第{index}集", url=None, provider_episode_id=f"local_{item_id}_{index}",
        )
    # 通过统一保存入口持锁写入，不能绕过补偿机制直接修改文件路径。
    added = await save_danmaku_for_episode(
        episode_id, comments, force=bool(options.get("overwrite", False)),
    )
    async with db.transaction():
        await db.local_danmaku.mark_as_imported(item_id)
    return added
