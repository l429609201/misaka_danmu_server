"""通用订阅扫描任务（SubscriptionScanJob）。

设计依据：docs/subscription_page_implementation_plan.md 第 8 节。
两阶段：
  阶段1：读取 due 订阅目标 → 调用源 scan_subscription_target → 写候选项（新表 subscription_candidate_item）。
  阶段2：处理 waiting 候选项 → 源 fetch_subscription_item_comments 获取弹幕 → 建库 → 推进状态。
通用性：不感知具体 provider，全靠源声明的订阅能力 + 候选项 extraData 的建库字段。

方案 C（纯候选池）：
- 候选项表不记录导入状态（episode 表是单一数据源）
- 前端查询时 JOIN episode 表获取 is_imported
"""
import logging
from typing import Callable, Dict, Any, List, Optional

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func

from src.db import crud, orm_models
from src.db.crud import external_calendar as ext_cal_crud
from src.db.crud import subscription_candidate as cand_crud
from .base import BaseJob
from src.services import TaskSuccess


async def import_subscription_item(
    session: AsyncSession, scraper, item: Dict[str, Any],
    config_manager, title_recognition_manager=None,
) -> None:
    """对单个候选项：获取弹幕 → 建库（anime/source/episode）→ 存弹幕。

    模块级函数：供 SubscriptionScanJob 与「单目标立即导入」任务共用，避免逻辑重复。
    建库字段来自候选项 extraData：parentTitle/episodeIndex/mediaType/season。
    弹幕通过源的 fetch_subscription_item_comments 获取（源自行用 aid/cid 等定位）。
    """
    title = item.get("parentTitle") or item.get("animeTitle") or "未知订阅作品"
    media_type = item.get("mediaType") or "tv_series"
    season = int(item.get("season") or 1)
    episode_index = int(item.get("episodeIndex") or 1)
    provider = item.get("provider")
    parent_ext_id = item.get("parentExternalId") or item.get("externalId")

    comments = await scraper.fetch_subscription_item_comments(item)
    if not comments:
        raise ValueError("未获取到弹幕")

    # 建库：作品（按订阅父目标聚合）→ 源 → 分集 → 弹幕
    anime_id = await crud.get_or_create_anime(
        session, title, media_type, season, item.get("imageUrl"), None,
        item.get("year"), title_recognition_manager, provider,
    )

    # 修复：去掉前缀，使用纯数字/纯BV号作为 media_id 和 provider_episode_id
    # parent_ext_id 可能是 "collection:5219365" 或 "video:xxx"，去掉前缀
    clean_media_id = parent_ext_id.replace("collection:", "").replace("video:", "") if parent_ext_id else parent_ext_id
    # externalId 可能是 "video:BV1xxx" 格式，去掉前缀
    raw_episode_id = item.get("externalId") or ""
    clean_episode_id = raw_episode_id.replace("video:", "").replace("collection:", "")

    source_id = await crud.link_source_to_anime(session, anime_id, provider, clean_media_id)
    episode_title = item.get("animeTitle") or f"第{episode_index}集"
    episode_db_id = await crud.create_episode_if_not_exists(
        session, anime_id, source_id, episode_index, episode_title,
        None, clean_episode_id,  # 使用纯BV号
    )
    await crud.save_danmaku_for_episode(session, episode_db_id, comments, config_manager)
    await session.commit()


