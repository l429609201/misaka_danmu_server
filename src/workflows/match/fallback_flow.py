"""
匹配后备流程 - Workflow 层实现

职责：处理匹配后备场景的弹幕下载
- 虚拟 episodeId 解析（25000000000000+）
- fallback_info 缓存查询
- DB 兜底逻辑（从数据库重建后备信息）
- 分集信息获取和验证
- Anime/Source/Episode 条目创建
- 弹幕下载任务提交
- 整部剧缓存管理
- 整季缓存写入
"""

import logging
import asyncio
import time
from typing import Optional, Dict, Any

from sqlalchemy.ext.asyncio import AsyncSession
from fastapi import Request

from src.schemas.dandan import CommentResponse as DandanCommentResponse
# 后备编排使用全局配置入口，不依赖请求级配置注入。
from src.services.config_service import get_config_service
from src.services.service_container import (
    get_database_service,
    get_task_manager,
    get_scraper_manager,
    get_rate_limiter,
)
# 复用编排层缓存辅助函数，保留现有缓存键拼接和序列化契约。
from src.workflows.dandan.helpers import get_db_cache, set_db_cache, delete_db_cache
from src.utils.dandan.constants import (
    FALLBACK_SEARCH_CACHE_PREFIX,
    COMMENTS_FETCH_CACHE_PREFIX,
    COMMENTS_FETCH_CACHE_TTL,
)
from src.services.task_profiler import TaskProfiler, FLOW_FALLBACK_MATCH
# 从零依赖异常模块导入，避免旧任务管理器路径及额外依赖链。
from src.utils.diagnostics.task_exceptions import TaskSuccess
from src.utils.parsing.filename_parser import parse_search_keyword
from src.services.danmaku_service import DanmakuService
from src.workflows.danmaku_import import save_danmaku_for_episode
from src.workflows.comments.helpers import process_comments_for_dandanplay

logger = logging.getLogger(__name__)


