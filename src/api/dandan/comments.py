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
from src.services import ScraperManager, TaskManager, TaskSuccess
from src.utils import parse_search_keyword, sample_comments_evenly, record_play_history, handle_danmaku_likes, strip_danmaku_likes, is_movie_by_title
from src.utils import restyle_danmaku_likes
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
from src.api.control.dependencies import get_title_recognition_manager

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
from .danmaku_mode import convert_danmaku_position
from .danmaku_filter import apply_blacklist_filter

logger = logging.getLogger(__name__)

# 创建评论路由器
comments_router = APIRouter(route_class=DandanApiRoute)

# ============ 请求合并（Request Coalescing）============
# 同一个 episodeId 同一时间只允许一个刷新/下载任务，
# 其他并发请求（无论来自哪个 token）都等同一个 Event。
_episode_inflight: dict[int, asyncio.Event] = {}
_episode_inflight_lock = asyncio.Lock()


async def _coalesce_or_own(episode_id: int) -> tuple[bool, asyncio.Event]:
    """
    尝试获取指定 episodeId 的处理权。

    Returns:
        (is_owner, event)
        - is_owner=True:  你是第一个请求，负责实际执行任务并在完成后 set() event
        - is_owner=False: 已有请求在处理，你只需 await event.wait()
    """
    async with _episode_inflight_lock:
        if episode_id in _episode_inflight:
            return False, _episode_inflight[episode_id]
        event = asyncio.Event()
        _episode_inflight[episode_id] = event
        return True, event


