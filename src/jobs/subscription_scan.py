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
from typing import Any, Callable, Dict, List, Optional

from src.utils.diagnostics.task_exceptions import TaskFailed, TaskSuccess
from src.services.service_container import get_database_service
from src.services.subscription_manager import SubscriptionManager
from src.services.performance_service import profile_flow
from src.schemas.performance import FLOW_SUBSCRIPTION_SCAN
from src.workflows.subscription_scan_flow import import_subscription_item, persist_subscription_scan_result

from .base import BaseJob

class SubscriptionScanJob(BaseJob):
    job_type = "subscriptionScan"
    job_name = "订阅源扫描与导入"
    job_name_en = "Subscription Source Scan & Import"
    job_name_tw = "訂閱源掃描與匯入"
    description = "扫描到期的订阅目标（如 Bilibili UP 主/番剧），发现新候选项并自动导入高置信度弹幕。"
    description_en = "Scan due subscription targets, discover new candidates and auto-import high-confidence danmaku."
    description_tw = "掃描到期的訂閱目標，發現新候選項並自動匯入高置信度彈幕。"

    async def _scan_due_targets(self, progress_callback: Callable) -> int:
        """阶段1：扫描所有到期订阅目标，写入候选项并返回写入数量。"""
        db = get_database_service()
        async with db.transaction():
            targets = await db.external_calendar.get_due_subscription_targets()
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
        errors = []
        for i, target in enumerate(targets):
            provider = target.get("provider")
            external_id = target.get("externalId")
            parent_id = target.get("id")  # external_calendar_item.id
            if not parent_id:
                self.logger.warning(f"订阅目标 {provider}:{external_id} 缺少 id 字段，跳过")
                errors.append(f"{provider}:{external_id} 缺少 id 字段")
                continue

            subscription_manager = SubscriptionManager(self.scraper_manager, self.metadata_manager)
            try:
                scan_result = await subscription_manager.scan_target(target)
                total_written += await persist_subscription_scan_result(target, scan_result)
            except Exception as e:
                self.logger.error(f"扫描订阅目标 {provider}:{external_id} 失败: {e}", exc_info=True)
                errors.append(f"{provider}:{external_id}: {e}")
                async with db.transaction():
                    await db.external_calendar.update_subscription_next_scan(
                        provider, external_id, last_error=str(e)
                    )
                continue

            await progress_callback(5 + int((i + 1) / len(targets) * 45), f"已扫描 {i+1}/{len(targets)} 个订阅目标")

        if errors:
            raise RuntimeError(f"订阅目标扫描写入 {total_written} 项；失败: {errors[0]}")
        self.logger.info(f"阶段1：共写入 {total_written} 个候选项。")
        return total_written


    async def _import_waiting_items(self, progress_callback: Callable) -> int:
        """阶段2：查询候选项表（JOIN episode 判断未导入的集），获取弹幕并建库。返回成功导入数。

        方案 C：候选项表不存 status，从候选表查所有集 → JOIN episode 过滤已导入的 → 对未导入集建库。
        """
        # 查询所有已订阅的目标
        db = get_database_service()

        async with db.transaction():
            subscribed_targets = await db.external_calendar.list_subscription_targets(
                page_size=100
            )
        targets = subscribed_targets.get("list", [])
        if not targets:
            self.logger.info("阶段2：没有待处理的订阅目标。")
            return 0

        self.logger.info(f"阶段2：发现 {len(targets)} 个订阅目标，检查候选项。")
        imported = 0
        total_candidates = 0
        errors = []

        for target_idx, target in enumerate(targets):
            if not target.get("subscriptionType"):
                continue
            provider = target.get("provider")
            parent_id = target.get("id")
            if not parent_id:
                continue

            # 查询该目标的所有候选项（JOIN episode 获取 isImported）
            async with db.transaction():
                candidates = await db.subscription_candidate.list_with_import_status(parent_id)
            # 仅处理未导入的集
            waiting = [c for c in candidates if not c.get("isImported")]
            total_candidates += len(waiting)

            if not waiting:
                continue

            scraper = self.scraper_manager.get_scraper(provider)
            if scraper is None:
                errors.append(f"订阅源不可用: {provider}")
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
                        scraper, item, self.config_service, self.title_recognition_manager
                    )
                    imported += 1
                except Exception as e:
                    self.logger.error(f"导入候选项 {provider}:{external_id} 失败: {e}", exc_info=True)
                    errors.append(f"{provider}:{external_id}: {e}")

            await progress_callback(
                50 + int((target_idx + 1) / len(targets) * 50),
                f"已处理 {target_idx+1}/{len(targets)} 个目标，导入 {imported}/{total_candidates}"
            )

        if errors:
            raise RuntimeError(f"候选项导入 {imported}/{total_candidates} 成功；失败: {errors[0]}")
        self.logger.info(f"阶段2：成功导入 {imported} 个候选项。")
        return imported


    @profile_flow(FLOW_SUBSCRIPTION_SCAN)
    async def run(self, session: Any, progress_callback: Callable):
        """任务核心：阶段1 扫描目标 → 阶段2 导入候选项。"""
        await progress_callback(0, "阶段1：扫描到期订阅目标...")
        errors = []
        try:
            written = await self._scan_due_targets(progress_callback)
        except Exception as e:
            self.logger.error(f"扫描订阅目标阶段异常：{e}", exc_info=True)
            errors.append(f"扫描阶段: {e}")
            written = 0

        await progress_callback(50, "阶段2：导入待处理候选项...")
        try:
            imported = await self._import_waiting_items(progress_callback)
        except Exception as e:
            self.logger.error(f"导入候选项阶段异常：{e}", exc_info=True)
            errors.append(f"导入阶段: {e}")
            imported = 0

        if errors:
            raise TaskFailed("订阅扫描部分失败：" + "；".join(errors))
        raise TaskSuccess(f"订阅扫描完成：写入候选项 {written} 个，导入弹幕 {imported} 个。")


