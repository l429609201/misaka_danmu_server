"""补充源分集编排：从元数据源播放链接构建弹幕源分集。"""
import logging
from typing import Any, Callable, List

from src.schemas import ProviderEpisodeInfo

logger = logging.getLogger(__name__)


async def fetch_supplement_episodes(
    provider: str, supplement_provider: str, supplement_media_id: str,
    metadata_manager: Any, scraper: Any, progress_callback: Callable,
) -> List[ProviderEpisodeInfo]:
    """补充源不可用或链接解析失败时返回已有解析结果，不创建数据库记录。"""
    episodes: List[ProviderEpisodeInfo] = []
    await progress_callback(12, f"主源无分集,尝试使用补充源 {supplement_provider}...")
    try:
        source = metadata_manager.sources.get(supplement_provider)
        if not source or not getattr(source, "supports_episode_urls", False):
            logger.warning("补充源 %s 不可用或不支持分集URL获取", supplement_provider)
            return episodes
        details = await source.get_details(supplement_media_id, None)
        if not details:
            logger.warning("无法获取补充源详情 (mediaId=%s)", supplement_media_id)
            return episodes
        await progress_callback(12, f"正在从{supplement_provider}获取分集URL...")
        urls = await source.get_episode_urls(supplement_media_id, provider)
        await progress_callback(18, f"{supplement_provider}解析完成,获取到 {len(urls)} 个播放链接")
        for index, url in urls:
            try:
                episode_id = await scraper.get_id_from_url(url)
                if episode_id:
                    episodes.append(ProviderEpisodeInfo(
                        provider=provider, episodeId=episode_id,
                        title=f"第{index}集", episodeIndex=index, url=url,
                    ))
            except Exception as exc:
                logger.warning("解析URL失败 (第%s集): %s", index, exc)
        if urls:
            await progress_callback(20, f"补充源成功获取 {len(episodes)} 个分集")
    except Exception as exc:
        logger.error("使用补充源获取分集失败: %s", exc, exc_info=True)
    return episodes
