import logging
from typing import Callable
import asyncio

import aiomysql

from .. import crud
from .base import BaseJob
from ..task_manager import TaskSuccess
from ..api.ui import generic_import_task


class IncrementalRefreshJob(BaseJob):
    job_type = "incremental_refresh"
    job_name = "定时增量追更"

    async def run(self, progress_callback: Callable):
        """定时任务的核心逻辑: 按最新分集ID+1 抓取新集"""
        await progress_callback(0, "正在获取所有数据源...")
        source_ids = await crud.get_sources_with_incremental_refresh_enabled(self.pool)
        total_sources = len(source_ids)
        if not total_sources:
            raise TaskSuccess("没有找到任何数据源，任务结束。")

        self.logger.info(f"开始对 {total_sources} 个数据源进行增量更新检查。")
        await progress_callback(5, f"找到 {total_sources} 个数据源，开始检查...")

        for i, source_id in enumerate(source_ids):
            source_info = await crud.get_anime_source_info(self.pool, source_id)
            if not source_info:
                self.logger.warning(f"无法找到数据源(id={source_id})的信息，跳过。")
                continue

            anime_title = source_info.get("title", "未知作品")
            progress = 5 + int(((i + 1) / total_sources) * 90)
            await progress_callback(progress, f"正在检查: {anime_title} ({i+1}/{total_sources})")

            # 获取当前最新分集
            episodes = await crud.get_episodes_for_source(self.pool, source_id)
            latest_episode_index = max(ep['episode_index'] for ep in episodes) if episodes else 0
            
            next_episode_index = latest_episode_index + 1
            self.logger.info(f"为 '{anime_title}' (源ID: {source_id}) 尝试获取第 {next_episode_index} 集...")

            try:
                # 调用导入任务，只导入指定的一集
                await generic_import_task(
                    provider=source_info["provider_name"], media_id=source_info["media_id"],
                    anime_title=anime_title, media_type=source_info["type"],
                    season=source_info.get("season", 1), current_episode_index=next_episode_index,
                    image_url=None, douban_id=None, tmdb_id=source_info.get("tmdb_id"), 
                    imdb_id=None, tvdb_id=None, progress_callback=lambda p, d: None, # type: ignore
                    pool=self.pool, manager=self.scraper_manager, task_manager=self.task_manager
                )
            except TaskSuccess as e:
                message = str(e)
                if "未能获取到任何分集" in message:
                    # This is considered a "failure" for incremental refresh
                    new_failure_count = await crud.increment_incremental_refresh_failures(self.pool, source_id)
                    self.logger.warning(f"'{anime_title}' (源ID: {source_id}) 未找到新分集，失败次数: {new_failure_count}。")
                    if new_failure_count >= 15:
                        await crud.disable_incremental_refresh(self.pool, source_id)
                        self.logger.info(f"'{anime_title}' (源ID: {source_id}) 已连续15次未找到新分集，已自动取消追更。")
                else:
                    # Any other success message means a new episode was found (even if with 0 comments)
                    await crud.reset_incremental_refresh_failures(self.pool, source_id)
                    self.logger.info(f"为 '{anime_title}' (源ID: {source_id}) 的增量更新成功: {message}")
            except Exception as e:
                self.logger.error(f"为 '{anime_title}' (源ID: {source_id}) 的增量更新失败: {e}", exc_info=True)

        await progress_callback(100, "所有数据源检查完毕。")
        raise TaskSuccess(f"自动增量更新任务完成，共检查了 {total_sources} 个数据源。")
