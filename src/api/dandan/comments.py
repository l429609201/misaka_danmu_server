"""
弹弹Play 兼容 API 的弹幕评论功能

包含弹幕获取、外部弹幕获取等功能。
"""

import asyncio
import logging
import time
from typing import List, Dict, Any, Optional

from opencc import OpenCC
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from fastapi import APIRouter, Depends, HTTPException, Path, Query, Request

from src.db import crud, orm_models, models, get_db_session, sync_postgres_sequence, ConfigManager
from src.core import get_now
from src.core.cache import get_cache_backend
from src.services import ScraperManager, TaskManager
from src.utils import parse_search_keyword, sample_comments_evenly, record_play_history, handle_danmaku_likes
from src.rate_limiter import RateLimiter
from src import tasks

# 从 orm_models 和 models 导入需要的类型
Anime = orm_models.Anime
AnimeSource = orm_models.AnimeSource
Episode = orm_models.Episode
ProviderEpisodeInfo = models.ProviderEpisodeInfo

# 同包内相对导入
from . import models as dandan_models
from .constants import (
    FALLBACK_SEARCH_CACHE_PREFIX,
    USER_LAST_BANGUMI_CHOICE_PREFIX,
    COMMENTS_FETCH_CACHE_PREFIX,
    SAMPLED_COMMENTS_CACHE_PREFIX,
    FALLBACK_SEARCH_CACHE_TTL,
    COMMENTS_FETCH_CACHE_TTL,
    SAMPLED_COMMENTS_CACHE_TTL_DB,
    SAMPLED_CACHE_TTL,
)
from .helpers import (
    get_db_cache, set_db_cache, delete_db_cache,
    get_episode_mapping, get_cache_keys,
)
from .route_handler import get_token_from_path, DandanApiRoute
from .dependencies import (
    get_config_manager,
    get_task_manager,
    get_rate_limiter,
    get_scraper_manager,
)

# 从主文件导入预下载和刷新等待函数（这些函数依赖较多，暂时保留在主文件中）
# 注意：这是临时方案，后续可以考虑将这些函数移到单独的模块
def _get_predownload_functions():
    """延迟导入预下载相关函数，避免循环导入"""
    from .predownload import wait_for_refresh_task, try_predownload_next_episode
    return wait_for_refresh_task, try_predownload_next_episode
from .danmaku_color import (
    DEFAULT_RANDOM_COLOR_MODE,
    DEFAULT_RANDOM_COLOR_PALETTE,
    DEFAULT_REPEAT_HIGHLIGHT_MIN_COUNT,
    apply_random_color,
    apply_repeat_highlight,
    parse_palette,
)
from .danmaku_filter import apply_blacklist_filter

logger = logging.getLogger(__name__)

# 创建评论路由器
comments_router = APIRouter(route_class=DandanApiRoute)


# === process_comments_for_dandanplay ===
def process_comments_for_dandanplay(comments_data: List[Dict[str, Any]]) -> List[models.Comment]:
    """
    将弹幕字典列表处理为符合 dandanplay 客户端规范的格式。
    核心逻辑是移除 p 属性中的字体大小参数，同时保留其他所有部分。
    原始格式: "时间,模式,字体大小,颜色,[来源]"
    目标格式: "时间,模式,颜色,[来源]"
    """
    processed_comments = []
    for i, item in enumerate(comments_data):
        p_attr = item.get("p", "")
        p_parts = p_attr.split(',')

        # 查找可选的用户标签（如[bilibili]），以确定核心参数的数量
        core_parts_count = len(p_parts)
        for j, part in enumerate(p_parts):
            if '[' in part and ']' in part:
                core_parts_count = j
                break

        if core_parts_count == 4:
            del p_parts[2] # 移除字体大小 (index 2)

        new_p_attr = ','.join(p_parts)
        processed_comments.append(models.Comment(cid=i, p=new_p_attr, m=item.get("m", "")))
    return processed_comments

# === get_external_comments_from_url ===
@comments_router.get(
    "/extcomment",
    response_model=models.CommentResponse,
    summary="[dandanplay兼容] 获取外部弹幕"
)
async def get_external_comments_from_url(
    url: str = Query(..., description="外部视频链接 (支持 Bilibili, 腾讯, 爱奇艺, 优酷, 芒果TV)"),
    chConvert: int = Query(0, description="中文简繁转换。0-不转换，1-转换为简体，2-转换为繁体。"),
    token: str = Depends(get_token_from_path),
    session: AsyncSession = Depends(get_db_session),
    manager: ScraperManager = Depends(get_scraper_manager),
    config_manager: ConfigManager = Depends(get_config_manager)
):
    """
    从外部URL获取弹幕，并转换为dandanplay格式。
    结果会被缓存5小时。
    """
    cache_key = f"ext_danmaku_v2_{url}"
    _backend = get_cache_backend()
    cached_comments = None
    if _backend is not None:
        try:
            cached_comments = await _backend.get(cache_key, region="default")
        except Exception as e:
            logger.warning(f"缓存后端读取失败，回退到数据库: {e}")
    if cached_comments is None:
        cached_comments = await crud.get_cache(session, cache_key)
    if cached_comments is not None:
        logger.info(f"外部弹幕缓存命中: {url}")
        comments_data = cached_comments
    else:
        logger.info(f"外部弹幕缓存未命中，正在从网络获取: {url}")
        scraper = manager.get_scraper_by_domain(url)
        if not scraper:
            raise HTTPException(status_code=400, detail="不支持的URL或视频源。")

        try:
            provider_episode_id = await scraper.get_id_from_url(url)
            if not provider_episode_id:
                raise ValueError(f"无法从URL '{url}' 中解析出有效的视频ID。")
            
            episode_id_for_comments = scraper.format_episode_id_for_comments(provider_episode_id)
            comments_data = await scraper.get_comments(episode_id_for_comments)
            comments_data = handle_danmaku_likes(comments_data, scraper.likes_fire_threshold)

            # 修正：使用 scraper.provider_name 修复未定义的 'provider' 变量
            if not comments_data: logger.warning(f"未能从 {scraper.provider_name} URL 获取任何弹幕: {url}")

        except Exception as e:
            logger.error(f"处理 {scraper.provider_name} 外部弹幕时出错: {e}", exc_info=True)
            raise HTTPException(status_code=500, detail=f"获取 {scraper.provider_name} 弹幕失败。")

        # 缓存结果5小时 (18000秒)
        _backend = get_cache_backend()
        if _backend is not None:
            try:
                await _backend.set(cache_key, comments_data, ttl=18000, region="default")
            except Exception:
                await crud.set_cache(session, cache_key, comments_data, 18000)
        else:
            await crud.set_cache(session, cache_key, comments_data, 18000)

    # 处理简繁转换（根据优先级决定使用服务端配置还是播放器参数）
    try:
        server_ch = int(await config_manager.get('danmakuChConvert', '0'))
        priority = await config_manager.get('danmakuChConvertPriority', 'player')
        if priority == 'server':
            final_convert = server_ch
        else:
            final_convert = chConvert if chConvert != 0 else server_ch

        if final_convert in [1, 2] and comments_data:
            converter = OpenCC('t2s') if final_convert == 1 else OpenCC('s2t')
            for comment in comments_data:
                if 'm' in comment and comment['m']:
                    comment['m'] = converter.convert(comment['m'])
            logger.debug(f"外部弹幕简繁转换 (url: {url}): 最终模式={final_convert}(优先级={priority}, 播放器={chConvert}, 服务端={server_ch}), 处理 {len(comments_data)} 条")
    except Exception as e:
        logger.error(f"应用简繁转换失败: {e}", exc_info=True)

    # 修正：使用统一的弹幕处理函数，以确保输出格式符合 dandanplay 客户端规范
    processed_comments = process_comments_for_dandanplay(comments_data)
    return models.CommentResponse(count=len(processed_comments), comments=processed_comments)

