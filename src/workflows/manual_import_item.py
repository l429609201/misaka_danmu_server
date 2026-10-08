"""批量手动导入单条业务，重试与进度由任务入口管理。"""
from typing import Any, Optional, Tuple

from src.schemas import BatchManualImportItem
from src.schemas.import_schemas import DanmakuEpisodeCreate
from src.utils import clean_xml_string
from src.utils.danmaku.custom_xml import convert_text_danmaku_to_xml, parse_xml_content
from src.workflows.danmaku_import import save_danmaku_for_episode


async def import_manual_item(
    source_id: int, anime_id: int, provider: str, item: BatchManualImportItem,
    title: str, manager: Any, rate_limiter: Any, scraper_provider: Optional[str],
) -> Tuple[int, bool, bool]:
    """返回新增数量、是否已存在及是否无弹幕，不吞掉下载或保存异常。"""
    if provider == "custom" and not scraper_provider:
        text = item.content.strip()
        if not text.startswith("<"):
            text = convert_text_danmaku_to_xml(text)
        comments = parse_xml_content(clean_xml_string(text))
        source_url = "from_xml_batch"
        episode_id = "custom_xml"
    else:
        effective_provider = scraper_provider if provider == "custom" and scraper_provider else provider
        scraper = manager.get_scraper(effective_provider)
        provider_id = await scraper.get_id_from_url(item.content)
        if not provider_id:
            raise ValueError("无法解析ID")
        episode_id = scraper.format_episode_id_for_comments(provider_id)
        await rate_limiter.check(effective_provider)
        comments = await scraper.get_comments(episode_id)
        source_url = item.content
    if not comments:
        return 0, False, True
    # 查重与写入共用保存层的锁，避免预查询后的并发重复建集。
    added = await save_danmaku_for_episode(
        None, comments, fire_threshold=10, force=True,
        create_episode=DanmakuEpisodeCreate(
            anime_id=anime_id, source_id=source_id, episode_index=item.episodeIndex,
            title=title, url=source_url, provider_episode_id=episode_id,
            skip_existing=provider == "custom",
        ),
    )
    return added, added == 0 and provider == "custom", False
