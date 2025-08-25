import logging
from typing import Callable, List, Optional
import asyncio
import re
import traceback
from datetime import datetime, timedelta, timezone

from thefuzz import fuzz
from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.exc import OperationalError

from . import crud, models, orm_models
from .rate_limiter import RateLimiter, RateLimitExceededError
from .image_utils import download_image
from .config import settings
from .scraper_manager import ScraperManager
from .metadata_manager import MetadataSourceManager
from .utils import parse_search_keyword
from .task_manager import TaskManager, TaskSuccess, TaskStatus
from sqlalchemy.exc import OperationalError

logger = logging.getLogger(__name__)


async def delete_anime_task(animeId: int, session: AsyncSession, progress_callback: Callable):
    """Background task to delete an anime and all its related data."""
    max_retries = 3
    for attempt in range(max_retries):
        try:
            await progress_callback(0, f"开始删除 (尝试 {attempt + 1}/{max_retries})...")
            
            # 检查作品是否存在
            anime_exists = await session.get(orm_models.Anime, animeId)
            if not anime_exists:
                raise TaskSuccess("作品未找到，无需删除。")

            # 1. 找到所有关联的源ID
            source_ids_res = await session.execute(select(orm_models.AnimeSource.id).where(orm_models.AnimeSource.animeId == animeId))
            source_ids = source_ids_res.scalars().all()
            await progress_callback(10, f"找到 {len(source_ids)} 个关联数据源。")

            if source_ids:
                # 2. 找到所有关联的分集ID
                await progress_callback(20, "正在查找关联分集...")
                episode_ids_res = await session.execute(select(orm_models.Episode.id).where(orm_models.Episode.sourceId.in_(source_ids)))
                episode_ids = episode_ids_res.scalars().all()
                await progress_callback(30, f"找到 {len(episode_ids)} 个关联分集。")

                if episode_ids:
                    # 3. 删除所有弹幕
                    comment_count_res = await session.execute(
                        select(func.count(orm_models.Comment.id)).where(orm_models.Comment.episodeId.in_(episode_ids))
                    )
                    comment_count = comment_count_res.scalar_one()
                    await progress_callback(40, f"正在删除 {comment_count} 条弹幕...")
                    await session.execute(delete(orm_models.Comment).where(orm_models.Comment.episodeId.in_(episode_ids)))
                    
                    # 4. 删除所有分集
                    await progress_callback(60, f"正在删除 {len(episode_ids)} 个分集...")
                    await session.execute(delete(orm_models.Episode).where(orm_models.Episode.id.in_(episode_ids)))

                # 5. 删除所有源
                await progress_callback(70, f"正在删除 {len(source_ids)} 个数据源...")
                await session.execute(delete(orm_models.AnimeSource).where(orm_models.AnimeSource.id.in_(source_ids)))

            # 6. 删除元数据和别名
            await progress_callback(80, "正在删除元数据和别名...")
            await session.execute(delete(orm_models.AnimeMetadata).where(orm_models.AnimeMetadata.animeId == animeId))
            await session.execute(delete(orm_models.AnimeAlias).where(orm_models.AnimeAlias.animeId == animeId))

            # 7. 删除作品本身
            await progress_callback(90, "正在删除主作品记录...")
            await session.delete(anime_exists)
            
            await session.commit()
            raise TaskSuccess("删除成功。")
        except OperationalError as e:
            await session.rollback()
            if "Lock wait timeout exceeded" in str(e) and attempt < max_retries - 1:
                wait_time = 2 ** (attempt + 1) # 2, 4, 8 seconds
                logger.warning(f"删除作品时遇到锁超时，将在 {wait_time} 秒后重试...")
                await progress_callback(0, f"数据库锁定，将在 {wait_time} 秒后重试...")
                await asyncio.sleep(wait_time)
                continue # Retry the loop
            else:
                logger.error(f"删除作品任务 (ID: {animeId}) 失败: {e}", exc_info=True)
                raise # Re-raise if it's not a lock error or retries are exhausted
        except TaskSuccess:
            raise # Propagate success exception
        except Exception as e:
            await session.rollback()
            logger.error(f"删除作品任务 (ID: {animeId}) 失败: {e}", exc_info=True)
            raise

async def delete_source_task(sourceId: int, session: AsyncSession, progress_callback: Callable):
    """Background task to delete a source and all its related data."""
    await progress_callback(0, "开始删除...")
    try:
        # 检查源是否存在
        source_exists = await session.get(orm_models.AnimeSource, sourceId)
        if not source_exists:
            raise TaskSuccess("数据源未找到，无需删除。")

        # 1. 找到所有关联的分集ID
        episode_ids_res = await session.execute(select(orm_models.Episode.id).where(orm_models.Episode.sourceId == sourceId))
        episode_ids = episode_ids_res.scalars().all()

        if episode_ids:
            # 2. 删除所有这些分集关联的弹幕
            await progress_callback(30, f"正在删除 {len(episode_ids)} 个分集的弹幕...")
            await session.execute(delete(orm_models.Comment).where(orm_models.Comment.episodeId.in_(episode_ids)))
            
            # 3. 删除所有这些分集
            await progress_callback(60, "正在删除分集记录...")
            await session.execute(delete(orm_models.Episode).where(orm_models.Episode.id.in_(episode_ids)))

        # 4. 删除源本身
        await progress_callback(90, "正在删除源记录...")
        await session.delete(source_exists)
        
        await session.commit()
        raise TaskSuccess("删除成功。")
    except TaskSuccess:
        # 显式地重新抛出 TaskSuccess，以确保它被 TaskManager 正确处理
        raise
    except Exception as e:
        await session.rollback()
        logger.error(f"删除源任务 (ID: {sourceId}) 失败: {e}", exc_info=True)
        raise