class SubscriptionScanJob(BaseJob):
    job_type = "subscriptionScan"
    job_name = "订阅源扫描与导入"
    job_name_en = "Subscription Source Scan & Import"
    job_name_tw = "訂閱源掃描與匯入"
    description = "扫描到期的订阅目标（如 Bilibili UP 主/番剧），发现新候选项并自动导入高置信度弹幕。"
    description_en = "Scan due subscription targets, discover new candidates and auto-import high-confidence danmaku."
    description_tw = "掃描到期的訂閱目標，發現新候選項並自動匯入高置信度彈幕。"

    async def _scan_due_targets(self, session: AsyncSession, progress_callback: Callable) -> int:
        """阶段1：扫描所有到期订阅目标，写入候选项（新表 subscription_candidate_item）。返回写入候选项总数。"""
        targets = await ext_cal_crud.get_due_subscription_targets(session)
        if not targets:
            self.logger.info("阶段1：没有到期的订阅目标。")
            return 0

        self.logger.info(f"阶段1：发现 {len(targets)} 个到期订阅目标，开始扫描。")
        # 调试日志：打印订阅目标的关键字段
        for t in targets:
            self.logger.debug(
                f"订阅目标: provider={t.get('provider')} externalId={t.get('externalId')} "
                f"subscriptionType={t.get('subscriptionType')} enabled={t.get('enabled')}"
            )
        total_written = 0
        for i, target in enumerate(targets):
            provider = target.get("provider")
            external_id = target.get("externalId")
            parent_id = target.get("id")  # external_calendar_item.id
            if not parent_id:
                self.logger.warning(f"订阅目标 {provider}:{external_id} 缺少 id 字段，跳过")
                continue

            scraper = self.scraper_manager.get_scraper(provider)
            if scraper is None or not getattr(scraper, "supports_subscription", False):
                self.logger.warning(f"订阅源 '{provider}' 未加载或不支持订阅，跳过目标 {external_id}")
                continue
            try:
                items = await scraper.scan_subscription_target(target)
            except Exception as e:
                self.logger.error(f"扫描订阅目标 {provider}:{external_id} 失败: {e}", exc_info=True)
                await ext_cal_crud.update_subscription_next_scan(
                    session, provider, external_id, last_error=str(e)
                )
                continue

            # 🔧 增量逻辑：针对 Bilibili 合集订阅，按发布时间排序并筛选新增集
            if items and provider == "bilibili":
                subscription_type = target.get("subscriptionType") or ""
                if subscription_type == "bilibili_collection":
                    items = await self._filter_incremental_collection_items(
                        session, target, items, external_id
                    )

            # 写入候选项到新表 subscription_candidate_item（纯候选池，无状态）
            if items:
                count = await cand_crud.upsert_candidates(session, parent_id, provider, items)
                total_written += count

            await ext_cal_crud.update_subscription_next_scan(session, provider, external_id)
            await progress_callback(5 + int((i + 1) / len(targets) * 45), f"已扫描 {i+1}/{len(targets)} 个订阅目标")

        self.logger.info(f"阶段1：共写入 {total_written} 个候选项。")
        return total_written

    async def _filter_incremental_collection_items(
        self, session: AsyncSession, target: Dict[str, Any], items: List[Dict[str, Any]],
        parent_ext_id: str
    ) -> List[Dict[str, Any]]:
        """
        针对 Bilibili 合集订阅的增量筛选逻辑：
        1. 按 pubdate 从旧到新排序
        2. 查询数据库已有的最大 episode_index
        3. 如果库内有集：从 max_index+1 开始返回
        4. 如果库内无集：只返回最新一集（避免首次全量）
        """
        if not items:
            return items

        # 1. 按 pubdate 排序（从旧到新）
        items_sorted = sorted(
            items,
            key=lambda x: (x.get("extraData") or {}).get("pubdate", 0)
        )

        # 2. 查询数据库已有的源和最大集号
        # 使用去掉前缀的纯数字/纯BV号作为 media_id
        clean_media_id = parent_ext_id.replace("collection:", "").replace("video:", "")
        source_exists = await crud.check_source_exists_by_media_id(
            session, "bilibili", clean_media_id
        )

        if not source_exists:
            # 3. 库内无集：只返回最新一集（pubdate 最大的）
            self.logger.info(f"合集 {parent_ext_id} 首次订阅，仅导入最新一集")
            return [items_sorted[-1]] if items_sorted else []

        # 4. 库内有集：查询最大 episode_index
        anime_id = await crud.get_anime_id_by_source_media_id(
            session, "bilibili", clean_media_id
        )
        if not anime_id:
            return [items_sorted[-1]] if items_sorted else []

        # 查询该源的所有集，获取最大 episode_index
        # 注意：ORM 属性名是 camelCase，不是 snake_case
        stmt = (
            select(func.max(orm_models.Episode.episodeIndex))
            .select_from(orm_models.Episode)
            .join(orm_models.AnimeSource, orm_models.Episode.sourceId == orm_models.AnimeSource.id)
            .where(
                orm_models.AnimeSource.animeId == anime_id,
                orm_models.AnimeSource.providerName == "bilibili",
                orm_models.AnimeSource.mediaId == clean_media_id
            )
        )
        result = await session.execute(stmt)
        max_index = result.scalar() or 0

        # 5. 筛选出 episode_index > max_index 的集
        new_items = [
            item for item in items_sorted
            if (item.get("extraData") or {}).get("episodeIndex", 0) > max_index
        ]

        self.logger.info(
            f"合集 {parent_ext_id} 增量更新：库内最大集号={max_index}，"
            f"发现 {len(new_items)} 个新集（共扫描 {len(items)} 集）"
        )
        return new_items

    async def _import_waiting_items(self, session: AsyncSession, progress_callback: Callable) -> int:
        """阶段2：查询候选项表（JOIN episode 判断未导入的集），获取弹幕并建库。返回成功导入数。

        方案 C：候选项表不存 status，从候选表查所有集 → JOIN episode 过滤已导入的 → 对未导入集建库。
        """
        # 查询所有已订阅的目标
        subscribed_targets = await ext_cal_crud.list_subscription_targets(
            session, status="pending", page_size=100
        )
        targets = subscribed_targets.get("list", [])
        if not targets:
            self.logger.info("阶段2：没有待处理的订阅目标。")
            return 0

        self.logger.info(f"阶段2：发现 {len(targets)} 个订阅目标，检查候选项。")
        imported = 0
        total_candidates = 0

        for target_idx, target in enumerate(targets):
            provider = target.get("provider")
            parent_id = target.get("id")
            if not parent_id:
                continue

            # 查询该目标的所有候选项（JOIN episode 获取 is_imported）
            candidates = await cand_crud.list_candidates_with_import_status(session, parent_id)
            # 仅处理未导入的集
            waiting = [c for c in candidates if not c.get("isImported")]
            total_candidates += len(waiting)

            if not waiting:
                continue

            scraper = self.scraper_manager.get_scraper(provider)
            if scraper is None:
                continue

            for cand in waiting:
                external_id = cand["externalId"]
                # 构建 item dict（展开 extraData，含 aid/cid/episodeIndex/parentTitle 等建库字段）
                item = {
                    "provider": provider,
                    "externalId": external_id,
                    "animeTitle": cand.get("title") or "",
                    **(cand.get("extraData") or {}),
                }
                try:
                    await import_subscription_item(
                        session, scraper, item, self.config_manager, self.title_recognition_manager
                    )
                    imported += 1
                except Exception as e:
                    self.logger.error(f"导入候选项 {provider}:{external_id} 失败: {e}", exc_info=True)

            await progress_callback(
                50 + int((target_idx + 1) / len(targets) * 50),
                f"已处理 {target_idx+1}/{len(targets)} 个目标，导入 {imported}/{total_candidates}"
            )

        self.logger.info(f"阶段2：成功导入 {imported} 个候选项。")
        return imported

    async def _import_single_item(self, session: AsyncSession, scraper, item: Dict[str, Any]) -> None:
        """对单个候选项建库（委托给模块级 import_subscription_item，逻辑统一）。"""
        await import_subscription_item(
            session, scraper, item, self.config_manager, self.title_recognition_manager
        )

    async def run(self, session: AsyncSession, progress_callback: Callable):
        """任务核心：阶段1 扫描目标 → 阶段2 导入候选项。"""
        await progress_callback(0, "阶段1：扫描到期订阅目标...")
        try:
            written = await self._scan_due_targets(session, progress_callback)
        except Exception as e:
            self.logger.error(f"扫描订阅目标阶段异常：{e}", exc_info=True)
            written = 0

        await progress_callback(50, "阶段2：导入待处理候选项...")
        try:
            imported = await self._import_waiting_items(session, progress_callback)
        except Exception as e:
            self.logger.error(f"导入候选项阶段异常：{e}", exc_info=True)
            imported = 0

        raise TaskSuccess(f"订阅扫描完成：写入候选项 {written} 个，导入弹幕 {imported} 个。")


