"""
弹幕获取核心流程（完整版）

职责：
1. 从数据库读取弹幕
2. 自动刷新检测和触发
3. 预下载下一集
4. 输出处理（简繁转换、采样、播放历史）
5. 路由到后备流程（匹配后备、搜索后备）
"""

import asyncio
import logging
from datetime import timedelta
from typing import Any

from fastapi import Request

from src.core.timezone import get_app_timezone, get_now
from src.schemas.dandan import CommentResponse as DandanCommentsResponse
from src.services.service_container import (
    get_database_service,
    get_task_manager,
    get_scraper_manager,
    get_rate_limiter,
    get_title_recognition_manager,
)
from src.services.config_service import get_config_service
from src.services.danmaku_service import DanmakuService
from src.workflows.comments.helpers import (
    coalesce_or_own,
    release_coalesce,
)
# 复用现有预下载入口，避免调用已删除的 tasks.download 模块。
from src.workflows.danmaku.predownload_flow import (
    predownload_next_episode_flow,
    wait_for_refresh_task as wait_for_episode_refresh,
)
from src.workflows.comments.output_flow import apply_output_config
from src.utils.misc.play_history import record_play_history
from src import tasks

# 导入后备流程
from src.workflows.match.fallback_flow import handle_match_fallback_comments
from src.workflows.search.fallback_flow import handle_search_fallback_comments

logger = logging.getLogger(__name__)


async def get_comments_for_dandan(
    episodeId: int,
    token: str,
    request: Request,
    chConvert: int = 0,
    fromTime: int = 0,
    withRelated: bool = True,
    async_mode: bool = False
) -> DandanCommentsResponse:
    """获取弹幕，协调自动刷新、预下载、输出处理和后备流程。"""
    database_service = get_database_service()
    task_manager = get_task_manager()
    config_service = get_config_service()
    _ = (fromTime, withRelated)

    await wait_for_refresh_task(episodeId, task_manager, max_wait_seconds=15.0)
    async with database_service.transaction():
        session = database_service._session
        danmaku_service = DanmakuService(database_service)
        comments_data = await danmaku_service.fetch_comments(episodeId)
        if comments_data:
            try:
                episode = await database_service.episode.get_by_id(episodeId)
                if episode:
                    await check_and_trigger_refresh(episode, task_manager, config_service)
            except Exception as e:
                logger.warning(f"分集 {episodeId} 自动刷新检测失败: {e}")
            try:
                await trigger_predownload_next_episode(
                    episodeId, session, task_manager, config_service
                )
            except Exception as e:
                logger.warning(f"分集 {episodeId} 预下载触发失败: {e}")
            return await process_output(
                comments_data, episodeId, token, chConvert, session, config_service
            )

        am_i_owner, event = await coalesce_or_own(episodeId)
        if not am_i_owner:
            try:
                await asyncio.wait_for(event.wait(), timeout=30.0)
            except asyncio.TimeoutError:
                return DandanCommentsResponse(count=0, comments=[])
            comments_data = await danmaku_service.fetch_comments(episodeId)
            if comments_data:
                return await process_output(
                    comments_data, episodeId, token, chConvert, session, config_service
                )
            return DandanCommentsResponse(count=0, comments=[])

        try:
            # request 必须按独立参数传递，不能让 async_mode 占据其位置。
            result = await handle_match_fallback_comments(
                episodeId, token, session, request, async_mode, chConvert=chConvert
            )
            if result:
                return result
            result = await handle_search_fallback_comments(
                episodeId, token, session, async_mode, chConvert=chConvert
            )
            if result:
                return result
            return DandanCommentsResponse(count=0, comments=[])
        finally:
            await release_coalesce(episodeId)


async def wait_for_refresh_task(episodeId: int, task_manager, max_wait_seconds: float = 15.0) -> None:
    """按实际任务去重键等待刷新，避免将去重键误当成任务 UUID。"""
    await wait_for_episode_refresh(episodeId, task_manager, max_wait_seconds)


async def check_and_trigger_refresh(episode: Any, task_manager, config_service) -> None:
    """按获取时间和条数阈值触发自动刷新，默认关闭。"""
    refresh_days = int(await config_service.get("danmakuAutoRefreshDays", "0"))
    if refresh_days <= 0 or not episode.fetchedAt:
        return
    threshold = int(await config_service.get("danmakuRefreshThreshold", "5000"))
    if threshold > 0 and (episode.commentCount or 0) >= threshold:
        return
    # 数据库时间使用应用时区的无时区时间，避免与 UTC aware 时间直接比较。
    fetched_at = episode.fetchedAt
    if fetched_at.tzinfo is not None:
        fetched_at = fetched_at.astimezone(get_app_timezone()).replace(tzinfo=None)
    if fetched_at >= get_now() - timedelta(days=refresh_days):
        return
    episode_id = episode.id
    manager = get_scraper_manager()
    rate_limiter = get_rate_limiter()
    await task_manager.submit_task(
        lambda s, cb: tasks.refresh_episode_task(
            episode_id, s, manager, rate_limiter, cb, config_service
        ),
        f"自动刷新分集 {episode_id}",
        unique_key=f"refresh-episode-{episode_id}",
        task_type="refresh_episode",
        task_parameters={"episodeId": episode_id},
        queue_type="download",
    )


async def trigger_predownload_next_episode(episodeId: int, _session, task_manager, config_service) -> None:
    """复用预下载编排；保留会话位置参数兼容调用，内部由编排管理事务。"""
    await predownload_next_episode_flow(
        episodeId, config_service, task_manager,
        get_scraper_manager(), get_rate_limiter(), get_title_recognition_manager(),
    )


async def process_output(
    comments_data, episodeId, token, chConvert, session, config_service
) -> DandanCommentsResponse:
    """通过统一输出编排应用显示配置，然后记录播放历史。"""
    processed_comments = await apply_output_config(comments_data, config_service, chConvert)

    # 记录播放历史
    await record_play_history(session, token, episodeId)

    # 返回响应
    return DandanCommentsResponse(
        count=len(processed_comments),
        comments=processed_comments
    )
