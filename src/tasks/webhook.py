"""Webhook任务模块"""
import asyncio
import json
import logging
from typing import Callable, Optional, List
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from thefuzz import fuzz
from fastapi import HTTPException

from src.db import crud, models, orm_models, ConfigManager
from src.core import get_now
from src.services import ScraperManager, MetadataSourceManager, TaskManager, TaskSuccess, TitleRecognitionManager
from src.ai import AIMatcherManager
from src.rate_limiter import RateLimiter
from src.utils import (
    parse_search_keyword, ai_type_and_season_mapping_and_correction,
    SearchTimer, SEARCH_TYPE_WEBHOOK
)

# ORM 模型别名
AnimeSource = orm_models.AnimeSource

logger = logging.getLogger(__name__)


# 延迟导入辅助函数
def _get_unified_search():
    from src.services.search import unified_search
    return unified_search

def _get_convert_to_chinese_title():
    from src.services.name_converter import convert_to_chinese_title
    return convert_to_chinese_title


# 延迟导入辅助函数
def _get_generic_import_task():
    from .import_core import generic_import_task
    return generic_import_task


async def run_webhook_tasks_directly_manual(
    session: AsyncSession,
    task_ids: List[int],
    task_manager: "TaskManager",
    scraper_manager: "ScraperManager",
    metadata_manager: "MetadataSourceManager",
    config_manager: "ConfigManager",
    ai_matcher_manager: "AIMatcherManager",
    rate_limiter: "RateLimiter",
    title_recognition_manager: "TitleRecognitionManager"
) -> int:
    """直接获取并执行指定的待处理Webhook任务。"""
    if not task_ids:
        return 0

    stmt = select(orm_models.WebhookTask).where(orm_models.WebhookTask.id.in_(task_ids), orm_models.WebhookTask.status == "pending")
    tasks_to_run = (await session.execute(stmt)).scalars().all()

    submitted_count = 0
    for task in tasks_to_run:
        try:
            payload = json.loads(task.payload)
            # 使用默认参数 t=task, p=payload 捕获当前循环变量的值,避免闭包问题
            task_coro = lambda s, cb, t=task, p=payload: webhook_search_and_dispatch_task(
                webhookSource=t.webhookSource, progress_callback=cb, session=s,
                manager=scraper_manager, task_manager=task_manager,
                metadata_manager=metadata_manager, config_manager=config_manager,
                ai_matcher_manager=ai_matcher_manager,
                rate_limiter=rate_limiter, title_recognition_manager=title_recognition_manager,
                **p
            )
            await task_manager.submit_task(task_coro, task.taskTitle, unique_key=task.uniqueKey)
            await session.delete(task)
            await session.commit()  # 为每个成功提交的任务单独提交删除操作
            submitted_count += 1
        except HTTPException as e:
            if e.status_code == 409:
                # 409 表示已有相同任务在队列中，视为成功并删除延迟任务
                logger.info(f"手动执行 Webhook 任务 (ID: {task.id}) 时发现相同任务已在队列中，跳过。")
                await session.delete(task)
                await session.commit()
                submitted_count += 1
            else:
                logger.error(f"手动执行 Webhook 任务 (ID: {task.id}) 时失败: {e}", exc_info=True)
                await session.rollback()
        except Exception as e:
            logger.error(f"手动执行 Webhook 任务 (ID: {task.id}) 时失败: {e}", exc_info=True)
            await session.rollback()
    return submitted_count