async def scan_and_import_target_task(
    progress_callback: Callable,
    session: AsyncSession,
    scraper_manager,
    config_manager,
    provider: str,
    external_id: str,
    title_recognition_manager=None,
    selected_episodes: Optional[List[str]] = None,
):
    """对单个「强标识订阅目标」（如 Bilibili 合集/UP主）立即扫描并导入。

    用于日历订阅 runNow：这类源靠 seasonId/mid/uid 拉视频列表，无法用标题搜索，
    故直接复用 scan_subscription_target（拉候选）+ import_subscription_item（建库）。

    方案 C：候选项写入新表 subscription_candidate_item（纯候选池，无状态）。

    :param selected_episodes: 可选，仅导入指定的候选项 externalId（订阅合集部分集场景）
    """
    logger = logging.getLogger("ScanImportTarget")
    target = await ext_cal_crud.get_by_external_id(session, provider, external_id)
    if not target:
        raise TaskSuccess(f"订阅目标不存在: {provider}:{external_id}")

    parent_id = target.get("id")
    if not parent_id:
        raise TaskSuccess(f"订阅目标缺少 id 字段: {provider}:{external_id}")

    scraper = scraper_manager.get_scraper(provider)
    if scraper is None or not getattr(scraper, "supports_subscription", False):
        raise TaskSuccess(f"订阅源 '{provider}' 未加载或不支持订阅")

    await progress_callback(5, "扫描订阅目标...")
    try:
        items = await scraper.scan_subscription_target(target)
    except Exception as e:
        logger.error(f"扫描订阅目标 {provider}:{external_id} 失败: {e}", exc_info=True)
        raise TaskSuccess(f"扫描失败: {e}")

    items = items or []
    if not items:
        await ext_cal_crud.update_subscription_next_scan(session, provider, external_id)
        raise TaskSuccess("未发现可导入的视频候选项")

    # 写入候选项到新表 subscription_candidate_item
    await cand_crud.upsert_candidates(session, parent_id, provider, items)

    # 若指定 selected_episodes，仅导入选中的集；否则全部导入
    if selected_episodes:
        items = [it for it in items if it["externalId"] in selected_episodes]
        if not items:
            logger.warning(f"选中集列表 {selected_episodes} 在候选项中未找到匹配项，跳过立即导入")
            await ext_cal_crud.update_subscription_next_scan(session, provider, external_id)
            raise TaskSuccess("未找到选中集对应的候选项，订阅已标记，后续定时任务将扫描")

    # 逐个建库（候选项的建库字段在 extraData，已展开到顶层）
    total = len(items)
    imported = 0
    for i, item in enumerate(items):
        # extraData 字段在写库后需重新展开：这里直接用 scan 返回的 extraData
        build_item = {"provider": item.get("provider", provider), "externalId": item["externalId"],
                      **(item.get("extraData") or {})}
        try:
            await import_subscription_item(session, scraper, build_item, config_manager, title_recognition_manager)
            imported += 1
        except Exception as e:
            logger.error(f"导入候选项 {build_item.get('externalId')} 失败: {e}", exc_info=True)
        await progress_callback(10 + int((i + 1) / total * 85), f"已导入 {i+1}/{total}")

    await ext_cal_crud.update_subscription_status(session, provider, external_id, "imported")
    await ext_cal_crud.update_subscription_next_scan(session, provider, external_id)
    suffix = f"（仅选中 {total} 集）" if selected_episodes else ""
    raise TaskSuccess(f"订阅导入完成{suffix}：候选 {total} 个，成功导入 {imported} 个。")
