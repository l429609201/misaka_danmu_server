"""核心导入任务模块"""
import logging
from typing import Callable, Optional, List
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.db import crud, orm_models, models, sync_postgres_sequence, ConfigManager
from src.services import ScraperManager, TaskManager, TaskSuccess, TaskPauseForRateLimit, MetadataSourceManager, TitleRecognitionManager
from src.rate_limiter import RateLimiter, RateLimitExceededError
from src.utils import download_image

logger = logging.getLogger(__name__)


# 延迟导入辅助函数
def _get_import_iteratively():
    from .download_helpers import _import_episodes_iteratively
    return _import_episodes_iteratively

def _get_generate_episode_range_string():
    from .utils import generate_episode_range_string
    return generate_episode_range_string


# 延迟导入别名服务
def _get_fetch_and_save_aliases():
    from src.services.alias_service import fetch_and_save_aliases
    return fetch_and_save_aliases


async def _get_or_create_anime_with_deduplication(
    session: AsyncSession,
    provider: str,
    mediaId: str,
    title: str,
    mediaType: str,
    season: int,
    imageUrl: Optional[str],
    local_image_path: Optional[str],
    year: Optional[int],
    title_recognition_manager,
) -> int:
    """
    智能查找或创建anime，避免条目分裂。

    优先级：
    1. 数据源检查（provider + mediaId）- 最精确
    2. 标题匹配（现有逻辑）- 兜底方案

    返回 anime_id
    """
    # 第1层：检查数据源是否已存在
    existing_anime_id = await crud.get_anime_id_by_source_media_id(session, provider, mediaId)
    if existing_anime_id:
        logger.info(f"✓ 数据源已存在: provider={provider}, mediaId={mediaId}, 复用anime_id={existing_anime_id}")
        return existing_anime_id

    # 第2层：使用标题匹配（现有逻辑）
    logger.info(f"○ 数据源未找到匹配，使用标题匹配: title='{title}', season={season}")
    anime_id = await crud.get_or_create_anime(
        session, title, mediaType, season, imageUrl, local_image_path, year, title_recognition_manager, provider
    )
    return anime_id


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
    config_manager: ConfigManager,
    metadata_manager: MetadataSourceManager,
    tmdbId: Optional[str],
    imdbId: Optional[str],
    tvdbId: Optional[str],
    bangumiId: Optional[str],
    progress_callback: Callable,
    session: AsyncSession,
    manager: ScraperManager,
    task_manager: TaskManager,
    rate_limiter: RateLimiter,
    title_recognition_manager: TitleRecognitionManager,
    # 新增: 补充源信息
    supplementProvider: Optional[str] = None,
    supplementMediaId: Optional[str] = None,
    # 新增: 后备任务标识
    is_fallback: bool = False,
    fallback_type: Optional[str] = None,
    # 新增: 预分配的anime_id（用于匹配后备）
    preassignedAnimeId: Optional[int] = None,
    # 新增: 媒体库整季导入时, 指定要导入的分集索引列表
    selectedEpisodes: Optional[List[int]] = None,
    # 新增: 追更任务标识,用于失败计数
    is_incremental_refresh: bool = False,
    incremental_refresh_source_id: Optional[int] = None,
):
    """
    后台任务：执行从指定数据源导入弹幕的完整流程。
    修改流程：先获取弹幕，成功后再创建数据库条目。

    Args:
        is_fallback: 是否为后备任务（默认False）
        fallback_type: 后备类型 ("match" 或 "search"，仅当is_fallback=True时需要）
    """
    _import_episodes_iteratively = _get_import_iteratively()
    _generate_episode_range_string = _get_generate_episode_range_string()

    scraper = manager.get_scraper(provider)
    title_to_use = animeTitle.strip()
    season_to_use = season

    await progress_callback(10, "正在获取分集列表...")

    # 媒体库整季导入时, 需要先获取完整分集列表, 再按 selectedEpisodes 做本地筛选
    target_episode_index = currentEpisodeIndex
    if selectedEpisodes is not None:
        target_episode_index = None

    episodes = await scraper.get_episodes(
        mediaId,
        target_episode_index=target_episode_index,
        db_media_type=mediaType
    )

    # 如果主源无分集且有补充源,使用补充源获取分集URL
    if not episodes and supplementProvider and supplementMediaId:
        logger.info(f"主源无分集,尝试使用补充源 {supplementProvider} 获取分集列表")
        await progress_callback(12, f"主源无分集,尝试使用补充源 {supplementProvider}...")

        try:
            # 获取补充源实例
            supplement_source = metadata_manager.sources.get(supplementProvider)
            if not supplement_source:
                logger.warning(f"补充源 {supplementProvider} 不可用")
            elif not getattr(supplement_source, 'supports_episode_urls', False):
                logger.warning(f"补充源 {supplementProvider} 不支持分集URL获取")
            else:
                # 获取补充源详情
                supplement_details = await supplement_source.get_details(supplementMediaId, None)
                if not supplement_details:
                    logger.warning(f"无法获取补充源详情 (mediaId={supplementMediaId})")
                else:
                    logger.info(f"补充源详情: {supplement_details.title}")

                    # 使用补充源获取分集URL列表 (统一使用公开方法)
                    await progress_callback(12, f"正在从{supplementProvider}获取分集URL...")
                    episode_urls = await supplement_source.get_episode_urls(
                        supplementMediaId, provider  # 目标平台
                    )
                    await progress_callback(18, f"{supplementProvider}解析完成,获取到 {len(episode_urls)} 个播放链接")

                    logger.info(f"补充源获取到 {len(episode_urls)} 个分集URL")

                    if episode_urls:
                        # 解析URL获取分集信息
                        episodes = []
                        for i, url in episode_urls:
                            try:
                                # 从URL提取episode_id
                                episode_id = await scraper.get_id_from_url(url)
                                if episode_id:
                                    episodes.append(models.ProviderEpisodeInfo(
                                        provider=provider,
                                        episodeId=episode_id,
                                        title=f"第{i}集",
                                        episodeIndex=i,
                                        url=url
                                    ))
                            except Exception as e:
                                logger.warning(f"解析URL失败 (第{i}集): {e}")

                        logger.info(f"补充源成功解析 {len(episodes)} 个分集")
                        await progress_callback(20, f"补充源成功获取 {len(episodes)} 个分集")
        except Exception as e:
            logger.error(f"使用补充源获取分集失败: {e}", exc_info=True)

    if not episodes:
        # 故障转移逻辑保持不变
        if currentEpisodeIndex:
            await progress_callback(15, "未找到分集列表，尝试故障转移...")
            comments = await scraper.get_comments(mediaId, progress_callback=lambda p, msg: progress_callback(15 + p * 0.05, msg))

            if comments:
                logger.info(f"故障转移成功，找到 {len(comments)} 条弹幕。正在保存...")
                await progress_callback(20, f"故障转移成功，找到 {len(comments)} 条弹幕。")

                local_image_path = await download_image(imageUrl, session, manager, provider)
                image_download_failed = bool(imageUrl and not local_image_path)

                # 使用智能去重逻辑：优先检查数据源
                anime_id = await _get_or_create_anime_with_deduplication(
                    session=session,
                    provider=provider,
                    mediaId=mediaId,
                    title=title_to_use,
                    mediaType=mediaType,
                    season=season_to_use,
                    imageUrl=imageUrl,
                    local_image_path=local_image_path,
                    year=year,
                    title_recognition_manager=title_recognition_manager,
                )

                # 更新元数据（如果anime是新创建的或字段为空）
                await crud.update_metadata_if_empty(
                    session, anime_id,
                    tmdb_id=tmdbId,
                    imdb_id=imdbId,
                    tvdb_id=tvdbId,
                    douban_id=doubanId,
                    bangumi_id=bangumiId
                )

                # 链接数据源（如果还没有链接）
                source_id = await crud.link_source_to_anime(session, anime_id, provider, mediaId)

                episode_title = f"第 {currentEpisodeIndex} 集"
                episode_db_id = await crud.create_episode_if_not_exists(session, anime_id, source_id, currentEpisodeIndex, episode_title, None, "failover")

                added_count = await crud.save_danmaku_for_episode(session, episode_db_id, comments, config_manager)
                await session.commit()

                # 自动获取别名
                await _get_fetch_and_save_aliases()(
                    session, anime_id, title_to_use, mediaType,
                    metadata_manager, tmdb_id=tmdbId, year=year,
                )

                final_message = f"通过故障转移导入完成，共新增 {added_count} 条弹幕。" + (" (警告：海报图片下载失败)" if image_download_failed else "")
                raise TaskSuccess(final_message)
            else:
                msg = f"未能找到第 {currentEpisodeIndex} 集。" if currentEpisodeIndex else "未能获取到任何分集。"
                logger.error(f"任务失败: {msg} (provider='{provider}', media_id='{mediaId}')")
                raise ValueError(msg)
        else:
            raise TaskSuccess("未找到任何分集信息。")

    # 如果是媒体库整季导入, 再按 selectedEpisodes 对分集做一次本地筛选
    if selectedEpisodes is not None:
        selected_set = {idx for idx in selectedEpisodes if idx is not None}
        original_count = len(episodes)
        if selected_set:
            episodes = [ep for ep in episodes if ep.episodeIndex in selected_set]
        else:
            episodes = []
        logger.info(
            f"媒体库整季导入: 按选择的分集筛选, 源共有 {original_count} 集, 保留 {len(episodes)} 集: {sorted(selected_set) if selected_set else []}"
        )
        if not episodes:
            raise TaskSuccess("源中没有媒体库选择的任一分集，未导入新的弹幕。")
        # 新增: 媒体库整季导入时, 在下载任何弹幕后先检查数据库中已有的分集
        indices_to_check = [ep.episodeIndex for ep in episodes if ep.episodeIndex is not None]
        existing_indices = []
        if indices_to_check:
            existing_stmt = (
                select(orm_models.Episode.episodeIndex)
                .join(orm_models.AnimeSource, orm_models.Episode.sourceId == orm_models.AnimeSource.id)
                .where(
                    orm_models.AnimeSource.providerName == provider,
                    orm_models.AnimeSource.mediaId == mediaId,
                    orm_models.Episode.episodeIndex.in_(indices_to_check),
                    orm_models.Episode.danmakuFilePath.isnot(None),
                    orm_models.Episode.commentCount > 0,
                )
            )
            existing_res = await session.execute(existing_stmt)
            existing_indices = list({row for row in existing_res.scalars().all()})

        if existing_indices:
            logger.info(
                f"媒体库整季导入: 在数据库中发现已存在弹幕的集数: {sorted(existing_indices)}"
            )

        # 如果媒体库选择的分集全部已经有弹幕, 直接返回成功, 完全跳过网络请求
        if indices_to_check and set(indices_to_check).issubset(set(existing_indices)):
            skipped_range_str = _generate_episode_range_string(sorted(existing_indices))
            final_message = f"导入完成，跳过集: < {skipped_range_str} > (已有弹幕)，未新增弹幕。"
            raise TaskSuccess(final_message)

        # 为了后续验证/下载优先处理"尚未导入"的分集, 将 episodes 重新排序:
        #   1) 还没有弹幕的集数在前
        #   2) 已有弹幕的集数在后
        if existing_indices:
            existing_set = set(existing_indices)
            episodes_to_import = [ep for ep in episodes if ep.episodeIndex not in existing_set]
            already_imported = [ep for ep in episodes if ep.episodeIndex in existing_set]
            episodes = episodes_to_import + already_imported


    # 修改：先尝试获取第一集的弹幕，确认能获取到弹幕后再创建条目
    anime_id = None
    source_id = None
    local_image_path = None
    image_download_failed = False
    first_episode_success = False

    # 先尝试获取第一集弹幕来验证数据源有效性
    first_episode = episodes[0]
    await progress_callback(20, f"正在验证数据源有效性: {first_episode.title}")

    try:
        # 根据是否为后备任务选择不同的速率限制方法
        if is_fallback:
            if not fallback_type:
                raise ValueError("后备任务必须指定fallback_type参数")
            await rate_limiter.check_fallback(fallback_type, scraper.provider_name)
        else:
            await rate_limiter.check(scraper.provider_name)
        first_comments = await scraper.get_comments(first_episode.episodeId, progress_callback=lambda p, msg: progress_callback(20 + p * 0.1, msg))

        # 只有在实际获取到弹幕时才增加计数
        if first_comments is not None:
            if is_fallback:
                await rate_limiter.increment_fallback(fallback_type, scraper.provider_name)
            else:
                await rate_limiter.increment(scraper.provider_name)

        if first_comments:
            first_episode_success = True
            logger.info(f"数据源验证成功，第一集获取到 {len(first_comments)} 条弹幕")
            await progress_callback(30, "数据源验证成功，正在创建数据库条目...")

            # 下载海报图片
            if imageUrl:
                try:
                    local_image_path = await download_image(imageUrl, session, manager, provider)
                except Exception as e:
                    logger.warning(f"海报下载失败: {e}")
                    image_download_failed = True

            # 创建主条目
            # 修正：确保在创建时也使用年份进行重复检查
            # 如果有预分配的anime_id（匹配后备），则直接使用
            if preassignedAnimeId:
                logger.info(f"使用预分配的anime_id: {preassignedAnimeId}")
                anime_id = preassignedAnimeId

                # 检查数据库中是否已有这个ID的条目
                from src.db.orm_models import Anime
                from src.core.timezone import get_now
                stmt = select(Anime).where(Anime.id == anime_id)
                result = await session.execute(stmt)
                existing_anime = result.scalar_one_or_none()

                if not existing_anime:
                    # 如果不存在，创建新的anime条目
                    new_anime = Anime(
                        id=anime_id,
                        title=title_to_use,
                        type=mediaType,
                        season=season_to_use,
                        imageUrl=imageUrl,
                        localImagePath=local_image_path,
                        year=year,
                        createdAt=get_now(),
                        updatedAt=get_now()
                    )
                    session.add(new_anime)
                    await session.flush()
                    logger.info(f"创建新的anime条目: ID={anime_id}, 标题='{title_to_use}'")

                    # 同步PostgreSQL序列(避免主键冲突)
                    await sync_postgres_sequence(session)
                else:
                    logger.info(f"anime条目已存在: ID={anime_id}, 标题='{existing_anime.title}'")
            else:
                # 使用智能去重逻辑：优先检查数据源
                anime_id = await _get_or_create_anime_with_deduplication(
                    session=session,
                    provider=provider,
                    mediaId=mediaId,
                    title=title_to_use,
                    mediaType=mediaType,
                    season=season_to_use,
                    imageUrl=imageUrl,
                    local_image_path=local_image_path,
                    year=year,
                    title_recognition_manager=title_recognition_manager,
                )

            # 更新元数据（如果anime是新创建的或字段为空）
            await crud.update_metadata_if_empty(
                session, anime_id,
                tmdb_id=tmdbId,
                imdb_id=imdbId,
                tvdb_id=tvdbId,
                douban_id=doubanId,
                bangumi_id=bangumiId
            )

            # 链接数据源（如果还没有链接）
            source_id = await crud.link_source_to_anime(session, anime_id, provider, mediaId)
            await session.commit()

            # 自动获取别名
            await _get_fetch_and_save_aliases()(
                session, anime_id, title_to_use, mediaType,
                metadata_manager, tmdb_id=tmdbId, year=year,
            )

            logger.info(f"主条目创建完成 (Anime ID: {anime_id}, Source ID: {source_id})")
        else:
            logger.warning(f"第一集未获取到弹幕，数据源可能无效")
    except RateLimitExceededError as e:
        # 抛出暂停异常，让任务管理器处理
        logger.warning(f"通用导入任务因达到速率限制而暂停: {e}")
        raise TaskPauseForRateLimit(
            retry_after_seconds=e.retry_after_seconds,
            message=f"速率受限，将在 {e.retry_after_seconds:.0f} 秒后自动重试..."
        )
    except Exception as e:
        logger.error(f"验证第一集时发生错误: {e}")

    # 如果第一集验证失败，不创建条目
    if not first_episode_success:
        raise TaskSuccess("数据源验证失败，未能获取到任何弹幕，未创建数据库条目。")

    # 处理所有分集（包括第一集）
    total_comments_added, successful_episodes_indices, skipped_episodes_indices, failed_episodes_count, failed_episodes_details = await _import_episodes_iteratively(
        session=session,
        scraper=scraper,
        rate_limiter=rate_limiter,
        progress_callback=progress_callback,
        episodes=episodes,
        anime_id=anime_id,
        source_id=source_id,
        first_episode_comments=first_comments,  # 传递第一集已获取的弹幕
        config_manager=config_manager,
        is_single_episode=currentEpisodeIndex is not None,  # 传递是否为单集下载模式
        is_fallback=is_fallback,  # 传递后备任务标识
        fallback_type=fallback_type  # 传递后备类型
    )

    # 处理追更任务的失败计数
    if is_incremental_refresh and incremental_refresh_source_id:
        from sqlalchemy import update as sql_update

        if not successful_episodes_indices and not skipped_episodes_indices and failed_episodes_count > 0:
            # 追更失败,增加失败计数
            stmt = sql_update(orm_models.AnimeSource).where(
                orm_models.AnimeSource.id == incremental_refresh_source_id
            ).values(
                incrementalRefreshFailures=orm_models.AnimeSource.incrementalRefreshFailures + 1
            )
            await session.execute(stmt)
            await session.commit()

            # 检查失败次数是否达到配置的上限
            source_stmt = select(orm_models.AnimeSource).where(orm_models.AnimeSource.id == incremental_refresh_source_id)
            source_result = await session.execute(source_stmt)
            source_obj = source_result.scalar_one_or_none()

            # 获取配置的最大失败次数（默认10次）
            max_failures = int(await crud.get_config_value(session, "incrementalRefreshMaxFailures", "10"))
            if source_obj and source_obj.incrementalRefreshFailures >= max_failures:
                # 达到上限,自动禁用追更
                disable_stmt = sql_update(orm_models.AnimeSource).where(
                    orm_models.AnimeSource.id == incremental_refresh_source_id
                ).values(
                    incrementalRefreshEnabled=False
                )
                await session.execute(disable_stmt)
                await session.commit()
                logger.warning(f"源 ID {incremental_refresh_source_id} 追更失败次数达到10次,已自动禁用追更")
        else:
            # 追更成功,重置失败计数
            stmt = sql_update(orm_models.AnimeSource).where(
                orm_models.AnimeSource.id == incremental_refresh_source_id
            ).values(
                incrementalRefreshFailures=0
            )
            await session.execute(stmt)
            await session.commit()
            logger.info(f"源 ID {incremental_refresh_source_id} 追更成功,已重置失败计数")

    if not successful_episodes_indices and not skipped_episodes_indices and failed_episodes_count > 0:
        # 生成失败详情消息
        failure_details = []
        for ep_index, error_msg in sorted(failed_episodes_details.items()):
            failure_details.append(f"第{ep_index}集: {error_msg}")
        failure_msg = "导入完成，但所有分集弹幕获取失败。\n失败详情:\n" + "\n".join(failure_details)
        raise TaskSuccess(failure_msg)

    # 生成最终消息
    final_message_parts = []

    if successful_episodes_indices:
        episode_range_str = _generate_episode_range_string(successful_episodes_indices)
        final_message_parts.append(f"导入集: < {episode_range_str} >，新增 {total_comments_added} 条弹幕")

    if skipped_episodes_indices:
        skipped_range_str = _generate_episode_range_string(skipped_episodes_indices)
        final_message_parts.append(f"跳过集: < {skipped_range_str} > (已有弹幕)")

    final_message = "导入完成，" + "；".join(final_message_parts) + "。"

    if failed_episodes_count > 0:
        final_message += f" {failed_episodes_count} 个分集因网络或解析错误获取失败。"
    if image_download_failed:
        final_message += " (警告：海报图片下载失败)"
    raise TaskSuccess(final_message)


