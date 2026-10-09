"""
弹幕预下载编排层

负责预下载下一集弹幕的业务逻辑编排。
"""

import asyncio
import logging
import time
from typing import Optional

from fastapi import HTTPException

from src.services.scraper_manager import ScraperManager
from src.services.task_manager import TaskManager
from src.utils.diagnostics.task_exceptions import TaskSuccess, TaskFailed
from src.services.service_container import get_database_service
from src.services.config_service import ConfigService
from src.rate_limiter import RateLimiter
from src.utils.parsing.episode_filter import get_and_apply_single_episode_filter
from src.schemas.import_schemas import DanmakuEpisodeCreate
from src.workflows.danmaku_import import save_danmaku_for_episode

from src.workflows.supplement_episodes import get_episodes_routed

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


async def predownload_next_episode_flow(
    current_episode_id: int,
    config_service: ConfigService,
    task_manager: TaskManager,
    scraper_manager: ScraperManager,
    rate_limiter: RateLimiter,
    title_recognition_manager = None,
) -> Optional[int]:
    """
    预下载下一集弹幕的编排流程

    触发条件:
    1. preDownloadNextEpisodeEnabled = true
    2. matchFallbackEnabled = true 或 searchFallbackEnabled = true
    3. 下一集没有弹幕(无论是否存在记录)
    4. 没有正在运行的下载任务

    Args:
        current_episode_id: 当前分集 ID
        config_service: 配置服务
        task_manager: 任务管理器
        scraper_manager: 弹幕源管理器
        rate_limiter: 流控管理器
        title_recognition_manager: 标题识别管理器（可选）

    Returns:
        当前分集 ID，如果成功提交预下载任务；否则 None
    """
    try:
        # 1. 检查配置: 是否启用预下载
        predownload_enabled = (await config_service.get("preDownloadNextEpisodeEnabled", "false")).lower() == 'true'
        if not predownload_enabled:
            logger.info(f"预下载跳过: 未启用预下载功能 (episodeId={current_episode_id})")
            return None

        # 2. 检查配置: 是否启用后备机制
        match_fallback_enabled = (await config_service.get("matchFallbackEnabled", "false")).lower() == 'true'
        search_fallback_enabled = (await config_service.get("searchFallbackEnabled", "false")).lower() == 'true'

        if not match_fallback_enabled and not search_fallback_enabled:
            logger.info(f"预下载跳过: 未启用任何后备机制 (episodeId={current_episode_id})")
            return None

        logger.info(f"预下载检查开始: episodeId={current_episode_id}")

        # 3. 通过 DatabaseService 查询数据
        db = get_database_service()
        async with db.transaction():
            # 4. 查询当前分集信息
            current_episode = await db.episode.get_by_id(current_episode_id)

            if not current_episode:
                logger.warning(f"预下载跳过: 当前分集 {current_episode_id} 不存在")
                return None

            # 5. 获取source信息(需要provider和mediaId)
            source = await db.source.get_by_id(current_episode.sourceId)

            if not source:
                logger.warning(f"预下载跳过: 当前分集的源 {current_episode.sourceId} 不存在")
                return None

            if source.isFinished:
                logger.info(f"预下载跳过: 源 '{source.providerName}' 已标记完结 (sourceId={source.id})")
                return None

            # 6. 查询下一集
            next_episode_index = current_episode.episodeIndex + 1
            next_episode = await db.episode.get_by_source_and_index(
                current_episode.sourceId, next_episode_index
            )

            # 7. 如果下一集已存在且有弹幕,跳过
            if next_episode and next_episode.commentCount > 0:
                logger.info(f"预下载跳过: 下一集 {next_episode.id} 已有 {next_episode.commentCount} 条弹幕")
                return None

            # 8. 准备下载参数
            provider = source.providerName
            media_id = source.mediaId
            anime_id = source.animeId

            # 获取anime信息
            anime = await db.anime.get_by_id(anime_id)

            if not anime:
                logger.warning(f"预下载跳过: anime {anime_id} 不存在")
                return None

        # 9. 检查是否已有相同的预下载任务正在执行
        unique_key = f"predownload-{anime_id}-{next_episode_index}"

        try:
            # 10. 提交预下载任务
            async def predownload_task(_session, progress_callback):
                """预下载任务的具体执行逻辑；会话参数用于遵循任务管理器统一签名。"""
                # 任务管理器会注入会话；本流程的数据库写入由统一编排事务管理。
                del _session
                try:
                    await progress_callback(10, f"正在获取分集列表...")

                    # 获取分集列表
                    episodes = await get_episodes_routed(scraper_manager, provider, media_id)

                    # 应用单剧过滤规则
                    if episodes and anime and anime.title:
                        episodes = await get_and_apply_single_episode_filter(
                            episodes, config_service, anime.title, provider, media_id
                        )

                    if not episodes or len(episodes) == 0:
                        logger.warning(f"预下载失败: 无法获取分集列表 (provider={provider}, mediaId={media_id})")
                        raise TaskFailed("无法获取分集列表")

                    # 查找下一集
                    # 如果有 partial_offset 规则，需要反向偏移到源站实际集数再查找
                    source_episode_index = next_episode_index
                    if title_recognition_manager and anime and anime.title:
                        try:
                            source_episode_index = await title_recognition_manager.reverse_episode_offset(
                                anime.title, next_episode_index, provider
                            )
                            if source_episode_index != next_episode_index:
                                logger.info(f"预下载反向偏移: '{anime.title}' 存储第{next_episode_index}集 => 源站第{source_episode_index}集")
                        except Exception as e:
                            logger.warning(f"预下载反向偏移失败，使用原始集数: {e}")
                            source_episode_index = next_episode_index

                    target_episode = None
                    for ep in episodes:
                        if ep.episodeIndex == source_episode_index:
                            target_episode = ep
                            break

                    if not target_episode:
                        logger.info(f"预下载跳过: 源站没有第 {source_episode_index} 集 (provider={provider}, mediaId={media_id})")
                        raise TaskSuccess(f"源站没有第 {source_episode_index} 集")

                    provider_episode_id = target_episode.episodeId
                    episode_title = target_episode.title

                    logger.info(f"预下载: 找到下一集 '{episode_title}' (provider_episode_id={provider_episode_id})")

                    await progress_callback(30, f"正在下载弹幕: {episode_title}...")

                    # 预下载使用后备流控（不消耗全局配额）
                    await rate_limiter.check_fallback("search", provider)

                    # 下载弹幕
                    scraper = scraper_manager.get_scraper(provider)
                    # 下载前占用搜索后备池，失败不退额，避免并发检查后超额。
                    comments = await scraper.get_comments(
                        provider_episode_id, pool="search",
                        progress_callback=lambda p, msg: progress_callback(30 + int(p * 0.6), msg)
                    )

                    if not comments or len(comments) == 0:
                        logger.warning(f"预下载: 第 {next_episode_index} 集没有弹幕")
                        raise TaskFailed("未找到弹幕")

                    logger.info(f"预下载: 获取到 {len(comments)} 条弹幕")

                    await progress_callback(90, "正在保存弹幕...")

                    # 建集与保存交由统一 Workflow 持锁提交，不再调用已移除的仓储保存方法。
                    added_count = await save_danmaku_for_episode(
                        None, comments, config_service,
                        create_episode=DanmakuEpisodeCreate(
                            anime_id=anime_id, source_id=source.id,
                            episode_index=next_episode_index, title=episode_title,
                            url=target_episode.url, provider_episode_id=provider_episode_id,
                        ),
                    )

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
                queue_type="fallback",  # 预下载使用后备队列
                # 结构化参数：供预下载完成通知渲染「作品名/季集/弹幕源」结构块 + 海报
                task_parameters={
                    "anime_title": anime.title,
                    "season": anime.season,
                    "episode": next_episode_index,
                    "provider": provider,
                    "year": anime.year,
                    "imageUrl": anime.imageUrl or "",
                    "is_movie": (anime.type == "movie"),
                },
            )
            logger.info(f"✓ 预下载任务已提交: anime='{anime.title}', index={next_episode_index}, taskId={task_id}")
            return current_episode_id

        except HTTPException as e:
            if e.status_code == 409:
                logger.info(f"预下载跳过: 任务已在运行中 (unique_key={unique_key})")
            else:
                logger.warning(f"预下载任务提交失败 (HTTP {e.status_code}): {e.detail}")
            return None
        except Exception as e:
            logger.warning(f"预下载任务提交失败: {e}", exc_info=True)
            return None

    except Exception as e:
        logger.error(f"预下载处理异常 (episodeId={current_episode_id}): {e}", exc_info=True)
        return None
