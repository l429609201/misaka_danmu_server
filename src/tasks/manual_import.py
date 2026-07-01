"""手动导入任务模块"""
import asyncio
import logging
from typing import Callable, Optional, List
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.db import crud, orm_models, models, ConfigManager
from src.services import ScraperManager, TaskSuccess, TaskPauseForRateLimit, TaskStatus
from src.rate_limiter import RateLimiter, RateLimitExceededError
from src.utils import clean_xml_string

logger = logging.getLogger(__name__)


# 延迟导入辅助函数
def _get_parse_xml_content():
    from .xml_utils import parse_xml_content
    return parse_xml_content

def _get_convert_text_danmaku_to_xml():
    from .xml_utils import convert_text_danmaku_to_xml
    return convert_text_danmaku_to_xml


async def manual_import_task(
    sourceId: int, animeId: int, title: Optional[str], episodeIndex: int, content: str, providerName: str,
    progress_callback: Callable, session: AsyncSession, manager: ScraperManager, rate_limiter: RateLimiter,
    config_manager = None, scraperProvider: Optional[str] = None
):
    """后台任务：从URL手动导入弹幕。

    scraperProvider: 自定义源 URL 导入时，前端解析出的真实平台名（如 'bilibili'）。
                     有此参数时，跳过 XML 处理，直接用 scraperProvider 对应的 scraper 抓取，
                     但 DB 写入仍绑定到 sourceId/animeId。
    """
    _parse_xml_content = _get_parse_xml_content()
    _convert_text_danmaku_to_xml = _get_convert_text_danmaku_to_xml()

    logger.info(f"开始手动导入任务: sourceId={sourceId}, title='{title or '未提供'}' ({providerName}){f', scraperProvider={scraperProvider}' if scraperProvider else ''}")
    await progress_callback(10, "正在准备导入...")

    try:
        # Case 1: Custom source with XML data（scraperProvider 存在时跳过，走 Case 2 URL 路径）
        if providerName == 'custom' and not scraperProvider:
            # 新增：自动检测内容格式。如果不是XML，则尝试从纯文本格式转换。
            content_to_parse = content.strip()
            if not content_to_parse.startswith('<'):
                logger.info("检测到非XML格式的自定义内容，正在尝试从纯文本格式转换...")
                content_to_parse = _convert_text_danmaku_to_xml(content_to_parse)
            await progress_callback(20, "正在解析XML文件...")
            cleaned_content = clean_xml_string(content_to_parse)
            comments = _parse_xml_content(cleaned_content)
            if not comments:
                raise TaskSuccess("未从XML中解析出任何弹幕。")

            await progress_callback(80, "正在写入数据库...")
            final_title = title if title else f"第 {episodeIndex} 集"
            episode_db_id = await crud.create_episode_if_not_exists(session, animeId, sourceId, episodeIndex, final_title, "from_xml", "custom_xml")
            added_count = await crud.save_danmaku_for_episode(session, episode_db_id, comments, config_manager)
            await session.commit()
            raise TaskSuccess(f"手动导入完成，从XML共获取 {added_count} 条弹幕。" if added_count > 0 else "手动导入完成，XML中暂无有效弹幕。")

        # Case 2: Scraper source with URL
        # 自定义源 URL 导入时，scraperProvider 为前端解析出的真实平台名
        effective_provider = scraperProvider if scraperProvider else providerName
        scraper = manager.get_scraper(effective_provider)
        if not hasattr(scraper, 'get_id_from_url'):
            raise NotImplementedError(f"搜索源 '{effective_provider}' 不支持从URL手动导入。")

        provider_episode_id = await scraper.get_id_from_url(content)
        if not provider_episode_id:
            raise ValueError(f"无法从URL '{content}' 中解析出有效的视频ID。")

        episode_id_for_comments = scraper.format_episode_id_for_comments(provider_episode_id)
        await progress_callback(20, f"已解析视频ID: {episode_id_for_comments}")

        # Auto-generate title if not provided
        final_title = title
        if not final_title:
            if hasattr(scraper, 'get_title_from_url'):
                try:
                    final_title = await scraper.get_title_from_url(content)
                except Exception:
                    pass # Ignore errors, fallback to default
            if not final_title:
                final_title = f"第 {episodeIndex} 集"

        try:
            await rate_limiter.check(effective_provider)
        except RuntimeError as e:
            # 配置错误（如速率限制配置验证失败），直接失败
            if "配置验证失败" in str(e):
                raise TaskSuccess(f"配置错误，任务已终止: {str(e)}")
            # 其他 RuntimeError 也应该失败
            raise
        except RateLimitExceededError as e:
            # 抛出暂停异常，让任务管理器处理
            logger.warning(f"手动导入任务因达到速率限制而暂停: {e}")
            raise TaskPauseForRateLimit(
                retry_after_seconds=e.retry_after_seconds,
                message=f"速率受限，将在 {e.retry_after_seconds:.0f} 秒后自动重试..."
            )

        comments = await scraper.get_comments(episode_id_for_comments, progress_callback=progress_callback)
        if not comments:
            raise TaskSuccess("未找到任何弹幕。")

        await rate_limiter.increment(effective_provider)

        await progress_callback(90, "正在写入数据库...")
        episode_db_id = await crud.create_episode_if_not_exists(session, animeId, sourceId, episodeIndex, final_title, content, episode_id_for_comments)
        added_count = await crud.save_danmaku_for_episode(session, episode_db_id, comments, config_manager)
        await session.commit()
        raise TaskSuccess(f"手动导入完成，共获取 {added_count} 条弹幕。" if added_count > 0 else "手动导入完成，暂无弹幕数据。")
    except TaskSuccess:
        raise
    except Exception as e:
        logger.error(f"手动导入任务失败: {e}", exc_info=True)
        raise