async def edited_import_task(
    request_data: "models.EditedImportRequest",
    progress_callback: Callable,
    session: AsyncSession,
    config_manager: ConfigManager,
    manager: ScraperManager,
    rate_limiter: RateLimiter,
    metadata_manager: MetadataSourceManager,
    title_recognition_manager: TitleRecognitionManager
):
    """后台任务：处理编辑后的导入请求。修改流程：先获取弹幕再创建条目。"""
    from .utils import extract_short_error_message
    _extract_short_error_message = extract_short_error_message
    _import_episodes_iteratively = _get_import_iteratively()
    _generate_episode_range_string = _get_generate_episode_range_string()

    scraper = manager.get_scraper(request_data.provider)

    episodes = request_data.episodes
    if not episodes:
        raise TaskSuccess("没有提供任何分集，任务结束。")

    # 首先检查是否已存在数据源
    anime_id = await crud.get_anime_id_by_source_media_id(session, request_data.provider, request_data.mediaId)
    source_id = None

    if anime_id:
        # 如果数据源已存在，检查哪些分集已经有弹幕
        sources = await crud.get_anime_sources(session, anime_id)
        for source in sources:
            if source['providerName'] == request_data.provider and source.get('mediaId') == request_data.mediaId:
                source_id = source['sourceId']
                break

        if source_id:
            existing_episodes = []
            for episode in episodes:
                # 检查该数据源的该集是否已经有弹幕（必须是相同 provider + media_id）
                stmt = (
                    select(orm_models.Episode.id)
                    .join(orm_models.AnimeSource, orm_models.Episode.sourceId == orm_models.AnimeSource.id)
                    .where(
                        orm_models.AnimeSource.providerName == request_data.provider,
                        orm_models.AnimeSource.mediaId == request_data.mediaId,
                        orm_models.Episode.episodeIndex == episode.episodeIndex,
                        orm_models.Episode.danmakuFilePath.isnot(None),
                        orm_models.Episode.commentCount > 0
                    )
                    .limit(1)
                )
                result = await session.execute(stmt)
                if result.scalar_one_or_none() is not None:
                    existing_episodes.append(episode.episodeIndex)

            if existing_episodes:
                episode_list = ", ".join(map(str, existing_episodes))
                logger.info(f"检测到已存在弹幕的分集: {episode_list}")
                # 过滤掉已存在的分集
                episodes = [ep for ep in episodes if ep.episodeIndex not in existing_episodes]
                if not episodes:
                    raise TaskSuccess(f"所有要导入的分集 ({episode_list}) 都已存在弹幕，无需重复导入。")
                else:
                    remaining_list = ", ".join(map(str, [ep.episodeIndex for ep in episodes]))
                    logger.info(f"将跳过已存在的分集 ({episode_list})，继续导入分集: {remaining_list}")

    # 先验证第一集能否获取弹幕
    first_episode = episodes[0]
    await progress_callback(10, f"正在验证数据源有效性: {first_episode.title}")

    first_episode_comments = None

    try:
        await rate_limiter.check(scraper.provider_name)
        first_episode_comments = await scraper.get_comments(first_episode.episodeId, progress_callback=lambda p, msg: progress_callback(10 + p * 0.1, msg))
        await rate_limiter.increment(scraper.provider_name)

        if first_episode_comments:
            await progress_callback(20, "数据源验证成功，正在创建数据库条目...")

            # 下载海报
            local_image_path = None
            if request_data.imageUrl:
                try:
                    local_image_path = await download_image(
                        request_data.imageUrl, session, manager, request_data.provider
                    )
                except Exception as e:
                    logger.warning(f"海报下载失败: {e}")

            # 使用智能去重逻辑：优先检查数据源
            anime_id = await _get_or_create_anime_with_deduplication(
                session=session,
                provider=request_data.provider,
                mediaId=request_data.mediaId,
                title=request_data.animeTitle,
                mediaType=request_data.mediaType,
                season=request_data.season,
                imageUrl=request_data.imageUrl,
                local_image_path=local_image_path,
                year=request_data.year,
                title_recognition_manager=title_recognition_manager,
            )

            # 更新元数据
            await crud.update_metadata_if_empty(
                session, anime_id,
                tmdb_id=request_data.tmdbId,
                imdb_id=request_data.imdbId,
                tvdb_id=request_data.tvdbId,
                douban_id=request_data.doubanId,
                bangumi_id=request_data.bangumiId,
                tmdb_episode_group_id=request_data.tmdbEpisodeGroupId
            )
            source_id = await crud.link_source_to_anime(session, anime_id, request_data.provider, request_data.mediaId)
            await session.commit()
        else:
            # 验证分集没有弹幕，数据源无效
            error_msg = f"数据源验证失败：'{first_episode.title}' 未获取到任何弹幕数据。请到 {request_data.provider} 源验证该视频是否有弹幕。未创建数据库条目。"
            logger.warning(error_msg)
            raise TaskSuccess(error_msg)
    except RateLimitExceededError as e:
        # 抛出暂停异常，让任务管理器处理
        logger.warning(f"编辑后导入任务因达到速率限制而暂停: {e}")
        raise TaskPauseForRateLimit(
            retry_after_seconds=e.retry_after_seconds,
            message=f"速率受限，将在 {e.retry_after_seconds:.0f} 秒后自动重试..."
        )
    except TaskSuccess:
        # 重新抛出 TaskSuccess 异常
        raise
    except Exception as e:
        # 其他异常（网络错误、解析错误等）
        short_error = _extract_short_error_message(e)
        error_msg = f"数据源验证失败：获取 '{first_episode.title}' 弹幕时发生错误 - {short_error}。未创建数据库条目。"
        logger.error(f"数据源验证失败：获取 '{first_episode.title}' 弹幕时发生错误: {e}", exc_info=True)
        raise TaskSuccess(error_msg)

    # 处理所有分集
    total_comments_added, successful_indices, skipped_indices, failed_count, failed_details = await _import_episodes_iteratively(
        session=session,
        scraper=scraper,
        rate_limiter=rate_limiter,
        progress_callback=progress_callback,
        episodes=episodes,
        anime_id=anime_id,
        source_id=source_id,
        first_episode_comments=first_episode_comments,
        config_manager=config_manager
    )

    if total_comments_added == 0:
        # 如果有失败详情，显示失败原因
        if failed_details:
            failure_details = []
            for ep_index, error_msg in sorted(failed_details.items()):
                failure_details.append(f"第{ep_index}集: {error_msg}")
            failure_msg = "编辑导入完成，但未找到任何新弹幕。\n失败详情:\n" + "\n".join(failure_details)
            raise TaskSuccess(failure_msg)
        else:
            raise TaskSuccess("编辑导入完成，但未找到任何新弹幕。")
    else:
        episode_range_str = _generate_episode_range_string(successful_indices)
        final_message = f"编辑导入完成，导入集: < {episode_range_str} >，新增 {total_comments_added} 条弹幕。"
        if failed_count > 0:
            # 添加失败详情
            failure_details = []
            for ep_index, error_msg in sorted(failed_details.items()):
                failure_details.append(f"第{ep_index}集: {error_msg}")
            final_message += f"\n失败 {failed_count} 集:\n" + "\n".join(failure_details)
        raise TaskSuccess(final_message)

