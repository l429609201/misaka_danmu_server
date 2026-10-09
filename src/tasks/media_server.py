"""媒体服务器任务模块"""
import asyncio
import logging
from typing import Callable, Optional, List
from sqlalchemy.ext.asyncio import AsyncSession

from src.workflows.media_import_preparation import (
    prepare_media_import, mark_media_submitted, collect_unimported_media_items,
)
from src.services.task_manager import TaskSuccess, TaskManager
from src.services.service_container import get_scraper_manager, get_metadata_service
from src.tasks.webhook import webhook_search_and_dispatch_task
from src.workflows.media_library_scan import scan_media_library

logger = logging.getLogger(__name__)


async def scan_media_server_library(
    server_id: int,
    library_ids: Optional[List[str]],
    session: AsyncSession,
    progress_callback: Callable
):
    """扫描媒体服务器的媒体库"""

    # 扫描及保存由 Workflow 管理，任务只转换完成状态。
    message = await scan_media_library(server_id, library_ids, progress_callback)
    raise TaskSuccess(message)


async def import_all_unimported_media_items(
    server_id: int,
    media_type: Optional[str],
    session: AsyncSession,
    task_manager: "TaskManager",
    progress_callback: Callable,
    scraper_manager=None,
    metadata_manager=None,
    config_service=None,
    ai_service=None,
    rate_limiter=None,
    title_recognition_manager=None
):
    """一键导入指定服务器下全部"未导入"媒体项。

    why：未导入清单的计算依赖 crud.get_unimported_item_ids 中的关联子查询
    （Anime×AnimeSource×Episode 三表 JOIN + func.replace 比对标题，索引失效），
    媒体库较大时耗时可达数十秒。原实现放在 HTTP 接口内同步执行，用户点击按钮后
    长时间无任何反馈，误以为功能失效（issue #441）。
    改为在任务内部计算：接口立即返回 taskId，耗时过程有进度可见。
    """
    await progress_callback(0, "正在统计未导入的媒体项...")

    # 待导入判定自持短事务，任务会话不跨后续批量派发占用连接。
    item_ids = await collect_unimported_media_items(server_id, media_type)
    if not item_ids:
        raise TaskSuccess("没有未导入的媒体项")

    await progress_callback(5, f"共 {len(item_ids)} 个未导入媒体项，开始导入...")

    # 复用既有导入逻辑；进度回调做区间压缩，把 5%~100% 留给实际导入过程
    async def _scaled_callback(progress: int, description: str):
        scaled = 5 + int(progress * 0.95)
        await progress_callback(min(scaled, 100), description)

    await import_media_items(
        item_ids,
        session,
        task_manager,
        _scaled_callback,
        scraper_manager=scraper_manager,
        metadata_manager=metadata_manager,
        config_service=config_service,
        ai_service=ai_service,
        rate_limiter=rate_limiter,
        title_recognition_manager=title_recognition_manager
    )


