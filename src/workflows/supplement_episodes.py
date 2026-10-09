"""补充源分集编排：从元数据源播放链接构建弹幕源分集。"""
import logging
from typing import Any, Callable, List, Optional

from src.services.service_container import get_metadata_service
from src.utils.parsing.filename_parser import parse_supplement_media_id
from src.utils.parsing.episode_filter import apply_global_episode_title_filter

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


async def get_episodes_routed(
    scraper_manager: Any,
    provider: str,
    media_id: str,
    db_media_type: Optional[str] = None,
    target_episode_index: Optional[int] = None,
    return_filtered: bool = False,
) -> Any:
    """统一分集获取路由：自动识别补充源 mediaId 并路由到正确的数据源。

    如果 media_id 以 'sup_' 开头，转给对应补充源的 get_episode_urls()；
    否则走弹幕源的 get_episodes()。
    """
    logger = logging.getLogger(__name__)
    parsed = parse_supplement_media_id(media_id)

    if media_id.startswith("sup_") and not parsed:
        raise ValueError(f"补充源媒体 ID 格式无效: {media_id}")
    if parsed:
        metadata_manager = get_metadata_service()
        if not metadata_manager:
            raise ValueError("补充源管理器不可用，无法重新解析补充源分集")
        supplement_source_name, original_media_id, platform_key = parsed
        logger.info(f"补充源路由: {media_id} -> 补充源={supplement_source_name}, 媒体ID={original_media_id}, 平台={platform_key}")

        source = metadata_manager.sources.get(supplement_source_name)
        # 包含过滤明细时，即使提前结束也保持二元组契约，避免调用方解包失败。
        if not source:
            logger.warning(f"补充源 '{supplement_source_name}' 不可用")
            return ([], []) if return_filtered else []

        # 调用补充源获取分集URL
        episode_urls = await source.get_episode_urls(
            original_media_id, target_provider=provider, target_platform=platform_key,
        )
        if not episode_urls:
            logger.warning(f"补充源 '{supplement_source_name}' 未返回分集URL")
            return ([], []) if return_filtered else []

        logger.info(f"补充源 '{supplement_source_name}' 返回 {len(episode_urls)} 个分集URL")

        # 尝试用弹幕源解析URL为分集信息
        scraper = scraper_manager.get_scraper(provider)
        if not scraper:
            logger.warning(f"补充源路由: 弹幕源 '{provider}' 不可用，无法将分集URL解析为原生ID")
            return ([], []) if return_filtered else []
        episodes = []
        for idx, url in episode_urls:
            if target_episode_index is not None and idx != target_episode_index:
                continue
            try:
                raw_id = await scraper.get_id_from_url(url)
            except Exception as e:
                # 解析异常：跳过该集，不塞 URL 制造必然取不到弹幕的假分集
                logger.warning(f"补充源分集URL解析异常，跳过第{idx}集: url={url}, 原因={e}")
                continue

            if not raw_id:
                # 无法从 URL 解析出弹幕源原生ID：该集在此源下无法取弹幕，跳过
                logger.warning(f"补充源无法从URL解析出 {provider} 原生分集ID，跳过第{idx}集: url={url}")
                continue

            # 归一化为 get_comments 可解析的字符串（如 mgtv 的 "cid,vid"）。
            # get_id_from_url 可能返回 dict（mgtv={cid,vid}、bilibili={aid,cid}）或字符串，
            # 统一经 format_episode_id_for_comments 转成 get_comments 期望的字符串格式。
            episode_id = scraper.format_episode_id_for_comments(raw_id)
            episodes.append(ProviderEpisodeInfo(
                provider=provider,
                episodeId=episode_id,
                title=f"第{idx}集",
                episodeIndex=idx,
                url=url
            ))
        # 汇总日志（单条解析细节已降级为 DEBUG，此处统一打印一条 INFO 汇总，避免刷屏）
        logger.info(f"补充源URL解析完成: {provider} 成功解析 {len(episodes)}/{len(episode_urls)} 个分集为原生ID")
        # 兜底全局分集标题过滤（统一收口，对所有调用路径生效）
        return await apply_global_episode_title_filter(
            episodes, scraper_manager.config_service, provider, media_id,
            return_filtered=return_filtered,
        )
    else:
        # 普通弹幕源路径
        scraper = scraper_manager.get_scraper(provider)
        if not scraper:
            raise ValueError(f"弹幕源 '{provider}' 不可用")
        episodes = await scraper.get_episodes(
            media_id,
            target_episode_index=target_episode_index,
            db_media_type=db_media_type
        )
        # 弹幕源内部已完成自身黑名单过滤；编辑导入需要取回其过滤明细。
        filtered_key = (provider, str(media_id))
        source_filtered = list(getattr(scraper, '_last_logged_filtered_out', []))
        scraper._last_logged_filtered_out = []
        if source_filtered:
            scraper_manager._episode_filtered_details[filtered_key] = source_filtered
        elif return_filtered:
            source_filtered = list(scraper_manager._episode_filtered_details.get(filtered_key, []))
        # 兜底全局分集标题过滤（统一收口，对所有调用路径生效）
        global_result = await apply_global_episode_title_filter(
            episodes, scraper_manager.config_service, provider, media_id,
            return_filtered=return_filtered,
        )
        if not return_filtered:
            return global_result
        kept, global_filtered = global_result
        return kept, [*source_filtered, *global_filtered]
