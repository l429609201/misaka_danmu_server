import asyncio
import logging
from typing import Callable, List

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func

from src.db import crud, orm_models, models
from .base import BaseJob
from src.services import TaskManager, TaskSuccess


class FillMissingEpisodesJob(BaseJob):
    job_type = "fillMissingEpisodes"
    job_name = "分集补全扫描"
    description = "扫描库内所有条目，检测因导入异常导致的缺集情况，自动补全缺失的分集弹幕。"
    config_schema = [
        {
            "key": "maxFillCount",
            "label": "每次最大补全源数",
            "type": "number",
            "default": 10,
            "min": 1,
            "max": 100,
            "suffix": "个",
            "description": "每次执行最多对多少个源进行补全，避免一次性请求过多。"
        },
        {
            "key": "skipFinished",
            "label": "跳过已完结源",
            "type": "boolean",
            "default": False,
            "description": "开启后，标记为已完结的源将被跳过，不进行缺集扫描。"
        },
    ]

    async def run(self, session: AsyncSession, progress_callback: Callable, task_config: dict = None):
        if task_config is None:
            task_config = {}
        max_fill_count = int(task_config.get("maxFillCount", 10))
        skip_finished = task_config.get("skipFinished", False)

        self.logger.info(f"开始执行 [{self.job_name}]... (最大补全: {max_fill_count}, 跳过已完结: {skip_finished})")
        await progress_callback(0, "正在扫描库内条目...")

        # 步骤1: 查询所有源及其实际分集数
        stmt = (
            select(
                orm_models.AnimeSource.id.label("source_id"),
                orm_models.AnimeSource.animeId.label("anime_id"),
                orm_models.AnimeSource.providerName.label("provider_name"),
                orm_models.AnimeSource.mediaId.label("media_id"),
                orm_models.AnimeSource.isFinished.label("is_finished"),
                orm_models.Anime.title.label("title"),
                func.count(orm_models.Episode.id).label("db_episode_count"),
            )
            .join(orm_models.Anime, orm_models.AnimeSource.animeId == orm_models.Anime.id)
            .outerjoin(orm_models.Episode, orm_models.AnimeSource.id == orm_models.Episode.sourceId)
            .group_by(
                orm_models.AnimeSource.id,
                orm_models.AnimeSource.animeId,
                orm_models.AnimeSource.providerName,
                orm_models.AnimeSource.mediaId,
                orm_models.AnimeSource.isFinished,
                orm_models.Anime.title,
            )
        )

        if skip_finished:
            stmt = stmt.where(orm_models.AnimeSource.isFinished == False)  # noqa: E712

        result = await session.execute(stmt)
        all_sources = [dict(row) for row in result.mappings()]
        total_sources = len(all_sources)

        self.logger.info(f"共扫描到 {total_sources} 个源")
        await progress_callback(5, f"共 {total_sources} 个源，正在逐个检查缺集...")

        # 步骤2: 遍历每个源，从 scraper 获取远程分集数并对比
        missing_sources = []  # (source_info, db_count, remote_count)
        checked_count = 0
        error_count = 0

        for i, source in enumerate(all_sources):
            provider_name = source["provider_name"]
            media_id = source["media_id"]
            title = source["title"]
            db_count = source["db_episode_count"]

            # 更新进度（扫描阶段占 5%-70%）
            scan_progress = 5 + int(((i + 1) / total_sources) * 65) if total_sources > 0 else 70
            if (i + 1) % 50 == 0 or i == 0:
                await progress_callback(scan_progress, f"扫描中: {i + 1}/{total_sources} ({title})")

            try:
                scraper = self.scraper_manager.get_scraper(provider_name)
                if not scraper:
                    continue

                # 获取源站分集列表
                remote_episodes = await scraper.get_episodes(media_id)
                if not remote_episodes:
                    continue

                remote_count = len(remote_episodes)
                if db_count < remote_count:
                    missing_count = remote_count - db_count
                    self.logger.info(
                        f"发现缺集: '{title}' [{provider_name}] "
                        f"数据库 {db_count} 集 / 源站 {remote_count} 集 (缺 {missing_count} 集)"
                    )
                    missing_sources.append((source, db_count, remote_count))

                checked_count += 1
            except Exception as e:
                self.logger.debug(f"检查 '{title}' [{provider_name}] 时出错: {e}")
                error_count += 1

            # 简单速率限制
            if (i + 1) % 10 == 0:
                await asyncio.sleep(0.5)

        self.logger.info(f"扫描完成: 检查 {checked_count} 个源, 发现 {len(missing_sources)} 个缺集源, {error_count} 个错误")
        await progress_callback(70, f"扫描完成，发现 {len(missing_sources)} 个缺集源")

        if not missing_sources:
            raise TaskSuccess(f"扫描完成：检查了 {checked_count} 个源，未发现缺集。")

        # 步骤3: 对缺集的源提交补全任务（最多 max_fill_count 个）
        # 按缺失集数从多到少排序，优先补全缺得多的
        missing_sources.sort(key=lambda x: x[2] - x[1], reverse=True)
        to_fill = missing_sources[:max_fill_count]
        skipped_fill = len(missing_sources) - len(to_fill)

        filled_count = 0
        fill_failed_count = 0

        for j, (source, db_count, remote_count) in enumerate(to_fill):
            source_id = source["source_id"]
            title = source["title"]
            provider_name = source["provider_name"]
            missing_count = remote_count - db_count

            fill_progress = 70 + int(((j + 1) / len(to_fill)) * 25)
            await progress_callback(fill_progress, f"补全中: {title} (缺 {missing_count} 集) ({j + 1}/{len(to_fill)})")

            try:
                # 使用独立 session，每个源的补全互不影响
                async with self._session_factory() as fill_session:
                    from src.tasks.refresh import fill_missing_task

                    # 创建一个静默的进度回调（不覆盖主任务进度）
                    async def _noop_progress(p, d):
                        pass

                    await fill_missing_task(
                        sourceId=source_id,
                        session=fill_session,
                        manager=self.scraper_manager,
                        task_manager=self.task_manager,
                        config_manager=self.config_manager,
                        rate_limiter=self.rate_limiter,
                        metadata_manager=self.metadata_manager,
                        progress_callback=_noop_progress,
                        animeTitle=title,
                        title_recognition_manager=self.title_recognition_manager,
                    )
                    filled_count += 1
                    self.logger.info(f"✓ '{title}' [{provider_name}] 补全完成")
            except TaskSuccess:
                # fill_missing_task 正常完成也会抛 TaskSuccess
                filled_count += 1
                self.logger.info(f"✓ '{title}' [{provider_name}] 补全完成")
            except Exception as e:
                fill_failed_count += 1
                self.logger.error(f"✗ '{title}' [{provider_name}] 补全失败: {e}", exc_info=True)

            # 补全之间休眠，避免请求过快
            await asyncio.sleep(1)

        # 汇总
        summary_parts = [
            f"扫描 {checked_count} 个源",
            f"发现 {len(missing_sources)} 个缺集",
            f"补全 {filled_count} 个",
        ]
        if fill_failed_count:
            summary_parts.append(f"失败 {fill_failed_count} 个")
        if skipped_fill:
            summary_parts.append(f"超出限制跳过 {skipped_fill} 个")
        if error_count:
            summary_parts.append(f"扫描出错 {error_count} 个")

        raise TaskSuccess(f"分集补全完成：{'，'.join(summary_parts)}。")