async def delete_episode_task(episodeId: int, session: AsyncSession, progress_callback: Callable):
    """Background task to delete an episode and its comments."""
    await progress_callback(0, "开始删除...")
    try:
        # 检查分集是否存在
        episode_exists = await session.get(orm_models.Episode, episodeId)
        if not episode_exists:
            raise TaskSuccess("分集未找到，无需删除。")

        # 1. 显式删除关联的弹幕
        await session.execute(delete(orm_models.Comment).where(orm_models.Comment.episodeId == episodeId))
        
        # 2. 删除分集本身
        await session.execute(delete(orm_models.Episode).where(orm_models.Episode.id == episodeId))
        
        await session.commit()
        raise TaskSuccess("删除成功。")
    except TaskSuccess:
        # 显式地重新抛出 TaskSuccess，以确保它被 TaskManager 正确处理
        raise
    except Exception as e:
        await session.rollback()
        logger.error(f"删除分集任务 (ID: {episodeId}) 失败: {e}", exc_info=True)
        raise

async def delete_bulk_episodes_task(episodeIds: List[int], session: AsyncSession, progress_callback: Callable):
    """后台任务：批量删除多个分集。"""
    total = len(episodeIds)
    await progress_callback(5, f"准备删除 {total} 个分集...")
    deleted_count = 0
    try:
        for i, episode_id in enumerate(episodeIds):
            progress = 5 + int(((i + 1) / total) * 90) if total > 0 else 95
            await progress_callback(progress, f"正在删除分集 {i+1}/{total} (ID: {episode_id}) 的数据...")

            # 为每个分集单独执行删除操作，以减小事务大小和锁定的时间
            # 1. 删除关联的弹幕
            await session.execute(delete(orm_models.Comment).where(orm_models.Comment.episodeId == episode_id))
            
            # 2. 删除分集本身
            result = await session.execute(delete(orm_models.Episode).where(orm_models.Episode.id == episode_id))
            
            # 检查是否有行被删除
            if result.rowcount > 0:
                deleted_count += 1
            
            # 3. 为每个分集提交一次事务，以尽快释放锁
            await session.commit()
            
            # 短暂休眠，以允许其他数据库操作有机会执行
            await asyncio.sleep(0.1)

        raise TaskSuccess(f"批量删除完成，共处理 {total} 个，成功删除 {deleted_count} 个。")
    except TaskSuccess:
        # 显式地重新抛出 TaskSuccess，以确保它被 TaskManager 正确处理
        raise
    except Exception as e:
        await session.rollback()
        logger.error(f"批量删除分集任务失败: {e}", exc_info=True)
        raise

