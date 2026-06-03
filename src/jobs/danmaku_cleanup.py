"""
定时删除弹幕任务

根据弹幕获取时间自动清理过期的分集弹幕，并级联处理空源和空条目。
"""
import asyncio
import logging
from datetime import timedelta
from typing import Callable, List, Optional

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func

from src.db import orm_models
from src.core import get_now
from src.tasks.delete import delete_danmaku_files_batch
from src.services import TaskSuccess
from .base import BaseJob

logger = logging.getLogger(__name__)

DEFAULT_RETENTION_DAYS = 7


class DanmakuCleanupJob(BaseJob):
    """
    定时删除弹幕任务

    检查所有分集的弹幕获取时间（fetchedAt），若与当前时间之差超过配置的保留天数，
    则删除该分集的弹幕文件及数据库记录，并级联删除空源和空条目。
    """
    job_type = "danmakuCleanup"
    job_name = "定时删除弹幕"
    job_name_en = "Scheduled Danmaku Cleanup"
    job_name_tw = "定時刪除彈幕"
    description = "定期清理超过保留期限的弹幕数据。若某源的弹幕全部删除则删除该源，若某条目的所有源均被删除则删除该条目。"
    description_en = "Periodically clean danmaku data beyond the retention period. Sources and entries with all danmaku deleted will be removed."
    description_tw = "定期清理超過保留期限的彈幕資料。若某源的彈幕全部刪除則刪除該源，若某條目的所有源均被刪除則刪除該條目。"

    config_schema = [
        {
            "key": "retentionDays",
            "label": "弹幕保留天数",
            "label_en": "Danmaku Retention Days",
            "label_tw": "彈幕保留天數",
            "type": "number",
            "default": DEFAULT_RETENTION_DAYS,
            "min": 1,
            "max": 3650,
            "suffix": "天",
            "suffix_en": "days",
            "suffix_tw": "天",
            "description": "弹幕获取时间距今超过该天数后将被自动删除。默认为 7 天。",
            "description_en": "Danmaku older than this many days will be auto-deleted. Default is 7 days.",
            "description_tw": "彈幕取得時間距今超過該天數後將被自動刪除。預設為 7 天。"
        }
    ]

    async def run(self, session: AsyncSession, progress_callback: Callable, task_config: dict = None):
        if task_config is None:
            task_config = {}

        retention_days = int(task_config.get("retentionDays", DEFAULT_RETENTION_DAYS))
        if retention_days < 1:
            retention_days = DEFAULT_RETENTION_DAYS

        self.logger.info(f"[定时删除弹幕] 开始执行，保留天数: {retention_days}")
        await progress_callback(0, f"开始扫描过期弹幕（保留期限: {retention_days} 天）...")

        now = get_now()
        cutoff = now - timedelta(days=retention_days)

        # 查询所有 fetchedAt 不为 None 且早于截止时间的分集
        stmt = select(orm_models.Episode).where(
            orm_models.Episode.fetchedAt.isnot(None),
            orm_models.Episode.fetchedAt < cutoff
        )
        result = await session.execute(stmt)
        expired_episodes = result.scalars().all()

        total = len(expired_episodes)
        if total == 0:
            self.logger.info("[定时删除弹幕] 没有发现过期的弹幕，任务结束。")
            raise TaskSuccess("没有发现过期的弹幕，任务结束。")

        self.logger.info(f"[定时删除弹幕] 发现 {total} 个过期分集，开始删除...")
        await progress_callback(10, f"发现 {total} 个过期分集，正在删除弹幕文件...")

        # 收集受影响的 sourceId，用于后续级联检查
        affected_source_ids: set = set()
        file_paths: List[Optional[str]] = []

        for episode in expired_episodes:
            affected_source_ids.add(episode.sourceId)
            if episode.danmakuFilePath:
                file_paths.append(episode.danmakuFilePath)

        # 批量删除弹幕文件
        if file_paths:
            await asyncio.to_thread(delete_danmaku_files_batch, file_paths)
            self.logger.info(f"[定时删除弹幕] 已删除 {len(file_paths)} 个弹幕文件")

        await progress_callback(40, "正在删除数据库中的过期分集记录...")

        # 删除所有过期分集的数据库记录
        deleted_episode_count = 0
        for episode in expired_episodes:
            await session.delete(episode)
            deleted_episode_count += 1

        await session.commit()
        self.logger.info(f"[定时删除弹幕] 已删除 {deleted_episode_count} 条分集记录")

        await progress_callback(60, "正在检查并清理空源...")

        # 级联：检查受影响的源，若无剩余分集则删除
        affected_anime_ids: set = set()
        deleted_source_count = 0

        for source_id in affected_source_ids:
            try:
                count_stmt = select(func.count(orm_models.Episode.id)).where(
                    orm_models.Episode.sourceId == source_id
                )
                count_result = await session.execute(count_stmt)
                episode_count = count_result.scalar_one()

                if episode_count == 0:
                    source = await session.get(orm_models.AnimeSource, source_id)
                    if source:
                        affected_anime_ids.add(source.animeId)
                        await session.delete(source)
                        await session.commit()
                        deleted_source_count += 1
                        self.logger.info(f"[定时删除弹幕] 已删除空源 (ID: {source_id})")
                else:
                    # 源还有分集，记录其 animeId 但不删除源
                    source = await session.get(orm_models.AnimeSource, source_id)
                    if source:
                        affected_anime_ids.add(source.animeId)
            except Exception as e:
                self.logger.error(f"[定时删除弹幕] 检查源 (ID: {source_id}) 时出错: {e}", exc_info=True)

            await asyncio.sleep(0.05)

        await progress_callback(80, "正在检查并清理空条目...")

        # 级联：检查受影响的条目，若无剩余源则删除
        deleted_anime_count = 0

        for anime_id in affected_anime_ids:
            try:
                count_stmt = select(func.count(orm_models.AnimeSource.id)).where(
                    orm_models.AnimeSource.animeId == anime_id
                )
                count_result = await session.execute(count_stmt)
                source_count = count_result.scalar_one()

                if source_count == 0:
                    anime = await session.get(orm_models.Anime, anime_id)
                    if anime:
                        await session.delete(anime)
                        await session.commit()
                        deleted_anime_count += 1
                        self.logger.info(f"[定时删除弹幕] 已删除空条目 (ID: {anime_id})")
            except Exception as e:
                self.logger.error(f"[定时删除弹幕] 检查条目 (ID: {anime_id}) 时出错: {e}", exc_info=True)

            await asyncio.sleep(0.05)

        await progress_callback(100, "清理完成")

        summary_parts = [f"共删除 {deleted_episode_count} 个过期分集"]
        if deleted_source_count > 0:
            summary_parts.append(f"清理 {deleted_source_count} 个空源")
        if deleted_anime_count > 0:
            summary_parts.append(f"清理 {deleted_anime_count} 个空条目")

        final_message = "、".join(summary_parts) + "。"
        self.logger.info(f"[定时删除弹幕] 任务完成：{final_message}")
        raise TaskSuccess(final_message)

