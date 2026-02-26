"""媒体服务器任务模块"""
import asyncio
import logging
from typing import Callable, Optional, List
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from src.db import crud, ConfigManager
from src.services import TaskManager, TaskSuccess, ScraperManager, MetadataSourceManager, TitleRecognitionManager, get_media_server_manager
from src.rate_limiter import RateLimiter

logger = logging.getLogger(__name__)


# 延迟导入辅助函数
def _get_webhook_search_and_dispatch_task():
    from .webhook import webhook_search_and_dispatch_task
    return webhook_search_and_dispatch_task


async def scan_media_server_library(
    server_id: int,
    library_ids: Optional[List[str]],
    session: AsyncSession,
    progress_callback: Callable
):
    """扫描媒体服务器的媒体库"""

    await progress_callback(0, "开始扫描媒体库...")

    manager = get_media_server_manager()
    server = manager.servers.get(server_id)
    if not server:
        raise ValueError(f"媒体服务器 {server_id} 不存在或未启用")

    # 获取服务器配置
    server_config = await crud.get_media_server_by_id(session, server_id)
    if not server_config:
        raise ValueError(f"媒体服务器配置 {server_id} 不存在")

    # 确定要扫描的媒体库
    selected_libraries = server_config.get("selectedLibraries", [])
    if library_ids:
        # 使用指定的媒体库
        scan_libraries = library_ids
    elif selected_libraries:
        # 使用配置中选中的媒体库
        scan_libraries = selected_libraries
    else:
        # 扫描所有媒体库
        all_libraries = await server.get_libraries()
        scan_libraries = [lib.id for lib in all_libraries]

    logger.info(f"开始扫描 {len(scan_libraries)} 个媒体库")

    total_items = 0
    for idx, library_id in enumerate(scan_libraries):
        library_progress_base = int((idx / len(scan_libraries)) * 100)
        library_progress_range = int(100 / len(scan_libraries))

        await progress_callback(
            library_progress_base,
            f"正在扫描媒体库 {idx + 1}/{len(scan_libraries)}..."
        )

        try:
            # 获取媒体库中的所有项目
            items = await server.get_library_items(library_id)

            logger.info(f"媒体库 {library_id} 获取到 {len(items)} 个项目,开始保存...")

            # 保存到数据库,并显示进度
            for item_idx, item in enumerate(items):
                # 每10个项目更新一次进度
                if item_idx % 10 == 0:
                    item_progress = int((item_idx / len(items)) * library_progress_range)
                    await progress_callback(
                        library_progress_base + item_progress,
                        f"正在保存媒体库 {idx + 1}/{len(scan_libraries)} 的项目 {item_idx}/{len(items)}..."
                    )

                await crud.create_media_item(
                    session,
                    server_id=server_id,
                    media_id=item.media_id,
                    library_id=library_id,
                    title=item.title,
                    media_type=item.media_type,
                    season=item.season,
                    episode=item.episode,
                    year=item.year,
                    tmdb_id=item.tmdb_id,
                    tvdb_id=item.tvdb_id,
                    imdb_id=item.imdb_id,
                    poster_url=item.poster_url
                )
                total_items += 1

            await session.commit()
            logger.info(f"媒体库 {library_id} 扫描完成,共 {len(items)} 个项目")

        except Exception as e:
            logger.error(f"扫描媒体库 {library_id} 失败: {e}", exc_info=True)
            await session.rollback()
            continue

    await progress_callback(100, f"扫描完成,共 {total_items} 个媒体项")
    raise TaskSuccess(f"媒体库扫描完成,共扫描到 {total_items} 个媒体项")