async def scan_and_import_target_task(
    progress_callback: Callable,
    session: Any,
    scraper_manager,
    config_service,
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
    db = get_database_service()
    async with db.transaction():
        target = await db.external_calendar.get_by_external_id_as_dict(provider, external_id)
    if not target:
        raise TaskFailed(f"订阅目标不存在: {provider}:{external_id}")

    parent_id = target.get("id")
    if not parent_id:
        raise TaskFailed(f"订阅目标缺少 id 字段: {provider}:{external_id}")

    scraper = scraper_manager.get_scraper(provider)
    if scraper is None or not getattr(scraper, "supports_subscription", False):
        raise TaskFailed(f"订阅源 '{provider}' 未加载或不支持订阅")

    await progress_callback(5, "扫描订阅目标...")
    try:
        items = await scraper.scan_subscription_target(target)
    except Exception as e:
        logger.error(f"扫描订阅目标 {provider}:{external_id} 失败: {e}", exc_info=True)
        raise TaskFailed(f"扫描失败: {e}") from e

    items = items or []
    if not items:
        async with db.transaction():
            await db.external_calendar.update_subscription_status(provider, external_id, "pending")
            await db.external_calendar.update_subscription_next_scan(provider, external_id)
        raise TaskSuccess("未发现可导入的视频候选项")

    # 先持久化完整候选池，再按用户所选集过滤导入集合。
    await persist_subscription_scan_result(target, {"mode": "candidates", "items": items})

    # 若指定 selected_episodes，仅导入选中的集；否则全部导入
    if selected_episodes:
        items = [it for it in items if it["externalId"] in selected_episodes]
        if not items:
            logger.warning(f"选中集列表 {selected_episodes} 在候选项中未找到匹配项，跳过立即导入")
            async with db.transaction():
                await db.external_calendar.update_subscription_status(provider, external_id, "pending")
                await db.external_calendar.update_subscription_next_scan(provider, external_id)
            raise TaskSuccess("未找到选中集对应的候选项，订阅已标记，后续定时任务将扫描")

    # 逐个建库（候选项的建库字段在 extraData，已展开到顶层）
    total = len(items)
    imported = 0
    failed = []
    for i, item in enumerate(items):
        # extraData 字段在写库后需重新展开：这里直接用 scan 返回的 extraData
        build_item = {"provider": item.get("provider", provider), "externalId": item["externalId"],
                      **(item.get("extraData") or {})}
        try:
            await import_subscription_item(scraper, build_item, config_service, title_recognition_manager)
            imported += 1
        except Exception as e:
            logger.error(f"导入候选项 {build_item.get('externalId')} 失败: {e}", exc_info=True)
            failed.append(str(e))
        await progress_callback(10 + int((i + 1) / total * 85), f"已导入 {i+1}/{total}")

    async with db.transaction():
        await db.external_calendar.update_subscription_status(
            provider, external_id,
            "imported" if imported == total else ("pending" if imported else "failed"),
            increment_failure=bool(failed and not imported),
        )
        await db.external_calendar.update_subscription_next_scan(provider, external_id)
    if failed:
        raise TaskFailed(f"订阅导入 {imported}/{total} 集成功，失败: {failed[0]}")
    suffix = f"（仅选中 {total} 集）" if selected_episodes else ""
    raise TaskSuccess(f"订阅导入完成{suffix}：候选 {total} 个，成功导入 {imported} 个。")
