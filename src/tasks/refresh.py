"""刷新任务模块"""
import logging
import asyncio
from typing import Callable, List
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.db import crud, orm_models, models, ConfigManager
from src.rate_limiter import RateLimiter, RateLimitExceededError
from src.services import ScraperManager, MetadataSourceManager, TaskManager, TaskSuccess, TitleRecognitionManager
from .delete import delete_danmaku_file
from .utils import generate_episode_range_string

# 从 models 导入需要的类
ProviderEpisodeInfo = models.ProviderEpisodeInfo

logger = logging.getLogger(__name__)

# 延迟导入以避免循环依赖
def _get_download_concurrent():
    from .download_helpers import _download_episode_comments_concurrent
    return _download_episode_comments_concurrent

def _get_import_iteratively():
    from .download_helpers import _import_episodes_iteratively
    return _import_episodes_iteratively

def _get_generic_import():
    from .import_core import generic_import_task
    return generic_import_task


async def full_refresh_task(sourceId: int, session: AsyncSession, scraper_manager: ScraperManager, task_manager: TaskManager, rate_limiter: RateLimiter, progress_callback: Callable, metadata_manager: MetadataSourceManager, config_manager = None):
    """
    后台任务：全量刷新一个已存在的番剧。

    优化：直接使用数据库中已存储的分集 ID 获取弹幕，不依赖源站的"获取分集列表"接口。
    这样可以避免因源站接口不稳定（如限流）导致的刷新失败。
    """
    logger.info(f"开始刷新源 ID: {sourceId}")
    try:
        source_info = await crud.get_anime_source_info(session, sourceId)
        if not source_info:
            raise ValueError(f"找不到源ID {sourceId} 的信息。")

        provider_name = source_info["providerName"]
        scraper = scraper_manager.get_scraper(provider_name)

        # 步骤 1: 从数据库获取已存储的分集列表（不依赖源站接口）
        await progress_callback(5, "正在获取已存储的分集列表...")

        episodes_result = await session.execute(
            select(orm_models.Episode)
            .where(orm_models.Episode.sourceId == sourceId)
            .order_by(orm_models.Episode.episodeIndex)
        )
        existing_episodes = episodes_result.scalars().all()

        if not existing_episodes:
            raise TaskSuccess("刷新失败：该源没有已存储的分集。请先导入分集。")

        logger.info(f"从数据库获取到 {len(existing_episodes)} 个已存储的分集")

        # 步骤 2: 直接使用已存储的 providerEpisodeId 获取弹幕
        total_comments_added = 0
        successful_indices = []
        skipped_indices = []
        failed_count = 0
        failed_details = {}

        total_episodes = len(existing_episodes)
        _download_episode_comments_concurrent = _get_download_concurrent()

        for idx, episode in enumerate(existing_episodes):
            episode_index = episode.episodeIndex
            episode_id = episode.id
            provider_episode_id = episode.providerEpisodeId

            # 计算进度：5% 用于初始化，90% 用于下载，5% 用于收尾
            progress = 5 + int((idx / total_episodes) * 90)
            await progress_callback(progress, f"正在刷新第 {episode_index} 集 ({idx + 1}/{total_episodes})...")

            if not provider_episode_id:
                logger.warning(f"分集 {episode_id} (第{episode_index}集) 没有 providerEpisodeId，跳过")
                failed_count += 1
                failed_details[episode_index] = "缺少源站分集ID"
                continue

            while True:  # 流控重试循环
                try:
                    # 先在外层检查流控（_download_episode_comments_concurrent 内部的
                    # except Exception 会吞掉 RateLimitExceededError，导致流控错误
                    # 被当成"无弹幕"静默跳过。在外层显式检查可以让 except
                    # RateLimitExceededError 正确捕获并原地等待重试）
                    await rate_limiter.check(scraper.provider_name)

                    # 创建虚拟分集对象用于下载
                    virtual_episode = ProviderEpisodeInfo(
                        provider=provider_name,
                        episodeIndex=episode_index,
                        title=episode.title or f"第{episode_index}集",
                        episodeId=provider_episode_id,
                        url=episode.sourceUrl or ""
                    )

                    # 使用并发下载获取弹幕
                    download_results = await _download_episode_comments_concurrent(
                        scraper, [virtual_episode], rate_limiter,
                        lambda p, d: progress_callback(progress, d)  # 子进度回调
                    )

                    # 提取弹幕数据
                    comments = None
                    if download_results and len(download_results) > 0:
                        _, comments = download_results[0]

                    if not comments or len(comments) == 0:
                        logger.warning(f"分集 {episode_id} (第{episode_index}集) 未获取到弹幕")
                        await crud.update_episode_fetch_time(session, episode_id)
                        skipped_indices.append(episode_index)
                        break  # 跳出 while，进入下一集

                    # 智能刷新：比较弹幕数量，只有新弹幕更多才覆盖
                    existing_count = episode.commentCount or 0
                    new_count = len(comments)

                    if new_count > existing_count:
                        # 新弹幕更多，保存
                        added_count = await crud.save_danmaku_for_episode(
                            session, episode_id, comments, config_manager,
                            fire_threshold=scraper.likes_fire_threshold
                        )
                        total_comments_added += added_count
                        successful_indices.append(episode_index)
                        logger.info(f"分集 {episode_id} (第{episode_index}集) 刷新成功: {existing_count} -> {new_count} 条弹幕")
                    else:
                        # 新弹幕不比旧的多，跳过
                        await crud.update_episode_fetch_time(session, episode_id)
                        skipped_indices.append(episode_index)
                        logger.info(f"分集 {episode_id} (第{episode_index}集) 跳过: 新弹幕({new_count}) <= 旧弹幕({existing_count})")

                    await session.commit()

                    # 短暂休眠，避免请求过快
                    await asyncio.sleep(0.1)
                    break  # 成功处理，跳出 while，进入下一集

                except RateLimitExceededError as e:
                    # 流控错误，原地等待后重试当前分集
                    wait_seconds = e.retry_after_seconds
                    logger.info(f"分集 {episode_id} (第{episode_index}集) 触发流控，原地等待 {wait_seconds:.0f} 秒后重试...")
                    await progress_callback(progress, f"流控等待中，{wait_seconds:.0f} 秒后继续 ({idx+1}/{total_episodes})...")
                    await asyncio.sleep(wait_seconds + 1)
                    continue  # 重试当前分集

                except Exception as e:
                    logger.error(f"分集 {episode_id} (第{episode_index}集) 刷新失败: {e}", exc_info=True)
                    failed_count += 1
                    # 提取简短的错误信息
                    error_msg = str(e)
                    if len(error_msg) > 50:
                        error_msg = error_msg[:50] + "..."
                    failed_details[episode_index] = error_msg
                    break  # 失败，跳出 while，进入下一集

        # 步骤 3: 构造最终的成功消息
        await progress_callback(98, "正在生成刷新报告...")

        episode_range_str = generate_episode_range_string(successful_indices)
        final_message = f"全量刷新完成，处理了 {total_episodes} 个分集，新增 {total_comments_added} 条弹幕。"

        if successful_indices:
            final_message += f"\n成功刷新: {len(successful_indices)} 集"
        if skipped_indices:
            final_message += f"\n跳过(弹幕未增加): {len(skipped_indices)} 集"
        if failed_count > 0:
            # 添加失败详情
            failure_details = []
            for ep_index, error_msg in sorted(failed_details.items()):
                failure_details.append(f"第{ep_index}集: {error_msg}")
            final_message += f"\n失败 {failed_count} 集:\n" + "\n".join(failure_details[:10])  # 最多显示10条
            if len(failure_details) > 10:
                final_message += f"\n... 还有 {len(failure_details) - 10} 条失败记录"

        raise TaskSuccess(final_message)

    except TaskSuccess:
        raise
    except Exception as e:
        await session.rollback()
        logger.error(f"全量刷新任务 (源ID: {sourceId}) 失败: {e}", exc_info=True)
        raise