async def handle_match_fallback_comments(
    episodeId: int,
    token: str,
    session: AsyncSession,
    request: Request,
    async_mode: bool = False
) -> Optional[DandanCommentResponse]:
    """
    匹配后备流程：处理虚拟 episodeId 的弹幕下载

    流程：
    1. 解析虚拟 episodeId（25000000000000+）
    2. 查询 fallback_info 缓存（或从数据库重建）
    3. 获取分集信息并验证
    4. 创建 Anime/Source/Episode 条目
    5. 提交弹幕下载任务
    6. 等待下载完成或超时返回

    :param episodeId: 虚拟集数ID
    :param token: 用户token
    :param session: 数据库会话
    :param request: FastAPI 请求对象（用于获取 session_factory）
    :param async_mode: 是否异步模式
    :return: 弹幕响应或None
    """
    # 后备信息只读取调用方会话；建库交给下载任务，避免等待时持有写锁。
    db = get_database_service()
    scraper_manager = get_scraper_manager()
    task_manager = get_task_manager()
    rate_limiter = get_rate_limiter()
    config_service = get_config_service()

    # 后备路径性能统计
    _fallback_profiler: Optional[TaskProfiler] = None

    # 步骤1：解析虚拟 episodeId
    if episodeId < 25000000000000:
        # 不是匹配后备的 episodeId
        return None

    # 提取 anime_id, source_order, episode_number
    temp_id = episodeId - 25000000000000
    anime_id_part = temp_id // 1000000
    temp_id = temp_id % 1000000
    source_order_part = temp_id // 10000
    episode_number = temp_id % 10000

    logger.info(f"解析虚拟episodeId: {episodeId} → anime_id={anime_id_part}, source_order={source_order_part}, episode={episode_number}")

    # 步骤2：查询 fallback_info 缓存
    fallback_info = await _get_fallback_info(
        session, episodeId, anime_id_part, source_order_part, episode_number, db
    )

    if not fallback_info:
        logger.warning(f"无法获取 fallback_info，跳过匹配后备流程: episodeId={episodeId}")
        return None

    # 步骤3：开始匹配后备流程
    _fallback_profiler = TaskProfiler(FLOW_FALLBACK_MATCH)
    logger.info(f"检测到匹配后备的episodeId: {episodeId}, 集数: {episode_number}")

    # 从缓存中提取信息
    real_anime_id = fallback_info["real_anime_id"]
    provider = fallback_info["provider"]
    mediaId = fallback_info["mediaId"]
    final_title = fallback_info["final_title"]
    display_title = fallback_info.get("original_title") or final_title
    final_season = fallback_info["final_season"]
    media_type = fallback_info["media_type"]
    imageUrl = fallback_info.get("imageUrl")
    year = fallback_info.get("year")

    # 步骤4：获取分集信息
    logger.info(f"开始获取分集信息: provider={provider}, mediaId={mediaId}, episode_number={episode_number}")

    scraper = scraper_manager.get_scraper(provider)
    if not scraper:
        logger.error(f"无法获取 scraper: provider={provider}")
        return None

    try:
        episodes_list = await scraper_manager.get_episodes_routed(provider, mediaId, db_media_type=media_type)
        if not episodes_list:
            logger.error(f"无法获取分集列表")
            return None

        # 按 episodeIndex 精确查找目标分集
        target_episode = None
        for ep in episodes_list:
            if ep.episodeIndex == episode_number:
                target_episode = ep
                break

        if not target_episode:
            logger.error(f"分集列表中未找到第{episode_number}集（共{len(episodes_list)}条记录）")
            return None

        provider_episode_id = target_episode.episodeId
        episode_title = target_episode.title
        episode_url = target_episode.url

        logger.info(f"获取到分集信息: title='{episode_title}', provider_episode_id='{provider_episode_id}'")

    except Exception as e:
        logger.error(f"获取分集信息失败: {e}", exc_info=True)
        return None

    # 作品和源由下载任务创建，主请求不得持有写锁等待下载任务。
    task_unique_key = f"match_fallback_comments_{episodeId}"
    async with db.transaction(session=session):
        recent_task = await db.task.find_recent_task_by_unique_key(task_unique_key, 1)
    existing_task = (
        recent_task
        if recent_task and recent_task.status in {'排队中', '运行中', '已暂停'}
        else None
    )

    if existing_task:
        logger.info(f"弹幕下载任务正在执行: {task_unique_key}，等待任务完成...")
        # 等待最多30秒，检查缓存中是否有结果
        cache_key = f"comments_{episodeId}"
        for _ in range(30):
            await asyncio.sleep(1)
            cached_comments = await get_db_cache(session, COMMENTS_FETCH_CACHE_PREFIX, cache_key)
            if cached_comments:
                logger.info(f"从缓存中获取到弹幕数据，共 {len(cached_comments)} 条")
                # 命中缓存时直接返回实际弹幕，避免成功下载仍输出空列表。
                processed = process_comments_for_dandanplay(cached_comments)
                return DandanCommentResponse(count=len(processed), comments=processed)

        if async_mode:
            return DandanCommentResponse(
                count=0, comments=[], status="pending", taskId=existing_task.taskId,
            )
        return DandanCommentResponse(count=0, comments=[])

    # 步骤7：提交弹幕下载任务
    comments_data = await _submit_download_task(
        episodeId=episodeId,
        real_anime_id=real_anime_id,
        provider=provider,
        mediaId=mediaId,
        episode_number=episode_number,
        episode_title=episode_title,
        episode_url=episode_url,
        provider_episode_id=provider_episode_id,
        final_title=final_title,
        display_title=display_title,
        final_season=final_season,
        media_type=media_type,
        imageUrl=imageUrl,
        year=year,
        episodes_list=episodes_list,
        scraper=scraper,
        rate_limiter=rate_limiter,
        config_service=config_service,
        task_manager=task_manager,
        task_unique_key=task_unique_key,
        session=session,
        request=request,
        async_mode=async_mode,
    )

    # 写入性能统计
    if _fallback_profiler is not None:
        _fallback_profiler.record_step("全流程", _fallback_profiler.total_duration_ms)
        await _fallback_profiler.flush(session, session_factory=request.app.state.db_session_factory)

    if comments_data:
        # 主流程直接返回本响应，必须携带实际弹幕，不能返回空占位响应。
        processed = process_comments_for_dandanplay(comments_data)
        return DandanCommentResponse(count=len(processed), comments=processed)

    if async_mode:
        return DandanCommentResponse(count=0, comments=[], status="pending")

    return DandanCommentResponse(count=0, comments=[])