async def generic_import_task(
    provider: str,
    mediaId: str,
    animeTitle: str,
    mediaType: str,
    season: int,
    year: Optional[int],
    currentEpisodeIndex: Optional[int],
    imageUrl: Optional[str],
    doubanId: Optional[str],
    metadata_manager: MetadataSourceManager,
    tmdbId: Optional[str],
    imdbId: Optional[str],
    tvdbId: Optional[str],
    bangumiId: Optional[str],
    progress_callback: Callable,
    session: AsyncSession,
    manager: ScraperManager, 
    task_manager: TaskManager,
    rate_limiter: RateLimiter
):
    """
    后台任务：执行从指定数据源导入弹幕的完整流程。
    """
    # 重构导入逻辑以避免创建空条目
    scraper = manager.get_scraper(provider)

    # 修正：在创建作品前，再次从标题中解析季和集，以确保数据一致性
    parsed_info = parse_search_keyword(animeTitle)
    title_to_use = parsed_info["title"]
    # 优先使用从标题解析出的季度，如果解析不出，则回退到传入的 season 参数
    season_to_use = parsed_info["season"] if parsed_info["season"] is not None else season
    normalized_title = animeTitle.replace(":", "：")

    await progress_callback(10, "正在获取分集列表...")
    episodes = await scraper.get_episodes(
        mediaId,
        target_episode_index=currentEpisodeIndex,
        db_media_type=mediaType
    )
    if not episodes:
        # --- FAILOVER LOGIC ---
        logger.info(f"主源 '{provider}' 未能找到分集，尝试故障转移...")
        await progress_callback(15, "主源未找到分集，尝试故障转移...")
        
        user = models.User(id=1, username="scheduled_task") # Create a dummy user for metadata calls
        
        comments = await metadata_manager.get_failover_comments(
            title=animeTitle,
            season=season,
            episode_index=currentEpisodeIndex,
            user=user
        )
        
        if comments:
            logger.info(f"故障转移成功，找到 {len(comments)} 条弹幕。正在保存...")
            await progress_callback(20, f"故障转移成功，找到 {len(comments)} 条弹幕。")
            
            local_image_path = await download_image(imageUrl, session, manager, provider)
            image_download_failed = bool(imageUrl and not local_image_path)
            
            anime_id = await crud.get_or_create_anime(session, title_to_use, mediaType, season_to_use, imageUrl, local_image_path, year)
            await crud.update_metadata_if_empty(session, anime_id, tmdbId, imdbId, tvdbId, doubanId, bangumiId)
            source_id = await crud.link_source_to_anime(session, anime_id, provider, mediaId)
            
            episode_title = f"第 {currentEpisodeIndex} 集"
            episode_db_id = await crud.create_episode_if_not_exists(session, anime_id, source_id, currentEpisodeIndex, episode_title, None, "failover")
            
            added_count = await crud.bulk_insert_comments(session, episode_db_id, comments)
            await session.commit()
            
            final_message = f"通过故障转移导入完成，共新增 {added_count} 条弹幕。" + (" (警告：海报图片下载失败)" if image_download_failed else "")
            raise TaskSuccess(final_message)
        else:
            msg = f"未能找到第 {currentEpisodeIndex} 集。" if currentEpisodeIndex else "未能获取到任何分集。"
            logger.warning(f"任务终止: {msg} (provider='{provider}', media_id='{mediaId}')")
            raise TaskSuccess(msg)

    if mediaType == "movie" and episodes:
        logger.info(f"检测到媒体类型为电影，将只处理第一个分集 '{episodes[0].title}'。")
        episodes = episodes[:1]

    anime_id: Optional[int] = None
    source_id: Optional[int] = None
    total_comments_added = 0
    image_download_failed = False
    total_episodes = len(episodes)
    i = 0
    while i < total_episodes:
        episode = episodes[i]
        logger.info(f"--- 开始处理分集 {i+1}/{total_episodes}: '{episode.title}' (ID: {episode.episodeId}) ---")
        base_progress = 10 + int((i / total_episodes) * 85)
        await progress_callback(base_progress, f"正在处理: {episode.title} ({i+1}/{total_episodes})")

        async def sub_progress_callback(danmaku_progress: int, danmaku_description: str):
            current_total_progress = base_progress + (danmaku_progress / 100) * (85 / total_episodes)
            await progress_callback(current_total_progress, f"处理: {episode.title} - {danmaku_description}")

        try:
            # 1. 先检查是否允许下载
            await rate_limiter.check(provider)
        except RateLimitExceededError as e:
            logger.warning(f"任务 '{normalized_title}' 因达到速率限制而暂停: {e}")
            # 任务因速率限制而自动暂停，并在等待后重试
            await progress_callback(base_progress, f"速率受限，将在 {e.retry_after_seconds:.0f} 秒后自动重试...", status=TaskStatus.PAUSED)
            await asyncio.sleep(e.retry_after_seconds)
            continue # 重试当前分集

        comments = await scraper.get_comments(episode.episodeId, progress_callback=sub_progress_callback)

        if comments:
            # 2. 只有成功获取到弹幕后，才增加计数
            await rate_limiter.increment(provider)
        if comments and anime_id is None:
            logger.info("首次成功获取弹幕，正在创建数据库主条目...")
            await progress_callback(base_progress + 1, "正在创建数据库主条目...")

            # 修正：不再通过修改标题来区分同年份的作品，而是直接依赖 crud.get_or_create_anime 来处理唯一性。
            title_to_use = normalized_title

            local_image_path = await download_image(imageUrl, session, manager, provider)
            if imageUrl and not local_image_path:
                image_download_failed = True
            anime_id = await crud.get_or_create_anime(session, title_to_use, mediaType, season, imageUrl, local_image_path, year)
            await crud.update_metadata_if_empty(session, anime_id, tmdbId, imdbId, tvdbId, doubanId, bangumiId)
            source_id = await crud.link_source_to_anime(session, anime_id, provider, mediaId)
            logger.info(f"主条目创建完成 (Anime ID: {anime_id}, Source ID: {source_id})。")


        if anime_id and source_id:
            episode_db_id = await crud.create_episode_if_not_exists(session, anime_id, source_id, episode.episodeIndex, episode.title, episode.url, episode.episodeId)
            if comments:
                added_count = await crud.bulk_insert_comments(session, episode_db_id, comments)
                total_comments_added += added_count
                logger.info(f"分集 '{episode.title}' (DB ID: {episode_db_id}) 新增 {added_count} 条弹幕。")
            else:
                logger.info(f"分集 '{episode.title}' (DB ID: {episode_db_id}) 未找到弹幕，但已创建分集记录。")

            # 新增：在每个分集处理完毕后提交一次数据库，以实现渐进式保存
            await session.commit()
            logger.info(f"分集 '{episode.title}' 的数据已提交到数据库。")
        else:
            logger.info(f"分集 '{episode.title}' 未找到弹幕，跳过创建主条目。")
        
        i += 1 # 成功处理完一个分集，移动到下一个

    final_message = ""
    if total_comments_added == 0:
        final_message = "导入完成，但未找到任何新弹幕。"
    else:
        final_message = f"导入完成，共新增 {total_comments_added} 条弹幕。"
    if image_download_failed:
        final_message += " (警告：海报图片下载失败)"
    raise TaskSuccess(final_message)
    
