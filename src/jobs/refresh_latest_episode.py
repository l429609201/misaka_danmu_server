"""
刷新最新集弹幕定时任务
自动检测已启用追更的作品,对最新一集弹幕数未达到阈值的进行刷新
"""
import logging
from typing import Callable
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, update
from datetime import datetime, timedelta

from src.db import crud, orm_models
from .base import BaseJob
from src.services import TaskSuccess
from src.tasks import refresh_episode_task
from src.core import get_now


class RefreshLatestEpisodeJob(BaseJob):
    """刷新最新集弹幕定时任务"""
    
    job_type = "refreshLatestEpisode"
    job_name = "刷新最新集弹幕"
    job_name_en = "Refresh Latest Episode Danmaku"
    job_name_tw = "重新整理最新集彈幕"
    description = "自动检测已启用追更的作品,对最新一集弹幕数未达到阈值的进行定时刷新。适用于正在连载的动画/电视剧。"
    description_en = "Auto-detect tracked works and refresh danmaku for the latest episode when count is below threshold. For ongoing anime/TV series."
    description_tw = "自動偵測已啟用追更的作品，對最新一集彈幕數未達到閾值的進行定時重新整理。適用於正在連載的動畫/電視劇。"

    async def run(self, session: AsyncSession, progress_callback: Callable):
        """定时任务的核心逻辑: 刷新最新一集的弹幕"""
        await progress_callback(0, "正在获取所有启用追更的源...")
        
        # 获取所有启用追更的源
        source_ids = await crud.get_sources_with_incremental_refresh_enabled(session)
        total_sources = len(source_ids)
        
        if not total_sources:
            raise TaskSuccess("没有找到任何启用追更的源，任务结束。")

        self.logger.info(f"刷新最新集弹幕：找到 {total_sources} 个源")
        await progress_callback(10, f"找到 {total_sources} 个源，正在检查...")

        refreshed_count = 0
        skipped_count = 0
        
        for i, source_id in enumerate(source_ids):
            try:
                # 获取源信息
                source_info = await crud.get_anime_source_info(session, source_id)
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

                # 从全局配置获取弹幕阈值
                threshold_str = await crud.get_config_value(session, "latestEpisodeCommentThreshold", "20000")
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
                        config_manager=self.config_manager
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