async def _get_fallback_info(
    session: AsyncSession,
    episodeId: int,
    anime_id_part: int,
    source_order_part: int,
    episode_number: int,
    db
) -> Optional[Dict[str, Any]]:
    """
    获取 fallback_info 缓存

    优先级：
    1. 整部剧缓存（fallback_episode_{virtual_anime_base}）
    2. 单集缓存（fallback_episode_{episodeId}）
    3. DB 兜底（从 Anime + AnimeSource 重建）
    """
    fallback_info = None

    # 1. 尝试从整部剧缓存获取
    virtual_anime_base = 25000000000000 + anime_id_part * 1000000 + source_order_part * 10000
    fallback_series_key = f"fallback_episode_{virtual_anime_base}"
    fallback_info = await get_db_cache(session, "", fallback_series_key)
    logger.debug(f"查找整部剧缓存: {fallback_series_key}, 找到: {fallback_info is not None}")

    # 2. 尝试从单集缓存获取
    if not fallback_info:
        fallback_episode_cache_key = f"fallback_episode_{episodeId}"
        fallback_info = await get_db_cache(session, FALLBACK_SEARCH_CACHE_PREFIX, fallback_episode_cache_key)
        if fallback_info:
            logger.debug(f"查找单集缓存: {fallback_episode_cache_key}, 找到")
            # 更新 episode_number（单集缓存中可能有）
            if "episode_number" in fallback_info:
                episode_number = fallback_info["episode_number"]

    # 3. DB 兜底：从数据库重建
    if not fallback_info:
        try:
            # 借用外部会话，确保仓储代理在有效上下文内访问。
            async with db.transaction(session=session):
                db_anime = await db.anime.get_by_id(anime_id_part)
                db_sources = await db.source.get_sources_by_anime(anime_id_part, order_by_priority=True)
            db_src = next((s for s in db_sources if s.sourceOrder == source_order_part), None)

            if db_anime and db_src:
                fallback_info = {
                    "real_anime_id": anime_id_part,
                    "provider": db_src.providerName,
                    "mediaId": db_src.mediaId,
                    "final_title": db_anime.title,
                    "original_title": db_anime.title,
                    "final_season": db_anime.season,
                    "media_type": db_anime.type,
                    "imageUrl": db_anime.imageUrl,
                    "year": db_anime.year,
                }
                logger.info(
                    f"[DB兜底] 缓存缺失，从数据库重建后备信息: anime_id={anime_id_part}, "
                    f"provider={db_src.providerName}, mediaId={db_src.mediaId}, 集号={episode_number}"
                )
            else:
                logger.debug(
                    f"[DB兜底] 未找到可重建的作品/源 (anime_id={anime_id_part}, "
                    f"source_order={source_order_part})"
                )
        except Exception as e:
            logger.warning(f"[DB兜底] 从数据库重建后备信息失败: {e}")

    return fallback_info