async def edited_import_task(
    request_data: "models.EditedImportRequest",
    progress_callback: Callable,
    session: AsyncSession,
    manager: ScraperManager,
    rate_limiter: RateLimiter,
    metadata_manager: MetadataSourceManager
):
    """后台任务：处理编辑后的导入请求。"""
    scraper = manager.get_scraper(request_data.provider)
    normalized_title = request_data.animeTitle.replace(":", "：")
    
    episodes = request_data.episodes
    if not episodes:
        raise TaskSuccess("没有提供任何分集，任务结束。")

    # 新增：从标题中解析季和集，以确保数据一致性
    parsed_info = parse_search_keyword(normalized_title)
    title_to_use = parsed_info["title"]
    season_to_use = parsed_info["season"] if parsed_info["season"] is not None else request_data.season


    anime_id: Optional[int] = None
    source_id: Optional[int] = None
    total_comments_added = 0
    image_download_failed = False
    total_episodes = len(episodes)
    i = 0
    while i < total_episodes:
        episode = episodes[i]
        await progress_callback(10 + int((i / total_episodes) * 85), f"正在处理: {episode.title} ({i+1}/{total_episodes})")
        base_progress = 10 + int((i / total_episodes) * 85)
        try:
            await rate_limiter.check(request_data.provider)
        except RateLimitExceededError as e:
            logger.warning(f"编辑后导入任务 '{normalized_title}' 因达到速率限制而暂停: {e}")
            await progress_callback(base_progress, f"速率受限，将在 {e.retry_after_seconds:.0f} 秒后自动重试...", status=TaskStatus.PAUSED)
            await asyncio.sleep(e.retry_after_seconds)
            continue

        comments = await scraper.get_comments(episode.episodeId)

        if comments:
            await rate_limiter.increment(request_data.provider)
        if comments and anime_id is None:
            local_image_path = await download_image(request_data.imageUrl, session, manager, request_data.provider)
            if request_data.imageUrl and not local_image_path:
                image_download_failed = True
            
            anime_id = await crud.get_or_create_anime(
                session, title_to_use, request_data.mediaType,
                season_to_use,
                request_data.imageUrl, local_image_path, request_data.year
            )
            await crud.update_metadata_if_empty(
                session, anime_id,
                tmdbId=request_data.tmdbId,
                imdbId=request_data.imdbId,
                tvdbId=request_data.tvdbId,
                doubanId=request_data.doubanId,
                bangumiId=request_data.bangumiId,
                tmdbEpisodeGroupId=request_data.tmdbEpisodeGroupId
            )
            source_id = await crud.link_source_to_anime(session, anime_id, request_data.provider, request_data.mediaId)

        if anime_id and source_id:
            episode_db_id = await crud.create_episode_if_not_exists(session, anime_id, source_id, episode.episodeIndex, episode.title, episode.url, episode.episodeId)
            if comments:
                total_comments_added += await crud.bulk_insert_comments(session, episode_db_id, comments)
            
            # 新增：在每个分集处理完毕后提交一次数据库
            await session.commit()
            logger.info(f"编辑后导入：分集 '{episode.title}' 的数据已提交。")
        
        i += 1

    final_message = ""
    if total_comments_added == 0:
        final_message = "导入完成，但未找到任何新弹幕。"
    else:
        final_message = f"导入完成，共新增 {total_comments_added} 条弹幕。"
    if image_download_failed:
        final_message += " (警告：海报图片下载失败)"
    raise TaskSuccess(final_message)

async def full_refresh_task(sourceId: int, session: AsyncSession, scraper_manager: ScraperManager, task_manager: TaskManager, rate_limiter: RateLimiter, metadata_manager: MetadataSourceManager, progress_callback: Callable):
    """
    后台任务：全量刷新一个已存在的番剧，采用先获取后删除的安全策略。
    """
    logger.info(f"开始刷新源 ID: {sourceId}")
    source_info = await crud.get_anime_source_info(session, sourceId)
    if not source_info:
        await progress_callback(100, "失败: 找不到源信息")
        logger.error(f"刷新失败：在数据库中找不到源 ID: {sourceId}")
        raise TaskSuccess("刷新失败: 找不到源信息")

    scraper = scraper_manager.get_scraper(source_info["providerName"])

    # 步骤 1: 获取所有新数据，但不写入数据库
    await progress_callback(10, "正在获取新分集列表...")
    new_episodes = await scraper.get_episodes(source_info["mediaId"])
    if not new_episodes:
        raise TaskSuccess("刷新失败：未能从源获取任何分集信息。旧数据已保留。")

    await progress_callback(20, f"获取到 {len(new_episodes)} 个新分集，正在获取弹幕...")
    
    new_data_package = []
    total_comments_fetched = 0
    total_episodes = len(new_episodes)

    for i, episode in enumerate(new_episodes):
        base_progress = 20 + int((i / total_episodes) * 70) if total_episodes > 0 else 90
        
        async def sub_progress_callback(danmaku_progress: int, danmaku_description: str):
            current_sub_progress = (danmaku_progress / 100) * (70 / total_episodes)
            await progress_callback(base_progress + current_sub_progress, f"处理: {episode.title} - {danmaku_description}")

        comments = await scraper.get_comments(episode.episodeId, progress_callback=sub_progress_callback)
        new_data_package.append((episode, comments))
        if comments:
            total_comments_fetched += len(comments)

    if total_comments_fetched == 0:
        raise TaskSuccess("刷新完成，但未找到任何新弹幕。旧数据已保留。")

    # 步骤 2: 数据获取成功，现在在一个事务中执行清空和写入操作
    await progress_callback(95, "数据获取成功，正在清空旧数据并写入新数据...")
    try:
        await crud.clear_source_data(session, sourceId)
        
        for episode_info, comments in new_data_package:
            episode_db_id = await crud.create_episode_if_not_exists(
                session, source_info["animeId"], sourceId, 
                episode_info.episodeIndex, episode_info.title, 
                episode_info.url, episode_info.episodeId
            )
            if comments:
                await crud.bulk_insert_comments(session, episode_db_id, comments)
        
        await session.commit()
        raise TaskSuccess(f"全量刷新完成，共导入 {len(new_episodes)} 个分集，{total_comments_fetched} 条弹幕。")
    except Exception as e:
        await session.rollback()
        logger.error(f"全量刷新源 {sourceId} 时数据库写入失败: {e}", exc_info=True)
        raise