async def refresh_episode_task(episodeId: int, session: AsyncSession, manager: ScraperManager, rate_limiter: RateLimiter, progress_callback: Callable, config_manager = None):
    """后台任务：刷新单个分集的弹幕"""
    logger.info(f"开始刷新分集 ID: {episodeId}")
    try:
        await progress_callback(0, "正在获取分集信息...")
        # 1. 获取分集的源信息
        info = await crud.get_episode_provider_info(session, episodeId)
        if not info or not info.get("providerName") or not info.get("providerEpisodeId"):
            logger.error(f"刷新失败：在数据库中找不到分集 ID: {episodeId} 的源信息")
            await progress_callback(100, "失败: 找不到源信息")
            return

        provider_name = info["providerName"]
        provider_episode_id = info["providerEpisodeId"]

        # 调试信息：检查获取到的信息
        logger.info(f"刷新分集 {episodeId}: provider_name='{provider_name}', provider_episode_id='{provider_episode_id}'")

        if not provider_name:
            raise ValueError(f"分集 {episodeId} 的 provider_name 为空")
        if not provider_episode_id:
            raise ValueError(f"分集 {episodeId} 的 provider_episode_id 为空")

        scraper = manager.get_scraper(provider_name)

        # 移除这里的 check，让并发下载函数自己处理流控
        # 这样避免重复 check 导致占用2个配额

        await progress_callback(30, "正在从源获取新弹幕...")

        # 使用三线程下载模式获取弹幕
        # 创建一个虚拟的分集对象用于并发下载
        virtual_episode = ProviderEpisodeInfo(
            provider=provider_name,
            episodeIndex=1,
            title=f"刷新分集 {episodeId}",
            episodeId=provider_episode_id,
            url=""
        )

        async def sub_progress_callback(danmaku_progress: int, danmaku_description: str):
            # 30% for setup, 65% for download, 5% for db write
            current_total_progress = 30 + (danmaku_progress / 100) * 65
            await progress_callback(current_total_progress, danmaku_description)

        # 使用并发下载获取弹幕（三线程模式）
        _download_episode_comments_concurrent = _get_download_concurrent()
        download_results = await _download_episode_comments_concurrent(
            scraper, [virtual_episode], rate_limiter, sub_progress_callback
        )

        # 提取弹幕数据
        all_comments_from_source = None
        if download_results and len(download_results) > 0:
            _, comments = download_results[0]  # 忽略episode_index
            all_comments_from_source = comments

        if not all_comments_from_source:
            await crud.update_episode_fetch_time(session, episodeId)
            raise TaskSuccess("未找到任何弹幕。")


        await progress_callback(96, f"正在写入 {len(all_comments_from_source)} 条新弹幕...")

        # 获取 animeId 用于文件路径
        anime_id = info["animeId"]
        added_count = await crud.save_danmaku_for_episode(
            session, episodeId, all_comments_from_source, config_manager,
            fire_threshold=scraper.likes_fire_threshold
        )

        await session.commit()
        raise TaskSuccess(f"刷新完成，新增 {added_count} 条弹幕。")
    except TaskSuccess:
        # 任务成功完成，直接重新抛出，由 TaskManager 处理
        raise
    except Exception as e:
        logger.error(f"刷新分集 ID: {episodeId} 时发生严重错误: {e}", exc_info=True)
        raise # Re-raise so the task manager catches it and marks as FAILED