async def import_media_items(
    item_ids: List[int],
    session: AsyncSession,
    task_manager: TaskManager,
    progress_callback: Callable,
    scraper_manager=None,
    metadata_manager=None,
    config_manager=None,
    ai_matcher_manager=None,
    rate_limiter=None,
    title_recognition_manager=None
):
    """导入媒体项(按季度导入电视剧,电影直接导入)"""
    from src.db.orm_models import MediaItem

    webhook_search_and_dispatch_task = _get_webhook_search_and_dispatch_task()

    # 如果没有传入manager,从全局获取
    if scraper_manager is None:
        from src.main import scraper_manager as global_scraper_manager
        scraper_manager = global_scraper_manager
    if metadata_manager is None:
        from src.main import metadata_manager as global_metadata_manager
        metadata_manager = global_metadata_manager
    if config_manager is None:
        raise ValueError("config_manager is required")
    if ai_matcher_manager is None:
        raise ValueError("ai_matcher_manager is required")
    if rate_limiter is None:
        raise ValueError("rate_limiter is required")
    if title_recognition_manager is None:
        raise ValueError("title_recognition_manager is required")

    await progress_callback(0, "开始导入媒体项...")

    # 获取所有媒体项
    items_stmt = select(MediaItem).where(MediaItem.id.in_(item_ids))
    result = await session.execute(items_stmt)
    items = result.scalars().all()

    if not items:
        raise ValueError("未找到要导入的媒体项")

    # 按类型分组
    movies = []
    tv_shows = {}  # {(title, season): [items]}

    for item in items:
        if item.mediaType == 'movie':
            movies.append(item)
        elif item.mediaType == 'tv_series':
            key = (item.title, item.season)
            if key not in tv_shows:
                tv_shows[key] = []
            tv_shows[key].append(item)

    # 计算任务数: 电影数 + 电视剧集数(每集单独计算)
    # 统计任务数量: 电影按部, 电视按季度
    tv_season_count = len(tv_shows)
    total_tasks = len(movies) + tv_season_count
    completed = 0

    logger.info(f"准备导入: {len(movies)} 部电影, {tv_season_count} 个电视季度")

    # 导入电影
    for movie in movies:
        await progress_callback(
            int((completed / total_tasks) * 100),
            f"导入电影: {movie.title}..."
        )

        try:
            # 触发webhook式搜索
            # 注意: lambda 使用默认参数 m=movie 来捕获当前循环变量的值,
            # 避免闭包捕获引用导致所有任务都使用最后一个 movie 的数据
            task_id, _ = await task_manager.submit_task(
                lambda session, progress_callback, m=movie: webhook_search_and_dispatch_task(
                    animeTitle=m.title,
                    mediaType="movie",
                    season=1,
                    currentEpisodeIndex=1,
                    searchKeyword=m.title,
                    year=m.year,
                    tmdbId=m.tmdbId,
                    tvdbId=m.tvdbId,
                    imdbId=m.imdbId,
                    doubanId=None,
                    bangumiId=None,
                    webhookSource="media_server",
                    session=session,
                    progress_callback=progress_callback,
                    manager=scraper_manager,
                    task_manager=task_manager,
                    metadata_manager=metadata_manager,
                    config_manager=config_manager,
                    ai_matcher_manager=ai_matcher_manager,
                    rate_limiter=rate_limiter,
                    title_recognition_manager=title_recognition_manager
                ),
                title=f"自动导入 (库内): {movie.title}",
                queue_type="download"
            )
            logger.info(f"电影 {movie.title} 导入任务已提交: {task_id}")

            # 标记为已导入
            await crud.mark_media_items_imported(session, [movie.id])
            await session.commit()

            # 小延迟，避免瞬间提交过多任务
            await asyncio.sleep(0.2)

        except Exception as e:
            logger.error(f"导入电影 {movie.title} 失败: {e}", exc_info=True)
            await session.rollback()

        completed += 1

    # 导入电视节目(按季度合并为单个任务)
    for (title, season), season_items in tv_shows.items():
        season_str = f"S{season:02d}" if season is not None else "S??"
        await progress_callback(
            int((completed / total_tasks) * 100),
            f"导入电视节目: {title} {season_str} (共 {len(season_items)} 集)..."
        )

        try:
            # 选取该季度中集数最小的一集作为代表，用于搜索和匹配
            representative_item = min(
                season_items,
                key=lambda item: item.episode or 0
            )

            selected_episodes = sorted([item.episode for item in season_items if item.episode is not None])
            logger.info(f"电视节目 {title} {season_str} 选中的分集: {selected_episodes}")

            task_id, _ = await task_manager.submit_task(
                lambda session, progress_callback, item=representative_item, selected_eps=selected_episodes: webhook_search_and_dispatch_task(
                    animeTitle=item.title,
                    mediaType="tv_series",
                    season=item.season,
                    currentEpisodeIndex=item.episode,  # 使用代表集数进行匹配
                    searchKeyword=f"{item.title} S{item.season or 1:02d}E{item.episode or 1:02d}",
                    year=item.year,
                    tmdbId=item.tmdbId,
                    tvdbId=item.tvdbId,
                    imdbId=item.imdbId,
                    doubanId=None,
                    bangumiId=None,
                    webhookSource="media_server",
                    session=session,
                    progress_callback=progress_callback,
                    manager=scraper_manager,
                    task_manager=task_manager,
                    metadata_manager=metadata_manager,
                    config_manager=config_manager,
                    ai_matcher_manager=ai_matcher_manager,
                    rate_limiter=rate_limiter,
                    title_recognition_manager=title_recognition_manager,
                    selectedEpisodes=selected_eps,
                ),
                title=f"自动导入 (库内): {title} S{season:02d} (共 {len(season_items)} 集)",
                queue_type="download"
            )
            logger.info(f"电视节目 {title} S{season:02d} (共 {len(season_items)} 集) 导入任务已提交: {task_id}")

            # 标记该季度内所有选中分集为已导入
            await crud.mark_media_items_imported(session, [item.id for item in season_items])
            await session.commit()

            # 小延迟，避免瞬间提交过多任务
            await asyncio.sleep(0.2)

        except Exception as e:
            logger.error(f"导入电视节目 {title} S{season:02d} 失败: {e}", exc_info=True)
            await session.rollback()

        completed += 1  # 每个季度任务完成后递增

    await progress_callback(100, f"导入完成,共提交 {total_tasks} 个任务")
    raise TaskSuccess(f"媒体项导入完成,共提交 {total_tasks} 个任务")