async def delete_bulk_sources_task(sourceIds: List[int], session: AsyncSession, progress_callback: Callable):
    """Background task to delete multiple sources."""
    total = len(sourceIds)
    deleted_count = 0
    for i, sourceId in enumerate(sourceIds):
        progress = int((i / total) * 100)
        await progress_callback(progress, f"正在删除源 {i+1}/{total} (ID: {sourceId})...")
        try:
            source = await session.get(orm_models.AnimeSource, sourceId)
            if source:
                await session.delete(source)
                await session.commit()
                deleted_count += 1
        except Exception as e:
            logger.error(f"批量删除源任务中，删除源 (ID: {sourceId}) 失败: {e}", exc_info=True)
            # Continue to the next one
    await session.commit()
    raise TaskSuccess(f"批量删除完成，共处理 {total} 个，成功删除 {deleted_count} 个。")

async def refresh_episode_task(episodeId: int, session: AsyncSession, manager: ScraperManager, rate_limiter: RateLimiter, progress_callback: Callable):
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
        scraper = manager.get_scraper(provider_name)

        # 新增：在获取弹幕前进行速率限制检查
        try:
            await rate_limiter.check(provider_name)
        except RateLimitExceededError as e:
            raise TaskSuccess(f"达到速率限制。请在 {e.retry_after_seconds:.0f} 秒后重试。")

        # 3. 获取新弹幕并插入
        await progress_callback(30, "正在从源获取新弹幕...")

        async def sub_progress_callback(danmaku_progress: int, danmaku_description: str):
            # 30% for setup, 65% for download, 5% for db write
            current_total_progress = 30 + (danmaku_progress / 100) * 65
            await progress_callback(current_total_progress, danmaku_description)

        all_comments_from_source = await scraper.get_comments(provider_episode_id, progress_callback=sub_progress_callback)

        if not all_comments_from_source:
            await crud.update_episode_fetch_time(session, episodeId)
            raise TaskSuccess("未找到任何弹幕。")

        # 新增：成功获取到弹幕后，增加计数
        await rate_limiter.increment(provider_name)

        # 新增：在插入前，先筛选出数据库中不存在的新弹幕，以避免产生大量的“重复条目”警告。
        await progress_callback(95, "正在比对新旧弹幕...")
        existing_cids = await crud.get_existing_comment_cids(session, episodeId)
        new_comments = [c for c in all_comments_from_source if str(c.get('cid')) not in existing_cids]

        if not new_comments:
            await crud.update_episode_fetch_time(session, episodeId)
            raise TaskSuccess("刷新完成，没有新增弹幕。")

        await progress_callback(96, f"正在写入 {len(new_comments)} 条新弹幕...")
        added_count = await crud.bulk_insert_comments(session, episodeId, new_comments)
        await crud.update_episode_fetch_time(session, episodeId)
        logger.info(f"分集 ID: {episodeId} 刷新完成，新增 {added_count} 条弹幕。")
        await session.commit()
        raise TaskSuccess(f"刷新完成，新增 {added_count} 条弹幕。")
    except TaskSuccess:
        # 任务成功完成，直接重新抛出，由 TaskManager 处理
        raise
    except Exception as e:
        logger.error(f"刷新分集 ID: {episodeId} 时发生严重错误: {e}", exc_info=True)
        raise # Re-raise so the task manager catches it and marks as FAILED

async def reorder_episodes_task(sourceId: int, session: AsyncSession, progress_callback: Callable):
    """后台任务：重新编号一个源的所有分集，并同步更新其ID。"""
    logger.info(f"开始重整源 ID: {sourceId} 的分集顺序。")
    await progress_callback(0, "正在获取分集列表...")
    
    try:
        # 1. 获取所有分集ORM对象，按现有顺序排序
        episodes_orm_res = await session.execute(
            select(orm_models.Episode)
            .where(orm_models.Episode.sourceId == sourceId)
            .order_by(orm_models.Episode.episodeIndex)
        )
        episodes_orm = episodes_orm_res.scalars().all()

        if not episodes_orm:
            raise TaskSuccess("没有找到分集，无需重整。")

        await progress_callback(10, "正在计算新的分集ID...")

        # 2. 获取计算新ID所需的信息
        source_info = await crud.get_anime_source_info(session, sourceId)
        if not source_info:
            raise ValueError(f"找不到源ID {sourceId} 的信息。")
        anime_id = source_info['animeId']

        all_anime_sources_stmt = select(orm_models.AnimeSource.id).where(orm_models.AnimeSource.animeId == anime_id).order_by(orm_models.AnimeSource.id)
        all_anime_sources_res = await session.execute(all_anime_sources_stmt)
        all_source_ids = all_anime_sources_res.scalars().all()
        try:
            source_order = all_source_ids.index(sourceId) + 1
        except ValueError:
            raise ValueError(f"内部错误: Source ID {sourceId} 不属于 Anime ID {anime_id}")

        # 3. 识别需要迁移的分集
        migrations = []
        for i, old_ep in enumerate(episodes_orm):
            new_index = i + 1
            if old_ep.episodeIndex != new_index:
                new_id_str = f"25{anime_id:06d}{source_order:02d}{new_index:04d}"
                new_id = int(new_id_str)
                
                new_episode_obj = orm_models.Episode(
                    id=new_id, sourceId=old_ep.sourceId, episodeIndex=new_index,
                    title=old_ep.title, sourceUrl=old_ep.sourceUrl,
                    providerEpisodeId=old_ep.providerEpisodeId, fetchedAt=old_ep.fetchedAt,
                    commentCount=old_ep.commentCount
                )
                migrations.append({"old_ep": old_ep, "new_ep": new_episode_obj})

        if not migrations:
            raise TaskSuccess("所有分集顺序正确，无需重整。")

        await progress_callback(30, f"准备迁移 {len(migrations)} 个分集...")

        # 4. 执行迁移
        for m in migrations:
            await session.execute(update(orm_models.Comment).where(orm_models.Comment.episodeId == m["old_ep"].id).values(episodeId=m["new_ep"].id))
            await session.delete(m["old_ep"])
        
        await session.flush()
        session.add_all([m["new_ep"] for m in migrations])
        
        await session.commit()
        raise TaskSuccess(f"重整完成，共更新了 {len(migrations)} 个分集的集数和ID。")
    except Exception as e:
        logger.error(f"重整分集任务 (源ID: {sourceId}) 失败: {e}", exc_info=True)
        raise