async def refresh_bulk_episodes_task(episodeIds: List[int], session: AsyncSession, manager: ScraperManager, rate_limiter: RateLimiter, progress_callback: Callable, config_manager = None):
    """后台任务：批量刷新多个分集的弹幕"""
    total = len(episodeIds)
    logger.info(f"开始批量刷新 {total} 个分集")
    await progress_callback(5, f"准备刷新 {total} 个分集...")

    success_episodes = []  # 存储 (episodeNumber, episodeId) 元组
    failed_episodes = []   # 存储 (episodeNumber, episodeId) 元组
    total_added_comments = 0

    def format_episode_ranges(episodes: List[tuple]) -> str:
        """将集数列表格式化为区间字符串，如 <1,3,5,7-9>"""
        if not episodes:
            return "<无>"

        # 按集数排序
        sorted_episodes = sorted(episodes, key=lambda x: x[0])
        episode_numbers = [ep[0] for ep in sorted_episodes]

        ranges = []
        start = episode_numbers[0]
        end = episode_numbers[0]

        for i in range(1, len(episode_numbers)):
            if episode_numbers[i] == end + 1:
                end = episode_numbers[i]
            else:
                if start == end:
                    ranges.append(str(start))
                else:
                    ranges.append(f"{start}-{end}")
                start = episode_numbers[i]
                end = episode_numbers[i]

        # 添加最后一个区间
        if start == end:
            ranges.append(str(start))
        else:
            ranges.append(f"{start}-{end}")

        return "<" + ",".join(ranges) + ">"

    try:
        _download_episode_comments_concurrent = _get_download_concurrent()

        # 维护受限源的集合（单源配额满时记录）
        rate_limited_providers = set()

        for i, episode_id in enumerate(episodeIds):
            progress = 5 + int(((i + 1) / total) * 90) if total > 0 else 95
            await progress_callback(progress, f"正在刷新分集 {i+1}/{total} (ID: {episode_id})...")

            try:
                # 1. 获取分集的源信息
                info = await crud.get_episode_provider_info(session, episode_id)
                if not info or not info.get("providerName") or not info.get("providerEpisodeId"):
                    logger.warning(f"分集 ID {episode_id} 的源信息不完整，跳过")
                    # 如果没有集数信息，使用ID作为fallback
                    episode_index = info.get("episodeIndex", episode_id) if info else episode_id
                    failed_episodes.append((episode_index, episode_id))
                    continue

                provider_name = info["providerName"]
                provider_episode_id = info["providerEpisodeId"]
                episode_index = info.get("episodeIndex", episode_id)

                # 检查该源是否已被标记为受限
                if provider_name in rate_limited_providers:
                    logger.info(f"源 '{provider_name}' 配额已满，跳过分集 {episode_id} (第{episode_index}集)")
                    failed_episodes.append((episode_index, episode_id))
                    continue

                scraper = manager.get_scraper(provider_name)

                # 2. 检查流控（原地等待重试，避免任务被踢到队列末尾）
                rate_check_passed = False
                while True:
                    try:
                        await rate_limiter.check(provider_name)
                        rate_check_passed = True
                        break  # 通过流控检查
                    except RuntimeError as e:
                        # 配置错误（如速率限制配置验证失败），跳过当前分集
                        if "配置验证失败" in str(e):
                            logger.error(f"配置错误，跳过分集 {episode_id}: {str(e)}")
                        else:
                            logger.error(f"运行时错误，跳过分集 {episode_id}: {str(e)}")
                        failed_episodes.append((episode_index, episode_id))
                        break
                    except RateLimitExceededError as e:
                        # 判断是全局配额满还是单源配额满
                        error_msg = str(e)
                        if "全局速率限制" in error_msg or "__global__" in error_msg:
                            # 全局配额满，原地等待后重试
                            wait_seconds = e.retry_after_seconds
                            logger.info(f"批量刷新遇到全局速率限制，原地等待 {wait_seconds:.0f} 秒后继续...")
                            await progress_callback(progress, f"全局流控等待中，{wait_seconds:.0f} 秒后继续 ({i+1}/{total})...")
                            await asyncio.sleep(wait_seconds + 1)
                            continue  # 重试流控检查
                        else:
                            # 单源配额满，标记该源并跳过当前分集
                            rate_limited_providers.add(provider_name)
                            logger.warning(f"源 '{provider_name}' 配额已满，跳过分集 {episode_id} (第{episode_index}集): {e}")
                            failed_episodes.append((episode_index, episode_id))
                            break

                if not rate_check_passed:
                    continue  # 流控检查未通过，跳过当前分集

                # 3. 下载弹幕
                virtual_episode = ProviderEpisodeInfo(
                    provider=provider_name,
                    episodeIndex=1,
                    title=f"批量刷新分集 {episode_id}",
                    episodeId=provider_episode_id,
                    url=""
                )

                download_results = await _download_episode_comments_concurrent(
                    scraper, [virtual_episode], rate_limiter, lambda p, d: asyncio.sleep(0)
                )

                all_comments_from_source = None
                if download_results and len(download_results) > 0:
                    _, comments = download_results[0]
                    all_comments_from_source = comments

                if not all_comments_from_source:
                    await crud.update_episode_fetch_time(session, episode_id)
                    logger.warning(f"分集 {episode_id} 未找到任何弹幕")
                    failed_episodes.append((episode_index, episode_id))
                    continue

                await rate_limiter.increment(provider_name)

                # 4. 保存弹幕
                added_count = await crud.save_danmaku_for_episode(
                    session, episode_id, all_comments_from_source, config_manager,
                    fire_threshold=scraper.likes_fire_threshold
                )
                total_added_comments += added_count
                success_episodes.append((episode_index, episode_id))

                # 提交事务，释放锁
                await session.commit()

                # 短暂休眠，允许其他操作执行
                await asyncio.sleep(0.1)

            except Exception as e:
                logger.error(f"刷新分集 ID {episode_id} 时发生错误: {e}", exc_info=True)
                # 尝试获取集数，如果失败则使用ID
                try:
                    info = await crud.get_episode_provider_info(session, episode_id)
                    ep_idx = info.get("episodeIndex", episode_id) if info else episode_id
                except:
                    ep_idx = episode_id
                failed_episodes.append((ep_idx, episode_id))
                continue

        success_count = len(success_episodes)
        failed_count = len(failed_episodes)

        success_ranges = format_episode_ranges(success_episodes)
        failed_ranges = format_episode_ranges(failed_episodes)

        message = f"批量刷新完成，共处理 {total} 个，成功 {success_count} 个 {success_ranges}，失败 {failed_count} 个 {failed_ranges}，新增 {total_added_comments} 条弹幕。"
        raise TaskSuccess(message)
    except TaskSuccess:
        raise
    except Exception as e:
        await session.rollback()
        logger.error(f"批量刷新分集任务失败: {e}", exc_info=True)
        raise


