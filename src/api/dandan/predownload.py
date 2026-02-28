"""
弹弹Play 兼容 API 的预下载功能

包含刷新任务等待和预下载下一集弹幕等功能。
"""

import asyncio
import logging
import time

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from fastapi import HTTPException

from src.db import crud, orm_models, ConfigManager
from src.services import ScraperManager, TaskManager, TaskSuccess
from src.rate_limiter import RateLimiter

logger = logging.getLogger(__name__)


async def wait_for_refresh_task(
    episode_id: int,
    task_manager: TaskManager,
    max_wait_seconds: float = 15.0
) -> bool:
    """
    等待指定分集的刷新任务完成

    Args:
        episode_id: 分集 ID
        task_manager: 任务管理器
        max_wait_seconds: 最大等待时间（秒）

    Returns:
        True 如果任务在等待期间完成，False 如果超时或无任务
    """
    unique_key = f"refresh-episode-{episode_id}"

    # 检查是否有刷新任务正在执行
    async with task_manager._lock:
        if unique_key not in task_manager._active_unique_keys:
            # 没有刷新任务
            return False
        logger.info(f"检测到分集 {episode_id} 正在刷新，等待最多 {max_wait_seconds} 秒")

    # 等待任务完成
    start_time = time.time()
    check_interval = 0.5  # 每0.5秒检查一次

    while time.time() - start_time < max_wait_seconds:
        await asyncio.sleep(check_interval)

        # 检查任务是否完成
        async with task_manager._lock:
            if unique_key not in task_manager._active_unique_keys:
                elapsed = time.time() - start_time
                logger.info(f"分集 {episode_id} 刷新任务在 {elapsed:.2f} 秒内完成")
                return True

    # 超时
    logger.warning(f"分集 {episode_id} 刷新任务等待超时（{max_wait_seconds}秒）")
    return False