async def incremental_refresh_task(sourceId: int, nextEpisodeIndex: int, session: AsyncSession, manager: ScraperManager, task_manager: TaskManager, rate_limiter: RateLimiter, metadata_manager: MetadataSourceManager, progress_callback: Callable, animeTitle: str):
    """后台任务：增量刷新一个已存在的番剧。"""
    logger.info(f"开始增量刷新源 ID: {sourceId}，尝试获取第{nextEpisodeIndex}集")
    source_info = await crud.get_anime_source_info(session, sourceId)
    if not source_info:
        progress_callback(100, "失败: 找不到源信息")
        logger.error(f"刷新失败：在数据库中找不到源 ID: {sourceId}")
        return
    try:
        # 重新执行通用导入逻辑, 只导入指定的一集
        await generic_import_task(
            provider=source_info["providerName"], mediaId=source_info["mediaId"],
            animeTitle=animeTitle, mediaType=source_info["type"],
            season=source_info.get("season", 1), year=source_info.get("year"),
            currentEpisodeIndex=nextEpisodeIndex, imageUrl=None,
            doubanId=None, tmdbId=source_info.get("tmdbId"), metadata_manager=metadata_manager,
            imdbId=None, tvdbId=None, bangumiId=source_info.get("bangumiId"),
            progress_callback=progress_callback,
            session=session,
            manager=manager, # type: ignore
            task_manager=task_manager,
            rate_limiter=rate_limiter)
    except Exception as e:
        logger.error(f"增量刷新源任务 (ID: {sourceId}) 失败: {e}", exc_info=True)
        raise

async def manual_import_task(
    sourceId: int, animeId: int, title: str, episodeIndex: int, url: str, providerName: str,
    progress_callback: Callable, session: AsyncSession, manager: ScraperManager, rate_limiter: RateLimiter
):
    """后台任务：从URL手动导入弹幕。"""
    logger.info(f"开始手动导入任务: sourceId={sourceId}, title='{title}', url='{url}'")
    await progress_callback(10, "正在准备导入...")
    
    try:
        scraper = manager.get_scraper(providerName)
        # 修正：统一使用 get_id_from_url 方法，并检查其是否存在
        if not hasattr(scraper, 'get_id_from_url'):
            raise NotImplementedError(f"搜索源 '{providerName}' 不支持从URL手动导入。")

        provider_episode_id = await scraper.get_id_from_url(url)
        
        if not provider_episode_id: raise ValueError(f"无法从URL '{url}' 中解析出有效的视频ID。")

        episode_id_for_comments = scraper.format_episode_id_for_comments(provider_episode_id)

        await progress_callback(20, f"已解析视频ID: {episode_id_for_comments}")
        try:
            await rate_limiter.check(providerName)
        except RateLimitExceededError as e:
            raise TaskSuccess(f"达到速率限制。请在 {e.retry_after_seconds:.0f} 秒后重试。")

        comments = await scraper.get_comments(episode_id_for_comments, progress_callback=progress_callback)
        if not comments:
            raise TaskSuccess("未找到任何弹幕。")

        # 成功获取到弹幕后，增加计数
        await rate_limiter.increment(providerName)

        await progress_callback(90, "正在写入数据库...")
        episode_db_id = await crud.create_episode_if_not_exists(session, animeId, sourceId, episodeIndex, title, url, episode_id_for_comments)
        added_count = await crud.bulk_insert_comments(session, episode_db_id, comments)
        await session.commit()
        raise TaskSuccess(f"手动导入完成，新增 {added_count} 条弹幕。")
    except TaskSuccess:
        raise
    except Exception as e:
        logger.error(f"手动导入任务失败: {e}", exc_info=True)
        raise
        