async def incremental_refresh_task(sourceId: int, nextEpisodeIndex: int, session: AsyncSession, manager: ScraperManager, task_manager: TaskManager, config_manager: ConfigManager, rate_limiter: RateLimiter, metadata_manager: MetadataSourceManager, progress_callback: Callable, animeTitle: str, title_recognition_manager: TitleRecognitionManager):
    """后台任务：增量刷新一个已存在的番剧。"""
    logger.info(f"开始增量刷新源 ID: {sourceId}，尝试获取第{nextEpisodeIndex}集")
    source_info = await crud.get_anime_source_info(session, sourceId)
    if not source_info:
        await progress_callback(100, "失败: 找不到源信息")
        logger.error(f"刷新失败：在数据库中找不到源 ID: {sourceId}")
        return
    try:
        # 重新执行通用导入逻辑, 只导入指定的一集
        generic_import_task = _get_generic_import()
        await generic_import_task(
            provider=source_info["providerName"], mediaId=source_info["mediaId"],
            animeTitle=animeTitle, mediaType=source_info["type"],
            season=source_info.get("season", 1), year=source_info.get("year"),
            currentEpisodeIndex=nextEpisodeIndex, imageUrl=source_info.get("imageUrl"),
            doubanId=None, tmdbId=source_info.get("tmdbId"), config_manager=config_manager, metadata_manager=metadata_manager,
            imdbId=None, tvdbId=None, bangumiId=source_info.get("bangumiId"),
            progress_callback=progress_callback,
            session=session,
            manager=manager, # type: ignore
            task_manager=task_manager,
            rate_limiter=rate_limiter,
            title_recognition_manager=title_recognition_manager)
    except TaskSuccess:
        # 显式地重新抛出 TaskSuccess，以确保它被 TaskManager 正确处理
        raise
    except Exception as e:
        logger.error(f"增量刷新源任务 (ID: {sourceId}) 失败: {e}", exc_info=True)
        raise

