"""
刷新最新集弹幕定时任务
自动检测已启用追更的作品，对最新一集弹幕数未达到阈值的进行刷新（"养弹幕"）。
支持全局扫描或绑定指定源，支持配置弹幕阈值和最近集数。
"""
import logging
from typing import Callable, Optional
from datetime import datetime
from sqlalchemy.ext.asyncio import AsyncSession

from .base import BaseJob
from src.services.service_container import get_database_service
from src.utils.diagnostics.task_exceptions import TaskSuccess
from src.tasks import refresh_episode_task
from src.services.performance_service import profile_flow
from src.schemas.performance import FLOW_REFRESH_LATEST_EPISODE


class RefreshLatestEpisodeJob(BaseJob):
    """刷新最新集弹幕定时任务（养弹幕）"""

    job_type = "refreshLatestEpisode"
    job_name = "刷新最新集弹幕"
    job_name_en = "Refresh Latest Episode Danmaku"
    job_name_tw = "重新整理最新集彈幕"
    description = "自动检测已启用追更的作品，对最新一集弹幕数未达阈值的进行定时刷新。支持全局扫描或绑定指定源。适用于正在连载的动画/电视剧。"
    description_en = "Auto-detect tracked works and refresh danmaku for the latest episode when count is below threshold. Supports global scan or bound sources. For ongoing anime/TV series."
    description_tw = "自動偵測已啟用追更的作品，對最新一集彈幕數未達到閾值的進行定時重新整理。支援全域掃描或綁定指定來源。適用於正在連載的動畫/電視劇。"
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
        {
            "key": "recentEpisodesCount",
            "label": "刷新最近几集",
            "label_en": "Recent Episodes Count",
            "label_tw": "重新整理最近幾集",
            "type": "number",
            "default": 1,
            "min": 1,
            "max": 10,
            "suffix": "集",
            "suffix_en": "episodes",
            "suffix_tw": "集",
            "description": "刷新最新的 N 集（默认 1 = 只刷新最新集）",
            "description_en": "Refresh the latest N episodes (default 1 = latest episode only)",
            "description_tw": "重新整理最新的 N 集（預設 1 = 只重新整理最新集）",
        },
    ]

    @profile_flow(FLOW_REFRESH_LATEST_EPISODE)
    async def run(self, session: AsyncSession, progress_callback: Callable, task_config: Optional[dict] = None):
        """定时任务的核心逻辑：刷新最近集的弹幕
        
        Args:
            session: 数据库会话（由调度器提供，本任务不使用）
            progress_callback: 进度回调
            task_config: 任务配置，可选包含：
                - commentThreshold: 弹幕数阈值
                - recentEpisodesCount: 刷新最近几集
                - bound_source_ids: 绑定的数据源ID列表（由调度器注入）
        """
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
        
        # 刷新最近几集
        recent_episodes_count = task_config.get("recentEpisodesCount", 1)
        try:
            recent_episodes_count = max(1, int(recent_episodes_count))
        except (ValueError, TypeError):
            recent_episodes_count = 1
        
        # 绑定源（由调度器注入）
        bound_source_ids = task_config.get("bound_source_ids")

        await progress_callback(0, "正在获取所有启用追更的源...")

        # 获取候选源列表
        db = get_database_service()
        async with db.transaction():
            if bound_source_ids is not None:
                # 专属任务：只处理绑定的源
                self.logger.info(f"专属任务模式：处理 {len(bound_source_ids)} 个绑定源")
                all_enabled = await db.source.get_sources_with_incremental_refresh_enabled()
                source_ids = [sid for sid in bound_source_ids if sid in all_enabled]
                if len(source_ids) < len(bound_source_ids):
                    skipped = len(bound_source_ids) - len(source_ids)
                    self.logger.info(f"已过滤掉 {skipped} 个未启用追更的绑定源")
            else:
                # 全局任务：扫描所有启用的源，但排除已有专属任务的源
                all_enabled = await db.source.get_sources_with_incremental_refresh_enabled()
                bound_by_others = await db.task_binding.get_bound_source_ids_by_job_type(
                    job_type=self.job_type,
                    enabled_tasks_only=True
                )
                source_ids = [sid for sid in all_enabled if sid not in bound_by_others]
                if bound_by_others:
                    self.logger.info(f"全局任务模式：排除了 {len(bound_by_others)} 个已有专属任务的源")

        total_sources = len(source_ids)
        mode = "专属任务" if bound_source_ids is not None else "全局扫描"

        if not total_sources:
            raise TaskSuccess(f"{mode}：没有找到任何启用追更的源，任务结束。")

        self.logger.info(f"{mode}：找到 {total_sources} 个源，刷新最近 {recent_episodes_count} 集")
        await progress_callback(10, f"找到 {total_sources} 个源，正在检查...")

        refreshed_count = 0
        skipped_count = 0

        for i, source_id in enumerate(source_ids):
            try:
                # 每个源使用独立短事务
                async with db.transaction():
                    source_info = await db.source.get_anime_source_info(source_id)
                    if not source_info:
                        self.logger.warning(f"无法找到数据源(id={source_id})的信息，跳过。")
                        skipped_count += 1
                        continue

                    # 阈值：任务级配置优先，否则读全局
                    if task_threshold is not None:
                        threshold = task_threshold
                    else:
                        threshold_str = await db.config.get_value("latestEpisodeCommentThreshold", "20000")
                        try:
                            threshold = int(threshold_str)
                        except (ValueError, TypeError):
                            threshold = 20000
                            self.logger.warning(f"无法解析弹幕阈值配置，使用默认值: {threshold}")
                    
                    # 获取最近 N 集
                    recent_episodes = await db.episode.get_recent_episodes(
                        source_id=source_id,
                        limit=recent_episodes_count
                    )

                if not recent_episodes:
                    self.logger.info(f"源 '{source_info['title']}' (ID: {source_id}) 没有任何分集，跳过。")
                    skipped_count += 1
                    continue

                # 对每一集判断是否需要刷新
                for episode in recent_episodes:
                    if episode.commentCount >= threshold:
                        self.logger.info(
                            f"源 '{source_info['title']}' 第{episode.episodeIndex}集 "
                            f"弹幕数({episode.commentCount})已达到阈值({threshold})，跳过。"
                        )
                        continue

                    # 创建刷新任务
                    task_title = f"刷新弹幕: {source_info['title']} - 第{episode.episodeIndex}集"
                    unique_key = f"refresh-latest-{source_id}-ep{episode.episodeIndex}"

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
                            create_refresh_task(episode.id, source_info),
                            task_title,
                            unique_key=unique_key
                        )
                        refreshed_count += 1
                        self.logger.info(
                            f"已为源 '{source_info['title']}' 第{episode.episodeIndex}集 "
                            f"创建刷新任务 (当前弹幕数: {episode.commentCount}/{threshold})"
                        )
                    except Exception as e:
                        self.logger.error(f"为源 '{source_info['title']}' 第{episode.episodeIndex}集创建刷新任务失败: {e}")
                        skipped_count += 1

            except Exception as e:
                self.logger.error(f"处理源 ID {source_id} 时发生错误: {e}", exc_info=True)
                skipped_count += 1

            # 更新进度
            progress = 10 + int(((i + 1) / total_sources) * 90)
            await progress_callback(progress, f"已处理 {i+1}/{total_sources} 个源")

        final_message = (
            f"{mode}完成：扫描 {total_sources} 个源，"
            f"创建 {refreshed_count} 个刷新任务，跳过 {skipped_count} 个。"
        )
        raise TaskSuccess(final_message)