async def auto_search_and_import_task(
    payload: "models.ControlAutoImportRequest",
    progress_callback: Callable,
    session: AsyncSession,
    scraper_manager: ScraperManager,
    metadata_manager: MetadataSourceManager,
    task_manager: TaskManager,
    rate_limiter: Optional[RateLimiter] = None,
):
    """
    全自动搜索并导入的核心任务逻辑。
    """
    # 防御性检查：确保 rate_limiter 已被正确传递。
    if rate_limiter is None:
        error_msg = "任务启动失败：内部错误（速率限制器未提供）。请检查任务提交处的代码。"
        logger.error(f"auto_search_and_import_task was called without a rate_limiter. This is a bug. Payload: {payload}")
        raise ValueError(error_msg)

    search_type = payload.searchType
    search_term = payload.searchTerm
    
    await progress_callback(5, f"开始处理，类型: {search_type}, 搜索词: {search_term}")

    aliases = {search_term}
    main_title = search_term
    media_type = payload.mediaType
    season = payload.season
    image_url = None
    tmdb_id, bangumi_id, douban_id, tvdb_id, imdb_id = None, None, None, None, None

    # 为后台任务创建一个虚拟用户对象
    user = models.User(id=1, username="admin")

    # 1. 获取元数据和别名
    if search_type != "keyword":
        # --- Start of fix for TMDB/TVDB mediaType ---
        # 如果是TMDB或TVDB搜索，且没有提供mediaType，则根据有无季/集信息进行推断
        # 同时，将内部使用的 'tv_series'/'movie' 转换为特定提供商需要的格式
        provider_media_type = media_type
        if search_type in ["tmdb", "tvdb"]:
            # TVDB API v4 使用 'series' 和 'movies'
            provider_specific_tv_type = "tv" if search_type == "tmdb" else "series"
            provider_specific_movie_type = "movie" if search_type == "tmdb" else "movies"

            if not media_type:
                # 修正：只要提供了季度信息，就应推断为电视剧
                if payload.season is not None:
                    provider_media_type = provider_specific_tv_type
                    media_type = "tv_series" # 更新内部使用的类型
                    logger.info(f"{search_type.upper()} 搜索未提供 mediaType，根据季/集信息推断为 '{provider_specific_tv_type}'。")
                else:
                    provider_media_type = provider_specific_movie_type
                    media_type = "movie" # 更新内部使用的类型
                    logger.info(f"{search_type.upper()} 搜索未提供 mediaType 和季/集信息，默认推断为 '{provider_specific_movie_type}'。")
            elif media_type == "tv_series":
                provider_media_type = provider_specific_tv_type
            elif media_type == "movie":
                provider_media_type = provider_specific_movie_type
        # --- End of fix ---
        try:
            await progress_callback(10, f"正在从 {search_type.upper()} 获取元数据...")
            details = await metadata_manager.get_details(
                provider=search_type, item_id=search_term, user=user, mediaType=provider_media_type
            )
            
            if details:
                main_title = details.title or main_title
                image_url = details.imageUrl
                aliases.add(main_title)
                aliases.update(details.aliasesCn or [])
                aliases.add(details.nameEn)
                aliases.add(details.nameJp)
                tmdb_id, bangumi_id, douban_id, tvdb_id, imdb_id = (
                    details.tmdbId, details.bangumiId, details.doubanId,
                    details.tvdbId, details.imdbId
                )
                # 修正：从元数据源获取最准确的媒体类型
                if hasattr(details, 'type') and details.type:
                    media_type = details.type
                
                # 新增：从其他启用的元数据源获取更多别名，以提高搜索覆盖率
                logger.info(f"正在为 '{main_title}' 从其他源获取更多别名...")
                enriched_aliases = await metadata_manager.search_aliases_from_enabled_sources(main_title, user)
                if enriched_aliases:
                    aliases.update(enriched_aliases)
                    logger.info(f"别名已扩充: {aliases}")
        except Exception as e:
            logger.error(f"从 {search_type.upper()} 获取元数据失败: {e}\n{traceback.format_exc()}")
            # Don't fail the whole task, just proceed with the original search term

    # 2. 检查媒体库中是否已存在
    await progress_callback(20, "正在检查媒体库...")
    existing_anime = await crud.find_anime_by_title_and_season(session, main_title, season)
    if existing_anime:
        favorited_source = await crud.find_favorited_source_for_anime(session, main_title, season)
        if favorited_source:
            source_to_use = favorited_source
            logger.info(f"媒体库中已存在作品，并找到精确标记源: {source_to_use['providerName']}")
        else:
            all_sources = await crud.get_anime_sources(session, existing_anime['id'])
            if all_sources:
                ordered_settings = await crud.get_all_scraper_settings(session)
                provider_order = {s['providerName']: s['displayOrder'] for s in ordered_settings}
                all_sources.sort(key=lambda s: provider_order.get(s['providerName'], 999))
                source_to_use = all_sources[0]
                logger.info(f"媒体库中已存在作品，选择优先级最高的源: {source_to_use['providerName']}")
            else: source_to_use = None
        
        if source_to_use:
            await progress_callback(30, f"已存在，使用源: {source_to_use['providerName']}")
            task_coro = lambda s, cb: generic_import_task(
                provider=source_to_use['providerName'], mediaId=source_to_use['mediaId'],
                animeTitle=main_title, mediaType=media_type, season=season,
                year=source_to_use.get('year'), currentEpisodeIndex=payload.episode, imageUrl=image_url,
                metadata_manager=metadata_manager,
                doubanId=douban_id, tmdbId=tmdb_id, imdbId=imdb_id, tvdbId=tvdb_id, bangumiId=bangumi_id,
                progress_callback=cb, session=s, manager=scraper_manager, task_manager=task_manager,
                rate_limiter=rate_limiter
            )
            await task_manager.submit_task(task_coro, f"自动导入 (库内): {main_title}")
            raise TaskSuccess("作品已在库中，已为已有源创建导入任务。")

    # 3. 如果库中不存在，则进行全网搜索
    await progress_callback(40, "媒体库未找到，开始全网搜索...")
    episode_info = {"season": season, "episode": payload.episode} if payload.episode else {"season": season}
    
    # 使用主标题进行搜索
    logger.info(f"将使用主标题 '{main_title}' 进行全网搜索...")
    all_results = await scraper_manager.search_all([main_title], episode_info=episode_info)
    logger.info(f"直接搜索完成，找到 {len(all_results)} 个原始结果。")

    # 使用所有别名进行过滤
    def normalize_for_filtering(title: str) -> str:
        if not title: return ""
        title = re.sub(r'[\[【(（].*?[\]】)）]', '', title)
        return title.lower().replace(" ", "").replace("：", ":").strip()

    normalized_filter_aliases = {normalize_for_filtering(alias) for alias in aliases if alias}
    filtered_results = []
    for item in all_results:
        normalized_item_title = normalize_for_filtering(item.title)
        if not normalized_item_title: continue
        if any((alias in normalized_item_title) or (normalized_item_title in alias) for alias in normalized_filter_aliases):
            filtered_results.append(item)
    logger.info(f"别名过滤: 从 {len(all_results)} 个原始结果中，保留了 {len(filtered_results)} 个相关结果。")
    all_results = filtered_results

    if not all_results:
        raise TaskSuccess("全网搜索未找到任何结果。")

    # 4. 选择最佳源
    ordered_settings = await crud.get_all_scraper_settings(session)
    provider_order = {s['providerName']: s['displayOrder'] for s in ordered_settings}
    
    # 修正：使用更智能的排序逻辑来选择最佳匹配
    # 1. 媒体类型是否匹配
    # 2. 标题相似度 (使用 a_main_title 确保与原始元数据标题比较)
    # 3. 用户设置的源优先级
    all_results.sort(
        key=lambda item: (
            1 if item.type == media_type else 0,  # 媒体类型匹配得1分，否则0分
            fuzz.token_set_ratio(main_title, item.title), # 标题相似度得分
            -provider_order.get(item.provider, 999) # 源优先级，取负数因为值越小优先级越高
        ),
        reverse=True # 按得分从高到低排序
    )
    best_match = all_results[0]

    await progress_callback(80, f"选择最佳源: {best_match.provider}")
    task_coro = lambda s, cb: generic_import_task(
        provider=best_match.provider, mediaId=best_match.mediaId,
        animeTitle=main_title, mediaType=media_type, season=season, year=best_match.year,
        metadata_manager=metadata_manager,
        currentEpisodeIndex=payload.episode, imageUrl=image_url,
        doubanId=douban_id, tmdbId=tmdb_id, imdbId=imdb_id, tvdbId=tvdb_id, bangumiId=bangumi_id,
        progress_callback=cb, session=s, manager=scraper_manager, task_manager=task_manager,
        rate_limiter=rate_limiter
    )
    await task_manager.submit_task(task_coro, f"自动导入 (新): {main_title}")
    raise TaskSuccess("已为最佳匹配源创建导入任务。")
