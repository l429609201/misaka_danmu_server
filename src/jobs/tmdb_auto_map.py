import asyncio
import logging
from typing import Any, Callable, Dict, List, Optional

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
import httpx

from .. import crud, models
from .base import BaseJob
from ..rate_limiter import RateLimiter
from ..task_manager import TaskManager, TaskSuccess
from ..scraper_manager import ScraperManager
from ..metadata_manager import MetadataSourceManager

class TmdbAutoMapJob(BaseJob):
    job_type = "tmdbAutoMap"
    job_name = "TMDB自动映射与更新"

    # 修正：此任务不涉及弹幕下载，因此移除不必要的 rate_limiter 依赖
    # 修正：接收正确的依赖项
    def __init__(self, session_factory: async_sessionmaker[AsyncSession], task_manager: TaskManager, metadata_manager: MetadataSourceManager):
        # 由于此任务的依赖项与基类不同，我们不调用 super().__init__，
        # 而是直接初始化此任务所需的属性。
        self._session_factory = session_factory
        self.task_manager = task_manager
        self.metadata_manager = metadata_manager
        self.logger = logging.getLogger(self.__class__.__name__)


    async def run(self, session: AsyncSession, progress_callback: Callable):
        """定时任务的核心逻辑。"""
        self.logger.info(f"开始执行 [{self.job_name}] 定时任务...")
        progress_callback(0, "正在初始化...")
        
        # 为元数据管理器调用创建一个虚拟用户对象
        user = models.User(id=0, username="scheduled_task")

        shows_to_update = await crud.get_animes_with_tmdb_id(session)
        total_shows = len(shows_to_update)
        self.logger.info(f"找到 {total_shows} 个带TMDB ID的电视节目需要处理。")
        progress_callback(5, f"找到 {total_shows} 个节目待处理")

        for i, show in enumerate(shows_to_update):
            current_progress = 5 + int((i / total_shows) * 95) if total_shows > 0 else 95
            progress_callback(current_progress, f"正在处理: {show['title']} ({i+1}/{total_shows})")

            anime_id, tmdb_id, title = show['animeId'], show['tmdbId'], show['title']
            self.logger.info(f"正在处理: '{title}' (Anime ID: {anime_id}, TMDB ID: {tmdb_id})")
            try:
                # 核心变更：将获取详情的逻辑委托给元数据管理器
                details = await self.metadata_manager.get_details("tmdb", tmdb_id, user, mediaType="tv")
                
                if not details:
                    self.logger.warning(f"未能从 TMDB 获取 '{title}' (ID: {tmdb_id}) 的详情。")
                    continue

                # 如果本地没有剧集组ID，但远程获取到了，则更新
                if not show.get('tmdbEpisodeGroupId') and details.tmdbEpisodeGroupId:
                    self.logger.info(f"为 '{title}' 找到并更新剧集组 ID: {details.tmdbEpisodeGroupId}")
                    await crud.update_anime_tmdb_group_id(session, anime_id, details.tmdbEpisodeGroupId)

                # 从详情中提取别名并更新数据库（如果为空）
                aliases_to_update = {
                    "name_en": details.nameEn,
                    "name_jp": details.nameJp,
                    "name_romaji": details.nameRomaji,
                    "aliases_cn": details.aliasesCn
                }
                
                if any(aliases_to_update.values()):
                    self.logger.info(f"为 '{title}' 找到别名: {aliases_to_update}")
                    await crud.update_anime_aliases_if_empty(session, anime_id, aliases_to_update)
                else:
                    self.logger.info(f"未能为 '{title}' 找到任何别名。")

                await asyncio.sleep(1) # 简单的速率限制
            except Exception as e:
                self.logger.error(f"处理 '{title}' (TMDB ID: {tmdb_id}) 时发生错误: {e}", exc_info=True)
        
        self.logger.info(f"定时任务 [{self.job_name}] 执行完毕。")
        # 修正：抛出 TaskSuccess 异常，以便 TaskManager 可以用一个有意义的消息来结束任务
        raise TaskSuccess(f"任务执行完毕，共处理 {total_shows} 个节目。")