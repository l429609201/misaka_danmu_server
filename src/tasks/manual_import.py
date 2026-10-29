"""手动导入任务模块"""
import asyncio
import logging
from typing import Callable, Optional, List
from sqlalchemy.ext.asyncio import AsyncSession

from src.schemas import BatchManualImportItem
from src.rate_limiter import RateLimiter, RateLimitExceededError
from src.services.scraper_manager import ScraperManager
from src.utils.diagnostics.task_exceptions import TaskSuccess, TaskPauseForRateLimit
from src.services.task_manager import TaskStatus
# 单集及批量条目的业务直接交给 Workflow，任务只管理执行状态。
from src.workflows.manual_import import execute_manual_import
from src.workflows.manual_import_item import import_manual_item

logger = logging.getLogger(__name__)


async def manual_import_task(
    sourceId: int, animeId: int, title: Optional[str], episodeIndex: int, content: str, providerName: str,
    progress_callback: Callable, session: AsyncSession, manager: ScraperManager, rate_limiter: RateLimiter,
    config_service = None, scraperProvider: Optional[str] = None
):
    """后台任务：从URL手动导入弹幕。

    scraperProvider: 自定义源 URL 导入时，前端解析出的真实平台名（如 'bilibili'）。
                     有此参数时，跳过 XML 处理，直接用 scraperProvider 对应的 scraper 抓取，
                     但 DB 写入仍绑定到 sourceId/animeId。
    """

    # 入口只适配任务控制流，解析、抓取与保存不保留重复实现。
    try:
        message = await execute_manual_import(
            sourceId, animeId, title, episodeIndex, content, providerName,
            progress_callback, manager, rate_limiter, config_service, scraperProvider,
        )
    except RateLimitExceededError as exc:
        raise TaskPauseForRateLimit(
            retry_after_seconds=exc.retry_after_seconds,
            message=f"速率受限，将在 {exc.retry_after_seconds:.0f} 秒后自动重试...",
        ) from exc
    raise TaskSuccess(message)


async def batch_manual_import_task(
    sourceId: int, animeId: int, providerName: str, items: List[BatchManualImportItem],
    progress_callback: Callable, session: AsyncSession, manager: ScraperManager, rate_limiter: RateLimiter,
    scraperProvider: Optional[str] = None
):
    """后台任务：批量手动导入弹幕。

    scraperProvider: 自定义源批量 URL 导入时（如 B站合集），前端/调用方解析出的真实平台名
                     （如 'bilibili'）。有此参数时，即便 providerName='custom'，也按 URL 逐个调
                     scraperProvider 对应的 scraper 的 get_id_from_url 抓取弹幕，DB 写入仍绑定到
                     sourceId/animeId（与单集 manual_import_task 的 scraperProvider 语义一致）。
    """

    total_items = len(items)
    logger.info(f"开始批量手动导入任务: sourceId={sourceId}, provider='{providerName}', items={total_items}")
    await progress_callback(5, f"准备批量导入 {total_items} 个条目...")

    total_added_comments = 0
    failed_items = 0
    skipped_items = 0

    i = 0
    while i < total_items:
        item = items[i]
        progress = 5 + int(((i + 1) / total_items) * 90) if total_items > 0 else 95
        # 修正：使用 getattr 安全地访问可能不存在的 'title' 属性，
        # 以修复当请求体中的项目不包含 title 字段时引发的 AttributeError。
        # 这提供了向后兼容性，并使 title 字段成为可选。
        item_desc = getattr(item, 'title', None) or f"第 {item.episodeIndex} 集"
        await progress_callback(progress, f"正在处理: {item_desc} ({i+1}/{total_items})")

        try:
            # 单条业务返回统计值，任务只负责批次进度和重试。
            added_count, skipped, empty = await import_manual_item(
                sourceId, animeId, providerName, item, item_desc,
                manager, rate_limiter, scraperProvider,
            )
            total_added_comments += added_count
            skipped_items += int(skipped)
            failed_items += int(empty)
            i += 1 # 成功处理，移动到下一个
        except RuntimeError as e:
            # 保存 Workflow 已完成自身回滚及补偿，不操作任务管理器传入的会话。
            logger.error(f"运行时错误，跳过条目 '{item_desc}': {str(e)}")
            failed_items += 1
            i += 1
            continue
        except RateLimitExceededError as e:
            logger.warning(f"批量导入任务因达到速率限制而暂停: {e}")
            await progress_callback(progress, f"速率受限，将在 {e.retry_after_seconds:.0f} 秒后自动重试...", status=TaskStatus.PAUSED)
            await asyncio.sleep(e.retry_after_seconds)
            continue # 不增加 i，以便重试当前条目
        except Exception as e:
            logger.error(f"处理批量导入条目 '{item_desc}' 时失败: {e}", exc_info=True)
            failed_items += 1
            i += 1 # 处理失败，移动到下一个

    comment_part = f"共获取 {total_added_comments} 条弹幕" if total_added_comments > 0 else "暂无弹幕数据"
    final_message = f"批量导入完成。共处理 {total_items} 个条目，{comment_part}。"
    if skipped_items > 0:
        final_message += f" {skipped_items} 个因已存在而被跳过。"
    if failed_items > 0:
        final_message += f" {failed_items} 个条目处理失败。"
    raise TaskSuccess(final_message)