# === get_comments_for_dandan ===
@comments_router.get(
    "/comment/{episodeId}",
    response_model=models.CommentResponse,
    summary="[dandanplay兼容] 获取弹幕"
)
async def get_comments_for_dandan(
    request: Request,
    episodeId: int = Path(..., description="分集ID (来自 /search/episodes 响应中的 episodeId)"),
    chConvert: int = Query(0, description="中文简繁转换。0-不转换，1-转换为简体，2-转换为繁体。"),
    # 'from' 是 Python 的关键字，所以我们必须使用别名
    fromTime: int = Query(0, alias="from", description="弹幕开始时间(秒)"),
    withRelated: bool = Query(True, description="是否包含关联弹幕"),
    token: str = Depends(get_token_from_path),
    session: AsyncSession = Depends(get_db_session),
    config_manager: ConfigManager = Depends(get_config_manager),
    scraper_manager: ScraperManager = Depends(get_scraper_manager),
    task_manager: TaskManager = Depends(get_task_manager),
    rate_limiter: RateLimiter = Depends(get_rate_limiter)
):
    """
    模拟 dandanplay 的弹幕获取接口。
    优化：优先使用弹幕库，如果没有则直接从源站获取并异步存储。
    """
    # 延迟导入预下载相关函数，避免循环导入
    wait_for_refresh_task, try_predownload_next_episode = _get_predownload_functions()

    # 检查是否有刷新任务正在执行，如果有则等待（最多15秒）
    await wait_for_refresh_task(episodeId, task_manager, max_wait_seconds=15.0)

    # 1. 优先从弹幕库获取弹幕
    comments_data = await crud.fetch_comments(session, episodeId)

    # 预下载下一集弹幕 (异步,不阻塞当前响应)
    # 只有当前集已存在于数据库时才触发预下载（后备场景会在任务完成后单独触发）
    if comments_data:
        predownload_task = asyncio.create_task(try_predownload_next_episode(
            episodeId, request.app.state.db_session_factory, config_manager, task_manager,
            scraper_manager, rate_limiter
        ))

        # 添加异常处理回调
        def handle_predownload_exception(task):
            try:
                task.result()  # 如果任务有异常，这里会抛出
            except Exception as e:
                logger.error(f"预下载任务异常 (episodeId={episodeId}): {e}", exc_info=True)

        predownload_task.add_done_callback(handle_predownload_exception)

    if not comments_data:
        logger.info(f"弹幕库中未找到 episodeId={episodeId} 的弹幕，尝试直接从源站获取")

        # 检查是否是后备搜索/匹配后备的episodeId
        # 虚拟episodeId格式: 25000166010002 (166=anime_id, 01=source_order, 0002=episode_number)
        # 缓存key格式: fallback_episode_25000166010000 (最后4位为0000表示整部剧)

        fallback_info = None
        episode_number = None

        # 尝试解析虚拟episodeId
        if episodeId >= 25000000000000:
            # 提取anime_id, source_order, episode_number
            temp_id = episodeId - 25000000000000
            anime_id_part = temp_id // 1000000
            temp_id = temp_id % 1000000
            source_order_part = temp_id // 10000
            episode_number = temp_id % 10000

            # 构造整部剧的缓存key
            virtual_anime_base = 25000000000000 + anime_id_part * 1000000 + source_order_part * 10000
            fallback_series_key = f"fallback_episode_{virtual_anime_base}"

            # 从数据库缓存中查找整部剧的信息
            _backend = get_cache_backend()
            fallback_info = None
            if _backend is not None:
                try:
                    fallback_info = await _backend.get(fallback_series_key, region="default")
                except Exception as e:
                    logger.warning(f"缓存后端读取失败，回退到数据库: {e}")
            if fallback_info is None:
                fallback_info = await crud.get_cache(session, fallback_series_key)
            logger.debug(f"查找缓存: {fallback_series_key}, 找到: {fallback_info is not None}")

        # 如果数据库缓存中没有,再从数据库缓存中查找(使用新的前缀)
        if not fallback_info:
            fallback_episode_cache_key = f"fallback_episode_{episodeId}"
            fallback_info = await get_db_cache(session, FALLBACK_SEARCH_CACHE_PREFIX, fallback_episode_cache_key)
            if fallback_info:
                episode_number = fallback_info.get("episode_number")

        if fallback_info:
            logger.info(f"检测到后备搜索/匹配后备的episodeId: {episodeId}, 集数: {episode_number}")

            # 从缓存中获取信息
            real_anime_id = fallback_info["real_anime_id"]
            provider = fallback_info["provider"]
            mediaId = fallback_info["mediaId"]
            final_title = fallback_info["final_title"]
            final_season = fallback_info["final_season"]
            media_type = fallback_info["media_type"]
            imageUrl = fallback_info.get("imageUrl")
            year = fallback_info.get("year")

            # 步骤1：创建或获取anime条目
            stmt = select(Anime).where(Anime.id == real_anime_id)
            result = await session.execute(stmt)
            existing_anime = result.scalar_one_or_none()

            if not existing_anime:
                # 创建anime条目
                logger.info(f"创建anime条目: id={real_anime_id}, title='{final_title}'")
                new_anime = Anime(
                    id=real_anime_id,
                    title=final_title,
                    type=media_type,
                    season=final_season,
                    imageUrl=imageUrl,
                    year=year,
                    createdAt=get_now()
                )
                session.add(new_anime)
                await session.flush()
                # 同步PostgreSQL序列(避免主键冲突)
                await sync_postgres_sequence(session)
            else:
                logger.info(f"anime条目已存在: id={real_anime_id}, title='{existing_anime.title}'")

            # 步骤2：创建或获取source关联
            source_id = await crud.link_source_to_anime(session, real_anime_id, provider, mediaId)
            logger.info(f"source_id={source_id}")

            # 提交anime和source创建，避免与后台任务产生锁冲突
            await session.commit()
            logger.info(f"已提交anime和source创建")

            # 步骤3：获取分集信息
            logger.info(f"开始获取分集信息: provider={provider}, mediaId={mediaId}, episode_number={episode_number}")

            # 获取scraper
            scraper = scraper_manager.get_scraper(provider)
            if not scraper:
                logger.error(f"无法获取scraper: {provider}")
                await session.rollback()
                return models.CommentResponse(count=0, comments=[])

            # 获取分集列表
            try:
                episodes_list = await scraper.get_episodes(mediaId, db_media_type=media_type)
                if not episodes_list or len(episodes_list) < episode_number:
                    logger.error(f"无法获取第{episode_number}集的信息")
                    await session.rollback()
                    return models.CommentResponse(count=0, comments=[])

                # 获取目标分集信息
                target_episode = episodes_list[episode_number - 1]
                provider_episode_id = target_episode.episodeId
                episode_title = target_episode.title
                episode_url = target_episode.url

                logger.info(f"获取到分集信息: title='{episode_title}', provider_episode_id='{provider_episode_id}'")

            except Exception as e:
                logger.error(f"获取分集信息失败: {e}", exc_info=True)
                await session.rollback()
                return models.CommentResponse(count=0, comments=[])

            # 步骤4：下载弹幕 (使用task_manager提交到后备队列)
            logger.info(f"开始下载弹幕: provider_episode_id={provider_episode_id}")

            # 检查是否已有相同的弹幕下载任务正在进行
            task_unique_key = f"match_fallback_comments_{episodeId}"
            existing_task = await crud.find_recent_task_by_unique_key(session, task_unique_key, 1)
            if existing_task:
                logger.info(f"弹幕下载任务已存在: {task_unique_key}，等待任务完成...")
                # 等待最多30秒，检查缓存中是否有结果
                cache_key = f"comments_{episodeId}"
                for i in range(30):
                    await asyncio.sleep(1)
                    cached_comments = await get_db_cache(session, COMMENTS_FETCH_CACHE_PREFIX, cache_key)
                    if cached_comments:
                        logger.info(f"从缓存中获取到弹幕数据，共 {len(cached_comments)} 条")
                        break
                # 跳过任务提交，直接进入缓存读取逻辑
            else:
                # 任务不存在，提交新任务
                # 保存当前作用域的变量，避免闭包问题
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
                current_final_season = final_season
                current_media_type = media_type
                current_imageUrl = imageUrl
                current_year = year
                current_episodes_list = episodes_list  # 保存整部剧的分集列表

                async def download_match_fallback_comments_task(task_session, progress_callback):
                    """匹配后备弹幕下载任务"""
                    try:
                        await progress_callback(10, "开始下载弹幕...")

                        # 检查流控
                        await current_rate_limiter.check_fallback("match", current_provider)

                        # 下载弹幕
                        comments = await current_scraper.get_comments(current_provider_episode_id, progress_callback=progress_callback)
                        if not comments:
                            logger.warning(f"下载失败，未获取到弹幕")
                            return None

                        # 增加流控计数
                        await current_rate_limiter.increment_fallback("match", current_provider)
                        logger.info(f"下载成功，共 {len(comments)} 条弹幕")

                        # 立即存储到数据库缓存中，让主接口能快速返回
                        cache_key = f"comments_{current_episodeId}"
                        await set_db_cache(task_session, COMMENTS_FETCH_CACHE_PREFIX, cache_key, comments, COMMENTS_FETCH_CACHE_TTL)
                        logger.info(f"弹幕已存入缓存: {cache_key}")

                        await progress_callback(60, "创建数据库条目...")

                        # 在task_session中创建或获取anime条目
                        stmt = select(Anime).where(Anime.id == current_real_anime_id)
                        result = await task_session.execute(stmt)
                        existing_anime = result.scalar_one_or_none()

                        if not existing_anime:
                            # 创建anime条目
                            logger.info(f"任务中创建anime条目: id={current_real_anime_id}, title='{current_final_title}'")
                            new_anime = Anime(
                                id=current_real_anime_id,
                                title=current_final_title,
                                type=current_media_type,
                                season=current_final_season,
                                imageUrl=current_imageUrl,
                                year=current_year,
                                createdAt=get_now()
                            )
                            task_session.add(new_anime)
                            await task_session.flush()

                            # 同步PostgreSQL序列(避免主键冲突)
                            await sync_postgres_sequence(task_session)
                        else:
                            logger.info(f"任务中anime条目已存在: id={current_real_anime_id}, title='{existing_anime.title}'")

                        # 创建或获取source关联 (在task_session中)
                        source_id = await crud.link_source_to_anime(task_session, current_real_anime_id, current_provider, current_mediaId)
                        logger.info(f"source_id={source_id}")

                        # 获取source_order用于生成虚拟episodeId
                        stmt_source = select(AnimeSource.sourceOrder).where(AnimeSource.id == source_id)
                        result_source = await task_session.execute(stmt_source)
                        source_order = result_source.scalar_one()

                        # 创建当前Episode条目
                        episode_db_id = await crud.create_episode_if_not_exists(
                            task_session, current_real_anime_id, source_id, current_episode_number,
                            current_episode_title, current_episode_url, current_provider_episode_id
                        )
                        await task_session.flush()
                        logger.info(f"Episode条目已创建/存在: id={episode_db_id}")

                        # 为整部剧创建一条缓存记录(不下载弹幕,不创建数据库记录)
                        # 这样播放器推理下一集时能通过缓存触发弹幕下载
                        # 缓存条目保留3小时,支持连续播放
                        try:
                            # 使用虚拟anime_id作为缓存key的前缀
                            # 格式: fallback_episode_25000166010000 (最后4位为0000表示整部剧)
                            virtual_anime_base = 25000000000000 + current_real_anime_id * 1000000 + source_order * 10000
                            fallback_series_key = f"fallback_episode_{virtual_anime_base}"

                            cache_value = {
                                "real_anime_id": current_real_anime_id,
                                "provider": current_provider,
                                "mediaId": current_mediaId,
                                "final_title": current_final_title,
                                "final_season": current_final_season,
                                "media_type": current_media_type,
                                "imageUrl": current_imageUrl,
                                "year": current_year,
                                "total_episodes": len(current_episodes_list)
                            }

                            # 存储到缓存,3小时过期
                            _backend = get_cache_backend()
                            if _backend is not None:
                                try:
                                    await _backend.set(fallback_series_key, cache_value, ttl=10800, region="default")
                                except Exception:
                                    await crud.set_cache(task_session, fallback_series_key, cache_value, 10800)
                            else:
                                await crud.set_cache(task_session, fallback_series_key, cache_value, 10800)
                            await task_session.flush()
                            logger.info(f"为整部剧创建了缓存记录: {fallback_series_key} (共{len(current_episodes_list)}集)")
                        except Exception as e:
                            logger.warning(f"创建缓存记录失败: {e}")

                        await progress_callback(80, "保存弹幕...")

                        # 保存弹幕
                        added_count = await crud.save_danmaku_for_episode(
                            task_session, current_episodeId, comments, config_manager,
                            fire_threshold=current_scraper.likes_fire_threshold
                        )
                        await task_session.commit()
                        logger.info(f"保存成功，共 {added_count} 条弹幕")

                        # 将弹幕数据写入缓存表,供外部会话读取
                        cache_key = f"comments_{current_episodeId}"
                        await set_db_cache(task_session, COMMENTS_FETCH_CACHE_PREFIX, cache_key, comments, 300)  # 5分钟过期
                        await task_session.commit()
                        logger.debug(f"弹幕数据已写入缓存: {cache_key}")

                        # 清理数据库缓存
                        await delete_db_cache(task_session, FALLBACK_SEARCH_CACHE_PREFIX, current_fallback_episode_cache_key)
                        logger.debug(f"清理数据库缓存: {current_fallback_episode_cache_key}")

                        # 注意:不删除数据库缓存中的整部剧记录,保留3小时以支持连续播放
                        # 数据库缓存会自动过期

                        await progress_callback(100, "完成")
                        return comments

                    except Exception as e:
                        logger.error(f"匹配后备弹幕下载任务执行失败: {e}", exc_info=True)
                        await task_session.rollback()
                        return None

                # 提交弹幕下载任务到后备队列
                try:
                    task_id, done_event = await task_manager.submit_task(
                        download_match_fallback_comments_task,
                        f"匹配后备弹幕下载: episodeId={episodeId}",
                        unique_key=task_unique_key,
                        task_type="download_comments",
                        queue_type="fallback"  # 使用后备队列
                    )
                    logger.info(f"已提交匹配后备弹幕下载任务: {task_id}")

                    # 用于标记是否已触发预下载（避免重复触发）
                    predownload_triggered = False
                    predownload_lock = asyncio.Lock()

                    # 添加后台任务完成回调，用于超时场景下触发预下载
                    def handle_task_completion(event):
                        """后台任务完成时的回调，仅在超时场景下触发预下载"""
                        async def trigger_predownload():
                            nonlocal predownload_triggered
                            try:
                                # 等待任务完成
                                await event.wait()

                                # 检查是否已经触发过预下载（30秒内完成的情况）
                                async with predownload_lock:
                                    if predownload_triggered:
                                        logger.info(f"预下载已在30秒内触发，跳过回调触发 (episodeId={episodeId})")
                                        return
                                    predownload_triggered = True

                                logger.info(f"匹配后备任务已完成（超时后），检查是否需要触发预下载 (episodeId={episodeId})")

                                # 创建新的session检查弹幕是否下载成功
                                async with request.app.state.db_session_factory() as check_session:
                                    check_comments = await crud.fetch_comments(check_session, episodeId)
                                    if check_comments:
                                        logger.info(f"匹配后备任务成功，触发预下载下一集 (episodeId={episodeId})")
                                        await try_predownload_next_episode(
                                            episodeId, request.app.state.db_session_factory, config_manager,
                                            task_manager, scraper_manager, rate_limiter
                                        )
                                    else:
                                        logger.warning(f"匹配后备任务完成但未找到弹幕，跳过预下载 (episodeId={episodeId})")
                            except Exception as e:
                                logger.error(f"匹配后备完成回调异常 (episodeId={episodeId}): {e}", exc_info=True)

                        # 在后台执行预下载触发逻辑
                        asyncio.create_task(trigger_predownload())

                    # 注册完成回调
                    handle_task_completion(done_event)

                    # 等待任务完成，但设置较短的超时时间（30秒）
                    try:
                        await asyncio.wait_for(done_event.wait(), timeout=30.0)
                        # 任务完成，刷新会话以看到任务会话的提交
                        await session.commit()
                        logger.info(f"匹配后备弹幕下载任务完成，从数据库重新读取弹幕")
                        # 重新从数据库读取弹幕
                        comments_data = await crud.fetch_comments(session, episodeId)
                        if comments_data:
                            logger.info(f"从数据库读取到 {len(comments_data)} 条弹幕")

                            # 30秒内完成，立即触发预下载
                            async with predownload_lock:
                                if not predownload_triggered:
                                    predownload_triggered = True
                                    predownload_task = asyncio.create_task(try_predownload_next_episode(
                                        episodeId, request.app.state.db_session_factory, config_manager, task_manager,
                                        scraper_manager, rate_limiter
                                    ))
                                    def handle_predownload_exception(task):
                                        try:
                                            task.result()
                                        except Exception as e:
                                            logger.error(f"预下载任务异常 (episodeId={episodeId}): {e}", exc_info=True)
                                    predownload_task.add_done_callback(handle_predownload_exception)
                                    logger.info(f"匹配后备场景：已触发预下载下一集")
                        else:
                            logger.warning(f"任务完成但数据库中未找到弹幕数据")
                    except asyncio.TimeoutError:
                        logger.info(f"匹配后备弹幕下载任务超时（30秒），任务将在后台继续执行，完成后会自动触发预下载")
                        # 超时后返回空结果，但任务继续在后台运行，完成后会通过回调触发预下载
                        return models.CommentResponse(count=0, comments=[])

                except HTTPException as e:
                    # 如果是409错误(任务已在运行中),等待一段时间
                    if e.status_code == 409:
                        logger.info(f"任务已在运行中，等待现有任务完成...")
                        # 等待最多30秒
                        for _ in range(30):
                            await asyncio.sleep(1)
                            # 尝试从数据库读取episode记录,检查是否已有弹幕文件
                            try:
                                stmt = select(Episode).where(Episode.id == episodeId)
                                result = await session.execute(stmt)
                                episode = result.scalar_one_or_none()
                                if episode and episode.danmakuFilePath:
                                    logger.info(f"检测到弹幕文件已创建: {episode.danmakuFilePath}")
                                    break
                            except Exception:
                                pass
                        # 继续执行后续逻辑，从数据库读取弹幕
                    else:
                        logger.error(f"提交匹配后备弹幕下载任务失败: {e}", exc_info=True)
                        await session.rollback()
                        return models.CommentResponse(count=0, comments=[])
                except Exception as e:
                    logger.error(f"提交匹配后备弹幕下载任务失败: {e}", exc_info=True)
                    await session.rollback()
                    return models.CommentResponse(count=0, comments=[])

        # 任务完成后,弹幕已经保存到数据库,不再从缓存读取
        # 2. 检查是否是后备搜索的特殊episodeId（以25开头的新格式）
        if str(episodeId).startswith("25") and len(str(episodeId)) >= 13:  # 新的ID格式
            # 解析episodeId：25 + animeId(6位) + 源顺序(2位) + 集编号(4位)
            episode_id_str = str(episodeId)
            real_anime_id = int(episode_id_str[2:8])  # 提取真实animeId
            _ = int(episode_id_str[8:10])  # 提取源顺序（暂时不使用）
            episode_number = int(episode_id_str[10:14])  # 提取集编号

            # 查找对应的映射信息
            episode_url = None
            provider = None

            # 首先尝试从数据库缓存中获取episodeId的映射
            mapping_data = await get_episode_mapping(session, episodeId)
            if mapping_data:
                episode_url = mapping_data["media_id"]
                provider = mapping_data["provider"]
                logger.info(f"从缓存获取episodeId映射: episodeId={episodeId}, provider={provider}, url={episode_url}")
            else:
                # 如果缓存中没有，从数据库缓存中查找
                # 首先尝试根据用户最后的选择来确定源
                try:
                    all_cache_keys = await get_cache_keys(session, f"{FALLBACK_SEARCH_CACHE_PREFIX}*")
                    for cache_key in all_cache_keys:
                        search_key = cache_key.replace(FALLBACK_SEARCH_CACHE_PREFIX, "")
                        search_info = await get_db_cache(session, FALLBACK_SEARCH_CACHE_PREFIX, search_key)

                        if search_info and search_info.get("status") == "completed" and "bangumi_mapping" in search_info:
                            # 检查是否有用户最后的选择记录
                            last_bangumi_id = await get_db_cache(session, USER_LAST_BANGUMI_CHOICE_PREFIX, search_key)
                            if last_bangumi_id and last_bangumi_id in search_info["bangumi_mapping"]:
                                mapping_info = search_info["bangumi_mapping"][last_bangumi_id]
                                # 检查真实animeId是否匹配
                                if mapping_info.get("real_anime_id") == real_anime_id:
                                    episode_url = mapping_info["media_id"]
                                    provider = mapping_info["provider"]
                                    logger.info(f"根据用户最后选择找到映射: bangumiId={last_bangumi_id}, provider={provider}")
                                    break
                except Exception as e:
                    logger.error(f"查找用户选择映射失败: {e}")

                # 如果没有找到用户最后的选择，则使用原来的逻辑
                if not episode_url:
                    try:
                        all_cache_keys_fallback = await get_cache_keys(session, f"{FALLBACK_SEARCH_CACHE_PREFIX}*")
                        for cache_key_fallback in all_cache_keys_fallback:
                            search_key_fallback = cache_key_fallback.replace(FALLBACK_SEARCH_CACHE_PREFIX, "")
                            search_info_fallback = await get_db_cache(session, FALLBACK_SEARCH_CACHE_PREFIX, search_key_fallback)

                            if search_info_fallback and search_info_fallback.get("status") == "completed" and "bangumi_mapping" in search_info_fallback:
                                for bangumi_id, mapping_info in search_info_fallback["bangumi_mapping"].items():
                                    # 检查真实animeId是否匹配
                                    if mapping_info.get("real_anime_id") == real_anime_id:
                                        episode_url = mapping_info["media_id"]
                                        provider = mapping_info["provider"]
                                        logger.info(f"根据真实animeId={real_anime_id}找到映射: bangumiId={bangumi_id}, provider={provider}")
                                        break
                                if episode_url:
                                    break
                    except Exception as e:
                        logger.error(f"查找真实animeId映射失败: {e}")

            if episode_url and provider:
                logger.info(f"找到后备搜索映射: provider={provider}, url={episode_url}")

                # 检查是否已有相同的弹幕下载任务正在进行或最近完成
                task_unique_key = f"fallback_comments_{episodeId}"
                existing_task = await crud.find_recent_task_by_unique_key(session, task_unique_key, 1)
                if existing_task:
                    logger.info(f"弹幕下载任务已存在: {task_unique_key}，从数据库缓存读取...")
                    # 直接从数据库缓存表读取弹幕数据
                    cache_key = f"comments_{episodeId}"
                    comments_data = await get_db_cache(session, COMMENTS_FETCH_CACHE_PREFIX, cache_key)
                    if comments_data:
                        logger.info(f"从数据库缓存获取到弹幕数据，共 {len(comments_data)} 条")
                    else:
                        logger.warning(f"任务已存在但数据库缓存中未找到弹幕数据")
                    # 跳过任务提交,直接使用缓存数据或继续后续逻辑
                else:
                    # 3. 将弹幕下载包装成任务管理器任务
                    # 保存当前作用域的变量，避免闭包问题
                    current_provider = provider
                    current_episode_url = episode_url
                    current_episode_number = episode_number
                    current_episodeId = episodeId
                    current_config_manager = config_manager
                    current_scraper_manager = scraper_manager
                    current_rate_limiter = rate_limiter
                    current_episodes_list_ref = None  # 用于保存整部剧的分集列表

                    async def download_comments_task(task_session, progress_callback):
                        try:
                            await progress_callback(10, "开始获取弹幕...")
                            scraper = current_scraper_manager.get_scraper(current_provider)
                            if scraper:
                                # 首先获取分集列表
                                await progress_callback(30, "获取分集列表...")
                                # 查找映射信息（根据real_anime_id匹配）
                                mapping_info = None
                                try:
                                    all_cache_keys_mapping = await get_cache_keys(task_session, f"{FALLBACK_SEARCH_CACHE_PREFIX}*")
                                    for cache_key_mapping in all_cache_keys_mapping:
                                        search_key = cache_key_mapping.replace(FALLBACK_SEARCH_CACHE_PREFIX, "")
                                        last_bangumi_id = await get_db_cache(task_session, USER_LAST_BANGUMI_CHOICE_PREFIX, search_key)
                                        if last_bangumi_id:
                                            search_info = await get_db_cache(task_session, FALLBACK_SEARCH_CACHE_PREFIX, search_key)
                                            if search_info and last_bangumi_id in search_info.get("bangumi_mapping", {}):
                                                temp_mapping = search_info["bangumi_mapping"][last_bangumi_id]
                                                # 检查real_anime_id是否匹配
                                                if temp_mapping.get("real_anime_id") == real_anime_id:
                                                    mapping_info = temp_mapping
                                                    logger.info(f"找到匹配的映射信息: search_key={search_key}, bangumiId={last_bangumi_id}, real_anime_id={real_anime_id}")
                                                    break
                                except Exception as e:
                                    logger.error(f"查找映射信息失败: {e}")

                                if not mapping_info:
                                    logger.error(f"无法找到real_anime_id={real_anime_id}的映射信息")
                                    return None

                                media_type = mapping_info.get("type", "movie")
                                episodes_list = await scraper.get_episodes(current_episode_url, db_media_type=media_type)
                                # 保存到外层作用域，用于后续批量创建Episode记录
                                nonlocal current_episodes_list_ref
                                current_episodes_list_ref = episodes_list

                            if episodes_list and len(episodes_list) >= current_episode_number:
                                # 获取对应集数的分集信息（episode_number是从1开始的）
                                target_episode = episodes_list[current_episode_number - 1]
                                provider_episode_id = target_episode.episodeId
                                # 使用原生分集标题和URL
                                original_episode_title = target_episode.title
                                original_episode_url = target_episode.url or ""

                                if provider_episode_id:
                                    episode_id_for_comments = scraper.format_episode_id_for_comments(provider_episode_id)

                                    # 使用三线程下载模式获取弹幕
                                    virtual_episode = ProviderEpisodeInfo(
                                        provider=current_provider,
                                        episodeIndex=current_episode_number,
                                        title=original_episode_title,  # 使用原生标题
                                        episodeId=episode_id_for_comments,
                                        url=original_episode_url  # 使用原生URL
                                    )

                                    # 使用并发下载获取弹幕（三线程模式）
                                    async def dummy_progress_callback(_, _unused):
                                        pass  # 空的异步进度回调，忽略所有参数

                                    download_results = await tasks._download_episode_comments_concurrent(
                                        scraper, [virtual_episode], current_rate_limiter,
                                        dummy_progress_callback,
                                        is_fallback=True,
                                        fallback_type="search"
                                    )

                                    # 提取弹幕数据
                                    raw_comments_data = None
                                    if download_results and len(download_results) > 0:
                                        _, comments = download_results[0]  # 忽略episode_index
                                        raw_comments_data = comments
                                else:
                                    logger.warning(f"无法获取 {current_provider} 的分集ID: episode_number={current_episode_number}")
                                    raw_comments_data = None
                            else:
                                logger.warning(f"从 {current_provider} 获取分集列表失败或集数不足: media_id={current_episode_url}, episode_number={current_episode_number}")
                                raw_comments_data = None

                            if raw_comments_data:
                                    logger.info(f"成功从 {current_provider} 获取 {len(raw_comments_data)} 条弹幕")
                                    await progress_callback(90, "弹幕获取完成，正在创建数据库条目...")

                                    # 参考 WebUI 导入逻辑：先获取弹幕成功，再创建数据库条目
                                    try:
                                        # 从映射信息中获取创建条目所需的数据
                                        original_title = mapping_info.get("original_title", "未知标题")
                                        media_type = mapping_info.get("type", "movie")

                                        # 从搜索缓存中获取更多信息（年份、海报等）和搜索关键词
                                        year = None
                                        image_url = None
                                        search_keyword = None
                                        try:
                                            all_cache_keys_info = await get_cache_keys(task_session, f"{FALLBACK_SEARCH_CACHE_PREFIX}*")
                                            for cache_key_info in all_cache_keys_info:
                                                search_key = cache_key_info.replace(FALLBACK_SEARCH_CACHE_PREFIX, "")
                                                last_bangumi_id = await get_db_cache(task_session, USER_LAST_BANGUMI_CHOICE_PREFIX, search_key)
                                                if last_bangumi_id:
                                                    search_info = await get_db_cache(task_session, FALLBACK_SEARCH_CACHE_PREFIX, search_key)
                                                    if search_info and last_bangumi_id in search_info.get("bangumi_mapping", {}):
                                                        # 获取搜索关键词（从search_key中提取）
                                                        if search_key.startswith("search_"):
                                                            # 从数据库缓存中获取原始搜索词
                                                            search_keyword = search_info.get("search_term")

                                                        for result in search_info.get("results", []):
                                                            # result 是字典（从 model_dump() 转换而来）
                                                            if isinstance(result, dict) and result.get('bangumiId') == last_bangumi_id:
                                                                year = result.get('year')
                                                                image_url = result.get('imageUrl')
                                                                break
                                                        break
                                        except Exception as e:
                                            logger.error(f"查找搜索缓存信息失败: {e}")

                                        # 解析搜索关键词，提取纯标题（如"天才基本法 S01E13" -> "天才基本法"）
                                        search_term = search_keyword or original_title
                                        parsed_info = parse_search_keyword(search_term)
                                        base_title = parsed_info["title"]

                                        # 由于我们在分配real_anime_id时已经检查了数据库，这里直接使用real_anime_id
                                        # 如果数据库中已有相同标题的条目，real_anime_id就是已有的anime_id
                                        # 如果没有，real_anime_id就是新分配的anime_id，需要创建条目

                                        # 检查数据库中是否已有这个anime_id的条目
                                        stmt = select(Anime.id).where(Anime.id == real_anime_id)
                                        result = await task_session.execute(stmt)
                                        existing_anime_row = result.scalar_one_or_none()

                                        if existing_anime_row:
                                            # 如果已存在，直接使用
                                            anime_id = real_anime_id
                                            logger.info(f"使用已存在的番剧: ID={anime_id}")
                                        else:
                                            # 如果不存在，直接创建新的（使用real_anime_id作为指定ID）
                                            new_anime = Anime(
                                                id=real_anime_id,
                                                title=base_title,
                                                type=media_type,
                                                season=1,
                                                year=year,
                                                imageUrl=image_url,
                                                createdAt=get_now()
                                            )
                                            task_session.add(new_anime)
                                            await task_session.flush()  # 确保ID可用
                                            anime_id = real_anime_id
                                            logger.info(f"创建新番剧: ID={anime_id}, 标题='{base_title}', 年份={year}")

                                            # 同步PostgreSQL序列(避免主键冲突)
                                            await sync_postgres_sequence(task_session)

                                        # 2. 创建源关联
                                        source_id = await crud.link_source_to_anime(
                                            task_session, anime_id, current_provider, current_episode_url
                                        )

                                        # 获取source_order用于生成虚拟episodeId
                                        stmt_source = select(AnimeSource.sourceOrder).where(AnimeSource.id == source_id)
                                        result_source = await task_session.execute(stmt_source)
                                        source_order = result_source.scalar_one()

                                        # 3. 创建分集条目（使用原生标题和URL）
                                        episode_db_id = await crud.create_episode_if_not_exists(
                                            task_session, anime_id, source_id, current_episode_number,
                                            original_episode_title, original_episode_url, provider_episode_id
                                        )

                                        # 为整部剧创建一条缓存记录(不下载弹幕,不创建数据库记录)
                                        # 这样播放器推理下一集时能通过缓存触发弹幕下载
                                        # 缓存条目保留3小时,支持连续播放
                                        if current_episodes_list_ref:
                                            try:
                                                # 使用虚拟anime_id作为缓存key的前缀
                                                # 格式: fallback_episode_25000166010000 (最后4位为0000表示整部剧)
                                                virtual_anime_base = 25000000000000 + anime_id * 1000000 + source_order * 10000
                                                fallback_series_key = f"fallback_episode_{virtual_anime_base}"

                                                cache_value = {
                                                    "real_anime_id": anime_id,
                                                    "provider": current_provider,
                                                    "mediaId": current_episode_url,
                                                    "final_title": base_title,
                                                    "final_season": 1,
                                                    "media_type": media_type,
                                                    "imageUrl": image_url,
                                                    "year": year,
                                                    "total_episodes": len(current_episodes_list_ref)
                                                }

                                                # 存储到缓存,3小时过期
                                                _backend = get_cache_backend()
                                                if _backend is not None:
                                                    try:
                                                        await _backend.set(fallback_series_key, cache_value, ttl=10800, region="default")
                                                    except Exception:
                                                        await crud.set_cache(task_session, fallback_series_key, cache_value, 10800)
                                                else:
                                                    await crud.set_cache(task_session, fallback_series_key, cache_value, 10800)
                                                await task_session.flush()
                                                logger.info(f"为整部剧创建了缓存记录: {fallback_series_key} (共{len(current_episodes_list_ref)}集)")
                                            except Exception as e:
                                                logger.warning(f"创建缓存记录失败: {e}")

                                        # 4. 保存弹幕到数据库
                                        added_count = await crud.save_danmaku_for_episode(
                                            task_session, episode_db_id, raw_comments_data, current_config_manager,
                                            fire_threshold=scraper.likes_fire_threshold
                                        )
                                        await task_session.commit()

                                        logger.info(f"数据库条目创建完成: anime_id={anime_id}, source_id={source_id}, episode_db_id={episode_db_id}, 保存了 {added_count} 条弹幕")

                                        # 清除缓存中所有使用这个real_anime_id的映射关系
                                        # 因为数据库中已经有了这个ID的记录，下次分配时不会再使用这个ID
                                        try:
                                            all_cache_keys_cleanup = await get_cache_keys(task_session, f"{FALLBACK_SEARCH_CACHE_PREFIX}*")
                                            for cache_key_cleanup in all_cache_keys_cleanup:
                                                search_key = cache_key_cleanup.replace(FALLBACK_SEARCH_CACHE_PREFIX, "")
                                                search_info = await get_db_cache(task_session, FALLBACK_SEARCH_CACHE_PREFIX, search_key)

                                                if search_info and search_info.get("status") == "completed" and "bangumi_mapping" in search_info:
                                                    for bangumi_id, mapping_info in list(search_info["bangumi_mapping"].items()):
                                                        if mapping_info.get("real_anime_id") == real_anime_id:
                                                            # 从映射中移除这个条目
                                                            del search_info["bangumi_mapping"][bangumi_id]
                                                            logger.info(f"清除缓存映射: search_key={search_key}, bangumiId={bangumi_id}, real_anime_id={real_anime_id}")
                                                    # 保存更新后的缓存
                                                    await set_db_cache(task_session, FALLBACK_SEARCH_CACHE_PREFIX, search_key, search_info, FALLBACK_SEARCH_CACHE_TTL)
                                        except Exception as e:
                                            logger.error(f"清除缓存映射失败: {e}")

                                    except Exception as db_error:
                                        logger.error(f"创建数据库条目失败: {db_error}", exc_info=True)
                                        await task_session.rollback()

                                    # 不再写入缓存,弹幕已经保存到数据库和XML文件
                                    # 外部会话会从数据库读取episode记录和弹幕文件
                                    logger.info(f"弹幕已保存到数据库和文件,任务完成")
                                    return raw_comments_data
                            else:
                                logger.warning(f"获取弹幕失败")
                                return None
                        except Exception as e:
                            logger.error(f"弹幕下载任务执行失败: {e}", exc_info=True)
                            return None

                    # 提交弹幕下载任务
                    try:
                        task_id, done_event = await task_manager.submit_task(
                            download_comments_task,
                            f"后备搜索弹幕下载: episodeId={episodeId}",
                            unique_key=task_unique_key,
                            task_type="download_comments",
                            queue_type="fallback"  # 使用后备队列
                        )
                        logger.info(f"已提交弹幕下载任务: {task_id}")

                        # 用于标记是否已触发预下载（避免重复触发）
                        predownload_triggered = False
                        predownload_lock = asyncio.Lock()

                        # 添加后台任务完成回调，用于超时场景下触发预下载
                        def handle_task_completion(event):
                            """后台任务完成时的回调，仅在超时场景下触发预下载"""
                            async def trigger_predownload():
                                nonlocal predownload_triggered
                                try:
                                    # 等待任务完成
                                    await event.wait()

                                    # 检查是否已经触发过预下载（30秒内完成的情况）
                                    async with predownload_lock:
                                        if predownload_triggered:
                                            logger.info(f"预下载已在30秒内触发，跳过回调触发 (episodeId={episodeId})")
                                            return
                                        predownload_triggered = True

                                    logger.info(f"后备搜索任务已完成（超时后），检查是否需要触发预下载 (episodeId={episodeId})")

                                    # 创建新的session检查弹幕是否下载成功
                                    async with request.app.state.db_session_factory() as check_session:
                                        check_comments = await crud.fetch_comments(check_session, episodeId)
                                        if check_comments:
                                            logger.info(f"后备搜索任务成功，触发预下载下一集 (episodeId={episodeId})")
                                            await try_predownload_next_episode(
                                                episodeId, request.app.state.db_session_factory, config_manager,
                                                task_manager, scraper_manager, rate_limiter
                                            )
                                        else:
                                            logger.warning(f"后备搜索任务完成但未找到弹幕，跳过预下载 (episodeId={episodeId})")
                                except Exception as e:
                                    logger.error(f"后备搜索完成回调异常 (episodeId={episodeId}): {e}", exc_info=True)

                            # 在后台执行预下载触发逻辑
                            asyncio.create_task(trigger_predownload())

                        # 注册完成回调
                        handle_task_completion(done_event)

                        # 等待任务完成，但设置较短的超时时间（30秒）
                        try:
                            await asyncio.wait_for(done_event.wait(), timeout=30.0)
                            # 任务完成，刷新会话以看到任务会话的提交
                            await session.commit()
                            logger.info(f"后备搜索弹幕下载任务完成，从数据库重新读取弹幕")
                            # 重新从数据库读取弹幕
                            comments_data = await crud.fetch_comments(session, episodeId)
                            if comments_data:
                                logger.info(f"从数据库读取到 {len(comments_data)} 条弹幕")

                                # 30秒内完成，立即触发预下载
                                async with predownload_lock:
                                    if not predownload_triggered:
                                        predownload_triggered = True
                                        predownload_task = asyncio.create_task(try_predownload_next_episode(
                                            episodeId, request.app.state.db_session_factory, config_manager, task_manager,
                                            scraper_manager, rate_limiter
                                        ))
                                        def handle_predownload_exception(task):
                                            try:
                                                task.result()
                                            except Exception as e:
                                                logger.error(f"预下载任务异常 (episodeId={episodeId}): {e}", exc_info=True)
                                        predownload_task.add_done_callback(handle_predownload_exception)
                                        logger.info(f"后备搜索场景：已触发预下载下一集")
                            else:
                                logger.warning(f"任务完成但数据库中未找到弹幕数据")
                        except asyncio.TimeoutError:
                            logger.info(f"后备搜索弹幕下载任务超时（30秒），任务将在后台继续执行，完成后会自动触发预下载")
                            # 任务继续在后台运行，完成后会通过回调触发预下载

                    except HTTPException as e:
                        if e.status_code == 409:  # 任务已在运行中
                            logger.info(f"弹幕下载任务已在运行中，等待现有任务完成...")
                            # 等待最多30秒
                            for _ in range(30):
                                await asyncio.sleep(1)
                                # 尝试从数据库读取episode记录,检查是否已有弹幕文件
                                try:
                                    stmt = select(Episode).where(Episode.id == episodeId)
                                    result = await session.execute(stmt)
                                    episode = result.scalar_one_or_none()
                                    if episode and episode.danmakuFilePath:
                                        logger.info(f"检测到弹幕文件已创建: {episode.danmakuFilePath}")
                                        break
                                except Exception:
                                    pass
                            # 继续执行后续逻辑，从数据库读取弹幕
                        else:
                            logger.error(f"提交弹幕下载任务失败: {e}", exc_info=True)
                    except Exception as e:
                        logger.error(f"提交弹幕下载任务失败: {e}", exc_info=True)

        # 如果仍然没有弹幕数据，返回空结果
        if not comments_data:
            logger.warning(f"无法获取 episodeId={episodeId} 的弹幕数据")
            return models.CommentResponse(count=0, comments=[])

    # 应用弹幕输出上限（按时间段均匀采样，带缓存）
    limit_str = await config_manager.get('danmakuOutputLimitPerSource', '-1')
    try:
        limit = int(limit_str)
    except (ValueError, TypeError):
        limit = -1

    # 检查是否启用合并输出
    merge_output_enabled = await config_manager.get('danmakuMergeOutputEnabled', 'false')
    if merge_output_enabled.lower() == 'true' and comments_data:
        # 获取合并后的弹幕（包含同一 anime 同一集数的所有源）
        merged_comments = await crud.fetch_merged_comments(session, episodeId)
        if merged_comments and len(merged_comments) > len(comments_data):
            logger.info(f"合并输出已启用: 原始 {len(comments_data)} 条 -> 合并后 {len(merged_comments)} 条")
            comments_data = merged_comments

    # 应用限制：按时间段均匀采样
    if limit > 0 and len(comments_data) > limit:
        # 检查缓存（合并输出时使用不同的缓存key）
        merge_suffix = "_merged" if merge_output_enabled.lower() == 'true' else ""
        cache_key = f"sampled_{episodeId}_{limit}{merge_suffix}"
        current_time = time.time()

        # 尝试从数据库缓存获取
        cached_data = await get_db_cache(session, SAMPLED_COMMENTS_CACHE_PREFIX, cache_key)
        if cached_data:
            # 缓存格式: {"comments": [...], "timestamp": 123456.789}
            cached_comments = cached_data.get("comments", [])
            cached_time = cached_data.get("timestamp", 0)
            if current_time - cached_time <= SAMPLED_CACHE_TTL:
                logger.info(f"使用缓存的采样结果: episodeId={episodeId}, limit={limit}, 缓存时间={int(current_time - cached_time)}秒前")
                comments_data = cached_comments
            else:
                # 缓存过期,重新采样
                logger.info(f"弹幕数量 {len(comments_data)} 超过限制 {limit}，开始均匀采样 (缓存已过期)")
                original_count = len(comments_data)
                comments_data = sample_comments_evenly(comments_data, limit)
                logger.info(f"弹幕采样完成: {original_count} -> {len(comments_data)} 条")

                # 更新缓存
                cache_value = {"comments": comments_data, "timestamp": current_time}
                await set_db_cache(session, SAMPLED_COMMENTS_CACHE_PREFIX, cache_key, cache_value, SAMPLED_COMMENTS_CACHE_TTL_DB)
        else:
            # 无缓存,执行采样
            logger.info(f"弹幕数量 {len(comments_data)} 超过限制 {limit}，开始均匀采样")
            original_count = len(comments_data)
            comments_data = sample_comments_evenly(comments_data, limit)
            logger.info(f"弹幕采样完成: {original_count} -> {len(comments_data)} 条")

            # 存入缓存
            cache_value = {"comments": comments_data, "timestamp": current_time}
            await set_db_cache(session, SAMPLED_COMMENTS_CACHE_PREFIX, cache_key, cache_value, SAMPLED_COMMENTS_CACHE_TTL_DB)
            logger.debug(f"采样结果已缓存: {cache_key}")

    # 应用黑名单过滤
    try:
        blacklist_enabled = await config_manager.get('danmakuBlacklistEnabled', 'false')
        if blacklist_enabled.lower() == 'true':
            blacklist_patterns = await config_manager.get('danmakuBlacklistPatterns', '')
            if blacklist_patterns:
                original_count = len(comments_data)
                comments_data = apply_blacklist_filter(comments_data, blacklist_patterns)
                filtered_count = original_count - len(comments_data)
                if filtered_count > 0:
                    logger.info(f"弹幕黑名单过滤 (episodeId: {episodeId}): 拦截 {filtered_count} 条，保留 {len(comments_data)} 条")
    except Exception as e:
        logger.error(f"应用弹幕黑名单过滤失败: {e}", exc_info=True)

    # 应用随机颜色 + 重复弹幕高亮（共用同一份色板）
    palette = DEFAULT_RANDOM_COLOR_PALETTE
    try:
        random_color_mode = await config_manager.get('danmakuRandomColorMode', DEFAULT_RANDOM_COLOR_MODE)
        random_color_palette_raw = await config_manager.get('danmakuRandomColorPalette', DEFAULT_RANDOM_COLOR_PALETTE)
        palette = parse_palette(random_color_palette_raw)
        comments_data = apply_random_color(comments_data, random_color_mode, palette)
    except Exception as e:
        logger.error(f"应用随机颜色失败: {e}", exc_info=True)

    # 重复弹幕自动上色（内容以 " X数字" 结尾且次数达到阈值，从色板随机取色）
    try:
        comments_data = apply_repeat_highlight(
            comments_data,
            min_count=DEFAULT_REPEAT_HIGHLIGHT_MIN_COUNT,
            palette=palette,
        )
    except Exception as e:
        logger.error(f"应用重复弹幕高亮失败: {e}", exc_info=True)

    # 处理简繁转换（根据优先级决定使用服务端配置还是播放器参数）
    try:
        server_ch = int(await config_manager.get('danmakuChConvert', '0'))
        priority = await config_manager.get('danmakuChConvertPriority', 'player')
        if priority == 'server':
            final_convert = server_ch
        else:
            final_convert = chConvert if chConvert != 0 else server_ch

        if final_convert in [1, 2] and comments_data:
            converter = OpenCC('t2s') if final_convert == 1 else OpenCC('s2t')
            for comment in comments_data:
                if 'm' in comment and comment['m']:
                    comment['m'] = converter.convert(comment['m'])
            logger.debug(f"弹幕简繁转换 (episodeId: {episodeId}): 最终模式={final_convert}(优先级={priority}, 播放器={chConvert}, 服务端={server_ch}), 处理 {len(comments_data)} 条")
    except Exception as e:
        logger.error(f"应用简繁转换失败: {e}", exc_info=True)

    # UA 已由 get_token_from_path 依赖项记录
    logger.debug(f"弹幕接口响应 (episodeId: {episodeId}): 总计 {len(comments_data)} 条弹幕")

    # 记录播放历史（用于 @SXDM 指令）
    try:
        await record_play_history(session, token, episodeId)
    except Exception as e:
        logger.error(f"记录播放历史失败: episodeId={episodeId}, error={e}", exc_info=True)

    # 修正：使用统一的弹幕处理函数，以确保输出格式符合 dandanplay 客户端规范
    processed_comments = process_comments_for_dandanplay(comments_data)

    return models.CommentResponse(count=len(processed_comments), comments=processed_comments)