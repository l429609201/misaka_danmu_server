"""单集刷新业务：解析后备占位 ID、下载并持锁保存弹幕。"""
from typing import Any, Callable

from src.schemas import ProviderEpisodeInfo
from src.services.service_container import get_database_service
from src.utils.diagnostics.task_exceptions import TaskFailed
from src.workflows.danmaku_import import save_danmaku_for_episode
from src.workflows.episode_download import download_episode_comments_concurrent
from src.workflows.supplement_episodes import get_episodes_routed


async def refresh_one_episode(
    episode_id: int, manager: Any, rate_limiter: Any,
    progress_callback: Callable, config_service: Any, profiler: Any,
) -> str:
    """使用短事务解析源信息，空响应保留旧抓取时间以允许后续重试。"""
    await progress_callback(0, "正在获取分集信息...")
    db = get_database_service()
    async with db.transaction():
        info = await db.episode.get_episode_provider_info(episode_id)
    if not info or not info.get("providerName") or not info.get("providerEpisodeId"):
        raise TaskFailed(f"刷新失败：找不到分集 {episode_id} 的源信息")
    provider, provider_episode_id = info["providerName"], info["providerEpisodeId"]
    if provider_episode_id.startswith("fallback_"):
        provider, provider_separator, remainder = provider_episode_id[len("fallback_"):].partition("_")
        media_id, index_separator, index_text = remainder.rpartition("_")
        if not provider_separator or not index_separator or not provider or not media_id:
            raise TaskFailed(f"刷新失败：fallback ID 格式异常: {provider_episode_id}")
        try:
            index = int(index_text)
        except ValueError as exc:
            raise TaskFailed(f"刷新失败：fallback ID 集数格式异常: {provider_episode_id}") from exc
        scraper = manager.get_scraper(provider)
        if not scraper:
            raise TaskFailed("刷新失败：找不到对应的弹幕源")
        await progress_callback(15, "正在重新获取分集信息...")
        # 后备占位 ID 可能携带补充源坐标，必须保留并经过统一路由。
        episodes = await get_episodes_routed(manager, provider, media_id, target_episode_index=index)
        target = next((item for item in episodes or [] if item.episodeIndex == index), None)
        if target is None:
            raise TaskFailed(f"刷新失败：分集列表中未找到第 {index} 集")
        provider_episode_id = target.episodeId
        if not provider_episode_id or provider_episode_id.startswith("fallback_"):
            raise TaskFailed("刷新失败：无法从源站获取真实的分集ID")
        # 在下载和文件保存前提交源站 ID，避免跨网络请求持有写锁。
        async with db.transaction():
            await db.episode.update(episode_id, providerEpisodeId=provider_episode_id)
    scraper = manager.get_scraper(provider)
    if not scraper:
        raise TaskFailed("刷新失败：找不到对应的弹幕源")
    await progress_callback(30, "正在从源获取新弹幕...")
    episode = ProviderEpisodeInfo(
        provider=provider, episodeIndex=1, title=f"刷新分集 {episode_id}",
        episodeId=provider_episode_id, url="",
    )

    async def download_progress(percent: int, description: str) -> None:
        """将下载阶段进度映射到刷新进度区间。"""
        await progress_callback(30 + percent * 0.65, description)

    async with profiler.step("下载弹幕"):
        results = await download_episode_comments_concurrent(
            scraper, [episode], rate_limiter, download_progress,
        )
    comments = results[0][1] if results else None
    if not comments:
        raise TaskFailed("未找到任何弹幕。")
    await progress_callback(96, f"正在写入 {len(comments)} 条新弹幕...")
    async with profiler.step("写入XML文件"):
        added = await save_danmaku_for_episode(
            episode_id, comments, config_service,
            force=True, update_fetch_time_on_skip=True,
        )
    return f"刷新完成，写入 {added} 条弹幕。" if added > 0 else "刷新完成，该分集暂无新弹幕。"