async def _submit_download_task(
    episodeId: int,
    real_anime_id: int,
    provider: str,
    mediaId: str,
    episode_number: int,
    episode_title: str,
    episode_url: str,
    provider_episode_id: str,
    final_title: str,
    display_title: str,
    final_season: int,
    media_type: str,
    imageUrl: Optional[str],
    year: Optional[int],
    episodes_list: list,
    scraper,
    rate_limiter,
    config_service,
    task_manager,
    task_unique_key: str,
    session: AsyncSession,
    request: Request,
    async_mode: bool,
) -> Optional[list]:
    """
    提交弹幕下载任务

    :return: 弹幕数据（如果在30秒内完成）或 None
    """
    # 保存变量到闭包（避免闭包问题）
    current_scraper = scraper
    current_provider_episode_id = provider_episode_id
    current_provider = provider
    current_real_anime_id = real_anime_id
    current_mediaId = mediaId
    current_episode_number = episode_number
    current_episode_title = episode_title
    current_episode_url = episode_url
    current_episodeId = episodeId
    current_fallback_episode_cache_key = f"fallback_episode_{episodeId}"
    current_rate_limiter = rate_limiter
    current_final_title = final_title
    current_display_title = display_title
    current_final_season = final_season
    current_media_type = media_type
    current_imageUrl = imageUrl
    current_year = year
    current_episodes_list = episodes_list

    async def download_match_fallback_comments_task(task_session, progress_callback):
        """匹配后备弹幕下载任务"""
        # 自持事务只覆盖建库阶段，下载和文件保存不占用该事务。
        task_db = get_database_service()

        try:
            await progress_callback(10, "开始下载弹幕...")

            # 检查流控
            await current_rate_limiter.check_fallback("match", current_provider)

            # 下载弹幕
            actual_episode_id = current_provider_episode_id
            if actual_episode_id and actual_episode_id.startswith("http"):
                try:
                    parsed_id = await current_scraper.get_id_from_url(actual_episode_id)
                    if parsed_id:
                        actual_episode_id = current_scraper.format_episode_id_for_comments(parsed_id)
                        logger.info(f"URL 已解析为 episode_id: {actual_episode_id}")
                except Exception as e:
                    logger.warning(f"URL 解析失败，尝试直接使用: {e}")

            # 下载前原子占额，故障转移换源时也必须重新获得对应许可。
            comments = await current_scraper.get_comments(
                actual_episode_id, progress_callback=progress_callback, pool="match"
            )

            if not comments:
                logger.warning(f"下载失败，未获取到弹幕")
                raise TaskSuccess("未获取到弹幕，源站可能暂时不可用")

            logger.info(f"下载成功，共 {len(comments)} 条弹幕")

            # 立即存储到数据库缓存中，让主接口能快速返回
            cache_key = f"comments_{current_episodeId}"
            await set_db_cache(task_session, COMMENTS_FETCH_CACHE_PREFIX, cache_key, comments, COMMENTS_FETCH_CACHE_TTL)
            logger.info(f"弹幕已存入缓存: {cache_key}")

            await progress_callback(60, "创建数据库条目...")

            # 先提交建库结果，后续保存编排自持事务才能读取分集且不会互相等待。
            async with task_db.transaction():
                existing_anime = await task_db.anime.get_by_id(current_real_anime_id)
                if not existing_anime:
                    await task_db.anime.create(
                        id=current_real_anime_id,
                        title=current_display_title,
                        type=current_media_type,
                        season=current_final_season,
                        imageUrl=current_imageUrl,
                        year=current_year,
                    )
                source_id = await task_db.source.link_source_to_anime(
                    current_real_anime_id, current_provider, current_mediaId
                )
                source_obj = await task_db.source.get_by_id(source_id)
                source_order = source_obj.sourceOrder
                episode_db_id = await task_db.episode.create_episode_if_not_exists(
                    current_real_anime_id, source_id, current_episode_number,
                    current_episode_title, current_episode_url, current_provider_episode_id
                )
            logger.info(f"分集已创建或已存在: id={episode_db_id}")

            # 为整部剧创建缓存记录
            try:
                virtual_anime_base = 25000000000000 + current_real_anime_id * 1000000 + source_order * 10000
                fallback_series_key = f"fallback_episode_{virtual_anime_base}"

                cache_value = {
                    "real_anime_id": current_real_anime_id,
                    "provider": current_provider,
                    "mediaId": current_mediaId,
                    "final_title": current_final_title,
                    "original_title": current_display_title,
                    "final_season": current_final_season,
                    "media_type": current_media_type,
                    "imageUrl": current_imageUrl,
                    "year": current_year,
                    "total_episodes": len(current_episodes_list)
                }

                # 存储到缓存，3小时过期
                await set_db_cache(task_session, "", fallback_series_key, cache_value, 10800)
                logger.info(f"为整部剧创建了缓存记录: {fallback_series_key} (共{len(current_episodes_list)}集)")
            except Exception as e:
                logger.warning(f"创建缓存记录失败: {e}")

            await progress_callback(80, "保存弹幕...")

            # 文件与数据库的一致性由现有保存编排统一管理。
            added_count = await save_danmaku_for_episode(
                episode_db_id, comments, config_service,
                fire_threshold=current_scraper.likes_fire_threshold
            )

            # 将弹幕数据写入短期缓存表
            cache_key = f"comments_{current_episodeId}"
            await set_db_cache(task_session, COMMENTS_FETCH_CACHE_PREFIX, cache_key, comments, 300)
            logger.info(f"保存成功，共 {added_count} 条弹幕")
            logger.debug(f"弹幕数据已写入缓存: {cache_key}")

            # 清理数据库缓存
            await delete_db_cache(task_session, FALLBACK_SEARCH_CACHE_PREFIX, current_fallback_episode_cache_key)
            logger.debug(f"清理数据库缓存: {current_fallback_episode_cache_key}")

            # 写入 match_season 整季缓存
            if current_media_type != "movie":
                try:
                    _parsed_for_cache = parse_search_keyword(current_final_title)
                    season_cache_key = f"match_season_{_parsed_for_cache['title']}_{current_final_season}"
                    season_cache_data = {
                        "provider": current_provider,
                        "mediaId": current_mediaId,
                        "real_anime_id": current_real_anime_id,
                        "virtual_anime_id": 900000,
                        "final_title": current_final_title,
                        "original_title": current_display_title,
                        "final_season": current_final_season,
                        "source_order": source_order,
                        "media_type": current_media_type,
                        "imageUrl": current_imageUrl,
                        "year": current_year,
                        "timestamp": time.time()
                    }
                    await set_db_cache(task_session, FALLBACK_SEARCH_CACHE_PREFIX, season_cache_key, season_cache_data, 3600)
                    logger.info(f"整季缓存已存储（匹配后备路径）: {season_cache_key}")
                except Exception as e:
                    logger.warning(f"写入整季缓存失败: {e}")

            await progress_callback(100, "完成")
            return f"后备下载完成，共获取 {len(comments)} 条弹幕"

        except TaskSuccess:
            raise
        except Exception as e:
            logger.error(f"匹配后备弹幕下载任务执行失败: {e}", exc_info=True)
            raise


    # 提交弹幕下载任务到后备队列
    try:
        # 结构化通知参数
        _mf_params = {
            "anime_title": display_title or final_title or "",
            "season": final_season,
            "episode": episode_number,
            "provider": provider,
            "imageUrl": imageUrl or "",
            "is_movie": (episode_number is None and media_type == "movie"),
            "media_type": media_type,
        }

        task_id, done_event = await task_manager.submit_task(
            download_match_fallback_comments_task,
            f"匹配后备弹幕下载: {final_title} 第{episode_number}集 [{provider}:{mediaId}]",
            unique_key=task_unique_key,
            task_type="download_comments",
            queue_type="fallback",
            task_parameters=_mf_params
        )
        logger.info(f"已提交匹配后备弹幕下载任务: {task_id}")

        # 用于标记是否已触发预下载
        predownload_triggered = False
        predownload_lock = asyncio.Lock()

        # 添加后台任务完成回调（超时场景下触发预下载）
        def handle_task_completion(event):
            """后台任务完成时的回调"""
            async def trigger_predownload():
                nonlocal predownload_triggered
                try:
                    await event.wait()

                    async with predownload_lock:
                        if predownload_triggered:
                            logger.info(f"预下载已在30秒内触发，跳过回调触发 (episodeId={episodeId})")
                            return
                        predownload_triggered = True

                    logger.info(f"匹配后备任务已完成（超时后），检查是否需要触发预下载 (episodeId={episodeId})")

                    # 创建新的 session 检查弹幕是否下载成功
                    async with request.app.state.db_session_factory() as check_session:
                        # 文件弹幕通过服务读取，不再访问已删除的仓储接口。
                        check_comments = await DanmakuService(check_session).fetch_comments(episodeId)
                        if check_comments:
                            logger.info(f"匹配后备任务成功，触发预下载下一集 (episodeId={episodeId})")
                            # TODO: 触发预下载逻辑
                        else:
                            logger.warning(f"匹配后备任务完成但未找到弹幕，跳过预下载 (episodeId={episodeId})")
                except Exception as e:
                    logger.error(f"匹配后备完成回调异常 (episodeId={episodeId}): {e}", exc_info=True)

            asyncio.create_task(trigger_predownload())

        # 注册完成回调
        handle_task_completion(done_event)

        # 等待任务完成（30秒超时）
        try:
            await asyncio.wait_for(done_event.wait(), timeout=30.0)
            logger.info(f"匹配后备弹幕下载任务完成，从数据库重新读取弹幕")

            # 新会话避免主请求的事务快照看不到后台刚提交的分集。
            db = get_database_service()
            async with db.transaction():
                comments_data = await DanmakuService(db._session).fetch_comments(episodeId)
            if comments_data:
                logger.info(f"从数据库读取到 {len(comments_data)} 条弹幕")

                # 30秒内完成，立即触发预下载
                async with predownload_lock:
                    if not predownload_triggered:
                        predownload_triggered = True
                        logger.info(f"匹配后备场景：已触发预下载下一集")
                        # TODO: 实际的预下载逻辑

                return comments_data
            else:
                logger.warning(f"任务完成但数据库中未找到弹幕数据")
                return None

        except asyncio.TimeoutError:
            logger.info(f"匹配后备弹幕下载任务超时（30秒），任务将在后台继续执行")
            return None

    except Exception as e:
        logger.error(f"提交匹配后备弹幕下载任务失败: {e}", exc_info=True)
        return None
