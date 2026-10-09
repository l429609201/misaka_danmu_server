import asyncio
from typing import Callable, Optional

from sqlalchemy.ext.asyncio import AsyncSession

from .base import BaseJob
from src.services.service_container import get_database_service
# 任务成功信号需在运行时 raise，必须真实导入
from src.services.task_manager import TaskSuccess
from src.services.performance_service import profile_flow
from src.schemas.performance import FLOW_FILL_MISSING_EPISODES
from src.tasks.refresh import fill_missing_task
from src.workflows.supplement_episodes import get_episodes_routed


class FillMissingEpisodesJob(BaseJob):
    job_type = "fillMissingEpisodes"
    job_name = "分集补全扫描"
    job_name_en = "Episode Fill Scan"
    job_name_tw = "分集補全掃描"
    description = "扫描库内所有条目，检测因导入异常导致的缺集情况，自动补全缺失的分集弹幕。"
    description_en = "Scan all library entries to detect missing episodes caused by import errors, and auto-fill missing danmaku."
    description_tw = "掃描庫內所有條目，偵測因匯入異常導致的缺集情況，自動補全缺失的分集彈幕。"
    config_schema = [
        {
            "key": "maxFillCount",
            "label": "每次最大补全源数",
            "label_en": "Max Fill Count Per Run",
            "label_tw": "每次最大補全源數",
            "type": "number",
            "default": 10,
            "min": 1,
            "max": 100,
            "suffix": "个",
            "suffix_en": "",
            "suffix_tw": "個",
            "description": "每次执行最多对多少个源进行补全，避免一次性请求过多。",
            "description_en": "Max number of sources to fill per run, to avoid excessive requests.",
            "description_tw": "每次執行最多對多少個源進行補全，避免一次性請求過多。"
        },
        {
            "key": "skipFinished",
            "label": "跳过已完结源",
            "label_en": "Skip Finished Sources",
            "label_tw": "跳過已完結源",
            "type": "boolean",
            "default": False,
            "description": "开启后，标记为已完结的源将被跳过，不进行缺集扫描。",
            "description_en": "When enabled, sources marked as finished will be skipped during scan.",
            "description_tw": "開啟後，標記為已完結的源將被跳過，不進行缺集掃描。"
        },
        {
            "key": "lowQualityThreshold",
            "label": "低质量弹幕阈值",
            "label_en": "Low Quality Danmaku Threshold",
            "label_tw": "低品質彈幕閾值",
            "type": "number",
            "default": 0,
            "min": 0,
            "max": 1000,
            "suffix": "条",
            "suffix_en": "comments",
            "suffix_tw": "條",
            "description": "弹幕数低于此值的分集将被标记为低质量并重新抓取。设为0则禁用低质量检测。",
            "description_en": "Episodes with fewer danmaku than this threshold will be re-fetched. Set to 0 to disable.",
            "description_tw": "彈幕數低於此值的分集將被標記為低品質並重新抓取。設為0則停用低品質檢測。"
        },
    ]

    @profile_flow(FLOW_FILL_MISSING_EPISODES)
    async def run(
        self, session: AsyncSession, progress_callback: Callable,
        task_config: Optional[dict] = None,
    ) -> None:
        """扫描源的缺集情况并逐源调用补全任务，低质量检测只统计。"""
        if task_config is None:
            task_config = {}
        max_fill_count = int(task_config.get("maxFillCount", 10))
        skip_finished = task_config.get("skipFinished", False)
        low_quality_threshold = int(task_config.get("lowQualityThreshold", 0))

        self.logger.info(f"开始执行 [{self.job_name}]... (最大补全: {max_fill_count}, 跳过已完结: {skip_finished}, 低质量阈值: {low_quality_threshold})")
        await progress_callback(0, "正在扫描库内条目...")

        # 聚合查询经统一服务入口执行，扫描远程目录前结束读事务。
        db = get_database_service()
        async with db.transaction():
            all_sources = await db.source.get_sources_with_episode_counts(skip_finished)
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

                # 已存储的补充源标识也必须经过统一路由，不能当成原生媒体 ID。
                remote_episodes = await get_episodes_routed(self.scraper_manager, provider_name, media_id)
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

        # 步骤2.5: 低质量弹幕检测（仅统计，不刷新已收录分集）
        low_quality_count = 0
        if low_quality_threshold > 0:
            await progress_callback(72, "正在检测低质量弹幕分集...")
            # 低质量检测仍只报告统计，不触发已有分集刷新。
            async with db.transaction():
                lq_sources = await db.source.get_low_quality_episode_sources(
                    low_quality_threshold, skip_finished,
                )
            low_quality_count = sum(row["episode_count"] for row in lq_sources)

            if lq_sources:
                self.logger.info(
                    f"发现 {len(lq_sources)} 个源共 {low_quality_count} 个低质量分集 "
                    f"(弹幕数 < {low_quality_threshold})"
                )
                for lq in lq_sources[:10]:  # 只日志前10条
                    self.logger.info(
                        f"  低质量: '{lq['title']}' [{lq['provider_name']}] "
                        f"{lq['episode_count']}集, 平均弹幕{lq['avg_comments']:.0f}条"
                    )

        await progress_callback(75, f"扫描完成，发现 {len(missing_sources)} 个缺集源" +
                                (f"，{low_quality_count} 个低质量分集" if low_quality_count > 0 else ""))

        if not missing_sources and low_quality_count == 0:
            raise TaskSuccess(
                f"扫描完成：检查了 {checked_count} 个源，未发现缺集" +
                (f"或低质量弹幕。" if low_quality_threshold > 0 else "。")
            )

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
                    # 创建一个静默的进度回调（不覆盖主任务进度）
                    async def _noop_progress(p: int, d: str) -> None:
                        pass

                    await fill_missing_task(
                        sourceId=source_id,
                        session=fill_session,
                        manager=self.scraper_manager,
                        task_manager=self.task_manager,
                        config_service=self.config_service,
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
        if low_quality_count > 0:
            summary_parts.append(f"低质量弹幕 {low_quality_count} 个分集")

        raise TaskSuccess(f"分集补全完成：{'，'.join(summary_parts)}。")