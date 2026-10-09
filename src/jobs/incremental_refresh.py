import logging
from src.services.service_container import get_database_service
from typing import Callable
from datetime import datetime
from sqlalchemy.ext.asyncio import AsyncSession # type: ignore

from fastapi import HTTPException, status
from src.schemas.import_schemas import (
    ControlAutoImportRequest,
    AutoImportMediaType,
    AutoImportSearchType,
)
from .base import BaseJob
from .subscription_scan import SubscriptionScanJob
from src.utils.diagnostics.task_exceptions import TaskSuccess
from src.services.performance_service import profile_flow
from src.schemas.performance import FLOW_INCREMENTAL_REFRESH
from src.tasks.auto_import import auto_search_and_import_task
from src.tasks.import_core import generic_import_task


class IncrementalRefreshJob(BaseJob):
    job_type = "incrementalRefresh"
    job_name = "定时订阅与追更"
    job_name_en = "Subscription & Incremental Refresh"
    job_name_tw = "定時訂閱與追更"
    description = "处理待订阅条目（自动建库）+ 自动检测已启用追更的作品并尝试获取下一集弹幕。支持按播出日智能调度。"
    description_en = "Process pending subscriptions (auto-import) and fetch next-episode danmaku for tracked works. Smart scheduling by air weekday."
    description_tw = "處理待訂閱條目（自動建庫）+ 自動偵測已啟用追更的作品並取得下一集彈幕。支援按播出日智慧排程。"

    async def _process_pending_subscriptions(self, session: AsyncSession) -> int:
        """阶段0：扫描所有 pending/failed 订阅意向，逐个触发自动导入任务。

        返回成功提交的任务数。状态机推进：pending → importing（任务提交成功）/ failed（提交失败）。
        任务实际成功/失败后，由任务内部根据建库结果回写最终状态
        （无回写时由下次扫描根据条目是否已建库自动收敛）。
        """
        db = get_database_service()
        # 订阅查询使用独立短事务，避免任务提交期间持有数据库连接。
        async with db.transaction():
            pending = await db.external_calendar.get_pending_subscriptions(max_failures=3)
        if not pending:
            self.logger.info("阶段0：没有待处理的订阅意向。")
            return 0

        self.logger.info(f"阶段0：发现 {len(pending)} 条待处理订阅，开始触发 auto_import 任务。")
        submitted = 0

        for item in pending:
            # why：RSS 目标只是用户订阅清单容器，由 SubscriptionScanJob 展开；
            # 它本身不是作品，不能提交标题自动导入任务。
            if item.get("subscriptionType") == "anibt_rss_feed":
                continue

            provider = item.get("provider")
            external_id = item.get("externalId")
            anime_title = item.get("animeTitle") or "未知"
            bgm_id = item.get("bangumiId")
            tmdb_id = item.get("tmdbId")
            season = item.get("season")
            anime_type = item.get("animeType") or "tv_series"
            is_movie = anime_type == "movie"

            # 选择搜索类型
            if tmdb_id:
                search_type = AutoImportSearchType.TMDB
                search_term = str(tmdb_id)
            elif bgm_id:
                search_type = AutoImportSearchType.BANGUMI
                search_term = str(bgm_id)
            else:
                search_type = AutoImportSearchType.KEYWORD
                search_term = anime_title.strip()
                if not search_term:
                    self.logger.warning(f"跳过订阅 {provider}:{external_id}：无法构造搜索词")
                    # 状态写入独立提交，不能依赖任务传入但未绑定的 session。
                    async with db.transaction():
                        await db.external_calendar.update_subscription_status(
                            provider, external_id, "failed", increment_failure=True
                        )
                    continue

            media_type = AutoImportMediaType.MOVIE if is_movie else AutoImportMediaType.TV_SERIES
            payload = ControlAutoImportRequest(
                searchType=search_type,
                searchTerm=search_term,
                season=None if is_movie else (season or 1),
                episode=None,
                mediaType=media_type,
                enableIncrementalRefresh=True,
            )

            unique_key_parts = [search_type.value, search_term, media_type.value]
            if not is_movie and (season or 1):
                unique_key_parts.append(f"s{season or 1}")
            unique_key = f"calendar-subscribe-{'-'.join(unique_key_parts)}"

            task_title = f"订阅: {anime_title}"
            if not is_movie and (season or 1):
                task_title += f" S{(season or 1):02d}"

            try:
                task_coro = lambda s, cb, p=payload: auto_search_and_import_task(
                    p, cb, s, self.config_service, self.scraper_manager, self.metadata_manager,
                    self.task_manager,
                    ai_service=getattr(self, "ai_service", None),
                    rate_limiter=self.rate_limiter,
                    title_recognition_manager=getattr(self, "title_recognition_manager", None),
                )
                await self.task_manager.submit_task(
                    task_coro, task_title, unique_key=unique_key,
                    task_type="auto_import",
                    task_parameters=payload.model_dump(),
                )
                # 提交任务不占用状态更新事务，最终状态仍由后续扫描收敛。
                async with db.transaction():
                    await db.external_calendar.update_subscription_status(
                        provider, external_id, "importing"
                    )
                submitted += 1
            except HTTPException as e:
                if e.status_code == status.HTTP_409_CONFLICT:
                    # 已有相同任务在跑，保持 importing 不变（或从 pending 推进到 importing）
                    async with db.transaction():
                        await db.external_calendar.update_subscription_status(
                            provider, external_id, "importing"
                        )
                    self.logger.info(f"订阅任务已在队列中：{task_title}")
                else:
                    async with db.transaction():
                        await db.external_calendar.update_subscription_status(
                            provider, external_id, "failed", increment_failure=True
                        )
                    self.logger.error(f"订阅任务提交失败 ({provider}:{external_id})：{e.detail}")
            except Exception as e:
                async with db.transaction():
                    await db.external_calendar.update_subscription_status(
                        provider, external_id, "failed", increment_failure=True
                    )
                self.logger.error(f"订阅任务提交异常 ({provider}:{external_id})：{e}", exc_info=True)

        return submitted

    async def _reconcile_imported_subscriptions(self, session: AsyncSession) -> int:
        """将已建库的 importing 订阅收敛为 imported。"""
        db = get_database_service()
        async with db.transaction():
            reconciled = await db.external_calendar.reconcile_imported_subscriptions()
        if reconciled:
            self.logger.info(f"阶段0.5：收敛 {reconciled} 条订阅为 imported。")
        return reconciled

    @profile_flow(FLOW_INCREMENTAL_REFRESH)
    async def run(self, session: AsyncSession, progress_callback: Callable):
        """定时任务核心：阶段A 强标识订阅扫描+导入 → 阶段0 处理订阅 → 阶段1 抓下一集"""
        # 阶段A：扫描强标识订阅目标（如 Bilibili 合集/UP主），生成候选项并导入弹幕
        # 复用 SubscriptionScanJob 的逻辑，避免代码重复；两者共享同一套 BaseJob 依赖
        await progress_callback(0, "阶段A：扫描强标识订阅目标（B站合集等）...")
        scan_written, scan_imported = 0, 0
        try:
            scan_job = SubscriptionScanJob(
                self._session_factory, self.task_manager, self.scraper_manager,
                self.rate_limiter, self.metadata_manager, self.config_service,
                title_recognition_manager=self.title_recognition_manager,
                ai_service=self.ai_service,
            )
            # 将扫描阶段的 0-100 进度压缩到 0-5%，避免与后续阶段进度跳动
            async def _scan_cb(p, desc):
                await progress_callback(min(int(p) // 20, 4), desc)
            scan_written = await scan_job._scan_due_targets(session, _scan_cb)
            scan_imported = await scan_job._import_waiting_items(session, _scan_cb)
        except Exception as e:
            self.logger.error(f"阶段A 强标识订阅扫描/导入异常：{e}", exc_info=True)

        # 阶段0：处理订阅意向（pending → importing）
        await progress_callback(5, "阶段0：处理待订阅条目...")
        try:
            submitted_subs = await self._process_pending_subscriptions(session)
        except Exception as e:
            self.logger.error(f"处理 pending 订阅时发生异常：{e}", exc_info=True)
            submitted_subs = 0
        # 阶段0.5：收敛 importing → imported（基于本地是否已建库）
        try:
            await self._reconcile_imported_subscriptions(session)
        except Exception as e:
            self.logger.warning(f"收敛 imported 订阅时发生异常（忽略）：{e}")

        # 阶段1：原有的增量追更（已建库的源 → 抓下一集）
        await progress_callback(5, "阶段1：扫描已启用追更的源...")
        db = get_database_service()
        # 源列表查询完成即释放连接，不将调度过程包进长事务。
        async with db.transaction():
            source_ids = await db.source.get_sources_with_incremental_refresh_enabled()
        total_sources = len(source_ids)
        if not total_sources:
            raise TaskSuccess(
                f"阶段A 扫描候选 {scan_written} 个、导入 {scan_imported} 个；"
                f"阶段0 提交订阅任务 {submitted_subs} 个；阶段1 没有找到任何启用的追更源，任务结束。"
            )

        # 获取当前星期几 (1=周一 ... 7=周日)
        today_weekday = datetime.now().isoweekday()

        self.logger.info(f"阶段1：找到 {total_sources} 个源，今天是星期{today_weekday}，将为每个源创建独立的导入任务。")
        await progress_callback(10, f"找到 {total_sources} 个源，正在创建任务...")

        submitted_count = 0
        skipped_by_schedule = 0
        for i, source_id in enumerate(source_ids):
            # 分集聚合查询只在短事务内执行；提交任务期间不持有会话。
            async with db.transaction():
                source_info = await db.source.get_anime_source_info(source_id)
                latest_episode_index = await db.episode.get_max_episode_index_by_source(source_id)
            if not source_info:
                self.logger.warning(f"无法找到数据源(id={source_id})的信息，跳过。")
                continue

            # ====== 智能调度：按播出日过滤 ======
            air_weekday = source_info.get("airWeekday")
            if air_weekday and air_weekday != today_weekday:
                skipped_by_schedule += 1
                self.logger.debug(f"跳过 '{source_info['title']}'：播出日为星期{air_weekday}，今天是星期{today_weekday}")
                continue

            next_episode_index = latest_episode_index + 1
            self.logger.info(f"为 '{source_info['title']}' (源ID: {source_id}) 尝试获取第 {next_episode_index} 集...")

            task_title = f"定时追更: {source_info['title']} - S{source_info.get('season', 1):02d}E{next_episode_index:02d}"
            unique_key = f"incremental-refresh:{source_info['providerName']}:{source_info['mediaId']}:{source_info.get('season', 1)}:{next_episode_index}"

            def create_task_coro_factory(info, next_ep, src_id):
                return lambda s, cb: generic_import_task(
                    provider=info["providerName"], mediaId=info["mediaId"], animeTitle=info["title"],
                    mediaType=info["type"], season=info.get("season", 1), year=info.get("year"),
                    currentEpisodeIndex=next_ep, imageUrl=None,
                    config_service=self.config_service, metadata_manager=self.metadata_manager,
                    progress_callback=cb, session=s, manager=self.scraper_manager,
                    task_manager=self.task_manager, rate_limiter=self.rate_limiter,
                    title_recognition_manager=self.title_recognition_manager,
                    doubanId=None, tmdbId=info.get("tmdbId"), imdbId=None, tvdbId=None,
                    bangumiId=info.get("bangumiId"),
                    is_incremental_refresh=True, incremental_refresh_source_id=src_id
                )

            try:
                await self.task_manager.submit_task(create_task_coro_factory(source_info, next_episode_index, source_id), task_title, unique_key=unique_key)
                submitted_count += 1
            except HTTPException as e:
                if e.status_code == status.HTTP_409_CONFLICT:
                    self.logger.info(f"跳过创建任务 '{task_title}'，因为它已在队列中或正在运行。")
                else:
                    self.logger.error(f"为源 '{source_info['title']}' (ID: {source_id}) 创建追更任务时发生HTTP错误: {e.detail}")
            except Exception as e:
                self.logger.error(f"为源 '{source_info['title']}' (ID: {source_id}) 创建追更任务时失败: {e}")

            progress = 10 + int(((i + 1) / total_sources) * 90)
            await progress_callback(progress, f"已处理 {i+1}/{total_sources} 个源")

        schedule_info = f"（智能调度：{skipped_by_schedule} 个源因非播出日被跳过）" if skipped_by_schedule else ""
        raise TaskSuccess(
            f"阶段A 扫描候选 {scan_written} 个、导入 {scan_imported} 个；"
            f"阶段0 提交订阅任务 {submitted_subs} 个；阶段1 为 {submitted_count} 个源创建了追更任务。{schedule_info}"
        )
