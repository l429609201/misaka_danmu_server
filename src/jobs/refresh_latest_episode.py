"""
刷新最新集弹幕定时任务
自动检测已启用追更的作品,对最新一集弹幕数未达到阈值的进行刷新
"""
from src.services.service_container import get_database_service
import logging
from typing import Callable
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, update
from datetime import datetime, timedelta

from src.db import orm_models
from .base import BaseJob
from src.utils.diagnostics.task_exceptions import TaskSuccess
from src.tasks import refresh_episode_task
from src.core import get_now
from src.services.task_profiler import profile_flow, FLOW_REFRESH_LATEST_EPISODE


class RefreshLatestEpisodeJob(BaseJob):
    """刷新最新集弹幕定时任务"""

    job_type = "refreshLatestEpisode"
    job_name = "刷新最新集弹幕"
    job_name_en = "Refresh Latest Episode Danmaku"
    job_name_tw = "重新整理最新集彈幕"
    description = "自动检测已启用追更的作品,对最新一集弹幕数未达到阈值的进行定时刷新。适用于正在连载的动画/电视剧。"
    description_en = "Auto-detect tracked works and refresh danmaku for the latest episode when count is below threshold. For ongoing anime/TV series."
    description_tw = "自動偵測已啟用追更的作品，對最新一集彈幕數未達到閾值的進行定時重新整理。適用於正在連載的動畫/電視劇。"
    config_schema = [
        {
            "key": "commentThreshold",
            "label": "弹幕数阈值",
            "label_en": "Danmaku Count Threshold",
            "label_tw": "彈幕數閾值",
            "type": "number",
            "default": 20000,
            "min": 0,
            "max": 1000000,
            "suffix": "条",
            "suffix_en": "comments",
            "suffix_tw": "條",
            "description": "最新一集弹幕数低于此值时才会刷新。留空或 0 则使用全局配置（latestEpisodeCommentThreshold，默认 20000）。",
            "description_en": "Refresh the latest episode only when its danmaku count is below this value. Set to 0 to use the global config (latestEpisodeCommentThreshold, default 20000).",
            "description_tw": "最新一集彈幕數低於此值時才會重新整理。留空或 0 則使用全域配置（latestEpisodeCommentThreshold，預設 20000）。"
        },
    ]

    @profile_flow(FLOW_REFRESH_LATEST_EPISODE)
    async def run(self, session: AsyncSession, progress_callback: Callable, task_config: dict = None):
        """定时任务的核心逻辑: 刷新最新一集的弹幕"""
        if task_config is None:
            task_config = {}

        # 任务级阈值优先；为 0 或未设置时回退到全局配置 latestEpisodeCommentThreshold
        configured_threshold = task_config.get("commentThreshold")
        task_threshold = None
        if configured_threshold not in (None, "", 0, "0"):
            try:
                task_threshold = int(configured_threshold)
            except (ValueError, TypeError):
                task_threshold = None
                self.logger.warning(f"无法解析任务级弹幕阈值配置: {configured_threshold!r}，将回退到全局配置")

        await progress_callback(0, "正在获取所有启用追更的源...")

        # 获取所有启用追更的源
        db = get_database_service()
        # 列表读取使用短事务，不在任务调度期间持有该事务。
        async with db.transaction():
            source_ids = await db.source.get_sources_with_incremental_refresh_enabled()
        total_sources = len(source_ids)

        if not total_sources:
            raise TaskSuccess("没有找到任何启用追更的源，任务结束。")

        self.logger.info(f"刷新最新集弹幕：找到 {total_sources} 个源")
        await progress_callback(10, f"找到 {total_sources} 个源，正在检查...")

        refreshed_count = 0
        skipped_count = 0

        for i, source_id in enumerate(source_ids):
            try:
                # 查询方法统一由 source 数据域暴露，并显式绑定短事务。
                async with db.transaction():
                    source_info = await db.source.get_anime_source_info(source_id)
                if not source_info:
                    self.logger.warning(f"无法找到数据源(id={source_id})的信息，跳过。")
                    skipped_count += 1
                    continue

                # 查询该源的最新一集
                stmt = (
                    select(orm_models.Episode)
                    .where(orm_models.Episode.sourceId == source_id)
                    .order_by(orm_models.Episode.episodeIndex.desc())
                    .limit(1)
                )
                result = await session.execute(stmt)
                latest_episode = result.scalar_one_or_none()

                if not latest_episode:
                    self.logger.info(f"源 '{source_info['title']}' (ID: {source_id}) 没有任何分集，跳过。")
                    skipped_count += 1
                    continue

                # 阈值优先使用任务级配置；未配置时回退到全局配置
                if task_threshold is not None:
                    threshold = task_threshold
                else:
                    # 配置读取也需要独立的 DatabaseService 事务上下文。
                    async with db.transaction():
                        threshold_str = await db.config.get_value("latestEpisodeCommentThreshold", "20000")
                    try:
                        threshold = int(threshold_str)
                    except (ValueError, TypeError):
                        threshold = 20000
                        self.logger.warning(f"无法解析弹幕阈值配置,使用默认值: {threshold}")

                # 检查弹幕数是否低于阈值
                if latest_episode.commentCount >= threshold:
                    self.logger.info(
                        f"源 '{source_info['title']}' 第{latest_episode.episodeIndex}集 "
                        f"弹幕数({latest_episode.commentCount})已达到阈值({threshold})，跳过。"
                    )
                    skipped_count += 1
                    continue

                # 创建刷新任务
                task_title = f"刷新最新集: {source_info['title']} - 第{latest_episode.episodeIndex}集"
                unique_key = f"refresh-latest-{source_id}-ep{latest_episode.episodeIndex}"

                def create_refresh_task(ep_id, s_info):
                    return lambda s, cb: refresh_episode_task(
                        episodeId=ep_id,
                        session=s,
                        manager=self.scraper_manager,
                        rate_limiter=self.rate_limiter,
                        progress_callback=cb,
                        config_service=self.config_service
                    )

                try:
                    await self.task_manager.submit_task(
                        create_refresh_task(latest_episode.id, source_info),
                        task_title,
                        unique_key=unique_key
                    )
                    refreshed_count += 1

                    # 更新最后刷新时间
                    await session.execute(
                        update(orm_models.AnimeSource)
                        .where(orm_models.AnimeSource.id == source_id)
                        .values(lastRefreshLatestEpisodeAt=get_now())
                    )
                    await session.commit()

                    self.logger.info(
                        f"已为源 '{source_info['title']}' 第{latest_episode.episodeIndex}集 "
                        f"创建刷新任务 (当前弹幕数: {latest_episode.commentCount}/{threshold})"
                    )
                except Exception as e:
                    self.logger.error(f"为源 '{source_info['title']}' 创建刷新任务失败: {e}")
                    skipped_count += 1

            except Exception as e:
                self.logger.error(f"处理源 ID {source_id} 时发生错误: {e}", exc_info=True)
                skipped_count += 1

            # 更新进度
            progress = 10 + int(((i + 1) / total_sources) * 90)
            await progress_callback(progress, f"已处理 {i+1}/{total_sources} 个源")

        final_message = f"刷新最新集弹幕任务完成，共创建 {refreshed_count} 个刷新任务，跳过 {skipped_count} 个源。"
        raise TaskSuccess(final_message)