async def try_predownload_next_episode(
    current_episode_id: int,
    session_factory,
    config_manager: ConfigManager,
    task_manager: TaskManager,
    scraper_manager: ScraperManager,
    rate_limiter: RateLimiter
):
    """
    尝试预下载下一集弹幕（异步，不阻塞当前请求）

    触发条件:
    1. preDownloadNextEpisodeEnabled = true
    2. matchFallbackEnabled = true 或 searchFallbackEnabled = true
    3. 下一集没有弹幕(无论是否存在记录)
    4. 没有正在运行的下载任务

    逻辑:
    - 如果下一集已有记录且有弹幕: 跳过
    - 如果下一集已有记录但无弹幕: 刷新弹幕
    - 如果下一集无记录: 从源站获取并创建记录+下载弹幕
    """
    try:
        # 1. 检查配置: 是否启用预下载
        predownload_enabled = (await config_manager.get("preDownloadNextEpisodeEnabled", "false")).lower() == 'true'
        if not predownload_enabled:
            logger.info(f"预下载跳过: 未启用预下载功能 (episodeId={current_episode_id})")
            return

        # 2. 检查配置: 是否启用后备机制
        match_fallback_enabled = (await config_manager.get("matchFallbackEnabled", "false")).lower() == 'true'
        search_fallback_enabled = (await config_manager.get("searchFallbackEnabled", "false")).lower() == 'true'

        if not match_fallback_enabled and not search_fallback_enabled:
            logger.info(f"预下载跳过: 未启用任何后备机制 (episodeId={current_episode_id}, matchFallback={match_fallback_enabled}, searchFallback={search_fallback_enabled})")
            return

        logger.info(f"预下载检查开始: episodeId={current_episode_id}, predownload={predownload_enabled}, matchFallback={match_fallback_enabled}, searchFallback={search_fallback_enabled}")

        # 3. 创建新的数据库会话 (避免与主请求的session冲突)
        async with session_factory() as session:
            # 4. 查询当前分集信息
            current_episode_stmt = select(orm_models.Episode).where(
                orm_models.Episode.id == current_episode_id
            )
            current_episode_result = await session.execute(current_episode_stmt)
            current_episode = current_episode_result.scalar_one_or_none()

            if not current_episode:
                logger.warning(f"预下载跳过: 当前分集 {current_episode_id} 不存在")
                return

            # 5. 获取source信息(需要provider和mediaId)
            source_stmt = select(orm_models.AnimeSource).where(
                orm_models.AnimeSource.id == current_episode.sourceId
            )
            source_result = await session.execute(source_stmt)
            source = source_result.scalar_one_or_none()

            if not source:
                logger.warning(f"预下载跳过: 当前分集的源 {current_episode.sourceId} 不存在")
                return

            if source.isFinished:
                logger.info(f"预下载跳过: 源 '{source.providerName}' 已标记完结 (sourceId={source.id})")
                return

            # 6. 查询下一集
            next_episode_index = current_episode.episodeIndex + 1
            next_episode_stmt = select(orm_models.Episode).where(
                orm_models.Episode.sourceId == current_episode.sourceId,
                orm_models.Episode.episodeIndex == next_episode_index
            )
            next_episode_result = await session.execute(next_episode_stmt)
            next_episode = next_episode_result.scalar_one_or_none()

            # 7. 如果下一集已存在且有弹幕,跳过
            if next_episode and next_episode.commentCount > 0:
                logger.info(f"预下载跳过: 下一集 {next_episode.id} 已有 {next_episode.commentCount} 条弹幕")
                return

            # 8. 准备下载参数
            provider = source.providerName
            media_id = source.mediaId
            anime_id = source.animeId

            # 获取anime信息
            anime_stmt = select(orm_models.Anime).where(orm_models.Anime.id == anime_id)
            anime_result = await session.execute(anime_stmt)
            anime = anime_result.scalar_one_or_none()

            if not anime:
                logger.warning(f"预下载跳过: anime {anime_id} 不存在")
                return

        # 9. 在session外提交预下载任务
        logger.info(f"预下载: 准备下载下一集 (index={next_episode_index}, provider={provider}, mediaId={media_id})")

        # 使用unique_key防止重复
        unique_key = f"predownload_{provider}_{media_id}_{next_episode_index}"

        try:
            # 创建下载任务
            async def predownload_task(session, progress_callback):
                """预下载任务: 获取分集信息并下载弹幕"""
                try:

                    await progress_callback(10, "正在获取分集列表...")

                    # 获取分集列表
                    logger.info(f"预下载: 正在获取分集列表 (provider={provider}, mediaId={media_id})")
                    scraper = scraper_manager.get_scraper(provider)
                    episodes = await scraper.get_episodes(media_id)

                    if not episodes or len(episodes) == 0:
                        logger.warning(f"预下载失败: 无法获取分集列表 (provider={provider}, mediaId={media_id})")
                        raise TaskSuccess("无法获取分集列表")

                    # 查找下一集
                    target_episode = None
                    for ep in episodes:
                        if ep.episodeIndex == next_episode_index:
                            target_episode = ep
                            break

                    if not target_episode:
                        logger.info(f"预下载跳过: 源站没有第 {next_episode_index} 集 (provider={provider}, mediaId={media_id})")
                        raise TaskSuccess(f"源站没有第 {next_episode_index} 集")

                    provider_episode_id = target_episode.episodeId
                    episode_title = target_episode.title

                    logger.info(f"预下载: 找到下一集 '{episode_title}' (provider_episode_id={provider_episode_id})")

                    await progress_callback(30, f"正在下载弹幕: {episode_title}...")

                    # 预下载使用后备流控（不消耗全局配额）
                    await rate_limiter.check_fallback("search", provider)

                    # 下载弹幕
                    comments = await scraper.get_comments(
                        provider_episode_id,
                        progress_callback=lambda p, msg: progress_callback(30 + int(p * 0.6), msg)
                    )

                    if not comments or len(comments) == 0:
                        logger.warning(f"预下载: 第 {next_episode_index} 集没有弹幕")
                        raise TaskSuccess("未找到弹幕")

                    await rate_limiter.increment_fallback("search", provider)

                    logger.info(f"预下载: 获取到 {len(comments)} 条弹幕")

                    await progress_callback(90, "正在保存弹幕...")

                    # 创建或获取Episode记录
                    episode_db_id = await crud.create_episode_if_not_exists(
                        session, anime_id, source.id, next_episode_index,
                        episode_title, target_episode.url, provider_episode_id
                    )

                    # 保存弹幕
                    added_count = await crud.save_danmaku_for_episode(
                        session, episode_db_id, comments, config_manager
                    )

                    await session.commit()

                    logger.info(f"✓ 预下载完成: '{episode_title}' (index={next_episode_index}, 新增{added_count}条弹幕)")
                    raise TaskSuccess(f"预下载完成，新增 {added_count} 条弹幕")

                except TaskSuccess:
                    raise
                except Exception as e:
                    logger.error(f"预下载任务失败: {e}", exc_info=True)
                    raise

            task_id, _ = await task_manager.submit_task(
                predownload_task,
                f"预下载弹幕: {anime.title} 第{next_episode_index}集",
                unique_key=unique_key,
                task_type="predownload",
                queue_type="fallback"  # 预下载使用后备队列
            )
            logger.info(f"✓ 预下载任务已提交: anime='{anime.title}', index={next_episode_index}, taskId={task_id}")

        except HTTPException as e:
            if e.status_code == 409:
                logger.info(f"预下载跳过: 任务已在运行中 (unique_key={unique_key})")
            else:
                logger.warning(f"预下载任务提交失败 (HTTP {e.status_code}): {e.detail}")
        except Exception as e:
            logger.warning(f"预下载任务提交失败: {e}", exc_info=True)

    except Exception as e:
        logger.error(f"预下载处理异常 (episodeId={current_episode_id}): {e}", exc_info=True)

    return current_episode_id