async def database_maintenance_task(session: AsyncSession, progress_callback: Callable):
    """
    执行数据库维护的核心任务：清理旧日志和优化表。
    """
    logger.info("开始执行数据库维护任务...")
    
    # --- 1. 应用日志清理 ---
    await progress_callback(10, "正在清理旧日志...")
    
    try:
        # 日志保留天数，默认为30天。
        retention_days_str = await crud.get_config_value(session, "logRetentionDays", "3")
        retention_days = int(retention_days_str)
    except (ValueError, TypeError):
        retention_days = 3
    
    if retention_days > 0:
        logger.info(f"将清理 {retention_days} 天前的日志记录。")
        cutoff_date = datetime.now(timezone.utc) - timedelta(days=retention_days)
        
        tables_to_prune = {
            "任务历史": (orm_models.TaskHistory, orm_models.TaskHistory.createdAt),
            "Token访问日志": (orm_models.TokenAccessLog, orm_models.TokenAccessLog.accessTime),
            "外部API访问日志": (orm_models.ExternalApiLog, orm_models.ExternalApiLog.accessTime),
        }
        
        total_deleted = 0
        for name, (model, date_column) in tables_to_prune.items():
            deleted_count = await crud.prune_logs(session, model, date_column, cutoff_date)
            if deleted_count > 0:
                logger.info(f"从 {name} 表中删除了 {deleted_count} 条旧记录。")
            total_deleted += deleted_count
        await progress_callback(40, f"应用日志清理完成，共删除 {total_deleted} 条记录。")
    else:
        logger.info("日志保留天数设为0或无效，跳过清理。")
        await progress_callback(40, "日志保留天数设为0，跳过清理。")

    # --- 2. Binlog 清理 (仅MySQL) ---
    db_type = settings.database.type.lower()
    if db_type == "mysql":
        await progress_callback(50, "正在清理 MySQL Binlog...")
        try:
            # 用户指定清理3天前的日志
            binlog_cleanup_message = await crud.purge_binary_logs(session, days=3)
            logger.info(binlog_cleanup_message)
            await progress_callback(60, binlog_cleanup_message)
        except OperationalError as e:
            # 检查是否是权限不足的错误 (MySQL error code 1227)
            if e.orig and hasattr(e.orig, 'args') and len(e.orig.args) > 0 and e.orig.args[0] == 1227:
                binlog_cleanup_message = "Binlog 清理失败: 数据库用户缺少 SUPER 或 BINLOG_ADMIN 权限。此为正常现象，可安全忽略。"
                logger.warning(binlog_cleanup_message)
                await progress_callback(60, binlog_cleanup_message)
            else:
                # 其他操作错误，仍然记录详细信息
                binlog_cleanup_message = f"Binlog 清理失败: {e}"
                logger.error(binlog_cleanup_message, exc_info=True)
                await progress_callback(60, binlog_cleanup_message)
        except Exception as e:
            # 记录错误，但不中断任务
            binlog_cleanup_message = f"Binlog 清理失败: {e}"
            logger.error(binlog_cleanup_message, exc_info=True)
            await progress_callback(60, binlog_cleanup_message)

    # --- 3. 数据库表优化 ---
    await progress_callback(70, "正在执行数据库表优化...")
    
    try:
        optimization_message = await crud.optimize_database(session, db_type)
        logger.info(f"数据库优化结果: {optimization_message}")
    except Exception as e:
        optimization_message = f"数据库优化失败: {e}"
        logger.error(optimization_message, exc_info=True)
        # 即使优化失败，也不应导致整个任务失败，仅记录错误

    await progress_callback(90, optimization_message)

    final_message = f"数据库维护完成。{optimization_message}"
    raise TaskSuccess(final_message)