async def webhook_search_and_dispatch_task(
    animeTitle: str,
    mediaType: str,
    season: int,
    currentEpisodeIndex: Optional[int],
    searchKeyword: str,
    doubanId: Optional[str],
    tmdbId: Optional[str],
    imdbId: Optional[str],
    tvdbId: Optional[str],
    bangumiId: Optional[str],
    webhookSource: str,
    year: Optional[int],
    progress_callback: Callable,
    session: AsyncSession,
    manager: ScraperManager,
    task_manager: TaskManager, # type: ignore
    metadata_manager: MetadataSourceManager,
    config_manager: ConfigManager,
    ai_matcher_manager: AIMatcherManager,
    rate_limiter: RateLimiter,
    title_recognition_manager: TitleRecognitionManager,
    # 媒体库整季导入时, 可选: 指定已在媒体库中选中的分集索引列表
    selectedEpisodes: Optional[List[int]] = None,
    # 媒体服务三级 ID（用于 webhook 删除联动）
    mediaServerType: Optional[str] = None,
    mediaServerSeriesId: Optional[str] = None,
    mediaServerSeasonId: Optional[str] = None,
    mediaServerEpisodeId: Optional[str] = None,
):
    """
    Webhook 触发的后台任务：搜索所有源，找到最佳匹配，并为该匹配分发一个新的、具体的导入任务。
    """
    generic_import_task = _get_generic_import_task()

    # 🚀 V2.1.6: 创建搜索计时器
    ep_label = f"E{currentEpisodeIndex:02d}" if currentEpisodeIndex is not None else "全季"
    timer = SearchTimer(SEARCH_TYPE_WEBHOOK, f"{animeTitle} S{season:02d}{ep_label}", logger)
    timer.start()

    # 🔒 Webhook 搜索锁：防止同一作品同季的多个请求同时搜索导致重复任务
    webhook_lock_key = f"webhook-{animeTitle}-S{season}"
    lock_acquired = await manager.acquire_webhook_search_lock(webhook_lock_key)
    if not lock_acquired:
        # 已有相同作品的搜索任务在运行，直接返回成功（任务已在处理中）
        logger.info(f"Webhook 任务: '{animeTitle}' S{season:02d} 已有搜索任务在运行，跳过重复请求。")
        raise TaskSuccess(f"相同作品已有搜索任务在处理中，无需重复提交。")

    try:
        logger.info(f"Webhook 任务: 开始为 '{animeTitle}' (S{season:02d}{ep_label}) 查找最佳源...")
        await progress_callback(5, "正在检查已收藏的源...")

        # 【性能优化】AI初始化预热：如果AI映射已启用，提前开始初始化（不阻塞）
        ai_matcher_warmup_task = None
        webhook_tmdb_enabled_check = await config_manager.get("webhookEnableTmdbSeasonMapping", "true")
        if webhook_tmdb_enabled_check.lower() == "true":
            ai_matcher_warmup_task = asyncio.create_task(ai_matcher_manager.get_matcher())
            logger.debug("Webhook AI匹配器预热已启动（并行）")

        timer.step_start("查找收藏源")

        # 1. 优先查找已收藏的源 (Favorited Source)
        # 🔧 修复：先用 title + season（不带年份）查询数据库
        # 因为 webhook 传来的年份可能是单集放映年份，而不是作品首播年份
        # 例如：《凡人修仙传》TV版首播于2020年，但2025年的新集 webhook 会传 year=2025
        logger.info(f"Webhook 任务: 查找已存在的anime - 标题='{animeTitle}', 季数={season}, webhook年份={year}")

        # 先不带年份查询，看数据库中是否已有这部作品
        existing_anime = await crud.find_anime_by_title_season_year(session, animeTitle, season, None, title_recognition_manager, source=None)

        # 如果找到了已有作品，使用数据库中的年份进行后续搜索
        effective_year = year  # 默认使用 webhook 传来的年份
        if existing_anime and existing_anime.get('year'):
            db_year = existing_anime['year']
            if year and db_year != year:
                logger.info(f"Webhook 任务: 数据库年份({db_year}) 与 webhook 年份({year}) 不一致，使用数据库年份进行搜索")
                effective_year = db_year
            else:
                effective_year = db_year
        if existing_anime:
            anime_id = existing_anime['id']
            favorited_source = await crud.find_favorited_source_for_anime(session, anime_id)
            if favorited_source:
                logger.info(f"Webhook 任务: 找到已收藏的源 '{favorited_source['providerName']}'，将直接使用此源。")
                await progress_callback(10, f"找到已收藏的源: {favorited_source['providerName']}")

                # 根据来源动态生成任务标题前缀
                if webhookSource == "media_server":
                    source_prefix = "媒体库读取导入"
                elif webhookSource in ["emby", "jellyfin", "plex"]:
                    source_prefix = f"Webhook自动导入 ({webhookSource.capitalize()})"
                else:
                    source_prefix = f"Webhook自动导入 ({webhookSource})"

                task_title = f"{source_prefix}: {favorited_source['animeTitle']} - S{season:02d}{ep_label} ({favorited_source['providerName']})"
                unique_key = f"import-{favorited_source['providerName']}-{favorited_source['mediaId']}-S{season}-ep{currentEpisodeIndex}"
                task_coro = lambda session, cb: generic_import_task(
                    provider=favorited_source['providerName'], mediaId=favorited_source['mediaId'], animeTitle=favorited_source['animeTitle'], year=year,
                    mediaType=favorited_source['mediaType'], season=season, currentEpisodeIndex=currentEpisodeIndex,
                    imageUrl=favorited_source['imageUrl'], doubanId=doubanId, tmdbId=tmdbId, imdbId=imdbId, tvdbId=tvdbId, config_manager=config_manager, metadata_manager=metadata_manager,
                    bangumiId=bangumiId, rate_limiter=rate_limiter,
                    progress_callback=cb, session=session, manager=manager,
                    task_manager=task_manager,
                    title_recognition_manager=title_recognition_manager,
                    selectedEpisodes=selectedEpisodes,
                    mediaServerType=mediaServerType, mediaServerSeriesId=mediaServerSeriesId,
                    mediaServerSeasonId=mediaServerSeasonId, mediaServerEpisodeId=mediaServerEpisodeId,
                )
                try:
                    await task_manager.submit_task(task_coro, task_title, unique_key=unique_key)
                except HTTPException as e:
                    if e.status_code == 409:
                        # 409 表示已有相同任务在队列中，视为成功
                        logger.info(f"Webhook 任务: 收藏源任务已在队列中 (unique_key={unique_key})，跳过重复提交。")
                        raise TaskSuccess(f"相同任务已在处理中，无需重复提交。")
                    raise

                timer.step_end(details="找到收藏源")
                timer.finish()  # 打印计时报告
                # 根据来源动态生成成功消息
                if webhookSource == "media_server":
                    success_message = f"已为收藏源 '{favorited_source['providerName']}' 创建导入任务。"
                else:
                    success_message = f"Webhook: 已为收藏源 '{favorited_source['providerName']}' 创建导入任务。"
                raise TaskSuccess(success_message)

        timer.step_end(details="无收藏源")

        # 2. 如果没有收藏源，则并发搜索所有启用的源
        logger.info(f"Webhook 任务: 未找到收藏源，开始并发搜索所有启用的源...")
        await progress_callback(20, "并发搜索所有源...")

        timer.step_start("关键词解析与预处理")
        parsed_keyword = parse_search_keyword(searchKeyword)
        original_title = parsed_keyword["title"]
        season_to_filter = parsed_keyword.get("season") or season
        episode_to_filter = parsed_keyword.get("episode") or currentEpisodeIndex

        # 2.1 Webhook AI映射配置检查
        webhook_tmdb_enabled = await config_manager.get("webhookEnableTmdbSeasonMapping", "true")
        if webhook_tmdb_enabled.lower() != "true":
            logger.info("○ Webhook 统一AI映射: 功能未启用")

        # 🚀 名称转换功能 - 检测非中文标题并尝试转换为中文（在预处理规则之前执行）
        # 创建一个虚拟用户用于元数据调用
        webhook_user = models.User(id=0, username="webhook")
        convert_to_chinese_title = _get_convert_to_chinese_title()
        converted_title, conversion_applied = await convert_to_chinese_title(
            original_title,
            config_manager,
            metadata_manager,
            ai_matcher_manager,
            webhook_user
        )
        # 🔧 用于匹配和排序的标题：
        # - 如果名称转换开关开启且转换成功，使用转换后的标题
        # - 否则使用原始标题（animeTitle）
        if conversion_applied:
            logger.info(f"✓ Webhook 名称转换: '{original_title}' → '{converted_title}'")
            original_title = converted_title  # 更新 original_title 用于后续搜索
            match_title = converted_title     # 使用转换后的标题进行匹配
        else:
            match_title = animeTitle          # 使用原始标题进行匹配

        # 应用与 WebUI 一致的标题预处理规则
        search_title = original_title
        if title_recognition_manager:
            (
                processed_title,
                processed_episode,
                processed_season,
                preprocessing_applied,
            ) = await title_recognition_manager.apply_search_preprocessing(
                original_title, episode_to_filter, season_to_filter
            )
            if preprocessing_applied:
                search_title = processed_title
                logger.info(
                    f"✓ Webhook搜索预处理: '{original_title}' -> '{search_title}'"
                )
                if processed_episode != episode_to_filter:
                    logger.info(
                        f"✓ Webhook集数预处理: {episode_to_filter} -> {processed_episode}"
                    )
                    episode_to_filter = processed_episode
                if processed_season != season_to_filter:
                    logger.info(
                        f"✓ Webhook季度预处理: {season_to_filter} -> {processed_season}"
                    )
                    season_to_filter = processed_season
            else:
                logger.info(f"○ Webhook搜索预处理未生效: '{original_title}'")
        else:
            logger.info("○ 未配置标题识别管理器，跳过Webhook搜索预处理。")

        # 构造 episode_info
        episode_info = (
            {"season": season_to_filter, "episode": episode_to_filter}
            if episode_to_filter is not None
            else {"season": season_to_filter}
        )

        logger.info(f"Webhook 任务: 已将搜索词 '{searchKeyword}' 解析为标题 '{search_title}' 进行搜索。")
        timer.step_end()

        timer.step_start("统一搜索")
        # 使用统一的搜索函数（与 WebUI 搜索保持一致）
        unified_search = _get_unified_search()
        all_search_results = await unified_search(
            search_term=search_title,
            session=session,
            scraper_manager=manager,
            metadata_manager=metadata_manager,
            use_alias_expansion=True,
            use_alias_filtering=True,
            use_title_filtering=True,
            use_source_priority_sorting=True,
            progress_callback=None,
            episode_info=episode_info,
            alias_similarity_threshold=70,
        )
        # 收集单源搜索耗时信息（分组显示）
        from src.utils.search_timer import SubStepTiming
        source_timing_sub_steps = []
        for name, dur, cnt in manager.last_search_timing:
            if name.startswith("补充:"):
                source_timing_sub_steps.append(
                    SubStepTiming(name=name[3:], duration_ms=dur, result_count=cnt, group="补充源")
                )
            else:
                source_timing_sub_steps.append(
                    SubStepTiming(name=name, duration_ms=dur, result_count=cnt, group="弹幕源")
                )
        timer.step_end(details=f"{len(all_search_results)}个结果", sub_steps=source_timing_sub_steps)

        if not all_search_results:
            timer.finish()  # 打印计时报告
            raise ValueError(f"未找到 '{match_title}' 的任何可用源。")

        # 使用统一的AI类型和季度映射修正函数
        if webhook_tmdb_enabled.lower() == "true":
            try:
                timer.step_start("AI映射修正")
                # 【性能优化】使用预热的AI匹配器
                ai_matcher = None
                if ai_matcher_warmup_task:
                    ai_matcher = await ai_matcher_warmup_task
                    ai_matcher_warmup_task = None  # 清空task，避免重复await
                else:
                    ai_matcher = await ai_matcher_manager.get_matcher()
                if ai_matcher:
                    logger.info(f"○ Webhook 开始统一AI映射修正: '{original_title}' ({len(all_search_results)} 个结果)")

                    # 使用新的统一函数进行类型和季度修正
                    mapping_result = await ai_type_and_season_mapping_and_correction(
                        search_title=original_title,
                        search_results=all_search_results,
                        metadata_manager=metadata_manager,
                        ai_matcher=ai_matcher,
                        logger=logger,
                        similarity_threshold=60.0
                    )

                    # 应用修正结果
                    if mapping_result['total_corrections'] > 0:
                        logger.info(f"✓ Webhook 统一AI映射成功: 总计修正了 {mapping_result['total_corrections']} 个结果")
                        logger.info(f"  - 类型修正: {len(mapping_result['type_corrections'])} 个")
                        logger.info(f"  - 季度修正: {len(mapping_result['season_corrections'])} 个")

                        # 更新搜索结果（已经直接修改了all_search_results）
                        all_search_results = mapping_result['corrected_results']
                        timer.step_end(details=f"修正{mapping_result['total_corrections']}个")
                    else:
                        logger.info(f"○ Webhook 统一AI映射: 未找到需要修正的信息")
                        timer.step_end(details="无修正")
                else:
                    logger.warning("○ Webhook AI映射: AI匹配器未启用或初始化失败")
                    timer.step_end(details="匹配器未启用")

            except Exception as e:
                logger.warning(f"Webhook 统一AI映射任务执行失败: {e}")
                timer.step_end(details=f"失败: {e}")
        else:
            logger.info("○ Webhook 统一AI映射: 功能未启用")

        # 3. 根据标题关键词修正媒体类型（与 WebUI 一致）
        from src.utils import is_movie_by_title

        for item in all_search_results:
            if item.type == "tv_series" and is_movie_by_title(item.title):
                logger.info(
                    f"Webhook: 标题 '{item.title}' 包含电影关键词，类型从 'tv_series' 修正为 'movie'。"
                )
                item.type = "movie"

        # 4. 如果搜索词中明确指定了季度，对结果进行过滤（与 WebUI 一致）
        # 注意：电影类型不进行季度过滤
        if season_to_filter and season_to_filter > 0 and mediaType != "movie":
            original_count = len(all_search_results)
            # 当指定季度时，我们只关心电视剧类型
            filtered_by_type = [item for item in all_search_results if item.type == "tv_series"]

            # 然后在电视剧类型中，我们按季度号过滤
            filtered_by_season = [
                item for item in filtered_by_type if item.season == season_to_filter
            ]

            logger.info(
                f"Webhook: 根据指定的季度 ({season_to_filter}) 进行过滤，从 {original_count} 个结果中保留了 {len(filtered_by_season)} 个。"
            )
            all_search_results = filtered_by_season

        timer.step_start("结果排序与匹配")
        # 5. 使用加权总分制选择最佳匹配项
        ordered_settings = await crud.get_all_scraper_settings(session)
        provider_order = {s['providerName']: s['displayOrder'] for s in ordered_settings}

        # 添加调试日志（合并为一条）
        _pre_sort_lines = [f"Webhook 任务: 排序前 media_type='{mediaType}', 共 {len(all_search_results)} 个结果:"] + \
            [f"  {i+1}. '{item.title}' ({item.provider}, {item.type})" for i, item in enumerate(all_search_results[:5])]
        logger.info("\n".join(_pre_sort_lines))

        # 🔧 查询库内已有源：搜索结果中哪些 provider+mediaId 已存在于 AnimeSource 表中
        existing_source_keys = set()
        if all_search_results:
            for result in all_search_results:
                stmt = (
                    select(AnimeSource.id)
                    .where(
                        AnimeSource.providerName == result.provider,
                        AnimeSource.mediaId == result.mediaId
                    )
                    .limit(1)
                )
                result_row = await session.execute(stmt)
                if result_row.scalar_one_or_none() is not None:
                    existing_source_keys.add(f"{result.provider}:{result.mediaId}")
            if existing_source_keys:
                logger.info(f"Webhook 任务: 发现 {len(existing_source_keys)} 个库内已有源: {existing_source_keys}")

        # 🔧 加权总分排序（替代旧的 tuple 字典序排序）
        # 所有因素贡献到一个总分，避免 tuple 字典序导致后面的因素成为死代码
        # 🔧 使用 effective_year（数据库年份优先）进行排序
        # 🔧 使用 match_title（名称转换后的标题）进行匹配
        normalized_match = match_title.replace("：", ":").replace(" ", "").strip()

        def _compute_webhook_score(item):
            """计算单个搜索结果的加权总分"""
            score = 0
            item_title_stripped = item.title.strip()
            match_title_stripped = match_title.strip()

            # 1. 完全匹配标题: +10000
            title_exact = item_title_stripped == match_title_stripped
            if title_exact:
                score += 10000

            # 2. 去标点完全匹配: +5000
            normalized_item = item.title.replace("：", ":").replace(" ", "").strip()
            if normalized_item == normalized_match:
                score += 5000

            # 3. 高相似度(>98%)且标题长度差异不大: +2000
            token_sort = fuzz.token_sort_ratio(match_title, item.title)
            len_diff = abs(len(item.title) - len(match_title))
            if token_sort > 98 and len_diff <= 10:
                score += 2000

            # 4. 较高相似度(>95%)且标题长度差异不大: +1000
            if token_sort > 95 and len_diff <= 20:
                score += 1000

            # 5. 长期连载作品优先: +800
            if (title_exact and effective_year is not None and
                    item.year is not None and effective_year - item.year >= 3):
                score += 800

            # 6. 年份匹配: +200（webhook 年份经常不准确，降低权重）
            if effective_year is not None and item.year is not None and item.year == effective_year:
                score += 200

            # 7. 季度匹配: +100
            if season is not None and mediaType == 'tv_series' and item.season == season:
                score += 100

            # 8. 一般相似度 (>=85%时计入实际分数 0~100)
            token_set = fuzz.token_set_ratio(match_title, item.title)
            if token_set >= 85:
                score += token_set

            # 9. 标题长度差异惩罚
            score -= len_diff * 2

            # 10. 年份不匹配惩罚: -200（webhook 年份经常不准确，降低权重）
            if effective_year is not None and item.year is not None and item.year != effective_year:
                score -= 200

            # 11. 源优先级加分 (displayOrder 越小越好，order=1 → +940, order=2 → +880, 相邻差60)
            order = provider_order.get(item.provider, 999)
            score += max(0, 1000 - order * 60)

            # 12. 🆕 库内已有源加分: +3000
            source_key = f"{item.provider}:{item.mediaId}"
            if source_key in existing_source_keys:
                score += 3000

            return score

        all_search_results.sort(key=_compute_webhook_score, reverse=True)

        # 添加排序后的调试日志（合并为一条，显示总分和库内已有状态）
        _sort_lines = [f"Webhook 任务: 排序后共 {len(all_search_results)} 个结果 (effective_year={effective_year}, match_title='{match_title}'):"]
        for i, item in enumerate(all_search_results[:5]):
            item_score = _compute_webhook_score(item)
            title_match = "✓" if item.title.strip() == match_title.strip() else "✗"
            year_match = "✓" if effective_year is not None and item.year is not None and item.year == effective_year else ("✗" if effective_year is not None and item.year is not None else "-")
            is_long_running = (
                item.title.strip() == match_title.strip() and
                effective_year is not None and
                item.year is not None and
                effective_year - item.year >= 3
            )
            long_running_mark = "📺" if is_long_running else ""
            source_key = f"{item.provider}:{item.mediaId}"
            in_library = "📚" if source_key in existing_source_keys else ""
            similarity = fuzz.token_set_ratio(match_title, item.title)
            year_info = f"年份: {item.year}" if item.year else "年份: 未知"
            src_order = provider_order.get(item.provider, 999)
            _sort_lines.append(f"  {i+1}. [{item_score}分] '{item.title}' ({item.provider}[#{src_order}], {item.type}, {year_info}, 年份匹配: {year_match}, 标题匹配: {title_match}, 相似度: {similarity}%) {long_running_mark}{in_library}")
        logger.info("\n".join(_sort_lines))

        # 使用AIMatcherManager进行AI匹配
        best_match = None
        ai_selected_index = None

        if await ai_matcher_manager.is_enabled():
            logger.info("Webhook 任务: AI匹配已启用，开始匹配...")
            try:
                # 构建查询信息（使用 effective_year 而不是 webhook 的 year）
                # 🔧 使用 match_title（名称转换后的标题）进行 AI 匹配
                query_info = {
                    'title': match_title,
                    'season': season if mediaType == 'tv_series' else None,
                    'episode': currentEpisodeIndex,
                    'year': effective_year,  # 使用数据库年份优先
                    'type': mediaType
                }

                # 获取精确标记信息
                favorited_info = {}

                for result in all_search_results:
                    # 查找是否有相同provider和mediaId的源被标记
                    stmt = (
                        select(AnimeSource.isFavorited)
                        .where(
                            AnimeSource.providerName == result.provider,
                            AnimeSource.mediaId == result.mediaId
                        )
                        .limit(1)
                    )
                    result_row = await session.execute(stmt)
                    is_favorited = result_row.scalar_one_or_none()
                    if is_favorited:
                        key = f"{result.provider}:{result.mediaId}"
                        favorited_info[key] = True

                # 使用AIMatcherManager进行匹配
                ai_selected_index = await ai_matcher_manager.select_best_match(
                    query_info, all_search_results, favorited_info
                )

                if ai_selected_index is not None:
                    best_match = all_search_results[ai_selected_index]
                    logger.info(f"Webhook 任务: AI匹配成功选择: {best_match.provider} - {best_match.title}")
                else:
                    # 检查是否启用传统匹配兜底
                    ai_fallback_enabled = (await config_manager.get("aiFallbackEnabled", "true")).lower() == 'true'
                    if ai_fallback_enabled:
                        logger.info("Webhook 任务: AI匹配未找到合适结果，降级到传统匹配")
                    else:
                        logger.warning("Webhook 任务: AI匹配未找到合适结果，且传统匹配兜底已禁用")
                        raise ValueError("AI匹配失败且传统匹配兜底已禁用")

            except Exception as e:
                # 检查是否启用传统匹配兜底
                ai_fallback_enabled = (await config_manager.get("aiFallbackEnabled", "true")).lower() == 'true'
                if ai_fallback_enabled:
                    logger.error(f"Webhook 任务: AI匹配失败，降级到传统匹配: {e}")
                else:
                    logger.error(f"Webhook 任务: AI匹配失败，且传统匹配兜底已禁用: {e}")
                    raise ValueError(f"AI匹配失败且传统匹配兜底已禁用: {e}")
                ai_selected_index = None

        # 如果AI选择成功，使用AI选择的结果
        if best_match is not None:
            logger.info(f"Webhook 任务: 使用AI选择的结果: {best_match.provider} - {best_match.title}")
            await progress_callback(50, f"在 {best_match.provider} 中找到最佳匹配项")

            current_time = get_now().strftime("%H:%M:%S")
            # 根据来源动态生成任务标题前缀
            if webhookSource == "media_server":
                source_prefix = "媒体库读取导入"
            elif webhookSource in ["emby", "jellyfin", "plex"]:
                source_prefix = f"Webhook自动导入 ({webhookSource.capitalize()})"
            else:
                source_prefix = f"Webhook自动导入 ({webhookSource})"

            if mediaType == "tv_series":
                task_title = f"{source_prefix}: {best_match.title} - S{season:02d}{ep_label} ({best_match.provider}) [{current_time}]"
            else:
                task_title = f"{source_prefix}: {best_match.title} ({best_match.provider}) [{current_time}]"
            unique_key = f"import-{best_match.provider}-{best_match.mediaId}-S{season}-ep{currentEpisodeIndex}"

            # 修正：优先使用搜索结果的年份，如果搜索结果没有年份则使用webhook传入的年份
            final_year = best_match.year if best_match.year is not None else year
            task_coro = lambda session, cb: generic_import_task(
                provider=best_match.provider, mediaId=best_match.mediaId, year=final_year,
                animeTitle=best_match.title, mediaType=best_match.type,
                season=season, currentEpisodeIndex=currentEpisodeIndex, imageUrl=best_match.imageUrl, config_manager=config_manager, metadata_manager=metadata_manager,
                doubanId=doubanId, tmdbId=tmdbId, imdbId=imdbId, tvdbId=tvdbId, bangumiId=bangumiId, rate_limiter=rate_limiter,
                progress_callback=cb, session=session, manager=manager,
                task_manager=task_manager,
                title_recognition_manager=title_recognition_manager,
                selectedEpisodes=selectedEpisodes,
                mediaServerType=mediaServerType, mediaServerSeriesId=mediaServerSeriesId,
                mediaServerSeasonId=mediaServerSeasonId, mediaServerEpisodeId=mediaServerEpisodeId,
            )
            try:
                await task_manager.submit_task(task_coro, task_title, unique_key=unique_key)
            except HTTPException as e:
                if e.status_code == 409:
                    logger.info(f"Webhook 任务: AI匹配任务已在队列中 (unique_key={unique_key})，跳过重复提交。")
                    raise TaskSuccess(f"相同任务已在处理中，无需重复提交。")
                raise

            timer.step_end(details="AI匹配成功")
            timer.finish()  # 打印计时报告
            # 根据来源动态生成成功消息
            if webhookSource == "media_server":
                success_message = f"已为源 '{best_match.provider}' 创建导入任务。"
            else:
                success_message = f"Webhook: 已为源 '{best_match.provider}' 创建导入任务。"
            raise TaskSuccess(success_message)

        # 传统匹配: 优先查找精确标记源，其次查找库内已有源 (需验证类型匹配和标题相似度)
        favorited_match = None
        existing_source_match = None  # 🆕 库内已有但未标记精确的源
        target_type = "movie" if mediaType == "movie" else "tv_series"

        for result in all_search_results:
            # 查找是否有相同provider和mediaId的源存在于库中
            stmt = (
                select(AnimeSource.isFavorited)
                .where(
                    AnimeSource.providerName == result.provider,
                    AnimeSource.mediaId == result.mediaId
                )
                .limit(1)
            )
            result_row = await session.execute(stmt)
            is_favorited = result_row.scalar_one_or_none()

            if is_favorited is not None:
                # 源存在于库中，验证类型匹配和标题相似度
                type_matched = result.type == target_type
                similarity = fuzz.token_set_ratio(match_title, result.title)

                if is_favorited:
                    # 精确标记源（最高优先级）
                    logger.info(f"Webhook 任务: 找到精确标记源: {result.provider} - {result.title} "
                               f"(类型: {result.type}, 类型匹配: {'✓' if type_matched else '✗'}, 相似度: {similarity}%)")
                    if type_matched and similarity >= 70:
                        favorited_match = result
                        logger.info(f"Webhook 任务: 精确标记源验证通过 (类型匹配: ✓, 相似度: {similarity}% >= 70%)")
                        break
                    else:
                        logger.warning(f"Webhook 任务: 精确标记源验证失败 (类型匹配: {'✓' if type_matched else '✗'}, "
                                     f"相似度: {similarity}% {'<' if similarity < 70 else '>='} 70%)，跳过")
                elif existing_source_match is None:
                    # 🆕 库内已有源（次优先级，只记录第一个通过验证的）
                    logger.info(f"Webhook 任务: 找到库内已有源: {result.provider} - {result.title} "
                               f"(类型: {result.type}, 类型匹配: {'✓' if type_matched else '✗'}, 相似度: {similarity}%)")
                    if type_matched and similarity >= 70:
                        existing_source_match = result
                        logger.info(f"Webhook 任务: 库内已有源验证通过 (类型匹配: ✓, 相似度: {similarity}% >= 70%)")
                    else:
                        logger.info(f"Webhook 任务: 库内已有源验证失败，继续查找")

        # 检查是否启用顺延机制
        fallback_enabled = (await config_manager.get("webhookFallbackEnabled", "false")).lower() == 'true'

        if favorited_match:
            best_match = favorited_match
            logger.info(f"Webhook 任务: 使用精确标记源: {best_match.provider} - {best_match.title}")
        elif existing_source_match:
            # 🆕 使用库内已有源（次优先级）
            best_match = existing_source_match
            logger.info(f"Webhook 任务: 使用库内已有源: {best_match.provider} - {best_match.title}")
        elif not fallback_enabled:
            # 顺延机制关闭，验证第一个结果是否满足条件
            if all_search_results:
                first_result = all_search_results[0]
                # 🔧 使用 match_title（名称转换后的标题）进行相似度计算
                type_matched = first_result.type == target_type
                similarity = fuzz.token_set_ratio(match_title, first_result.title)

                # 必须满足：类型匹配 AND 相似度 >= 70%
                if type_matched and similarity >= 70:
                    best_match = first_result
                    logger.info(f"Webhook 任务: 传统匹配成功: {first_result.provider} - {first_result.title} "
                               f"(类型匹配: ✓, 相似度: {similarity}%)")
                else:
                    best_match = None
                    logger.warning(f"Webhook 任务: 传统匹配失败: 第一个结果不满足条件 "
                                 f"(类型匹配: {'✓' if type_matched else '✗'}, 相似度: {similarity}%, 要求: ≥70%)")
            else:
                best_match = None
                logger.warning(f"Webhook 任务: 传统匹配失败: 没有搜索结果")

        if best_match is not None:
            await progress_callback(50, f"在 {best_match.provider} 中找到最佳匹配项")

            current_time = get_now().strftime("%H:%M:%S")
            # 根据来源动态生成任务标题前缀
            if webhookSource == "media_server":
                source_prefix = "媒体库读取导入"
            elif webhookSource in ["emby", "jellyfin", "plex"]:
                source_prefix = f"Webhook自动导入 ({webhookSource.capitalize()})"
            else:
                source_prefix = f"Webhook自动导入 ({webhookSource})"

            if mediaType == "tv_series":
                task_title = f"{source_prefix}: {best_match.title} - S{season:02d}{ep_label} ({best_match.provider}) [{current_time}]"
            else:
                task_title = f"{source_prefix}: {best_match.title} ({best_match.provider}) [{current_time}]"
            unique_key = f"import-{best_match.provider}-{best_match.mediaId}-S{season}-ep{currentEpisodeIndex}"

            # 修正：优先使用搜索结果的年份，如果搜索结果没有年份则使用webhook传入的年份
            final_year = best_match.year if best_match.year is not None else year
            task_coro = lambda session, cb: generic_import_task(
                provider=best_match.provider, mediaId=best_match.mediaId, year=final_year,
                animeTitle=best_match.title, mediaType=best_match.type,
                season=season, currentEpisodeIndex=currentEpisodeIndex, imageUrl=best_match.imageUrl, config_manager=config_manager, metadata_manager=metadata_manager,
                doubanId=doubanId, tmdbId=tmdbId, imdbId=imdbId, tvdbId=tvdbId, bangumiId=bangumiId, rate_limiter=rate_limiter,
                progress_callback=cb, session=session, manager=manager,
                task_manager=task_manager,
                title_recognition_manager=title_recognition_manager,
                selectedEpisodes=selectedEpisodes,
                mediaServerType=mediaServerType, mediaServerSeriesId=mediaServerSeriesId,
                mediaServerSeasonId=mediaServerSeasonId, mediaServerEpisodeId=mediaServerEpisodeId,
            )
            try:
                await task_manager.submit_task(task_coro, task_title, unique_key=unique_key)
            except HTTPException as e:
                if e.status_code == 409:
                    logger.info(f"Webhook 任务: 传统匹配任务已在队列中 (unique_key={unique_key})，跳过重复提交。")
                    raise TaskSuccess(f"相同任务已在处理中，无需重复提交。")
                raise

            timer.step_end(details="传统匹配成功")
            timer.finish()  # 打印计时报告
            # 根据来源动态生成成功消息
            if webhookSource == "media_server":
                success_message = f"已为源 '{best_match.provider}' 创建导入任务。"
            else:
                success_message = f"Webhook: 已为源 '{best_match.provider}' 创建导入任务。"
            raise TaskSuccess(success_message)

        # 顺延机制启用：依次验证候选源 (按分数从高到低)
        logger.info(f"🔄 Webhook 顺延机制: 已启用，共有 {len(all_search_results)} 个候选源待验证")
        for attempt, candidate in enumerate(all_search_results, 1):
            logger.info(f"→ [{attempt}/{len(all_search_results)}] 正在验证: {candidate.provider} - {candidate.title} (ID: {candidate.mediaId}, 类型: {candidate.type})")
            try:
                scraper = manager.get_scraper(candidate.provider)
                if not scraper:
                    logger.warning(f"    {attempt}. {candidate.provider} - 无法获取scraper，跳过")
                    continue

                # 获取分集列表进行验证
                episodes = await manager.get_episodes_routed(candidate.provider, candidate.mediaId, db_media_type=candidate.type)
                if not episodes:
                    logger.warning(f"    {attempt}. {candidate.provider} - 没有分集列表，跳过")
                    continue

                # 如果是电影，只匹配电影类型的候选源
                if mediaType == "movie":
                    if candidate.type != "movie":
                        logger.warning(f"    {attempt}. {candidate.provider} - 类型不匹配 (搜索电影，但候选源是{candidate.type})，跳过")
                        continue
                    logger.info(f"    {attempt}. {candidate.provider} - 验证通过 (电影)")
                # 如果是电视剧，检查是否有目标集数
                else:
                    target_episode = None
                    for ep in episodes:
                        if ep.episodeIndex == currentEpisodeIndex:
                            target_episode = ep
                            break

                    if not target_episode:
                        logger.warning(f"    {attempt}. {candidate.provider} - 没有第 {currentEpisodeIndex} 集，跳过")
                        continue

                    logger.info(f"    {attempt}. {candidate.provider} - 验证通过")

                best_match = candidate
                break
            except Exception as e:
                logger.warning(f"    {attempt}. {candidate.provider} - 验证失败: {e}")
                continue

        if not best_match:
            logger.warning(f"Webhook 任务: 所有候选源都无法提供有效分集")
            raise ValueError(f"所有候选源都无法提供第 {currentEpisodeIndex} 集")

        # 提交导入任务
        await progress_callback(50, f"在 {best_match.provider} 中找到最佳匹配项")

        current_time = get_now().strftime("%H:%M:%S")
        # 根据来源动态生成任务标题前缀
        if webhookSource == "media_server":
            source_prefix = "媒体库读取导入"
        elif webhookSource in ["emby", "jellyfin", "plex"]:
            source_prefix = f"Webhook自动导入 ({webhookSource.capitalize()})"
        else:
            source_prefix = f"Webhook自动导入 ({webhookSource})"

        if mediaType == "tv_series":
            task_title = f"{source_prefix}: {best_match.title} - S{season:02d}{ep_label} ({best_match.provider}) [{current_time}]"
        else:
            task_title = f"{source_prefix}: {best_match.title} ({best_match.provider}) [{current_time}]"
        unique_key = f"import-{best_match.provider}-{best_match.mediaId}-S{season}-ep{currentEpisodeIndex}"

        # 修正：优先使用搜索结果的年份，如果搜索结果没有年份则使用webhook传入的年份
        final_year = best_match.year if best_match.year is not None else year
        task_coro = lambda session, cb: generic_import_task(
            provider=best_match.provider, mediaId=best_match.mediaId, year=final_year,
            animeTitle=best_match.title, mediaType=best_match.type,
            season=season, currentEpisodeIndex=currentEpisodeIndex, imageUrl=best_match.imageUrl, config_manager=config_manager, metadata_manager=metadata_manager,
            doubanId=doubanId, tmdbId=tmdbId, imdbId=imdbId, tvdbId=tvdbId, bangumiId=bangumiId, rate_limiter=rate_limiter,
            progress_callback=cb, session=session, manager=manager,
            task_manager=task_manager,
            title_recognition_manager=title_recognition_manager,
            selectedEpisodes=selectedEpisodes,
            mediaServerType=mediaServerType, mediaServerSeriesId=mediaServerSeriesId,
            mediaServerSeasonId=mediaServerSeasonId, mediaServerEpisodeId=mediaServerEpisodeId,
        )
        try:
            await task_manager.submit_task(task_coro, task_title, unique_key=unique_key)
        except HTTPException as e:
            if e.status_code == 409:
                logger.info(f"Webhook 任务: 顺延匹配任务已在队列中 (unique_key={unique_key})，跳过重复提交。")
                raise TaskSuccess(f"相同任务已在处理中，无需重复提交。")
            raise

        timer.step_end(details="顺延匹配成功")
        timer.finish()  # 打印计时报告
        # 根据来源动态生成成功消息
        if webhookSource == "media_server":
            success_message = f"已为源 '{best_match.provider}' 创建导入任务。"
        else:
            success_message = f"Webhook: 已为源 '{best_match.provider}' 创建导入任务。"
        raise TaskSuccess(success_message)
    except TaskSuccess:
        raise
    except Exception as e:
        timer.finish()  # 打印计时报告（即使失败也打印）
        logger.error(f"Webhook 搜索与分发任务发生严重错误: {e}", exc_info=True)
        raise
    finally:
        # 🔓 释放 Webhook 搜索锁
        await manager.release_webhook_search_lock(webhook_lock_key)

