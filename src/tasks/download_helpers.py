"""下载辅助函数模块"""
import logging
import asyncio
from typing import Callable, List, Optional, Tuple, Dict
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.db import crud, orm_models
from src.rate_limiter import RateLimiter, RateLimitExceededError
from src.services import TaskStatus
from .utils import extract_short_error_message

logger = logging.getLogger(__name__)


async def _download_episode_comments_concurrent(
    scraper,
    episodes: List,
    rate_limiter: RateLimiter,
    progress_callback: Callable,
    first_episode_comments: Optional[List] = None,
    is_fallback: bool = False,
    fallback_type: Optional[str] = None
) -> List[Tuple[int, Optional[List]]]:
    """
    并发下载多个分集的弹幕（用于单集或少量分集的快速下载）

    Args:
        scraper: 弹幕源scraper
        episodes: 分集列表
        rate_limiter: 速率限制器
        progress_callback: 进度回调函数
        first_episode_comments: 第一集的预获取弹幕（可选）
        is_fallback: 是否为后备任务（默认False）
        fallback_type: 后备类型 ("match" 或 "search"，仅当is_fallback=True时需要）

    Returns:
        List[Tuple[episode_index, comments]]: 分集索引和对应的弹幕列表
    """
    logger.info(f"开始并发下载 {len(episodes)} 个分集的弹幕（三线程模式）{'[后备任务]' if is_fallback else ''}")

    async def download_single_episode(episode_info):
        episode_index, episode = episode_info
        try:
            # 如果是第一集且已有预获取的弹幕，直接使用
            if episode_index == 0 and first_episode_comments is not None:
                logger.info(f"使用预获取的第一集弹幕: {len(first_episode_comments)} 条")
                return (episode.episodeIndex, first_episode_comments)

            # 检查速率限制（根据是否为后备任务选择不同的方法）
            if is_fallback:
                if not fallback_type:
                    raise ValueError("后备任务必须指定fallback_type参数")
                await rate_limiter.check_fallback(fallback_type, scraper.provider_name)
            else:
                await rate_limiter.check(scraper.provider_name)

            # 创建子进度回调（异步版本）
            async def sub_progress_callback(p, msg):
                await progress_callback(
                    30 + int((episode_index + p/100) * 60 / len(episodes)),
                    f"[线程{episode_index+1}] {msg}"
                )

            # 下载弹幕
            comments = await scraper.get_comments(episode.episodeId, progress_callback=sub_progress_callback)

            # 增加速率限制计数（根据是否为后备任务选择不同的方法）
            if comments is not None:
                if is_fallback:
                    await rate_limiter.increment_fallback(fallback_type, scraper.provider_name)
                else:
                    await rate_limiter.increment(scraper.provider_name)
                logger.info(f"[并发下载] 分集 '{episode.title}' 获取到 {len(comments)} 条弹幕")
            else:
                logger.warning(f"[并发下载] 分集 '{episode.title}' 获取弹幕失败")

            return (episode.episodeIndex, comments)

        except Exception as e:
            logger.error(f"[并发下载] 分集 '{episode.title}' 下载失败: {e}")
            return (episode.episodeIndex, None)

    # 使用 asyncio.Semaphore 限制并发数为3
    semaphore = asyncio.Semaphore(3)

    async def download_with_semaphore(episode_info):
        async with semaphore:
            return await download_single_episode(episode_info)

    # 创建所有下载任务
    download_tasks = [
        download_with_semaphore((i, episode))
        for i, episode in enumerate(episodes)
    ]

    # 并发执行所有下载任务
    results = await asyncio.gather(*download_tasks, return_exceptions=True)

    # 处理结果，过滤异常
    valid_results = []
    for result in results:
        if isinstance(result, Exception):
            logger.error(f"[并发下载] 任务执行异常: {result}")
            continue
        valid_results.append(result)

    logger.info(f"并发下载完成，成功下载 {len([r for r in valid_results if r[1] is not None])}/{len(episodes)} 个分集")
    return valid_results


