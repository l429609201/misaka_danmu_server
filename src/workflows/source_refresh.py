"""全量刷新编排：只读取已入库分集，不依赖源站分集列表。"""
import asyncio
import logging
from typing import Any, Callable

from src.rate_limiter import RateLimitExceededError
from src.schemas import ProviderEpisodeInfo
from src.services.service_container import get_database_service
from src.utils.diagnostics.task_exceptions import TaskFailed
from src.workflows.danmaku_import import save_danmaku_for_episode
from src.workflows.episode_download import download_episode_comments_concurrent

logger = logging.getLogger(__name__)


async def refresh_stored_source(
    source_id: int, manager: Any, rate_limiter: Any,
    progress_callback: Callable, config_service: Any,
) -> str:
    """刷新已有源的全部分集，保持空响应和弹幕未增加时的原有策略。"""
    db = get_database_service()
    async with db.transaction():
        source = await db.source.get_anime_source_info(source_id)
    if not source:
        raise ValueError(f"找不到源ID {source_id} 的信息。")
    provider = source["providerName"]
    scraper = manager.get_scraper(provider)
    await progress_callback(5, "正在获取已存储的分集列表...")
    async with db.transaction():
        stored = await db.episode.get_all(source_id=source_id)
        # 在事务内冻结字段，下载阶段不依赖已脱离会话的 ORM 对象。
        episodes = [
            (ep.id, ep.episodeIndex, ep.providerEpisodeId, ep.title, ep.sourceUrl)
            for ep in stored
        ]
    if not episodes:
        raise TaskFailed("刷新失败：该源没有已存储的分集。请先导入分集。")
    added_total = 0
    succeeded, skipped, failures = [], [], {}
    total = len(episodes)
    for position, (episode_id, index, provider_id, title, url) in enumerate(episodes):
        progress = 5 + int(position / total * 90)
        await progress_callback(progress, f"正在刷新第 {index} 集 ({position + 1}/{total})...")
        if not provider_id:
            failures[index] = "缺少源站分集ID"
            continue
        while True:
            try:
                # 保留既有前置流控检查，下载器仍按自身契约处理配额。
                await rate_limiter.check(scraper.provider_name)
                episode = ProviderEpisodeInfo(
                    provider=provider, episodeIndex=index,
                    title=title or f"第{index}集", episodeId=provider_id, url=url or "",
                )
                results = await download_episode_comments_concurrent(
                    scraper, [episode], rate_limiter,
                    lambda _percent, description: progress_callback(progress, description),
                )
                comments = results[0][1] if results else None
                if not comments:
                    async with db.transaction():
                        await db.episode.update_fetch_time(episode_id)
                    skipped.append(index)
                    break
                added = await save_danmaku_for_episode(
                    episode_id, comments, config_service, update_fetch_time_on_skip=True,
                )
                if added > 0:
                    added_total += added
                    succeeded.append(index)
                else:
                    skipped.append(index)
                await asyncio.sleep(0.1)
                break
            except RateLimitExceededError as exc:
                await progress_callback(
                    progress, f"流控等待中，{exc.retry_after_seconds:.0f} 秒后继续 ({position + 1}/{total})...",
                )
                await asyncio.sleep(exc.retry_after_seconds + 1)
            except Exception as exc:
                logger.error("分集 %s (第%s集) 刷新失败: %s", episode_id, index, exc, exc_info=True)
                text = str(exc)
                failures[index] = text[:50] + "..." if len(text) > 50 else text
                break
    await progress_callback(98, "正在生成刷新报告...")
    comment_part = f"共获取 {added_total} 条弹幕" if added_total else "暂无新弹幕"
    message = f"全量刷新完成，处理了 {total} 个分集，{comment_part}。"
    if succeeded:
        message += f"\n成功刷新: {len(succeeded)} 集"
    if skipped:
        message += f"\n跳过(弹幕未增加): {len(skipped)} 集"
    if failures:
        details = [f"第{index}集: {text}" for index, text in sorted(failures.items())]
        message += f"\n失败 {len(failures)} 集:\n" + "\n".join(details[:10])
        if len(details) > 10:
            message += f"\n... 还有 {len(details) - 10} 条失败记录"
    return message
