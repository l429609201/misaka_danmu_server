import logging
import asyncio
from datetime import datetime
from typing import Callable, List, Dict, Any

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from src.db import crud, orm_models, models
from .base import BaseJob
from src.services import TaskSuccess


class AutoFinishJob(BaseJob):
    job_type = "autoFinish"
    job_name = "追更自动完结"
    job_name_en = "Auto-Finish Tracking"
    job_name_tw = "追更自動完結"
    description = "自动检测 TMDB 上已完结的番剧，延迟指定天数后自动取消追更。适用于不想手动关闭追更的场景。"
    description_en = "Auto-detect finished anime on TMDB and cancel tracking after a delay. Useful for automatic untracking."
    description_tw = "自動偵測 TMDB 上已完結的番劇，延遲指定天數後自動取消追更。適用於不想手動關閉追更的場景。"

    config_schema = [
        {
            "key": "delayDays",
            "label": "延迟天数",
            "label_en": "Delay Days",
            "label_tw": "延遲天數",
            "type": "number",
            "default": 2,
            "min": 0,
            "max": 30,
            "suffix": "天",
            "suffix_en": "days",
            "suffix_tw": "天",
            "description": "TMDB 标记完结后延迟几天再取消追更，让系统有时间抓取最后一集弹幕。",
            "description_en": "Days to wait after TMDB marks as finished before canceling tracking, allowing time to fetch the last episode's danmaku.",
            "description_tw": "TMDB 標記完結後延遲幾天再取消追更，讓系統有時間抓取最後一集彈幕。"
        }
    ]

    FINISHED_STATUSES = {"Ended", "Canceled"}

    async def run(self, session: AsyncSession, progress_callback: Callable, task_config: dict = None):
        if task_config is None:
            task_config = {}
        delay_days = int(task_config.get("delayDays", 2))

        await progress_callback(0, "正在获取所有启用追更的源...")
        source_ids = await crud.get_sources_with_incremental_refresh_enabled(session)
        total = len(source_ids)
        if not total:
            raise TaskSuccess("没有找到任何启用追更的源，任务结束。")

        self.logger.info(f"追更自动完结：找到 {total} 个追更源，延迟天数: {delay_days}")
        await progress_callback(5, f"找到 {total} 个追更源，正在检查 TMDB 状态...")

        tmdb_source = self.metadata_manager.sources.get("tmdb")
        if not tmdb_source:
            raise TaskSuccess("TMDB 元数据源未启用，无法检测完结状态。")

        canceled_count = 0
        skipped_count = 0
        error_count = 0
        today = datetime.now().date()

        # 使用 async with 管理 TMDB client，确保异常时也能释放连接
        try:
            client = await tmdb_source._create_client()
        except ValueError as e:
            raise TaskSuccess(f"TMDB 客户端创建失败: {e}")

        try:
            async with client:
                for i, source_id in enumerate(source_ids):
                    try:
                        async with self._session_factory() as task_session:
                            result = await self._check_and_finish_source(
                                task_session, client, source_id, delay_days, today
                            )
                            if result == "canceled":
                                canceled_count += 1
                            elif result == "error":
                                error_count += 1
                            else:
                                skipped_count += 1
                    except Exception as e:
                        self.logger.error(f"处理源 ID {source_id} 时发生错误: {e}", exc_info=True)
                        error_count += 1

                    progress = 5 + int(((i + 1) / total) * 90)
                    await progress_callback(progress, f"已检查 {i + 1}/{total} 个源")
        except Exception as e:
            self.logger.error(f"追更自动完结任务执行异常: {e}", exc_info=True)

        summary = f"追更自动完结完成：共 {total} 个源，自动取消 {canceled_count} 个，跳过 {skipped_count} 个"
        if error_count:
            summary += f"，失败 {error_count} 个"
        raise TaskSuccess(summary)

    async def _check_and_finish_source(
        self, session: AsyncSession, client, source_id: int, delay_days: int, today
    ) -> str:
        source = await session.get(orm_models.AnimeSource, source_id)
        if not source:
            return "skip"

        metadata = await session.execute(
            select(orm_models.AnimeMetadata).where(
                orm_models.AnimeMetadata.animeId == source.animeId
            )
        )
        meta = metadata.scalar_one_or_none()
        tmdb_id = meta.tmdbId if meta else None

        if not tmdb_id:
            return "skip"

        anime = await session.get(orm_models.Anime, source.animeId)
        title = anime.title if anime else f"源ID:{source_id}"

        try:
            response = await asyncio.wait_for(client.get(f"/tv/{tmdb_id}"), timeout=30)
            if response.status_code == 404:
                self.logger.warning(f"'{title}' TMDB ID {tmdb_id} 不存在(404)，跳过")
                return "skip"
            response.raise_for_status()
            data = response.json()
        except asyncio.TimeoutError:
            self.logger.warning(f"'{title}' 查询 TMDB 状态超时(30s)，跳过")
            return "error"
        except Exception as e:
            self.logger.warning(f"'{title}' 查询 TMDB 状态失败: {e}")
            return "error"

        tmdb_status = data.get("status", "")
        if tmdb_status not in self.FINISHED_STATUSES:
            return "skip"

        last_air_date_str = data.get("last_air_date")
        if not last_air_date_str:
            self.logger.info(f"'{title}' TMDB 状态为 {tmdb_status} 但无 last_air_date，跳过")
            return "skip"

        try:
            last_air_date = datetime.strptime(last_air_date_str, "%Y-%m-%d").date()
        except ValueError:
            self.logger.warning(f"'{title}' last_air_date 格式异常: {last_air_date_str}")
            return "skip"

        days_since = (today - last_air_date).days
        if days_since < delay_days:
            self.logger.info(
                f"'{title}' 已完结({tmdb_status})，最后播出 {last_air_date_str}，"
                f"距今 {days_since} 天 < 延迟 {delay_days} 天，保留追更"
            )
            return "skip"

        source.incrementalRefreshEnabled = False
        source.isFinished = True
        await session.commit()
        self.logger.info(
            f"'{title}' 已完结({tmdb_status})，最后播出 {last_air_date_str}，"
            f"距今 {days_since} 天 >= 延迟 {delay_days} 天，已自动取消追更并标记完结"
        )
        return "canceled"

