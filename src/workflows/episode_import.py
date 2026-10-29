"""逐集导入编排：协调下载、识别词偏移和弹幕保存。"""
import asyncio
import logging
from typing import Any, Callable, Dict, List, Optional, Tuple

from src.rate_limiter import RateLimiter, RateLimitExceededError
from src.schemas.import_schemas import DanmakuEpisodeCreate
from src.utils.diagnostics.error_message import extract_short_error_message
from src.workflows.danmaku_import import save_danmaku_for_episode
from src.workflows.episode_download import download_episode_comments_concurrent

logger = logging.getLogger(__name__)


async def import_episodes_iteratively(
    scraper: Any, rate_limiter: RateLimiter,
    progress_callback: Callable, episodes: List, anime_id: int, source_id: int,
    first_episode_comments: Optional[List] = None, config_service: Any = None,
    is_single_episode: bool = False, smart_refresh: bool = False,
    is_fallback: bool = False, fallback_type: Optional[str] = None,
    title_recognition_manager: Any = None, anime_title: Optional[str] = None,
    only_missing: bool = False,
) -> Tuple[int, List[int], List[int], int, Dict[int, str]]:
    """下载并逐集保存，保持首集复用、流控等待和部分成功统计语义。"""
    # 保存流程自持事务，不接收或借用任务会话。
    domains = getattr(scraper, 'handled_domains', [])
    chat_server = domains[0] if domains else None
    total_added = 0
    successful: List[int] = []
    skipped: List[int] = []
    failures: Dict[int, str] = {}
    failed_count = 0

    async def save_episode(episode: Any, comments: Optional[List]) -> None:
        nonlocal total_added, failed_count
        if not comments:
            failed_count += 1
            failures[episode.episodeIndex] = "获取弹幕失败" if comments is None else "获取弹幕为空"
            logger.warning(f"分集 '{episode.title}' {failures[episode.episodeIndex]}，不创建分集记录")
            return
        try:
            stored_index = episode.episodeIndex
            if title_recognition_manager and anime_title:
                _, _, _, _, converted = await title_recognition_manager.apply_storage_postprocessing(
                    anime_title, episode=episode.episodeIndex,
                )
                if converted is not None and converted != stored_index:
                    logger.info(f"部分集数偏移: '{anime_title}' 第{stored_index}集 => 第{converted}集")
                    stored_index = converted
            added = await save_danmaku_for_episode(
                episode_id=None, comments=comments, config_service=config_service,
                chat_server=chat_server,
                create_episode=DanmakuEpisodeCreate(
                    anime_id=anime_id, source_id=source_id, episode_index=stored_index,
                    title=episode.title, url=episode.url,
                    provider_episode_id=episode.episodeId, update_existing_title=False,
                    skip_existing=only_missing,
                ),
                # 补缺不能覆盖并发任务刚收录的集，普通导入仍按数量判断更新。
                import_if_increased=not only_missing,
            )
            if added == 0:
                skipped.append(stored_index)
                return
            total_added += added
            successful.append(stored_index)
            logger.info(f"分集 '{episode.title}' 写入 {added} 条弹幕并已提交")
        except Exception as exc:
            failed_count += 1
            failures[episode.episodeIndex] = f"写入数据库失败: {extract_short_error_message(exc)}"
            logger.error(f"分集 '{episode.title}' 写入数据库失败: {exc}", exc_info=True)

    if is_single_episode and len(episodes) == 1:
        results = await download_episode_comments_concurrent(
            scraper, episodes, rate_limiter, progress_callback,
            first_episode_comments, is_fallback, fallback_type,
        )
        await progress_callback(90, "正在写入数据库...")
        for index, comments in results:
            episode = next((item for item in episodes if item.episodeIndex == index), None)
            if episode is None:
                failed_count += 1
                failures[index] = "无法找到分集信息"
                logger.error(f"无法找到分集索引 {index} 对应的分集信息")
                continue
            await save_episode(episode, comments)
    else:
        for i, episode in enumerate(episodes):
            base_progress = 30 + i * 60 // len(episodes)
            await progress_callback(base_progress, f"正在处理分集: {episode.title}")
            try:
                if i == 0 and first_episode_comments is not None:
                    comments = first_episode_comments
                else:
                    # 全局额度等待保留原行为，单源额度不足向上传播以释放 worker。
                    while True:
                        try:
                            if is_fallback:
                                if not fallback_type:
                                    raise ValueError("后备任务必须指定fallback_type参数")
                                await rate_limiter.check_fallback(fallback_type, scraper.provider_name)
                            else:
                                await rate_limiter.check(scraper.provider_name)
                            break
                        except RateLimitExceededError as exc:
                            if "全局速率限制" not in str(exc):
                                raise
                            await progress_callback(
                                base_progress,
                                f"全局流控等待中，{exc.retry_after_seconds:.0f} 秒后继续 ({i + 1}/{len(episodes)})...",
                            )
                            await asyncio.sleep(exc.retry_after_seconds + 1)

                    async def sub_progress_callback(p: float, msg: str) -> None:
                        await progress_callback(base_progress + int(p * 0.6 / len(episodes)), msg)

                    comments = await scraper.get_comments(
                        episode.episodeId, progress_callback=sub_progress_callback,
                        pool=fallback_type if is_fallback else "global",
                    )
                await save_episode(episode, comments)
            except RateLimitExceededError:
                raise
            except Exception as exc:
                failed_count += 1
                if isinstance(exc, RuntimeError):
                    prefix = "配置错误" if "配置验证失败" in str(exc) else "运行时错误"
                    failures[episode.episodeIndex] = f"{prefix}: {exc}"
                else:
                    failures[episode.episodeIndex] = extract_short_error_message(exc)
                logger.error(f"处理分集 '{episode.title}' 时发生错误: {exc}", exc_info=True)
    return total_added, successful, skipped, failed_count, failures