async def batch_manual_import_task(
    sourceId: int, animeId: int, providerName: str, items: List[models.BatchManualImportItem],
    progress_callback: Callable, session: AsyncSession, manager: ScraperManager, rate_limiter: RateLimiter,
    scraperProvider: Optional[str] = None
):
    """后台任务：批量手动导入弹幕。

    scraperProvider: 自定义源批量 URL 导入时（如 B站合集），前端/调用方解析出的真实平台名
                     （如 'bilibili'）。有此参数时，即便 providerName='custom'，也按 URL 逐个调
                     scraperProvider 对应的 scraper 的 get_id_from_url 抓取弹幕，DB 写入仍绑定到
                     sourceId/animeId（与单集 manual_import_task 的 scraperProvider 语义一致）。
    """
    _parse_xml_content = _get_parse_xml_content()
    _convert_text_danmaku_to_xml = _get_convert_text_danmaku_to_xml()

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
            # 自定义源 URL 批量导入（如 B站合集）：providerName='custom' 但传入了 scraperProvider，
            # 此时跳过 XML 解析，按 URL 逐个用真实平台 scraper 抓取，DB 仍写入当前 sourceId。
            if providerName == 'custom' and not scraperProvider:
                # 新增：在处理前，先检查分集是否已存在
                existing_episode_stmt = select(orm_models.Episode.id).where(
                    orm_models.Episode.sourceId == sourceId,
                    orm_models.Episode.episodeIndex == item.episodeIndex
                )
                existing_episode_res = await session.execute(existing_episode_stmt)
                if existing_episode_res.scalar_one_or_none() is not None:
                    logger.warning(f"批量导入条目 '{item_desc}' (集数: {item.episodeIndex}) 已存在，已跳过。")
                    skipped_items += 1
                    i += 1
                    continue

                content_to_parse = item.content.strip()
                if not content_to_parse.startswith('<'):
                    logger.info(f"批量导入条目 '{item_desc}' 检测到非XML格式，正在尝试从纯文本格式转换...")
                    content_to_parse = _convert_text_danmaku_to_xml(content_to_parse)

                cleaned_content = clean_xml_string(content_to_parse)
                comments = _parse_xml_content(cleaned_content)

                if comments:
                    final_title = getattr(item, 'title', None) or f"第 {item.episodeIndex} 集"
                    episode_db_id = await crud.create_episode_if_not_exists(session, animeId, sourceId, item.episodeIndex, final_title, "from_xml_batch", "custom_xml")

                    added_count = await crud.save_danmaku_for_episode(session, episode_db_id, comments, None)
                    total_added_comments += added_count
                else:
                    logger.warning(f"批量导入条目 '{item_desc}' 解析失败或不含弹幕，已跳过。")
                    failed_items += 1
            else:
                # effective_provider：自定义源 URL 批量导入时用 scraperProvider（真实平台），否则用 providerName
                effective_provider = scraperProvider if (providerName == 'custom' and scraperProvider) else providerName

                # 自定义源批量 URL 导入：先查重，已存在的分集跳过（与上方 XML 分支行为一致）
                if providerName == 'custom' and scraperProvider:
                    existing_episode_stmt = select(orm_models.Episode.id).where(
                        orm_models.Episode.sourceId == sourceId,
                        orm_models.Episode.episodeIndex == item.episodeIndex
                    )
                    existing_episode_res = await session.execute(existing_episode_stmt)
                    if existing_episode_res.scalar_one_or_none() is not None:
                        logger.warning(f"批量导入条目 '{item_desc}' (集数: {item.episodeIndex}) 已存在，已跳过。")
                        skipped_items += 1
                        i += 1
                        continue

                scraper = manager.get_scraper(effective_provider)
                provider_episode_id = await scraper.get_id_from_url(item.content)
                if not provider_episode_id: raise ValueError("无法解析ID")
                episode_id_for_comments = scraper.format_episode_id_for_comments(provider_episode_id)
                final_title = getattr(item, 'title', None) or f"第 {item.episodeIndex} 集"

                await rate_limiter.check(effective_provider)
                comments = await scraper.get_comments(episode_id_for_comments)

                if comments:
                    await rate_limiter.increment(effective_provider)
                    episode_db_id = await crud.create_episode_if_not_exists(session, animeId, sourceId, item.episodeIndex, final_title, item.content, episode_id_for_comments)
                    added_count = await crud.save_danmaku_for_episode(session, episode_db_id, comments, None)
                    total_added_comments += added_count

            await session.commit()
            i += 1 # 成功处理，移动到下一个
        except RuntimeError as e:
            # 配置错误（如速率限制配置验证失败），跳过当前条目
            if "配置验证失败" in str(e):
                logger.error(f"配置错误，跳过条目 '{item_desc}': {str(e)}")
                failed_items += 1
                await session.rollback()
                i += 1
                continue
            # 其他 RuntimeError 也应该跳过
            logger.error(f"运行时错误，跳过条目 '{item_desc}': {str(e)}")
            failed_items += 1
            await session.rollback()
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
            await session.rollback()
            i += 1 # 处理失败，移动到下一个

    comment_part = f"共获取 {total_added_comments} 条弹幕" if total_added_comments > 0 else "暂无弹幕数据"
    final_message = f"批量导入完成。共处理 {total_items} 个条目，{comment_part}。"
    if skipped_items > 0:
        final_message += f" {skipped_items} 个因已存在而被跳过。"
    if failed_items > 0:
        final_message += f" {failed_items} 个条目处理失败。"
    raise TaskSuccess(final_message)

