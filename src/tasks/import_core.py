"""核心导入任务模块"""
import asyncio
import logging
from typing import Callable, Optional, List
from sqlalchemy import select, update as sql_update
from sqlalchemy.ext.asyncio import AsyncSession

from src.db import crud, orm_models, models, sync_postgres_sequence, ConfigManager
from src.services import ScraperManager, TaskManager, TaskSuccess, TaskPauseForRateLimit, MetadataSourceManager, TitleRecognitionManager
from src.services.import_existence_checker import check_anime_existence
from src.rate_limiter import RateLimiter, RateLimitExceededError
from src.utils import download_image
from src.utils.episode_filter import get_and_apply_single_episode_filter

logger = logging.getLogger(__name__)

# why：作品身份创建是极短的数据库步骤，使用单锁可覆盖别名/识别词导致的不同标题键，
# 同时避免立即任务与下载、后备队列并发各建一条；弹幕获取与导入不在锁内。
_ANIME_CREATE_LOCK = asyncio.Lock()


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
    tmdb_id: Optional[str] = None,
    tvdb_id: Optional[str] = None,
    imdb_id: Optional[str] = None,
) -> int:
    """
    智能查找或创建anime，避免条目分裂。

    优先级（三段式）：
    1. 数据源检查（provider + mediaId）- 最精确
    2. 元数据ID检查（tmdbId/tvdbId/imdbId + season）- 跨源防分裂
    3. 标题匹配（现有逻辑）- 兜底方案

    返回 anime_id
    """
    async with _ANIME_CREATE_LOCK:
        # why：锁内必须二次查询并把创建结果提交；否则其它 session 看不到 flush 的未提交行，
        # 立即任务与下载/fallback 队列仍可能同时各建一条。
        result = await check_anime_existence(
            session,
            provider=provider,
            media_id=mediaId,
            title=title,
            media_type=mediaType,
            season=season,
            year=year,
            tmdb_id=tmdb_id,
            tvdb_id=tvdb_id,
            imdb_id=imdb_id,
            title_recognition_manager=title_recognition_manager,
        )

        if result["found"]:
            anime_id = result["anime_id"]
            logger.info(
                f"✓ 三段式检查命中({result['stage']}): {result['reason']}, "
                f"复用anime_id={anime_id}"
            )
            # why：强标识需在释放创建锁前落库，后续并发任务才能立即通过 Stage2 复用。
            await crud.update_metadata_if_empty(
                session, anime_id, tmdb_id=tmdb_id, tvdb_id=tvdb_id, imdb_id=imdb_id,
            )
            if mediaId and str(mediaId).strip():
                await crud.link_source_to_anime(session, anime_id, provider, mediaId)
            await session.commit()
            return anime_id

        logger.info(
            f"○ 三段式检查未命中，创建新条目: title='{title}', "
            f"season={season}, type={mediaType}, year={year}"
        )
        anime_id = await crud.get_or_create_anime(
            session, title, mediaType, season, imageUrl, local_image_path,
            year, title_recognition_manager, provider,
        )
        await crud.update_metadata_if_empty(
            session, anime_id, tmdb_id=tmdb_id, tvdb_id=tvdb_id, imdb_id=imdb_id,
        )
        if mediaId and str(mediaId).strip():
            await crud.link_source_to_anime(session, anime_id, provider, mediaId)
        # why：先提交作品身份、数据源与强标识再释放锁，后续并发任务可立即复用。
        await session.commit()
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
    # 新增: 媒体服务三级 ID（用于 webhook 删除联动）
    mediaServerType: Optional[str] = None,
    mediaServerSeriesId: Optional[str] = None,
    mediaServerSeasonId: Optional[str] = None,
    mediaServerEpisodeId: Optional[str] = None,
    # 新增: 任务完成后是否自动开启增量追更（用于日历订阅等需要持续追更的入口）
    enable_incremental_refresh: bool = False,
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

    # ===== 反向偏移：将 Emby/用户传入的偏移后集号还原为源站原始集号 =====
    # Webhook/外部API 传来的 currentEpisodeIndex 和 selectedEpisodes 是偏移后的值（Emby视角），
    # 但源站分集列表使用的是原始集号。需要先反向偏移才能在源站找到正确的分集。
    # 入库时 _import_episodes_iteratively 中会再做正向偏移，恢复为数据库期望的偏移后值。
    source_episode_index = currentEpisodeIndex  # 用于源站查找的集号（反向偏移后）
    source_selected_episodes = selectedEpisodes  # 用于源站筛选的集号列表（反向偏移后）

    if title_recognition_manager and title_to_use:
        # 对单集导入的 currentEpisodeIndex 做反向偏移
        if currentEpisodeIndex is not None:
            try:
                reversed_ep = await title_recognition_manager.reverse_episode_offset(
                    title_to_use, currentEpisodeIndex, provider
                )
                if reversed_ep != currentEpisodeIndex:
                    logger.info(f"反向偏移: '{title_to_use}' Emby第{currentEpisodeIndex}集 => 源站第{reversed_ep}集")
                    source_episode_index = reversed_ep
            except Exception as e:
                logger.warning(f"反向偏移失败，使用原始集数: {e}")

        # 对多集导入的 selectedEpisodes 做反向偏移
        if selectedEpisodes is not None:
            reversed_selected = []
            for ep_idx in selectedEpisodes:
                if ep_idx is None:
                    reversed_selected.append(ep_idx)
                    continue
                try:
                    reversed_ep = await title_recognition_manager.reverse_episode_offset(
                        title_to_use, ep_idx, provider
                    )
                    reversed_selected.append(reversed_ep)
                    if reversed_ep != ep_idx:
                        logger.info(f"反向偏移: '{title_to_use}' selectedEpisode {ep_idx} => 源站 {reversed_ep}")
                except Exception as e:
                    logger.warning(f"反向偏移 selectedEpisode {ep_idx} 失败，使用原始值: {e}")
                    reversed_selected.append(ep_idx)
            if reversed_selected != list(selectedEpisodes):
                logger.info(f"selectedEpisodes 反向偏移: {list(selectedEpisodes)} => {reversed_selected}")
                source_selected_episodes = reversed_selected

    # 媒体库整季导入时, 需要先获取完整分集列表, 再按 selectedEpisodes 做本地筛选
    target_episode_index = source_episode_index  # 使用反向偏移后的集号查源站
    if selectedEpisodes is not None:
        target_episode_index = None

    episodes = await manager.get_episodes_routed(
        provider, mediaId,
        target_episode_index=target_episode_index,
        db_media_type=mediaType
    )

    # 应用单剧过滤规则
    if episodes:
        episodes = await get_and_apply_single_episode_filter(
            episodes, config_manager, title_to_use, provider, mediaId
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
                    tmdb_id=tmdbId,
                    tvdb_id=tvdbId,
                    imdb_id=imdbId,
                )

                # 更新元数据（如果anime是新创建的或字段为空）
                await crud.update_metadata_if_empty(
                    session, anime_id,
                    tmdb_id=tmdbId,
                    imdb_id=imdbId,
                    tvdb_id=tvdbId,
                    douban_id=doubanId,
                    bangumi_id=bangumiId,
                    media_server_type=mediaServerType,
                    media_server_series_id=mediaServerSeriesId,
                    media_server_season_id=mediaServerSeasonId,
                )

                # 链接数据源（如果还没有链接）
                source_id = await crud.link_source_to_anime(session, anime_id, provider, mediaId)

                # 任务级别的「完成后开启追更」标记（如日历订阅入口）
                if enable_incremental_refresh:
                    await session.execute(
                        sql_update(orm_models.AnimeSource)
                        .where(orm_models.AnimeSource.id == source_id)
                        .values(incrementalRefreshEnabled=True)
                    )
                    logger.info(f"已为源 ID {source_id} 开启增量追更（来自订阅入口）")

                episode_title = f"第 {currentEpisodeIndex} 集"
                episode_db_id = await crud.create_episode_if_not_exists(session, anime_id, source_id, currentEpisodeIndex, episode_title, None, "failover")

                # 写入媒体服务 Episode ID（webhook 删除联动用）
                if mediaServerEpisodeId:
                    await crud.update_episode_media_server_id(session, episode_db_id, mediaServerEpisodeId)

                added_count = await crud.save_danmaku_for_episode(session, episode_db_id, comments, config_manager)
                await session.commit()

                # 自动获取别名
                await _get_fetch_and_save_aliases()(
                    session, anime_id, title_to_use, mediaType,
                    metadata_manager, tmdb_id=tmdbId, year=year,
                )

                final_message = (f"通过故障转移导入完成，共获取 {added_count} 条弹幕。" if added_count > 0 else "通过故障转移导入完成，暂无弹幕数据。") + (" (警告：海报图片下载失败)" if image_download_failed else "")
                raise TaskSuccess(final_message)
            else:
                msg = f"未能找到第 {currentEpisodeIndex} 集。" if currentEpisodeIndex else "未能获取到任何分集。"
                logger.error(f"任务失败: {msg} (provider='{provider}', media_id='{mediaId}')")
                raise ValueError(msg)
        else:
            raise TaskSuccess("未找到任何分集信息。")

    # 如果是媒体库整季导入, 再按 selectedEpisodes 对分集做一次本地筛选
    # 关键：使用反向偏移后的 source_selected_episodes 与源站集号匹配
    if selectedEpisodes is not None:
        # 用反向偏移后的集号去匹配源站分集
        source_set = {idx for idx in source_selected_episodes if idx is not None}
        # 保留原始偏移后的集号集合（用于日志和降级逻辑中的数量计算）
        original_selected_set = {idx for idx in selectedEpisodes if idx is not None}
        original_count = len(episodes)
        if source_set:
            filtered = [ep for ep in episodes if ep.episodeIndex in source_set]
        else:
            filtered = []

        if filtered:
            # 按集号精确匹配成功（反向偏移后的源站集号匹配成功）
            episodes = filtered
            logger.info(
                f"媒体库整季导入: 按选择的分集筛选(精确匹配), 源共有 {original_count} 集, 保留 {len(episodes)} 集: {sorted(source_set)}"
            )
        else:
            # 精确匹配失败：尝试通过 TMDB 剧集组等价映射将 Emby 季内集号翻译为弹幕源绝对集号
            translated_set = set()
            if original_selected_set and season:
                try:
                    # 先通过 tmdbId 查 anime 的 group_id
                    group_id_for_mapping = None
                    if tmdbId:
                        # 查 anime_metadata 找关联的剧集组
                        from src.db.orm_models import AnimeMetadata as _AnimeMetadata
                        _meta_stmt = (
                            select(_AnimeMetadata.animeId, _AnimeMetadata.tmdbEpisodeGroupId)
                            .where(
                                _AnimeMetadata.tmdbId == str(tmdbId),
                                _AnimeMetadata.tmdbEpisodeGroupId.isnot(None),
                            )
                            .limit(1)
                        )
                        _meta_res = await session.execute(_meta_stmt)
                        _meta_row = _meta_res.first()
                        if _meta_row:
                            group_id_for_mapping = _meta_row.tmdbEpisodeGroupId

                    if group_id_for_mapping:
                        ep_translation = await crud.get_episode_equivalence_batch(
                            session, group_id_for_mapping, season, list(original_selected_set)
                        )
                        if ep_translation:
                            translated_set = set(ep_translation.values())
                            logger.info(
                                f"媒体库整季导入: 剧集组等价映射翻译成功 (groupId={group_id_for_mapping}), "
                                f"Emby相对集号 {sorted(original_selected_set)} → 弹幕源绝对集号 {sorted(translated_set)}"
                            )
                        else:
                            logger.info(
                                f"媒体库整季导入: 剧集组等价映射无结果 (groupId={group_id_for_mapping}, season={season}), 将降级为数量截取"
                            )
                    else:
                        logger.info("媒体库整季导入: 未找到关联的TMDB剧集组，将降级为数量截取")
                except Exception as _eq_err:
                    logger.warning(f"媒体库整季导入: 剧集组等价映射查询异常: {_eq_err}，将降级为数量截取")

            if translated_set:
                filtered2 = [ep for ep in episodes if ep.episodeIndex in translated_set]
                if filtered2:
                    episodes = filtered2
                    logger.info(
                        f"媒体库整季导入: 按翻译后绝对集号筛选, 源共有 {original_count} 集, 保留 {len(episodes)} 集: {sorted(translated_set)}"
                    )
                else:
                    # 翻译后仍无法匹配，降级为数量截取
                    limit = len(original_selected_set)
                    episodes = episodes[:limit]
                    logger.info(
                        f"媒体库整季导入: 翻译后集号仍无匹配(翻译集号={sorted(translated_set)})，降级为按数量截取前 {limit} 集"
                    )
            else:
                # 无法翻译，降级为按数量截取前 N 集
                limit = len(original_selected_set) if original_selected_set else original_count
                episodes = episodes[:limit]
                logger.info(
                    f"媒体库整季导入: 无法翻译集号，降级为按数量截取前 {limit} 集, 源共有 {original_count} 集, 保留 {len(episodes)} 集"
                )
            if not episodes:
                raise TaskSuccess("源中没有任何分集，未导入新的弹幕。")
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
                # 使用智能去重逻辑：三段式检查
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
                    tmdb_id=tmdbId,
                    tvdb_id=tvdbId,
                    imdb_id=imdbId,
                )

            # 更新元数据（如果anime是新创建的或字段为空）
            await crud.update_metadata_if_empty(
                session, anime_id,
                tmdb_id=tmdbId,
                imdb_id=imdbId,
                tvdb_id=tvdbId,
                douban_id=doubanId,
                bangumi_id=bangumiId,
                media_server_type=mediaServerType,
                media_server_series_id=mediaServerSeriesId,
                media_server_season_id=mediaServerSeasonId,
            )

            # 链接数据源（如果还没有链接）
            source_id = await crud.link_source_to_anime(session, anime_id, provider, mediaId)

            # 任务级别的「完成后开启追更」标记（如日历订阅入口）
            if enable_incremental_refresh:
                await session.execute(
                    sql_update(orm_models.AnimeSource)
                    .where(orm_models.AnimeSource.id == source_id)
                    .values(incrementalRefreshEnabled=True)
                )
                logger.info(f"已为源 ID {source_id} 开启增量追更（来自订阅入口）")

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
    try:
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
            fallback_type=fallback_type,  # 传递后备类型
            title_recognition_manager=title_recognition_manager,  # 传递识别词管理器（用于 partial_offset）
            anime_title=title_to_use,  # 传递番剧标题（用于 partial_offset 规则匹配）
        )
    except RateLimitExceededError as e:
        # 单源配额已满，转为任务暂停，释放 worker 给其他源
        logger.warning(f"下载分集时触发单源流控，暂停任务等待重试: {e}")
        raise TaskPauseForRateLimit(
            retry_after_seconds=e.retry_after_seconds,
            message=f"速率受限，将在 {e.retry_after_seconds:.0f} 秒后自动重试..."
        )

    # 处理追更任务的失败计数
    if is_incremental_refresh and incremental_refresh_source_id:
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

    # 写入媒体服务 Episode ID（webhook 删除联动用）
    # 仅对 webhook 单集导入有效：找到 currentEpisodeIndex 对应的 Episode 记录并写入 ItemId
    if mediaServerEpisodeId and currentEpisodeIndex is not None and source_id:
        try:
            ep_stmt = select(orm_models.Episode).where(
                orm_models.Episode.sourceId == source_id,
                orm_models.Episode.episodeIndex == currentEpisodeIndex
            ).limit(1)
            ep_result = await session.execute(ep_stmt)
            target_episode = ep_result.scalar_one_or_none()
            if target_episode:
                target_episode.mediaServerEpisodeId = mediaServerEpisodeId
                await session.flush()
                logger.info(f"已写入媒体服务 Episode ID: episode_id={target_episode.id}, mediaServerEpisodeId={mediaServerEpisodeId}")
        except Exception as e:
            logger.warning(f"写入媒体服务 Episode ID 失败: {e}")

    if successful_episodes_indices:
        episode_range_str = _generate_episode_range_string(successful_episodes_indices)
        if total_comments_added > 0:
            final_message_parts.append(f"导入集: < {episode_range_str} >，共获取 {total_comments_added} 条弹幕")
        else:
            final_message_parts.append(f"导入集: < {episode_range_str} >，暂无弹幕数据")

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

    # 首先检查是否已存在数据源（按 provider + mediaId + season 精确匹配）
    anime_id = await crud.get_anime_id_by_source_media_id(session, request_data.provider, request_data.mediaId, season=request_data.season)
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
                tmdb_id=request_data.tmdbId,
                tvdb_id=request_data.tvdbId,
                imdb_id=request_data.imdbId,
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
    try:
        total_comments_added, successful_indices, skipped_indices, failed_count, failed_details = await _import_episodes_iteratively(
            session=session,
            scraper=scraper,
            rate_limiter=rate_limiter,
            progress_callback=progress_callback,
            episodes=episodes,
            anime_id=anime_id,
            source_id=source_id,
            first_episode_comments=first_episode_comments,
            config_manager=config_manager,
            # 注意：edited_import_task 不传 title_recognition_manager/anime_title
            # 因为前端编辑导入时已通过 preview-offset API 应用了 partial_offset 偏移
        )
    except RateLimitExceededError as e:
        # 单源配额已满，转为任务暂停，释放 worker 给其他源
        logger.warning(f"编辑导入下载分集时触发单源流控，暂停任务等待重试: {e}")
        raise TaskPauseForRateLimit(
            retry_after_seconds=e.retry_after_seconds,
            message=f"速率受限，将在 {e.retry_after_seconds:.0f} 秒后自动重试..."
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
        final_message = f"编辑导入完成，导入集: < {episode_range_str} >，共获取 {total_comments_added} 条弹幕。" if total_comments_added > 0 else f"编辑导入完成，导入集: < {episode_range_str} >，暂无弹幕数据。"
        if failed_count > 0:
            # 添加失败详情
            failure_details = []
            for ep_index, error_msg in sorted(failed_details.items()):
                failure_details.append(f"第{ep_index}集: {error_msg}")
            final_message += f"\n失败 {failed_count} 集:\n" + "\n".join(failure_details)
        raise TaskSuccess(final_message)

