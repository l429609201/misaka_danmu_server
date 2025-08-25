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
        """
        定时任务的核心逻辑。
        参考了 EpisodeGroupMetaTest 插件的逻辑，以更全面地处理TMDB剧集组。
        """
        self.logger.info(f"开始执行 [{self.job_name}] 定时任务...")
        await progress_callback(0, "正在初始化...")
        
        # 为元数据管理器调用创建一个虚拟用户对象
        user = models.User(id=0, username="scheduled_task")

        shows_to_update = await crud.get_animes_with_tmdb_id(session)
        total_shows = len(shows_to_update)
        self.logger.info(f"找到 {total_shows} 个带TMDB ID的电视节目需要处理。")
        await progress_callback(5, f"找到 {total_shows} 个节目待处理")

        for i, show in enumerate(shows_to_update):
            current_progress = 5 + int((i / total_shows) * 95) if total_shows > 0 else 95
            anime_id, tmdb_id, title = show['animeId'], show['tmdbId'], show['title']
            await progress_callback(current_progress, f"正在处理: {title} ({i+1}/{total_shows})")
            self.logger.info(f"正在处理: '{title}' (Anime ID: {anime_id}, TMDB ID: {tmdb_id})")
            
            try:
                # 步骤 1: 获取媒体详情，包括别名
                details = await self.metadata_manager.get_details("tmdb", tmdb_id, user, mediaType="tv")
                if not details:
                    self.logger.warning(f"未能从 TMDB 获取 '{title}' (ID: {tmdb_id}) 的详情。")
                    continue

                # 步骤 2: 更新别名（如果本地为空）
                aliases_to_update = {
                    "name_en": details.nameEn,
                    "name_jp": details.nameJp,
                    "name_romaji": details.nameRomaji,
                    "aliases_cn": details.aliasesCn
                }
                if any(aliases_to_update.values()):
                    await crud.update_anime_aliases_if_empty(session, anime_id, aliases_to_update)
                    self.logger.info(f"为 '{title}' 更新了别名。")

                # 步骤 3: 获取所有剧集组
                # 此逻辑依赖于 TmdbMetadataSource 能够提供所有剧集组信息
                tmdb_source = self.metadata_manager.sources.get("tmdb")
                if not tmdb_source or not hasattr(tmdb_source, 'get_all_episode_groups'):
                    self.logger.warning(f"TMDB源不支持 get_all_episode_groups 方法，跳过 '{title}' 的剧集组处理。")
                    continue

                all_groups = await tmdb_source.get_all_episode_groups(tmdb_id, user)
                if not all_groups:
                    self.logger.info(f"'{title}' (TMDB ID: {tmdb_id}) 没有找到任何剧集组。")
                    continue
                
                self.logger.info(f"为 '{title}' 找到 {len(all_groups)} 个剧集组: {[g.get('name') for g in all_groups]}")

                # 步骤 4: 自动选择最佳剧集组进行处理 (优先处理 "Original Air Date")
                groups_to_process = [g for g in all_groups if g.get('type') == 1]
                if not groups_to_process:
                    self.logger.info(f"'{title}' 没有找到“原始播出顺序”(type=1)的剧集组，跳过映射更新。")
                    continue
                
                self.logger.info(f"为 '{title}' 选择了 {len(groups_to_process)} 个剧集组进行映射更新。")

                # 步骤 5: 为每个选定的剧集组，更新映射表
                for group in groups_to_process:
                    group_id = group.get('id')
                    if not group_id:
                        continue
                    
                    self.logger.info(f"正在为 '{title}' 更新剧集组 '{group.get('name')}' (ID: {group_id}) 的映射...")
                    await self.metadata_manager.update_tmdb_mappings(tmdb_id, group_id, user)

                    # 步骤 6: 更新作品关联的主剧集组ID
                    await crud.update_anime_tmdb_group_id(session, anime_id, group_id)
                    self.logger.info(f"已将 '{title}' 的主剧集组ID更新为: {group_id}")

                await session.commit() # 提交本次节目的所有更改

            except Exception as e:
                self.logger.error(f"处理 '{title}' (TMDB ID: {tmdb_id}) 时发生错误: {e}", exc_info=True)
                await session.rollback() # 出错时回滚
            finally:
                await asyncio.sleep(1) # 简单的速率限制，防止对TMDB API造成过大压力
        
        self.logger.info(f"定时任务 [{self.job_name}] 执行完毕。")
        # 修正：抛出 TaskSuccess 异常，以便 TaskManager 可以用一个有意义的消息来结束任务
        raise TaskSuccess(f"任务执行完毕，共处理 {total_shows} 个节目。")