async def import_media_items(
    item_ids: List[int],
    session: AsyncSession,
    task_manager: "TaskManager",
    progress_callback: Callable,
    scraper_manager=None,
    metadata_manager=None,
    config_service=None,
    ai_service=None,
    rate_limiter=None,
    title_recognition_manager=None,
    batch_size: int = 15,  # 有界批量提交：每批任务数
):
    """
    导入媒体项(按季度导入电视剧,电影直接导入)

    Args:
        batch_size: 批量提交大小，默认15个任务/批（阶段4优化：避免瞬时负载激增）
    """

    # 依赖统一从服务容器获取，禁止任务反向导入应用启动模块。
    if scraper_manager is None:
        scraper_manager = get_scraper_manager()
    if metadata_manager is None:
        metadata_manager = get_metadata_service()
    if config_service is None:
        raise ValueError("config_service is required")
    if ai_service is None:
        raise ValueError("必须提供共享 AI 服务 ai_service")
    if rate_limiter is None:
        raise ValueError("rate_limiter is required")
    if title_recognition_manager is None:
        raise ValueError("title_recognition_manager is required")

    await progress_callback(0, "开始导入媒体项...")

    # 查询与分组一次性生成值快照，排队期间不持有数据库连接。
    movies, tv_shows, media_server_type = await prepare_media_import(item_ids)

    # 计算任务数: 电影数 + 电视剧集数(每集单独计算)
    # 统计任务数量: 电影按部, 电视按季度
    tv_season_count = len(tv_shows)
    total_tasks = len(movies) + tv_season_count
    completed = 0

    logger.info(f"准备导入: {len(movies)} 部电影, {tv_season_count} 个电视季度")

    # 优化：批量提交电影导入任务，移除单个任务间的延迟
    movie_tasks = []
    movie_ids_to_mark = []

    for movie in movies:
        try:
            # 触发webhook式搜索
            # 注意: lambda 使用默认参数 m=movie 来捕获当前循环变量的值,
            # 避免闭包捕获引用导致所有任务都使用最后一个 movie 的数据
            task_coro = task_manager.submit_task(
                lambda session, progress_callback, m=movie, mst=media_server_type: webhook_search_and_dispatch_task(
                    animeTitle=m['title'],
                    mediaType="movie",
                    season=1,
                    currentEpisodeIndex=1,
                    searchKeyword=m['title'],
                    imageUrl=m.get('posterUrl'),
                    year=m['year'],
                    tmdbId=m['tmdbId'],
                    tvdbId=m['tvdbId'],
                    imdbId=m['imdbId'],
                    doubanId=None,
                    bangumiId=None,
                    webhookSource="media_server",
                    session=session,
                    progress_callback=progress_callback,
                    manager=scraper_manager,
                    task_manager=task_manager,
                    metadata_manager=metadata_manager,
                    config_service=config_service,
                    ai_service=ai_service,
                    rate_limiter=rate_limiter,
                    title_recognition_manager=title_recognition_manager,
                    # 媒体服务三级 ID（删除联动用）
                    mediaServerType=mst,
                    mediaServerSeriesId=str(m['seriesId'] or m['mediaId']) if (m['seriesId'] or m['mediaId']) is not None else None,
                    mediaServerSeasonId=str(m['seasonId']) if m['seasonId'] is not None else None,
                    mediaServerEpisodeId=str(m['episodeId'] or m['mediaId']) if (m['episodeId'] or m['mediaId']) is not None else None,
                ),
                title=f"自动导入 (库内): {movie['title']}",
                queue_type="download",
                # 关键修复(任务重启恢复)：补 task_type + task_parameters。
                # 原先未传 task_type → _run_task_wrapper 不写 TaskStateCache → 程序重启后
                # 无法恢复，只能被标"因程序重启而中断"。task_type=webhook_search 对应
                # _rebuild_coro_factory 的 webhook_search 分支（重建 webhook_search_and_dispatch_task）。
                task_type="webhook_search",
                task_parameters={
                    "animeTitle": movie['title'],
                    "mediaType": "movie",
                    "season": 1,
                    "currentEpisodeIndex": 1,
                    "searchKeyword": movie['title'],
                    "year": movie['year'],
                    "tmdbId": movie['tmdbId'],
                    "tvdbId": movie['tvdbId'],
                    "imdbId": movie['imdbId'],
                    "doubanId": None,
                    "bangumiId": None,
                    "webhookSource": "media_server",
                    "imageUrl": movie['posterUrl'],
                    # 恢复时保留首次提交的媒体库关联信息。
                    "mediaServerType": media_server_type,
                    "mediaServerSeriesId": str(movie['seriesId'] or movie['mediaId']) if (movie['seriesId'] or movie['mediaId']) is not None else None,
                    "mediaServerSeasonId": str(movie['seasonId']) if movie['seasonId'] is not None else None,
                    "mediaServerEpisodeId": str(movie['episodeId'] or movie['mediaId']) if (movie['episodeId'] or movie['mediaId']) is not None else None,
                },
            )
            movie_tasks.append((task_coro, movie))

        except Exception as e:
            logger.error(f"准备电影 {movie['title']} 导入任务失败: {e}", exc_info=True)

    # 分批提交，只有排队成功后才记录待标记的电影。
    if movie_tasks:
        logger.info(f"准备分批提交 {len(movie_tasks)} 个电影导入任务（每批 {batch_size} 个）...")

        for batch_start in range(0, len(movie_tasks), batch_size):
            batch_end = min(batch_start + batch_size, len(movie_tasks))
            batch = movie_tasks[batch_start:batch_end]

            logger.info(f"提交电影批次 {batch_start//batch_size + 1}/{(len(movie_tasks) + batch_size - 1)//batch_size}：{len(batch)} 个任务")

            results = await asyncio.gather(*[task for task, _ in batch], return_exceptions=True)

            for result, (_, movie) in zip(results, batch):
                if isinstance(result, Exception):
                    logger.error(f"电影 {movie['title']} 导入任务提交失败: {result}")
                else:
                    task_id, _ = result
                    movie_ids_to_mark.append(movie['id'])
                    logger.info(f"电影 {movie['title']} 导入任务已提交: {task_id}")

                # 更新进度
                completed += 1
                await progress_callback(
                    int((completed / total_tasks) * 100),
                    f"已提交 {completed}/{total_tasks} 个导入任务..."
                )

        # 排队标记由 Workflow 独立提交，失败时由事务上下文回滚。
        try:
            await mark_media_submitted(movie_ids_to_mark)
            logger.info(f"已批量标记 {len(movie_ids_to_mark)} 部电影为已导入")
        except Exception as e:
            logger.error(f"批量标记电影导入状态失败: {e}", exc_info=True)

    # 优化：批量提交电视剧导入任务
    tv_tasks = []
    tv_ids_to_mark = []

    for (title, season), season_items in tv_shows.items():
        season_str = f"S{season:02d}" if season is not None else "S??"

        try:
            # 选取该季度中集数最小的一集作为代表，用于搜索和匹配
            representative_item = min(
                season_items,
                key=lambda item: item['episode'] or 0
            )

            selected_episodes = sorted([item['episode'] for item in season_items if item['episode'] is not None])
            logger.info(f"电视节目 {title} {season_str} 选中的分集: {selected_episodes}")

            task_coro = task_manager.submit_task(
                lambda session, progress_callback, item=representative_item, selected_eps=selected_episodes, mst=media_server_type: webhook_search_and_dispatch_task(
                    animeTitle=item['title'],
                    mediaType="tv_series",
                    season=item['season'],
                    currentEpisodeIndex=item['episode'],  # 使用代表集数进行匹配
                    searchKeyword=f"{item['title']} S{item['season'] or 1:02d}E{item['episode'] or 1:02d}",
                    imageUrl=item.get('posterUrl'),
                    year=item['year'],
                    tmdbId=item['tmdbId'],
                    tvdbId=item['tvdbId'],
                    imdbId=item['imdbId'],
                    doubanId=None,
                    bangumiId=None,
                    webhookSource="media_server",
                    session=session,
                    progress_callback=progress_callback,
                    manager=scraper_manager,
                    task_manager=task_manager,
                    metadata_manager=metadata_manager,
                    config_service=config_service,
                    ai_service=ai_service,
                    rate_limiter=rate_limiter,
                    title_recognition_manager=title_recognition_manager,
                    selectedEpisodes=selected_eps,
                    mediaServerType=mst,
                    mediaServerSeriesId=str(item['seriesId'] or item['mediaId']) if (item['seriesId'] or item['mediaId']) is not None else None,
                    mediaServerSeasonId=str(item['seasonId']) if item['seasonId'] is not None else None,
                    mediaServerEpisodeId=str(item['episodeId'] or item['mediaId']) if (item['episodeId'] or item['mediaId']) is not None else None,
                ),
                title=f"自动导入 (库内): {title} {season_str} (共 {len(season_items)} 集)",
                queue_type="download",
                # 恢复参数与首次派发保持一致，避免重启后丢失媒体库关联。
                task_type="webhook_search",
                task_parameters={
                    "animeTitle": representative_item['title'],
                    "mediaType": "tv_series",
                    "season": representative_item['season'],
                    "currentEpisodeIndex": representative_item['episode'],
                    "searchKeyword": f"{representative_item['title']} S{representative_item['season'] or 1:02d}E{representative_item['episode'] or 1:02d}",
                    "year": representative_item['year'],
                    "tmdbId": representative_item['tmdbId'],
                    "tvdbId": representative_item['tvdbId'],
                    "imdbId": representative_item['imdbId'],
                    "doubanId": None,
                    "bangumiId": None,
                    "webhookSource": "media_server",
                    "selectedEpisodes": selected_episodes,
                    "imageUrl": representative_item['posterUrl'],
                    "mediaServerType": media_server_type,
                    "mediaServerSeriesId": str(representative_item['seriesId'] or representative_item['mediaId']) if (representative_item['seriesId'] or representative_item['mediaId']) is not None else None,
                    "mediaServerSeasonId": str(representative_item['seasonId']) if representative_item['seasonId'] is not None else None,
                    "mediaServerEpisodeId": str(representative_item['episodeId'] or representative_item['mediaId']) if (representative_item['episodeId'] or representative_item['mediaId']) is not None else None,
                },
            )
            tv_tasks.append((task_coro, title, season, season_items))

        except Exception as e:
            logger.error(f"准备电视节目 {title} {season_str} 导入任务失败: {e}", exc_info=True)

    # 分批提交避免瞬时负载激增，仅标记实际提交成功的媒体项。
    if tv_tasks:
        logger.info(f"准备分批提交 {len(tv_tasks)} 个电视剧季度导入任务（每批 {batch_size} 个）...")

        for batch_start in range(0, len(tv_tasks), batch_size):
            batch_end = min(batch_start + batch_size, len(tv_tasks))
            batch = tv_tasks[batch_start:batch_end]

            logger.info(f"提交电视剧批次 {batch_start//batch_size + 1}/{(len(tv_tasks) + batch_size - 1)//batch_size}：{len(batch)} 个任务")
            results = await asyncio.gather(*[task for task, _, _, _ in batch], return_exceptions=True)

            for result, (_, title, season, season_items) in zip(results, batch):
                season_str = f"S{season:02d}" if season is not None else "S??"
                if isinstance(result, Exception):
                    logger.error(f"电视节目 {title} {season_str} 导入任务提交失败: {result}")
                else:
                    task_id, _ = result
                    tv_ids_to_mark.extend(item['id'] for item in season_items)
                    logger.info(f"电视节目 {title} {season_str} (共 {len(season_items)} 集) 导入任务已提交: {task_id}")

                completed += 1
                await progress_callback(
                    int((completed / total_tasks) * 100),
                    f"已处理 {completed}/{total_tasks} 个导入任务..."
                )

        # Workflow 负责提交与失败回滚，不再操作任务注入会话。
        try:
            await mark_media_submitted(tv_ids_to_mark)
            logger.info(f"已批量标记 {len(tv_ids_to_mark)} 个电视剧集为已导入")
        except Exception as e:
            logger.error(f"批量标记电视剧导入状态失败: {e}", exc_info=True)

    await progress_callback(100, f"导入完成,共提交 {total_tasks} 个任务")
    raise TaskSuccess(f"媒体项导入完成,共提交 {total_tasks} 个任务")

