"""手动单集导入：解析自定义内容或 URL 并原子保存弹幕。"""
import logging
from typing import Any, Callable, Optional

from src.schemas.import_schemas import DanmakuEpisodeCreate
from src.utils import clean_xml_string
from src.utils.danmaku.custom_xml import convert_text_danmaku_to_xml, parse_xml_content
from src.workflows.danmaku_import import save_danmaku_for_episode

logger = logging.getLogger(__name__)


async def execute_manual_import(
    source_id: int, anime_id: int, title: Optional[str], episode_index: int,
    content: str, provider: str, progress_callback: Callable,
    manager: Any, rate_limiter: Any, config_service: Any,
    scraper_provider: Optional[str],
) -> str:
    """完成单集抓取与保存，返回文案；任务入口负责暂停与成功状态。"""
    await progress_callback(10, "正在准备导入...")
    custom_xml = provider == "custom" and not scraper_provider
    if custom_xml:
        text = content.strip()
        if not text.startswith("<"):
            text = convert_text_danmaku_to_xml(text)
        await progress_callback(20, "正在解析XML文件...")
        comments = parse_xml_content(clean_xml_string(text))
        if not comments:
            return "未从XML中解析出任何弹幕。"
        final_title = title or f"第 {episode_index} 集"
        source_url = "from_xml"
        episode_id = "custom_xml"
        await progress_callback(80, "正在写入数据库...")
    else:
        effective_provider = scraper_provider or provider
        scraper = manager.get_scraper(effective_provider)
        if not hasattr(scraper, "get_id_from_url"):
            raise NotImplementedError(f"搜索源 '{effective_provider}' 不支持从URL手动导入。")
        provider_id = await scraper.get_id_from_url(content)
        if not provider_id:
            raise ValueError(f"无法从URL '{content}' 中解析出有效的视频ID。")
        episode_id = scraper.format_episode_id_for_comments(provider_id)
        await progress_callback(20, f"已解析视频ID: {episode_id}")
        final_title = title
        if not final_title and hasattr(scraper, "get_title_from_url"):
            try:
                final_title = await scraper.get_title_from_url(content)
            except Exception:
                # 标题获取失败不阻断弹幕导入，使用确定的集号标题。
                logger.debug("URL 标题获取失败，使用分集默认标题", exc_info=True)
        final_title = final_title or f"第 {episode_index} 集"
        await rate_limiter.check(effective_provider)
        comments = await scraper.get_comments(episode_id, progress_callback=progress_callback)
        if not comments:
            return "未找到任何弹幕。"
        source_url = content
        await progress_callback(90, "正在写入数据库...")
    # 网络和解析结束后才进入保存事务，不借用任务会话。
    added = await save_danmaku_for_episode(
        None, comments, config_service, fire_threshold=10, force=True,
        create_episode=DanmakuEpisodeCreate(
            anime_id=anime_id, source_id=source_id, episode_index=episode_index,
            title=final_title, url=source_url, provider_episode_id=episode_id,
        ),
    )
    if custom_xml:
        return (f"手动导入完成，从XML共获取 {added} 条弹幕。"
                if added > 0 else "手动导入完成，XML中暂无有效弹幕。")
    return f"手动导入完成，共获取 {added} 条弹幕。" if added > 0 else "手动导入完成，暂无弹幕数据。"