async def _import_episodes_iteratively(
    session,
    scraper,
    rate_limiter,
    progress_callback,
    episodes: List,
    anime_id: int,
    source_id: int,
    first_episode_comments: Optional[List] = None,
    config_manager = None,
    is_single_episode: bool = False,
    smart_refresh: bool = False,
    is_fallback: bool = False,
    fallback_type: Optional[str] = None
):
    """
    迭代地导入分集弹幕。

    Args:
        first_episode_comments: 第一集预获取的弹幕（可选）
        is_single_episode: 是否为单集下载模式（启用并发下载）
        smart_refresh: 是否为智能刷新模式（先下载比较，只有更多弹幕才覆盖）
        is_fallback: 是否为后备任务（默认False）
        fallback_type: 后备类型 ("match" 或 "search"，仅当is_fallback=True时需要）

    Returns:
        Tuple[int, List[int], List[int], int, Dict[int, str]]:
            - total_comments_added: 总共新增的弹幕数
            - successful_episodes_indices: 成功导入的分集索引列表
            - skipped_episodes_indices: 已跳过的分集索引列表(已有弹幕)
            - failed_episodes_count: 失败的分集数量
            - failed_episodes_details: 失败分集的详细信息 {集数: 错误原因}
    """
    from typing import Dict, Tuple
    from sqlalchemy import select
    from src.db import crud, orm_models
    from src.services import TaskStatus

    _extract_short_error_message = extract_short_error_message

    total_comments_added = 0
    successful_episodes_indices = []
    skipped_episodes_indices = []  # 记录已跳过的分集(已有弹幕)
    failed_episodes_count = 0
    failed_episodes_details: Dict[int, str] = {}  # 记录失败分集的详细信息

    # 判断是否使用并发下载模式
    # 条件：严格的单集模式（只有1集）
    use_concurrent_download = is_single_episode and len(episodes) == 1

    if use_concurrent_download:
        # 使用并发下载获取所有弹幕
        download_results = await _download_episode_comments_concurrent(
            scraper, episodes, rate_limiter, progress_callback, first_episode_comments,
            is_fallback, fallback_type
        )

        # 处理下载结果，写入数据库
        await progress_callback(90, "正在写入数据库...")

        for episode_index, comments in download_results:
            # 找到对应的分集信息
            episode = next((ep for ep in episodes if ep.episodeIndex == episode_index), None)
            if episode is None:
                logger.error(f"无法找到分集索引 {episode_index} 对应的分集信息")
                failed_episodes_count += 1
                failed_episodes_details[episode_index] = "无法找到分集信息"
                continue

            # 修正：检查弹幕是否为空（None 或空列表）
            if comments is not None and len(comments) > 0:
                try:
                    episode_db_id = await crud.create_episode_if_not_exists(
                        session, anime_id, source_id, episode.episodeIndex,
                        episode.title, episode.url, episode.episodeId
                    )

                    # 智能刷新模式：比较弹幕数量
                    if smart_refresh:
                        episode_stmt = select(orm_models.Episode).where(orm_models.Episode.id == episode_db_id)
                        episode_result = await session.execute(episode_stmt)
                        existing_episode = episode_result.scalar_one_or_none()

                        if existing_episode and existing_episode.commentCount > 0:
                            new_count = len(comments)
                            existing_count = existing_episode.commentCount

                            if new_count > existing_count:
                                actual_new_count = new_count - existing_count
                                logger.info(f"分集 '{episode.title}' 弹幕总数 ({new_count}) 大于现有数量 ({existing_count})，实际新增 {actual_new_count} 条，更新弹幕。")
                                added_count = await crud.save_danmaku_for_episode(session, episode_db_id, comments, config_manager)
                                await session.commit()
                            elif new_count == existing_count:
                                logger.info(f"分集 '{episode.title}' 弹幕总数 ({new_count}) 与现有数量相同，跳过更新。")
                                skipped_episodes_indices.append(episode.episodeIndex)
                                continue
                            else:
                                logger.info(f"分集 '{episode.title}' 弹幕总数 ({new_count}) 少于现有数量 ({existing_count})，跳过更新。")
                                skipped_episodes_indices.append(episode.episodeIndex)
                                continue
                        else:
                            # 没有现有弹幕，直接导入
                            added_count = await crud.save_danmaku_for_episode(session, episode_db_id, comments, config_manager)
                            await session.commit()
                    else:
                        # 普通模式：检查是否已有弹幕，如果有则跳过
                        episode_stmt = select(orm_models.Episode).where(orm_models.Episode.id == episode_db_id)
                        episode_result = await session.execute(episode_stmt)
                        existing_episode = episode_result.scalar_one_or_none()
                        if existing_episode and existing_episode.danmakuFilePath and existing_episode.commentCount > 0:
                            logger.info(f"分集 '{episode.title}' (DB ID: {episode_db_id}) 已存在弹幕 ({existing_episode.commentCount} 条)，跳过导入。")
                            skipped_episodes_indices.append(episode.episodeIndex)  # 记录为跳过
                            continue

                        added_count = await crud.save_danmaku_for_episode(session, episode_db_id, comments, config_manager)
                        await session.commit()

                    total_comments_added += added_count
                    successful_episodes_indices.append(episode.episodeIndex)
                    logger.info(f"[并发模式] 分集 '{episode.title}' (DB ID: {episode_db_id}) 写入 {added_count} 条弹幕并已提交。")
                except Exception as e:
                    failed_episodes_count += 1
                    error_msg = _extract_short_error_message(e)
                    failed_episodes_details[episode.episodeIndex] = f"写入数据库失败: {error_msg}"
                    logger.error(f"[并发模式] 分集 '{episode.title}' 写入数据库失败: {e}", exc_info=True)
            else:
                # 修正：获取弹幕失败或为空时，不创建分集记录
                failed_episodes_count += 1
                if comments is None:
                    failed_episodes_details[episode.episodeIndex] = "获取弹幕失败"
                    logger.warning(f"[并发模式] 分集 '{episode.title}' 获取弹幕失败（返回 None），不创建分集记录。")
                else:
                    failed_episodes_details[episode.episodeIndex] = "获取弹幕为空"
                    logger.warning(f"[并发模式] 分集 '{episode.title}' 获取弹幕为空（0条），不创建分集记录。")

        logger.info(f"并发下载模式完成，成功处理 {len(successful_episodes_indices)} 个分集")

    else:
        # 传统的串行下载模式
        for i, episode in enumerate(episodes):
            base_progress = 30 + (i * 60 // len(episodes))
            await progress_callback(base_progress, f"正在处理分集: {episode.title}")

            try:
                # 串行模式下，在真正发起网络请求之前先检查数据库是否已有弹幕，避免重复下载
                if not smart_refresh and not is_fallback:
                    # 如果是第一集且已经有预获取的弹幕，则后面会直接复用，这里不再额外检查
                    if not (i == 0 and first_episode_comments is not None):
                        episode_stmt = select(orm_models.Episode).where(
                            orm_models.Episode.sourceId == source_id,
                            orm_models.Episode.episodeIndex == episode.episodeIndex,
                            orm_models.Episode.danmakuFilePath.isnot(None),
                            orm_models.Episode.commentCount > 0,
                        )
                        episode_result = await session.execute(episode_stmt)
                        existing_episode = episode_result.scalar_one_or_none()
                        if existing_episode:
                            logger.info(
                                f"分集 '{episode.title}' (源ID: {source_id}, 集数: {episode.episodeIndex}) 已存在弹幕 ({existing_episode.commentCount} 条)，跳过网络请求与导入。"
                            )
                            skipped_episodes_indices.append(episode.episodeIndex)
                            continue

                # 如果是第一集且已有预获取的弹幕，直接使用
                if i == 0 and first_episode_comments is not None:
                    comments = first_episode_comments
                    logger.info(f"使用预获取的第一集弹幕: {len(comments)} 条")
                else:
                    # 其他分集正常获取
                    # 根据是否为后备任务选择不同的速率限制方法
                    # 原地等待流控重置，避免任务被踢到队列末尾导致多集任务无法连续完成
                    while True:
                        try:
                            if is_fallback:
                                if not fallback_type:
                                    raise ValueError("后备任务必须指定fallback_type参数")
                                await rate_limiter.check_fallback(fallback_type, scraper.provider_name)
                            else:
                                await rate_limiter.check(scraper.provider_name)
                            break  # 通过流控检查，继续下载
                        except RateLimitExceededError as e:
                            wait_seconds = e.retry_after_seconds
                            logger.info(f"分集 '{episode.title}' 触发流控，原地等待 {wait_seconds:.0f} 秒后继续...")
                            await progress_callback(base_progress, f"流控等待中，{wait_seconds:.0f} 秒后继续 ({i+1}/{len(episodes)})...")
                            await asyncio.sleep(wait_seconds + 1)

                    async def sub_progress_callback(p, msg):
                        await progress_callback(
                            base_progress + int(p * 0.6 / len(episodes)), msg
                        )

                    comments = await scraper.get_comments(episode.episodeId, progress_callback=sub_progress_callback)

                    # 只有在实际进行了网络请求时才增加计数
                    if comments is not None:
                        if is_fallback:
                            await rate_limiter.increment_fallback(fallback_type, scraper.provider_name)
                        else:
                            await rate_limiter.increment(scraper.provider_name)

                # 修正：检查弹幕是否为空（None 或空列表）
                if comments is not None and len(comments) > 0:
                    try:
                        episode_db_id = await crud.create_episode_if_not_exists(
                            session, anime_id, source_id, episode.episodeIndex,
                            episode.title, episode.url, episode.episodeId
                        )

                        # 智能刷新模式：比较弹幕数量
                        if smart_refresh:
                            episode_stmt = select(orm_models.Episode).where(orm_models.Episode.id == episode_db_id)
                            episode_result = await session.execute(episode_stmt)
                            existing_episode = episode_result.scalar_one_or_none()

                            if existing_episode and existing_episode.commentCount > 0:
                                new_count = len(comments)
                                existing_count = existing_episode.commentCount

                                if new_count > existing_count:
                                    actual_new_count = new_count - existing_count
                                    logger.info(f"分集 '{episode.title}' 弹幕总数 ({new_count}) 大于现有数量 ({existing_count})，实际新增 {actual_new_count} 条，更新弹幕。")
                                elif new_count == existing_count:
                                    logger.info(f"分集 '{episode.title}' 弹幕总数 ({new_count}) 与现有数量相同，跳过更新。")
                                    skipped_episodes_indices.append(episode.episodeIndex)
                                    continue
                                else:
                                    logger.info(f"分集 '{episode.title}' 弹幕总数 ({new_count}) 少于现有数量 ({existing_count})，跳过更新。")
                                    skipped_episodes_indices.append(episode.episodeIndex)
                                    continue
                        else:
                            # 普通模式：检查是否已有弹幕，如果有则跳过
                            episode_stmt = select(orm_models.Episode).where(orm_models.Episode.id == episode_db_id)
                            episode_result = await session.execute(episode_stmt)
                            existing_episode = episode_result.scalar_one_or_none()
                            if existing_episode and existing_episode.danmakuFilePath and existing_episode.commentCount > 0:
                                logger.info(f"分集 '{episode.title}' (DB ID: {episode_db_id}) 已存在弹幕 ({existing_episode.commentCount} 条)，跳过导入。")
                                skipped_episodes_indices.append(episode.episodeIndex)  # 记录为跳过
                                continue

                        added_count = await crud.save_danmaku_for_episode(session, episode_db_id, comments, config_manager)
                        await session.commit()

                        total_comments_added += added_count
                        successful_episodes_indices.append(episode.episodeIndex)
                        logger.info(f"分集 '{episode.title}' (DB ID: {episode_db_id}) 写入 {added_count} 条弹幕并已提交。")
                    except Exception as db_error:
                        # 数据库写入失败
                        failed_episodes_count += 1
                        error_msg = _extract_short_error_message(db_error)
                        failed_episodes_details[episode.episodeIndex] = f"写入数据库失败: {error_msg}"
                        logger.error(f"分集 '{episode.title}' 写入数据库失败: {db_error}", exc_info=True)
                        continue
                else:
                    # 修正：获取弹幕失败或为空时，不创建分集记录
                    failed_episodes_count += 1
                    if comments is None:
                        failed_episodes_details[episode.episodeIndex] = "获取弹幕失败"
                        logger.warning(f"分集 '{episode.title}' 获取弹幕失败（返回 None），不创建分集记录。")
                    else:
                        failed_episodes_details[episode.episodeIndex] = "获取弹幕为空"
                        logger.warning(f"分集 '{episode.title}' 获取弹幕为空（0条），不创建分集记录。")

            except RuntimeError as e:
                # 配置错误（如速率限制配置验证失败），跳过当前分集
                if "配置验证失败" in str(e):
                    failed_episodes_count += 1
                    failed_episodes_details[episode.episodeIndex] = f"配置错误: {str(e)}"
                    logger.error(f"分集 '{episode.title}' 因配置错误而跳过: {str(e)}")
                    continue
                # 其他 RuntimeError 也应该跳过
                failed_episodes_count += 1
                failed_episodes_details[episode.episodeIndex] = f"运行时错误: {str(e)}"
                logger.error(f"分集 '{episode.title}' 因运行时错误而跳过: {str(e)}")
                continue

            except Exception as e:
                failed_episodes_count += 1
                error_msg = _extract_short_error_message(e)
                failed_episodes_details[episode.episodeIndex] = error_msg
                logger.error(f"处理分集 '{episode.title}' 时发生错误: {e}", exc_info=True)
                continue

    return total_comments_added, successful_episodes_indices, skipped_episodes_indices, failed_episodes_count, failed_episodes_details