async def _release_coalesce(episode_id: int):
    """任务完成后释放 episodeId 的处理权并唤醒所有等待者。"""
    async with _episode_inflight_lock:
        event = _episode_inflight.pop(episode_id, None)
    if event:
        event.set()


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
    cached_comments = await get_db_cache(session, "", cache_key)
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
            likes_enabled = (await config_manager.get('danmakuLikesOutputEnabled', 'true')).lower() == 'true'
            likes_style = await config_manager.get('danmakuLikesStyle', 'heart_white')
            # likes_style='off' 等价于 enabled=False
            comments_data = handle_danmaku_likes(
                comments_data, scraper.likes_fire_threshold,
                enabled=likes_enabled and likes_style != 'off',
                style=likes_style
            )

            # 修正：使用 scraper.provider_name 修复未定义的 'provider' 变量
            if not comments_data: logger.warning(f"未能从 {scraper.provider_name} URL 获取任何弹幕: {url}")

        except Exception as e:
            logger.error(f"处理 {scraper.provider_name} 外部弹幕时出错: {e}", exc_info=True)
            raise HTTPException(status_code=500, detail=f"获取 {scraper.provider_name} 弹幕失败。")

        # 缓存结果5小时 (18000秒)
        await set_db_cache(session, "", cache_key, comments_data, 18000)

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
    response_model_exclude_none=True,
    summary="[dandanplay兼容] 获取弹幕"
)
async def get_comments_for_dandan(
    request: Request,
    episodeId: int = Path(..., description="分集ID (来自 /search/episodes 响应中的 episodeId)"),
    chConvert: int = Query(0, description="中文简繁转换。0-不转换，1-转换为简体，2-转换为繁体。"),
    # 'from' 是 Python 的关键字，所以我们必须使用别名
    fromTime: int = Query(0, alias="from", description="弹幕开始时间(秒)"),
    withRelated: bool = Query(True, description="是否包含关联弹幕"),
    async_mode: bool = Query(False, alias="async", description="异步模式：传入1的时候，在超时响应的情况下返回taskid"),
    token: str = Depends(get_token_from_path),
    session: AsyncSession = Depends(get_db_session),
    config_manager: ConfigManager = Depends(get_config_manager),
    scraper_manager: ScraperManager = Depends(get_scraper_manager),
    task_manager: TaskManager = Depends(get_task_manager),
    rate_limiter: RateLimiter = Depends(get_rate_limiter),
    title_recognition_manager = Depends(get_title_recognition_manager),
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

    # 2. 弹幕过期自动刷新检测
    if comments_data:
        try:
            auto_refresh_days_str = await config_manager.get("danmakuAutoRefreshDays", "0")
            auto_refresh_days = int(auto_refresh_days_str)
        except (ValueError, TypeError):
            auto_refresh_days = 0

        # 弹幕条数阈值：仅当现有弹幕条数低于此值时才刷新，避免对已抓全的弹幕重复重抓。0 表示不限制条数。
        try:
            refresh_threshold = int(await config_manager.get("danmakuRefreshThreshold", "5000"))
        except (ValueError, TypeError):
            refresh_threshold = 5000

        if auto_refresh_days > 0:
            # 条数阈值过滤：当前弹幕已达到/超过阈值则跳过刷新
            current_count = len(comments_data)
            if refresh_threshold > 0 and current_count >= refresh_threshold:
                logger.debug(f"[自动刷新] episodeId={episodeId} 现有弹幕 {current_count} 条已达阈值（{refresh_threshold}），跳过自动刷新")
            else:
                fetched_at = await crud.get_episode_fetched_at(session, episodeId)
                if fetched_at is not None:
                    now = get_now()
                    age_days = (now - fetched_at).total_seconds() / 86400
                    if age_days >= auto_refresh_days:
                        unique_key = f"refresh-episode-{episodeId}"
                        # 检查是否已有刷新任务在跑（避免重复提交）
                        already_running = False
                        async with task_manager._lock:
                            already_running = unique_key in task_manager._active_unique_keys

                        if not already_running:
                            logger.info(f"[自动刷新] episodeId={episodeId} 弹幕已 {age_days:.1f} 天未更新（阈值={auto_refresh_days}天），触发自动刷新")
                            try:
                                _ep_id = episodeId
                                _scraper = scraper_manager
                                _rl = rate_limiter
                                _cfg = config_manager
                                await task_manager.submit_task(
                                    lambda s, cb, _eid=_ep_id, _sm=_scraper, _r=_rl, _c=_cfg: tasks.refresh_episode_task(
                                        _eid, s, _sm, _r, cb, _c
                                    ),
                                    f"自动刷新弹幕: episodeId={episodeId}",
                                    unique_key=unique_key,
                                    run_immediately=True
                                )
                            except Exception as e:
                                logger.warning(f"[自动刷新] 提交刷新任务失败: {e}")

                        # 等待刷新任务完成（最多30秒），完成后重取弹幕
                        refreshed = await wait_for_refresh_task(episodeId, task_manager, max_wait_seconds=30.0)
                        if refreshed:
                            comments_data = await crud.fetch_comments(session, episodeId)
                            logger.info(f"[自动刷新] episodeId={episodeId} 刷新完成，重新获取弹幕 {len(comments_data)} 条")

    # 预下载下一集弹幕 (异步,不阻塞当前响应)
    # 只有当前集已存在于数据库时才触发预下载（后备场景会在任务完成后单独触发）
    if comments_data:
        predownload_task = asyncio.create_task(try_predownload_next_episode(
            episodeId, request.app.state.db_session_factory, config_manager, task_manager,
            scraper_manager, rate_limiter, title_recognition_manager
        ))

        # 添加异常处理回调
        def handle_predownload_exception(task):
            try:
                task.result()  # 如果任务有异常，这里会抛出
            except Exception as e:
                logger.error(f"预下载任务异常 (episodeId={episodeId}): {e}", exc_info=True)

        predownload_task.add_done_callback(handle_predownload_exception)

    if not comments_data:
        # ── 请求合并：同一 episodeId 只允许一个请求执行下载/刷新 ──
        is_owner, coalesce_event = await _coalesce_or_own(episodeId)
        if not is_owner:
            # 已有请求在处理这个 episodeId，等它完成后直接从 DB 读取
            logger.info(f"[请求合并] episodeId={episodeId} 已有下载任务在执行，等待结果...")
            try:
                await asyncio.wait_for(coalesce_event.wait(), timeout=60.0)
            except asyncio.TimeoutError:
                logger.warning(f"[请求合并] episodeId={episodeId} 等待超时（60秒）")
            comments_data = await crud.fetch_comments(session, episodeId)
            if comments_data:
                logger.info(f"[请求合并] episodeId={episodeId} 从数据库读取到 {len(comments_data)} 条弹幕")
            else:
                logger.warning(f"[请求合并] episodeId={episodeId} 等待完成但仍无弹幕数据")
                return models.CommentResponse(count=0, comments=[])
            # 非 owner：数据已拿到，直接跳到输出处理（不进入下载逻辑）

    if not comments_data:
        # owner 路径：执行实际下载任务
        logger.info(f"弹幕库中未找到 episodeId={episodeId} 的弹幕，尝试直接从源站获取")

        # 检查是否是后备搜索/匹配后备的episodeId
        # 缓存key格式: fallback_episode_25000166010000 (最后4位为0000表示整部剧)

        fallback_info = None
        match_fallback_handled = False
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
            # 注意：整部剧缓存存储时无前缀，查询同样无前缀
            fallback_info = await get_db_cache(session, "", fallback_series_key)
            logger.debug(f"查找缓存: {fallback_series_key}, 找到: {fallback_info is not None}")

        # 如果数据库缓存中没有,再从数据库缓存中查找(使用新的前缀)
        if not fallback_info:
            fallback_episode_cache_key = f"fallback_episode_{episodeId}"
            fallback_info = await get_db_cache(session, FALLBACK_SEARCH_CACHE_PREFIX, fallback_episode_cache_key)
            if fallback_info:
                episode_number = fallback_info.get("episode_number")

        # DB 兜底：缓存全部 miss 但 episodeId 编码的 (animeId, sourceOrder) 在库中真实存在时，
        # 直接从 Anime + AnimeSource 反查 provider/mediaId 重建后备信息。
        # why：缓存(fallback_episode_*)会被清理或过期，而 episodeId 本身编码了 animeId+sourceOrder，
        # 只要作品与源仍在库中（如仅删了弹幕文件/清了缓存），就应能重新触发后备下载，
        # 而非卡在"尝试直接从源站获取"无任何后续动作。
        if not fallback_info and episodeId >= 25000000000000:
            try:
                db_anime = (await session.execute(
                    select(Anime).where(Anime.id == anime_id_part)
                )).scalar_one_or_none()
                db_src = (await session.execute(
                    select(AnimeSource.providerName, AnimeSource.mediaId)
                    .where(
                        AnimeSource.animeId == anime_id_part,
                        AnimeSource.sourceOrder == source_order_part,
                    )
                    .limit(1)
                )).first()
                if db_anime and db_src:
                    _db_provider, _db_media_id = db_src
                    fallback_info = {
                        "real_anime_id": anime_id_part,
                        "provider": _db_provider,
                        "mediaId": _db_media_id,
                        "final_title": db_anime.title,
                        "original_title": db_anime.title,
                        "final_season": db_anime.season,
                        "media_type": db_anime.type,
                        "imageUrl": db_anime.imageUrl,
                        "year": db_anime.year,
                    }
                    logger.info(
                        f"[DB兜底] 缓存缺失，从数据库重建后备信息: anime_id={anime_id_part}, "
                        f"provider={_db_provider}, mediaId={_db_media_id}, 集号={episode_number}"
                    )
                else:
                    logger.debug(
                        f"[DB兜底] 未找到可重建的作品/源 (anime_id={anime_id_part}, "
                        f"source_order={source_order_part})，跳过 DB 兜底"
                    )
            except Exception as e:
                logger.warning(f"[DB兜底] 从数据库重建后备信息失败: {e}")

        if fallback_info:
            # why：本请求已由匹配后备链路接管，后续不得再次落入 fallback_comments 第二套下载链路。
            match_fallback_handled = True
            logger.info(f"检测到后备搜索/匹配后备的episodeId: {episodeId}, 集数: {episode_number}")

            # 从缓存中获取信息
            real_anime_id = fallback_info["real_anime_id"]
            provider = fallback_info["provider"]
            mediaId = fallback_info["mediaId"]
            final_title = fallback_info["final_title"]
            # 创建条目用原始标题（如"碧蓝之海 第二季"），匹配查询用 final_title
            display_title = fallback_info.get("original_title") or final_title
            final_season = fallback_info["final_season"]
            media_type = fallback_info["media_type"]
            imageUrl = fallback_info.get("imageUrl")
            year = fallback_info.get("year")

            # 步骤1：获取分集信息（先验证能拿到数据，再创建数据库条目）
            logger.info(f"开始获取分集信息: provider={provider}, mediaId={mediaId}, episode_number={episode_number}")

            # 获取scraper（后续弹幕下载任务需要）
            scraper = scraper_manager.get_scraper(provider)

            # 获取分集列表（自动路由补充源 mediaId）
            try:
                episodes_list = await scraper_manager.get_episodes_routed(provider, mediaId, db_media_type=media_type)
                if not episodes_list:
                    logger.error(f"无法获取分集列表，跳过创建数据库条目")
                    await _release_coalesce(episodeId)
                    return models.CommentResponse(count=0, comments=[])

                # 按 episodeIndex 精确查找目标分集（不能用位置索引，因为可能缺集）
                target_episode = None
                for ep in episodes_list:
                    if ep.episodeIndex == episode_number:
                        target_episode = ep
                        break

                if not target_episode:
                    logger.error(f"分集列表中未找到第{episode_number}集（共{len(episodes_list)}条记录），跳过创建数据库条目")
                    await _release_coalesce(episodeId)
                    return models.CommentResponse(count=0, comments=[])
                provider_episode_id = target_episode.episodeId
                episode_title = target_episode.title
                episode_url = target_episode.url

                logger.info(f"获取到分集信息: title='{episode_title}', provider_episode_id='{provider_episode_id}'")

            except Exception as e:
                logger.error(f"获取分集信息失败: {e}", exc_info=True)
                await _release_coalesce(episodeId)
                return models.CommentResponse(count=0, comments=[])

            # 步骤2：分集获取成功，创建或获取anime条目
            stmt = select(Anime).where(Anime.id == real_anime_id)
            result = await session.execute(stmt)
            existing_anime = result.scalar_one_or_none()

            if not existing_anime:
                # 创建anime条目（使用原始标题展示，如"碧蓝之海 第二季"）
                logger.info(f"创建anime条目: id={real_anime_id}, title='{display_title}'")
                new_anime = Anime(
                    id=real_anime_id,
                    title=display_title,
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

            # 步骤3：创建或获取source关联
            source_id = await crud.link_source_to_anime(session, real_anime_id, provider, mediaId)
            logger.info(f"source_id={source_id}")

            # 提交anime和source创建，避免与后台任务产生锁冲突
            await session.commit()
            logger.info(f"已提交anime和source创建")

            # 步骤4：下载弹幕 (使用task_manager提交到后备队列)
            logger.info(f"开始下载弹幕: provider_episode_id={provider_episode_id}")

            # 检查是否已有相同的弹幕下载任务正在进行
            task_unique_key = f"match_fallback_comments_{episodeId}"
            recent_task = await crud.find_recent_task_by_unique_key(session, task_unique_key, 1)
            # why：历史查询也会返回最近完成的任务；只有活跃状态才应阻止重新提交。
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
                        comments_data = cached_comments
                        logger.info(f"从缓存中获取到弹幕数据，共 {len(cached_comments)} 条")
                        break
                if not comments_data:
                    # why：已有匹配后备任务仍在运行时，当前请求不能继续提交 fallback_comments，
                    # 否则同一 episodeId 会同时沿用旧 episode_mapping 再跑一套下载。
                    await _release_coalesce(episodeId)
                    if async_mode:
                        return models.CommentResponse(
                            count=0, comments=[], status="pending", taskId=existing_task.taskId,
                        )
                    return models.CommentResponse(count=0, comments=[])
                # 已取得缓存结果，跳过任务提交并进入统一输出处理。
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
                current_display_title = display_title
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
                        # 如果 provider_episode_id 是 URL 格式，用基类通用方法解析
                        actual_episode_id = current_provider_episode_id
                        if actual_episode_id and actual_episode_id.startswith("http"):
                            try:
                                parsed_id = await current_scraper.get_id_from_url(actual_episode_id)
                                if parsed_id:
                                    actual_episode_id = current_scraper.format_episode_id_for_comments(parsed_id)
                                    logger.info(f"URL 已解析为 episode_id: {actual_episode_id}")
                            except Exception as e:
                                logger.warning(f"URL 解析失败，尝试直接使用: {e}")

                        comments = await current_scraper.get_comments(actual_episode_id, progress_callback=progress_callback)
                        if not comments:
                            logger.warning(f"下载失败，未获取到弹幕")
                            raise TaskSuccess("未获取到弹幕，源站可能暂时不可用")

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
                            # 创建anime条目（使用原始标题展示）
                            logger.info(f"任务中创建anime条目: id={current_real_anime_id}, title='{current_display_title}'")
                            new_anime = Anime(
                                id=current_real_anime_id,
                                title=current_display_title,
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
                                "original_title": current_display_title,
                                "final_season": current_final_season,
                                "media_type": current_media_type,
                                "imageUrl": current_imageUrl,
                                "year": current_year,
                                "total_episodes": len(current_episodes_list)
                            }

                            # 存储到缓存,3小时过期
                            await set_db_cache(task_session, "", fallback_series_key, cache_value, 10800)
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

                        # 写入 match_season 整季缓存，让后续 /match 请求能直接命中（避免重新搜索）
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
                        return comments

                    except TaskSuccess:
                        raise  # TaskSuccess 直接穿透到 task_manager
                    except Exception as e:
                        logger.error(f"匹配后备弹幕下载任务执行失败: {e}", exc_info=True)
                        await task_session.rollback()
                        raise  # 让异常传播到 task_manager，标记任务为失败

                # 提交弹幕下载任务到后备队列
                try:
                    # 结构化通知参数：供完成通知渲染「作品名/季集/弹幕源/海报」结构块
                    _mf_params = {
                        "anime_title": current_display_title or current_final_title or "",
                        "season": current_final_season,
                        "episode": current_episode_number,
                        "provider": current_provider,
                        "imageUrl": current_imageUrl or "",
                        # 有具体集数即视为剧集；仅当无集数且类型为 movie 时才按电影展示
                        "is_movie": (current_episode_number is None and current_media_type == "movie"),
                        "media_type": current_media_type,
                    }
                    task_id, done_event = await task_manager.submit_task(
                        download_match_fallback_comments_task,
                        f"匹配后备弹幕下载: {final_title} 第{episode_number}集 [{provider}:{mediaId}]",
                        unique_key=task_unique_key,
                        task_type="download_comments",
                        queue_type="fallback",  # 使用后备队列
                        task_parameters=_mf_params
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
                        # async_mode：超时后返回 taskId 让客户端轮询
                        if async_mode:
                            await _release_coalesce(episodeId)
                            return models.CommentResponse(
                                count=0, comments=[],
                                status="pending", taskId=task_id,
                            )
                        # 同步模式：超时后返回空结果
                        await _release_coalesce(episodeId)
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
                        await _release_coalesce(episodeId)
                        return models.CommentResponse(count=0, comments=[])
                except Exception as e:
                    logger.error(f"提交匹配后备弹幕下载任务失败: {e}", exc_info=True)
                    await session.rollback()
                    await _release_coalesce(episodeId)
                    return models.CommentResponse(count=0, comments=[])

        # 任务完成后,弹幕已经保存到数据库,不再从缓存读取
        # 2. 检查是否是后备搜索的特殊episodeId（以25开头的新格式）
        if not match_fallback_handled and str(episodeId).startswith("25") and len(str(episodeId)) >= 13:  # 新的ID格式
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
                logger.info(f"从缓存获取episodeId映射: episodeId={episodeId}, provider={provider}, mediaId={episode_url}")
            else:
                # 如果缓存中没有，从数据库缓存中查找
                # 首先尝试根据用户最后的选择来确定源
                try:
                    all_cache_keys = await get_cache_keys(session, f"{FALLBACK_SEARCH_CACHE_PREFIX}*")
                    for cache_key in all_cache_keys:
                        search_key = cache_key.replace(FALLBACK_SEARCH_CACHE_PREFIX, "")
                        search_info = await get_db_cache(session, FALLBACK_SEARCH_CACHE_PREFIX, search_key)
                        if not isinstance(search_info, dict):
                            continue

                        if search_info.get("status") == "completed" and "bangumi_mapping" in search_info:
                            # 检查是否有用户最后的选择记录
                            last_bangumi_id = await get_db_cache(session, USER_LAST_BANGUMI_CHOICE_PREFIX, search_key)
                            if last_bangumi_id and last_bangumi_id in search_info["bangumi_mapping"]:
                                mapping_info = search_info["bangumi_mapping"][last_bangumi_id]
                                # 检查真实animeId是否匹配
                                if isinstance(mapping_info, dict) and mapping_info.get("real_anime_id") == real_anime_id:
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
                            if not isinstance(search_info_fallback, dict):
                                continue

                            if search_info_fallback.get("status") == "completed" and "bangumi_mapping" in search_info_fallback:
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
                logger.info(f"找到后备搜索映射: provider={provider}, mediaId={episode_url}")

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
                    # 【修复】在外层确定映射信息后直接传入任务，避免任务内二次查缓存时
                    # 因并发导致命中另一部剧的 mapping（标题串台 bug）
                    # mapping_data 来自 episode_mapping 缓存（按 episodeId 一对一，不会被串扰）
                    # mapping_info 来自 fallback_search 缓存（可能被并发覆盖，但此刻外层刚查到的是正确的）
                    # 优先使用 mapping_data，其次用外层刚查到的 mapping_info
                    current_mapping_info = None
                    if mapping_data and isinstance(mapping_data, dict):
                        current_mapping_info = mapping_data
                    else:
                        # mapping_info 可能在 816-824 行的分支中被赋值
                        try:
                            if mapping_info and isinstance(mapping_info, dict):
                                current_mapping_info = mapping_info
                        except NameError:
                            pass

                    async def download_comments_task(task_session, progress_callback):
                        try:
                            await progress_callback(10, "开始获取弹幕...")
                            scraper = current_scraper_manager.get_scraper(current_provider)
                            if scraper:
                                # 首先获取分集列表
                                await progress_callback(30, "获取分集列表...")
                                # 【修复】直接使用外层已确认的映射信息，不再二次遍历缓存
                                # 避免并发窗口期内缓存被其他搜索覆盖导致标题串台
                                mapping_info = current_mapping_info
                                if not mapping_info:
                                    # 兜底：如果外层没拿到，再去缓存查（保持向后兼容）
                                    try:
                                        all_cache_keys_mapping = await get_cache_keys(task_session, f"{FALLBACK_SEARCH_CACHE_PREFIX}*")
                                        for cache_key_mapping in all_cache_keys_mapping:
                                            search_key = cache_key_mapping.replace(FALLBACK_SEARCH_CACHE_PREFIX, "")
                                            last_bangumi_id = await get_db_cache(task_session, USER_LAST_BANGUMI_CHOICE_PREFIX, search_key)
                                            if last_bangumi_id:
                                                search_info = await get_db_cache(task_session, FALLBACK_SEARCH_CACHE_PREFIX, search_key)
                                                if not isinstance(search_info, dict):
                                                    continue
                                                if last_bangumi_id in search_info.get("bangumi_mapping", {}):
                                                    temp_mapping = search_info["bangumi_mapping"][last_bangumi_id]
                                                    if temp_mapping.get("real_anime_id") == real_anime_id:
                                                        mapping_info = temp_mapping
                                                        logger.info(f"找到匹配的映射信息(兜底): search_key={search_key}, bangumiId={last_bangumi_id}, real_anime_id={real_anime_id}")
                                                        break
                                    except Exception as e:
                                        logger.error(f"查找映射信息失败: {e}")
                                else:
                                    logger.info(f"使用外层已确认的映射信息: provider={mapping_info.get('provider')}, original_title={mapping_info.get('original_title')}, real_anime_id={real_anime_id}")

                                if not mapping_info:
                                    logger.error(f"无法找到real_anime_id={real_anime_id}的映射信息")
                                    return None

                                media_type = mapping_info.get("type", "movie")
                                episodes_list = await scraper.get_episodes(current_episode_url, db_media_type=media_type)
                                # 保存到外层作用域，用于后续批量创建Episode记录
                                nonlocal current_episodes_list_ref
                                current_episodes_list_ref = episodes_list

                            if episodes_list:
                                # 按 episodeIndex 精确查找目标分集（不能用位置索引，因为可能缺集）
                                target_episode = None
                                for ep in episodes_list:
                                    if ep.episodeIndex == current_episode_number:
                                        target_episode = ep
                                        break

                                if target_episode:
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

                                        # 并发下载前显式检查流控，RuntimeError（配置校验失败）直接上抛
                                        # _download_episode_comments_concurrent 内部会吞掉所有异常，
                                        # 需要在外层提前捕获致命错误，确保任务能被正确标记为 FAILED
                                        try:
                                            await current_rate_limiter.check_fallback("search", current_provider)
                                        except RuntimeError:
                                            raise  # 配置校验失败，直接向上抛，task_manager 标为 FAILED
                                        except Exception:
                                            pass  # 普通流控超限交给内部处理

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
                                    logger.warning(f"从 {current_provider} 分集列表中未找到第{current_episode_number}集: media_id={current_episode_url}, 共{len(episodes_list)}条记录")
                                    raw_comments_data = None
                            else:
                                logger.warning(f"从 {current_provider} 获取分集列表失败: media_id={current_episode_url}, episode_number={current_episode_number}")
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
                                                    if not isinstance(search_info, dict):
                                                        continue
                                                    if last_bangumi_id in search_info.get("bangumi_mapping", {}):
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

                                        # 直接使用源返回的原始标题（保留季度后缀，如"碧蓝之海 第二季"）
                                        search_term = search_keyword or original_title
                                        parsed_info = parse_search_keyword(search_term)
                                        base_title = original_title
                                        # 季度获取：① 映射数据自带 > ② 搜索词解析 > ③ 默认 1
                                        effective_season = mapping_data.get("season") if mapping_data else None
                                        if effective_season is None:
                                            effective_season = parsed_info.get("season") or 1
                                        # 电影/剧场版不使用季度概念（统一复用 is_movie_by_title 工具）
                                        is_movie_type = media_type == "movie" or is_movie_by_title(base_title)

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
                                            # 电影/剧场版季度存 1（数据库约束），非电影用 effective_season
                                            anime_season = 1 if is_movie_type else effective_season
                                            new_anime = Anime(
                                                id=real_anime_id,
                                                title=base_title,
                                                type=media_type,
                                                season=anime_season,
                                                year=year,
                                                imageUrl=image_url,
                                                createdAt=get_now()
                                            )
                                            task_session.add(new_anime)
                                            await task_session.flush()  # 确保ID可用
                                            anime_id = real_anime_id
                                            if is_movie_type:
                                                logger.info(f"创建新番剧: ID={anime_id}, 标题='{base_title}', 年份={year}")
                                            else:
                                                logger.info(f"创建新番剧: ID={anime_id}, 标题='{base_title}', 季度={anime_season}, 年份={year}")

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
                                                    "final_title": parsed_info["title"],
                                                    "original_title": base_title,
                                                    "final_season": 1 if is_movie_type else effective_season,
                                                    "media_type": media_type,
                                                    "imageUrl": image_url,
                                                    "year": year,
                                                    "total_episodes": len(current_episodes_list_ref)
                                                }

                                                # 存储到缓存,3小时过期
                                                await set_db_cache(task_session, "", fallback_series_key, cache_value, 10800)
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
                                                if not isinstance(search_info, dict):
                                                    continue

                                                if search_info.get("status") == "completed" and "bangumi_mapping" in search_info:
                                                    for bangumi_id, mapping_info in list(search_info["bangumi_mapping"].items()):
                                                        if mapping_info.get("real_anime_id") == real_anime_id:
                                                            # 从映射中移除这个条目
                                                            del search_info["bangumi_mapping"][bangumi_id]
                                                            logger.info(f"清除缓存映射: search_key={search_key}, bangumiId={bangumi_id}, real_anime_id={real_anime_id}")
                                                    # 保存更新后的缓存
                                                    await set_db_cache(task_session, FALLBACK_SEARCH_CACHE_PREFIX, search_key, search_info, FALLBACK_SEARCH_CACHE_TTL)
                                        except Exception as e:
                                            logger.error(f"清除缓存映射失败: {e}")

                                        # 写入 match_season 整季缓存，让后续 /match 请求能直接命中
                                        if not is_movie_type:
                                            try:
                                                _parsed_sc = parse_search_keyword(base_title)
                                                season_cache_key = f"match_season_{_parsed_sc['title']}_{effective_season}"
                                                season_cache_data = {
                                                    "provider": current_provider,
                                                    "mediaId": current_episode_url,
                                                    "real_anime_id": anime_id,
                                                    "virtual_anime_id": 900000,
                                                    "final_title": _parsed_sc["title"],
                                                    "original_title": base_title,
                                                    "final_season": effective_season,
                                                    "source_order": source_order,
                                                    "media_type": media_type,
                                                    "imageUrl": image_url,
                                                    "year": year,
                                                    "timestamp": time.time()
                                                }
                                                await set_db_cache(task_session, FALLBACK_SEARCH_CACHE_PREFIX, season_cache_key, season_cache_data, 3600)
                                                logger.info(f"整季缓存已存储（后备搜索路径）: {season_cache_key}")
                                            except Exception as e:
                                                logger.warning(f"写入整季缓存失败: {e}")

                                    except Exception as db_error:
                                        logger.error(f"创建数据库条目失败: {db_error}", exc_info=True)
                                        await task_session.rollback()

                                    # 不再写入缓存,弹幕已经保存到数据库和XML文件
                                    # 外部会话会从数据库读取episode记录和弹幕文件
                                    logger.info(f"弹幕已保存到数据库和文件,任务完成")
                                    return raw_comments_data
                            else:
                                logger.warning(f"获取弹幕失败")
                                raise TaskSuccess("获取弹幕失败，源站未返回数据")
                        except Exception as e:
                            logger.error(f"弹幕下载任务执行失败: {e}", exc_info=True)
                            raise  # 让异常传播到 task_manager，标记任务为失败

                    # 提交弹幕下载任务
                    try:
                        # 结构化通知参数：从外层已确认的映射信息提取，供完成通知渲染
                        # 「作品名/季集/弹幕源/海报」结构块（与预下载通知统一）。
                        _mi = current_mapping_info or {}
                        _dl_media_type = _mi.get("type", "")
                        _dl_params = {
                            "anime_title": _mi.get("original_title") or _mi.get("final_title") or "",
                            "season": _mi.get("season") if _mi.get("season") is not None else _mi.get("final_season"),
                            "episode": episode_number,
                            "provider": current_provider,
                            "imageUrl": _mi.get("imageUrl") or "",
                            # 有具体集数即视为剧集；仅当无集数且类型为 movie 时才按电影展示
                            "is_movie": (episode_number is None and _dl_media_type == "movie"),
                            "media_type": _dl_media_type,
                        }
                        task_id, done_event = await task_manager.submit_task(
                            download_comments_task,
                            f"后备搜索弹幕下载: episodeId={episodeId}",
                            unique_key=task_unique_key,
                            task_type="download_comments",
                            queue_type="fallback",  # 使用后备队列
                            task_parameters=_dl_params
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
                            # async_mode：超时后返回 taskId 让客户端轮询
                            if async_mode:
                                await _release_coalesce(episodeId)
                                return models.CommentResponse(
                                    count=0, comments=[],
                                    status="pending", taskId=task_id,
                                )
                            # 同步模式：超时后继续后续逻辑（返回空结果）

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
            await _release_coalesce(episodeId)
            return models.CommentResponse(count=0, comments=[])

        # ── owner 下载完毕，释放 coalesce 锁让等待者继续 ──
        await _release_coalesce(episodeId)

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

    try:
        likes_output_enabled = (await config_manager.get('danmakuLikesOutputEnabled', 'true')).lower() == 'true'
        if not likes_output_enabled and comments_data:
            comments_data = strip_danmaku_likes(comments_data)
        elif likes_output_enabled and comments_data:
            likes_style = await config_manager.get('danmakuLikesStyle', 'heart_white')
            if likes_style == 'off':
                comments_data = strip_danmaku_likes(comments_data)
            else:
                comments_data = restyle_danmaku_likes(comments_data, style=likes_style)
    except Exception as e:
        logger.error(f"应用点赞状态过滤失败: {e}", exc_info=True)

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

    # 弹幕位置转换：按配置把顶部(5)/底部(4)弹幕转为其他类型（仅输出时转换，基于原始 mode 一次性映射）
    try:
        top_to = await config_manager.get('danmakuTopConvertTo', 'none')
        bottom_to = await config_manager.get('danmakuBottomConvertTo', 'none')
        if comments_data and (top_to != 'none' or bottom_to != 'none'):
            comments_data = convert_danmaku_position(comments_data, top_to=top_to, bottom_to=bottom_to)
    except Exception as e:
        logger.error(f"应用弹幕位置转换失败: {e}", exc_info=True)

    # UA 已由 get_token_from_path 依赖项记录
    logger.debug(f"弹幕接口响应 (episodeId: {episodeId}): 总计 {len(comments_data)} 条弹幕")

    # 记录播放历史（用于 @SXDM 指令）
    try:
        await record_play_history(session, token, episodeId)
    except Exception as e:
        logger.error(f"记录播放历史失败: episodeId={episodeId}, error={e}", exc_info=True)

    # 修正：使用统一的弹幕处理函数，以确保输出格式符合 dandanplay 客户端规范
    processed_comments = process_comments_for_dandanplay(comments_data)

    return models.CommentResponse(
        count=len(processed_comments),
        comments=processed_comments,
    )