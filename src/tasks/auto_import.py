"""自动搜索和导入任务模块"""
import asyncio
import logging
import traceback
from typing import Callable, Optional, Dict, Any, TYPE_CHECKING
from sqlalchemy.ext.asyncio import AsyncSession
from thefuzz import fuzz

from src.db import crud, models, ConfigManager
from src.ai import AIMatcherManager
from src.services import ScraperManager, MetadataSourceManager, TaskManager, TaskSuccess, TitleRecognitionManager
from src.rate_limiter import RateLimiter
from src.utils import (
    ai_type_and_season_mapping_and_correction,
    SearchTimer, SEARCH_TYPE_CONTROL_AUTO_IMPORT,
    is_movie_by_title,
)

logger = logging.getLogger(__name__)


def _get_auto_import_media_type():
    from src.api.control.models import AutoImportMediaType
    return AutoImportMediaType


# 延迟导入辅助函数
def _get_unified_search():
    from src.services.search import unified_search
    return unified_search

def _get_convert_to_chinese_title():
    from src.services.name_converter import convert_to_chinese_title
    return convert_to_chinese_title

def _get_parse_episode_ranges():
    from .utils import parse_episode_ranges
    return parse_episode_ranges

def _get_is_chinese_title():
    from .utils import is_chinese_title
    return is_chinese_title

def _get_is_tmdb_reverse_lookup_enabled():
    from .metadata import is_tmdb_reverse_lookup_enabled
    return is_tmdb_reverse_lookup_enabled

def _get_reverse_lookup_tmdb_chinese_title():
    from .metadata import reverse_lookup_tmdb_chinese_title
    return reverse_lookup_tmdb_chinese_title

def _get_generic_import_task():
    from .import_core import generic_import_task
    return generic_import_task


async def auto_search_and_import_task(
    payload: "models.ControlAutoImportRequest",
    progress_callback: Callable,
    session: AsyncSession,
    config_manager: ConfigManager,
    scraper_manager: ScraperManager,
    metadata_manager: MetadataSourceManager,
    task_manager: TaskManager,
    ai_matcher_manager: AIMatcherManager,
    rate_limiter: Optional[RateLimiter] = None,
    api_key: Optional[str] = None,
    title_recognition_manager: Optional[TitleRecognitionManager] = None,
    oauth_user: Optional[models.User] = None,
):
    """
    全自动搜索并导入的核心任务逻辑。
    """
    parse_episode_ranges = _get_parse_episode_ranges()
    _is_chinese_title = _get_is_chinese_title()
    _is_tmdb_reverse_lookup_enabled = _get_is_tmdb_reverse_lookup_enabled()
    _reverse_lookup_tmdb_chinese_title = _get_reverse_lookup_tmdb_chinese_title()
    generic_import_task = _get_generic_import_task()
    
    # 初始化计时器并开始计时
    timer = SearchTimer(SEARCH_TYPE_CONTROL_AUTO_IMPORT, payload.searchTerm, logger)
    timer.start()

    # 【性能优化】AI初始化预热：提前检查是否需要AI，如果需要则启动预热（不阻塞）
    ai_matcher_warmup_task = None
    try:
        auto_import_tmdb_enabled_check = await config_manager.get("autoImportEnableTmdbSeasonMapping", "false")
        if auto_import_tmdb_enabled_check.lower() == "true":
            ai_matcher_warmup_task = asyncio.create_task(ai_matcher_manager.get_matcher())
            logger.debug("全自动导入 AI匹配器预热已启动（并行）")
    except Exception as e:
        logger.warning(f"全自动导入 AI预热失败: {e}")

    try:
        # 防御性检查：确保 rate_limiter 已被正确传递。
        if rate_limiter is None:
            error_msg = "任务启动失败：内部错误（速率限制器未提供）。请检查任务提交处的代码。"
            logger.error(f"auto_search_and_import_task was called without a rate_limiter. This is a bug. Payload: {payload}")
            raise ValueError(error_msg)

        search_type = payload.searchType
        search_term = payload.searchTerm
        media_type = payload.mediaType
        season = payload.season

        await progress_callback(5, f"开始处理，类型: {search_type}, 搜索词: {search_term}")

        aliases = {search_term}
        main_title = search_term
        image_url = None
        year: Optional[int] = None
        tmdb_id, bangumi_id, douban_id, tvdb_id, imdb_id = None, None, None, None, None

        # 为后台任务创建用户上下文：优先使用提交订阅的真实用户，避免 Trakt/Bangumi OAuth 读取不到授权
        user = oauth_user or models.User(id=1, username="admin")

        # 1. 获取元数据和别名
        details: Optional[models.MetadataDetailsResponse] = None

        # 智能检测：如果 searchType 是 keyword 但 searchTerm 是数字，则尝试将其作为 TMDB ID 处理
        effective_search_type = search_type.value
        if search_type == "keyword" and search_term.isdigit():
            logger.info(f"检测到关键词 '{search_term}' 为数字，将尝试作为TMDB ID进行元数据获取...")
            effective_search_type = "tmdb"

        if effective_search_type != "keyword":
            provider_media_type = None
            if media_type:
                if effective_search_type == 'tmdb':
                    provider_media_type = 'tv' if media_type == 'tv_series' else 'movie'
                elif effective_search_type == 'tvdb':
                    provider_media_type = 'series' if media_type == 'tv_series' else 'movies'

            try:
                await progress_callback(10, f"正在从 {effective_search_type.upper()} 获取元数据...")

                # --- 修正：当 mediaType 未提供时，智能地尝试两种类型 ---
                provider_media_type_to_try = None
                if media_type:
                    if effective_search_type == 'tmdb':
                        provider_media_type_to_try = 'tv' if media_type == 'tv_series' else 'movie'
                    elif effective_search_type == 'tvdb':
                        provider_media_type_to_try = 'series' if media_type == 'tv_series' else 'movies'

                if provider_media_type_to_try:
                    details = await metadata_manager.get_details(
                        provider=effective_search_type, item_id=search_term, user=user, mediaType=provider_media_type_to_try
                    )
                else:
                    # 如果无法推断，则依次尝试 TV 和 Movie
                    logger.info(f"未提供 mediaType，将依次尝试 TV 和 Movie 类型...")
                    tv_type = 'tv' if effective_search_type == 'tmdb' else 'series'
                    details = await metadata_manager.get_details(provider=effective_search_type, item_id=search_term, user=user, mediaType=tv_type)
                    if not details:
                        logger.info(f"作为 TV/Series 未找到，正在尝试作为 Movie...")
                        movie_type = 'movie' if effective_search_type == 'tmdb' else 'movies'
                        details = await metadata_manager.get_details(provider=effective_search_type, item_id=search_term, user=user, mediaType=movie_type)
                # --- 修正结束 ---
                if not details and search_type == "keyword":
                    logger.info(f"作为TMDB ID获取元数据失败，将按原样作为关键词处理。")
            except Exception as e:
                logger.error(f"从 {effective_search_type.upper()} 获取元数据失败: {e}\n{traceback.format_exc()}")
                if search_type == "keyword":
                    logger.warning(f"尝试将关键词作为TMDB ID处理时出错，将按原样作为关键词处理。")

        if details:
            main_title = details.title or main_title
            image_url = details.imageUrl
            aliases.add(main_title)
            aliases.update(details.aliasesCn or [])
            aliases.add(details.nameEn)
            aliases.add(details.nameJp)
            tmdb_id, bangumi_id, douban_id, tvdb_id, imdb_id = (
                details.tmdbId, details.bangumiId, details.doubanId,
                details.tvdbId, details.imdbId
            )

            # TMDB反查功能：如果标题不是中文且不是TMDB搜索，尝试通过其他ID反查TMDB获取中文标题
            logger.info(f"TMDB反查检查: effective_search_type='{effective_search_type}', main_title='{main_title}', is_chinese={_is_chinese_title(main_title)}")
            if effective_search_type != 'tmdb' and main_title and not _is_chinese_title(main_title):
                # 检查TMDB反查是否启用
                tmdb_reverse_enabled = await _is_tmdb_reverse_lookup_enabled(session, effective_search_type)
                logger.info(f"TMDB反查配置检查: enabled={tmdb_reverse_enabled}, source_type='{effective_search_type}'")
                if tmdb_reverse_enabled:
                    logger.info(f"检测到非中文标题 '{main_title}'，尝试通过其他ID反查TMDB获取中文标题...")
                    # 如果是通过外部ID搜索，直接使用搜索的ID
                    lookup_tmdb_id = tmdb_id
                    lookup_imdb_id = imdb_id if effective_search_type != 'imdb' else search_term
                    lookup_tvdb_id = tvdb_id if effective_search_type != 'tvdb' else search_term
                    lookup_douban_id = douban_id if effective_search_type != 'douban' else search_term
                    lookup_bangumi_id = bangumi_id if effective_search_type != 'bangumi' else search_term

                    chinese_title = await _reverse_lookup_tmdb_chinese_title(
                        metadata_manager, user, effective_search_type, search_term,
                        lookup_tmdb_id, lookup_imdb_id, lookup_tvdb_id, lookup_douban_id, lookup_bangumi_id
                    )
                    if chinese_title:
                        logger.info(f"TMDB反查成功，使用中文标题: '{chinese_title}' (原标题: '{main_title}')")
                        main_title = chinese_title
                        aliases.add(chinese_title)
                    else:
                        logger.info(f"TMDB反查未找到中文标题，继续使用原标题: '{main_title}'")
                else:
                    logger.info(f"TMDB反查功能未启用或不支持源 '{effective_search_type}'，继续使用原标题: '{main_title}'")
            if hasattr(details, 'type') and details.type:
                media_type = _get_auto_import_media_type()(details.type)
            if hasattr(details, 'year') and details.year:
                year = details.year

            logger.info(f"正在为 '{main_title}' 从其他源获取更多别名...")
            enriched_aliases = await metadata_manager.search_aliases_from_enabled_sources(main_title, user)
            if enriched_aliases:
                aliases.update(enriched_aliases)
                logger.info(f"别名已扩充: {aliases}")

        # 2. 检查媒体库中是否已存在
        existing_anime: Optional[Dict[str, Any]] = None
        await progress_callback(20, "正在检查媒体库...")

        # 步骤 2a: 优先通过元数据ID和季度号进行精确查找
        if search_type != "keyword" and season is not None:
            id_column_map = {
                "tmdb": "tmdbId", "tvdb": "tvdbId", "imdb": "imdbId",
                "douban": "doubanId", "bangumi": "bangumiId"
            }
            id_type = id_column_map.get(search_type.value)
            if id_type:
                logger.info(f"正在通过 {search_type.upper()} ID '{search_term}' 和季度 {season} 精确查找...")
                existing_anime = await crud.find_anime_by_metadata_id_and_season(
                    session, id_type, search_term, season
                )
                if existing_anime:
                    logger.info(f"精确查找到已存在的作品: {existing_anime['title']} (ID: {existing_anime['id']})")

        # 关键修复：如果媒体类型是电影，则强制使用季度1进行查找，
        # 以匹配UI导入时为电影设置的默认季度，从而防止重复导入。
        season_for_check = season
        if media_type == 'movie' and season_for_check is None:
            season_for_check = 1
            logger.info(f"检测到媒体类型为电影，将使用默认季度 {season_for_check} 进行重复检查。")

        # 步骤 2b: 如果精确查找未找到，则回退到按标题和季度查找
        if not existing_anime:
            if search_type != "keyword":
                logger.info("通过元数据ID+季度未找到匹配项，回退到按标题查找...")

            # 如果通过ID未找到，或不是按ID搜索，则回退到按标题和季度查找
            existing_anime = await crud.find_anime_by_title_season_year(
                session, main_title, season_for_check, year, title_recognition_manager, None  # source参数暂时为None，因为这里是查找现有条目
            )

        # 关键修复：对于单集/多集导入，需要使用经过识别词处理后的集数进行检查
        if payload.episode is not None and existing_anime:
            # 解析集数字符串为列表 (支持 "1,3,5,7,9,11-13" 格式)
            requested_episodes = parse_episode_ranges(payload.episode)
            logger.info(f"检查库内是否存在请求的集数: {requested_episodes}")

            anime_id_to_use = existing_anime.get('id') or existing_anime.get('animeId')
            if anime_id_to_use:
                # 检查所有请求的集数是否都已存在
                all_exist = True
                missing_episodes = []
                for ep in requested_episodes:
                    # 应用识别词转换获取实际的集数
                    episode_to_check = ep
                    if title_recognition_manager:
                        _, converted_episode, _, _, _ = await title_recognition_manager.apply_title_recognition(main_title, ep, season_for_check)
                        if converted_episode is not None:
                            episode_to_check = converted_episode
                            logger.info(f"识别词转换: 原始集数 {ep} -> 转换后集数 {episode_to_check}")

                    episode_exists = await crud.find_episode_by_index(session, anime_id_to_use, episode_to_check)
                    if not episode_exists:
                        all_exist = False
                        missing_episodes.append(ep)

                if all_exist:
                    final_message = f"作品 '{main_title}' 的所有请求集数 {requested_episodes} 已在媒体库中，无需重复导入。"
                    logger.info(f"自动导入任务检测到所有分集已存在，任务成功结束: {final_message}")
                    raise TaskSuccess(final_message)
                else:
                    logger.info(f"作品 '{main_title}' 已存在，但部分集数不存在: {missing_episodes}。将继续执行导入流程。")
            # 如果分集不存在，即使作品存在，我们也要继续执行后续的搜索和导入逻辑。
        # 关键修复：仅当这是一个整季导入请求时，才在找到作品后立即停止。
        # 对于单集导入，即使作品存在，也需要继续执行以检查和导入缺失的单集。
        if payload.episode is None and existing_anime:
            final_message = f"作品 '{main_title}' 已在媒体库中，无需重复导入整季。"
            logger.info(f"自动导入任务检测到作品已存在（整季导入），任务成功结束: {final_message}")
            raise TaskSuccess(final_message)


        if existing_anime:
            # 修正：从 existing_anime 字典中安全地获取ID。
            # 不同的查询路径可能返回 'id' 或 'animeId' 作为键。
            # 此更改确保无论哪个键存在，我们都能正确获取ID。
            anime_id_to_use = existing_anime.get('id') or existing_anime.get('animeId')
            if not anime_id_to_use:
                raise ValueError("在已存在的作品记录中未能找到有效的ID。")

            favorited_source = await crud.find_favorited_source_for_anime(session, anime_id_to_use)
            if favorited_source:
                source_to_use = favorited_source
                logger.info(f"媒体库中已存在作品，并找到精确标记源: {source_to_use['providerName']}")
            else:
                all_sources = await crud.get_anime_sources(session, anime_id_to_use)
                if all_sources:
                    ordered_settings = await crud.get_all_scraper_settings(session)
                    provider_order = {s['providerName']: s['displayOrder'] for s in ordered_settings}
                    all_sources.sort(key=lambda s: provider_order.get(s['providerName'], 999))
                    source_to_use = all_sources[0]
                    logger.info(f"媒体库中已存在作品，选择优先级最高的源: {source_to_use['providerName']}")
                else: source_to_use = None

            if source_to_use:
                # 关键修复：如果这是一个单集/多集导入，并且我们已经确认了部分分集不存在，
                # 那么我们应该使用库内已有的源继续执行导入，而不是在这里停止。
                # 只有在整季导入时，我们才在这里停止。
                if payload.episode is None:
                    final_message = f"作品 '{main_title}' 已在媒体库中，无需重复导入。"
                    logger.info(f"自动导入任务检测到作品已存在（整季导入），任务成功结束: {final_message}")
                    raise TaskSuccess(final_message)
                else:
                    # 对于单集/多集导入，使用库内已有的源创建导入任务
                    logger.info(f"作品 '{main_title}' 已存在，使用库内源 {source_to_use['providerName']} 导入缺失的集数。")

                    # 解析多集参数
                    selected_episodes = parse_episode_ranges(payload.episode)
                    logger.info(f"解析集数参数 '{payload.episode}' -> {selected_episodes}")

                    # 获取元数据ID
                    douban_id = existing_anime.get('doubanId')
                    tmdb_id = existing_anime.get('tmdbId')
                    imdb_id = existing_anime.get('imdbId')
                    tvdb_id = existing_anime.get('tvdbId')
                    bangumi_id = existing_anime.get('bangumiId')
                    image_url = existing_anime.get('imageUrl')

                    task_coro = lambda s, cb: generic_import_task(
                        provider=source_to_use['providerName'], mediaId=source_to_use['mediaId'],
                        animeTitle=existing_anime['title'], mediaType=existing_anime.get('type', 'tv_series'),
                        season=season_for_check, year=existing_anime.get('year'),
                        config_manager=config_manager, metadata_manager=metadata_manager,
                        currentEpisodeIndex=None, imageUrl=image_url,
                        doubanId=douban_id, tmdbId=tmdb_id, imdbId=imdb_id, tvdbId=tvdb_id, bangumiId=bangumi_id,
                        progress_callback=cb, session=s, manager=scraper_manager, task_manager=task_manager,
                        rate_limiter=rate_limiter,
                        title_recognition_manager=title_recognition_manager,
                        is_fallback=False,
                        preassignedAnimeId=anime_id_to_use,
                        selectedEpisodes=selected_episodes,
                        enable_incremental_refresh=bool(payload.enableIncrementalRefresh),
                    )

                    # 构建任务标题
                    title_parts = [f"自动导入 (库内): {existing_anime['title']}"]
                    if season_for_check is not None:
                        title_parts.append(f"S{season_for_check:02d}")
                    title_parts.append(f"E{payload.episode}")
                    task_title = " ".join(title_parts)

                    # 构建unique_key
                    unique_key_parts = ["import", source_to_use['providerName'], source_to_use['mediaId']]
                    if season_for_check is not None:
                        unique_key_parts.append(f"s{season_for_check}")
                    unique_key_parts.append(f"e{payload.episode}")
                    unique_key = "-".join(unique_key_parts)

                    # 准备任务参数
                    task_parameters = {
                        "provider": source_to_use['providerName'],
                        "mediaId": source_to_use['mediaId'],
                        "animeTitle": existing_anime['title'],
                        "mediaType": existing_anime.get('type', 'tv_series'),
                        "season": season_for_check,
                        "year": existing_anime.get('year'),
                        "currentEpisodeIndex": None,
                        "selectedEpisodes": selected_episodes,
                        "imageUrl": image_url,
                        "doubanId": douban_id,
                        "tmdbId": tmdb_id,
                        "imdbId": imdb_id,
                        "tvdbId": tvdb_id,
                        "bangumiId": bangumi_id,
                        "enableIncrementalRefresh": bool(payload.enableIncrementalRefresh),
                    }

                    execution_task_id, _ = await task_manager.submit_task(
                        task_coro,
                        task_title,
                        unique_key=unique_key,
                        task_type="generic_import",
                        task_parameters=task_parameters
                    )
                    final_message = f"已使用库内源创建导入任务。执行任务ID: {execution_task_id}"
                    raise TaskSuccess(final_message)

        # 3. 如果库中不存在，则进行全网搜索
        await progress_callback(40, "媒体库未找到，开始全网搜索...")
        # 注意：搜索阶段不传递episode信息，因为scraper的search方法不需要具体集数
        # 集数信息只在导入阶段使用
        episode_info = {"season": season}

        # 使用WebUI相同的搜索逻辑：先获取元数据源别名，再进行全网搜索
        await progress_callback(30, "正在获取元数据源别名...")

        # 使用元数据源获取别名（与WebUI相同的逻辑）
        if metadata_manager:
            try:
                # 从数据库获取admin用户（使用传入的session）
                admin_user = await crud.get_user_by_username(session, "admin")
                if admin_user:
                    user_model = models.User.model_validate(admin_user)

                    logger.info("一个或多个元数据源已启用辅助搜索，开始执行...")

                    # 调用正确的方法
                    supplemental_aliases, _ = await metadata_manager.search_supplemental_sources(main_title, user_model)
                    aliases.update(supplemental_aliases)

                    logger.info(f"所有辅助搜索完成，最终别名集大小: {len(aliases)}")
                    logger.info(f"用于过滤的别名列表: {list(aliases)}")
                else:
                    logger.warning("未找到admin用户，跳过元数据源辅助搜索")
            except Exception as e:
                logger.warning(f"元数据源辅助搜索失败: {e}")

        # 🚀 名称转换功能 - 检测非中文标题并尝试转换为中文（在预处理规则之前执行）
        # 创建一个虚拟用户用于元数据调用
        auto_import_user = oauth_user or models.User(id=0, username="auto_import")
        convert_to_chinese_title = _get_convert_to_chinese_title()
        converted_title, conversion_applied = await convert_to_chinese_title(
            main_title,
            config_manager,
            metadata_manager,
            ai_matcher_manager,
            auto_import_user
        )
        if conversion_applied:
            logger.info(f"✓ 全自动导入 名称转换: '{main_title}' → '{converted_title}'")
            main_title = converted_title  # 更新 main_title 用于后续处理

        # 应用搜索预处理规则
        search_title = main_title
        search_season = season
        if title_recognition_manager:
            processed_title, processed_episode, processed_season, preprocessing_applied = await title_recognition_manager.apply_search_preprocessing(main_title, payload.episode, season)
            if preprocessing_applied:
                search_title = processed_title
                logger.info(f"✓ 应用搜索预处理: '{main_title}' -> '{search_title}'")
                # 如果集数发生了变化，更新episode_info
                if processed_episode != payload.episode:
                    logger.info(f"✓ 集数预处理: {payload.episode} -> {processed_episode}")
                    # 这里可以根据需要更新episode_info
                # 如果季数发生了变化，更新搜索季数
                if processed_season != season:
                    search_season = processed_season
                    logger.info(f"✓ 季度预处理: {season} -> {search_season}")
                    # 更新episode_info中的季数（搜索阶段不传递episode）
                    episode_info = {"season": search_season}
            else:
                logger.info(f"○ 搜索预处理未生效: '{main_title}'")

        # 创建AI类型和季度映射任务(如果启用) - 与搜索并行运行
        mapping_task = None
        auto_import_tmdb_enabled = await config_manager.get("autoImportEnableTmdbSeasonMapping", "false")
        if auto_import_tmdb_enabled.lower() == "true" and media_type != "movie":
            logger.info(f"○ 全自动导入 AI映射: 开始为 '{search_title}' 进行类型和季度映射(并行)...")

            # 获取AI匹配器(如果启用) - 【性能优化】使用预热的AI匹配器
            ai_matcher = None
            if ai_matcher_warmup_task:
                ai_matcher = await ai_matcher_warmup_task
                ai_matcher_warmup_task = None  # 清空task，避免重复await
            else:
                ai_matcher = await ai_matcher_manager.get_matcher()
            if ai_matcher:
                logger.debug("全自动导入 AI映射: 使用AI匹配器")
            else:
                logger.debug("全自动导入 AI映射: AI匹配器未启用或初始化失败")

            # 创建并行任务
            async def get_ai_mapping():
                try:
                    # 先执行搜索获取结果，然后进行AI映射
                    # 这里先返回None，实际映射在搜索后进行
                    return None
                except Exception as e:
                    logger.warning(f"全自动导入 AI映射失败: {e}")
                    return None

            mapping_task = asyncio.create_task(get_ai_mapping())
        else:
            if auto_import_tmdb_enabled.lower() != "true":
                logger.info("○ 全自动导入 AI映射: 功能未启用")
            elif media_type == "movie":
                logger.info("○ 全自动导入 AI映射: 电影类型,跳过")

        logger.info(f"将使用标题 '{search_title}' 进行全网搜索...")

        timer.step_start("弹幕源搜索")
        # 使用统一的搜索函数（与 WebUI 搜索保持一致）
        # 使用严格过滤模式和自定义别名
        # 外部控制API启用AI别名扩展（如果配置启用）
        unified_search = _get_unified_search()
        all_results = await unified_search(
            search_term=search_title,
            session=session,
            scraper_manager=scraper_manager,
            metadata_manager=metadata_manager,  # 传入metadata_manager以支持AI别名扩展
            use_alias_expansion=True,  # 启用AI别名扩展（外部控制API专用）
            use_alias_filtering=False,
            use_title_filtering=True,  # 启用标题过滤
            use_source_priority_sorting=False,  # 不排序，后面自己处理
            strict_filtering=True,  # 使用严格过滤模式
            custom_aliases=aliases,  # 传入手动获取的别名
            progress_callback=None,
            episode_info=episode_info,  # 传递分集信息（与 WebUI 一致）
            alias_similarity_threshold=70,  # 使用 70% 别名相似度阈值（与 WebUI 一致）
        )
        # 收集单源搜索耗时信息
        from src.utils.search_timer import SubStepTiming
        source_timing_sub_steps = [
            SubStepTiming(name=name, duration_ms=dur, result_count=cnt)
            for name, dur, cnt in scraper_manager.last_search_timing
        ]
        timer.step_end(details=f"{len(all_results)}个结果", sub_steps=source_timing_sub_steps)

        logger.info(f"搜索完成，共 {len(all_results)} 个结果")

        # 使用统一的AI类型和季度映射修正函数
        if auto_import_tmdb_enabled.lower() == "true" and media_type != "movie":
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
                    logger.info(f"○ 全自动导入 开始统一AI映射修正: '{search_title}' ({len(all_results)} 个结果)")

                    # 使用新的统一函数进行类型和季度修正
                    mapping_result = await ai_type_and_season_mapping_and_correction(
                        search_title=search_title,
                        search_results=all_results,
                        metadata_manager=metadata_manager,
                        ai_matcher=ai_matcher,
                        logger=logger,
                        similarity_threshold=60.0
                    )

                    # 应用修正结果
                    if mapping_result['total_corrections'] > 0:
                        logger.info(f"✓ 全自动导入 统一AI映射成功: 总计修正了 {mapping_result['total_corrections']} 个结果")
                        logger.info(f"  - 类型修正: {len(mapping_result['type_corrections'])} 个")
                        logger.info(f"  - 季度修正: {len(mapping_result['season_corrections'])} 个")

                        # 更新搜索结果（已经直接修改了all_results）
                        all_results = mapping_result['corrected_results']
                        timer.step_end(details=f"修正{mapping_result['total_corrections']}个")
                    else:
                        logger.info(f"○ 全自动导入 统一AI映射: 未找到需要修正的信息")
                        timer.step_end(details="无修正")
                else:
                    logger.warning("○ 全自动导入 AI映射: AI匹配器未启用或初始化失败")
                    timer.step_end(details="匹配器未启用")

            except Exception as e:
                logger.warning(f"全自动导入 统一AI映射任务执行失败: {e}")
                timer.step_end(details=f"失败: {e}")
        else:
            if auto_import_tmdb_enabled.lower() != "true":
                logger.info("○ 全自动导入 统一AI映射: 功能未启用")
            elif media_type == "movie":
                logger.info("○ 全自动导入 统一AI映射: 电影类型,跳过")

        # 根据标题关键词修正媒体类型（与 WebUI 一致）
        for item in all_results:
            if item.type == "tv_series" and is_movie_by_title(item.title):
                logger.info(
                    f"Control API: 标题 '{item.title}' 包含电影关键词，类型从 'tv_series' 修正为 'movie'。"
                )
                item.type = "movie"

        # 添加WebUI的季度过滤逻辑
        if season and season > 0:
            original_count = len(all_results)
            # 当指定季度时，我们只关心电视剧类型
            filtered_by_type = [item for item in all_results if item.type == 'tv_series']

            # 然后在电视剧类型中，我们按季度号过滤
            filtered_by_season = []
            filtered_out = []
            for item in filtered_by_type:
                # 使用模型中已解析好的 season 字段进行比较
                if item.season == season:
                    filtered_by_season.append(item)
                else:
                    filtered_out.append(item)

            logger.info(f"根据指定的季度 ({season}) 进行过滤，从 {original_count} 个结果中保留了 {len(filtered_by_season)} 个。")

            # 打印过滤详情
            if filtered_out:
                logger.info("季度过滤结果:")
                for item in filtered_out:
                    logger.info(f"  - 已过滤: {item.title} (Provider: {item.provider}, Season: {item.season})")
            if filtered_by_season:
                for item in filtered_by_season:
                    logger.info(f"  - {item.title} (Provider: {item.provider}, Season: {item.season})")

            all_results = filtered_by_season

        if all_results:
            logger.info("保留的结果列表:")
            for i, item in enumerate(all_results, 1):  # 显示所有结果
                logger.info(f"  - {item.title} (Provider: {item.provider}, Type: {item.type}, Season: {item.season})")
            logger.info(f"总共 {len(all_results)} 个结果")

        if not all_results:
            raise ValueError("全网搜索未找到任何结果。")

        # 移除提前映射逻辑，改为在选择最佳匹配后应用识别词转换
        await progress_callback(50, "正在准备选择最佳源...")

        # 4. 选择最佳源
        ordered_settings = await crud.get_all_scraper_settings(session)
        provider_order = {s['providerName']: s['displayOrder'] for s in ordered_settings}

        # 修正：使用更智能的排序逻辑来选择最佳匹配
        # 1. 媒体类型是否匹配 (最优先)
        # 2. 如果请求指定了季度，季度是否匹配 (次优先)
        # 3. 标题相似度
        # 4. 新增：对完全匹配或非常接近的标题给予巨大奖励
        # 5. 标题长度惩罚 (标题越长，越可能是特别篇，得分越低)
        # 6. 用户设置的源优先级 (最后)
        # 添加调试日志
        logger.info(f"排序前的媒体类型: media_type='{media_type}', 前5个结果:")
        for i, item in enumerate(all_results[:5]):
            logger.info(f"  {i+1}. '{item.title}' (Provider: {item.provider}, Type: {item.type})")

        # 简化排序逻辑：由于已经有季度过滤和标题映射，主要按源优先级排序
        # 新增：年份匹配优先级
        all_results.sort(
            key=lambda item: (
                # 优先级1：年份匹配（最高优先级，避免下载错误年份的版本）
                10000 if year is not None and item.year is not None and item.year == year else 0,
                # 优先级2：完全匹配的标题
                1000 if item.title.strip() == main_title.strip() else 0,
                # 优先级3：标题相似度
                fuzz.token_set_ratio(main_title, item.title),
                # 优先级4：惩罚年份不匹配的结果
                -1000 if year is not None and item.year is not None and item.year != year else 0,
                # 优先级5：源优先级
                -provider_order.get(item.provider, 999)
            ),
            reverse=True # 按得分从高到低排序
        )

        # 添加排序后的调试日志
        logger.info(f"排序后的前5个结果:")
        for i, item in enumerate(all_results[:5]):
            title_match = "✓" if item.title.strip() == main_title.strip() else "✗"
            year_match = "✓" if year is not None and item.year is not None and item.year == year else ("✗" if year is not None and item.year is not None else "-")
            similarity = fuzz.token_set_ratio(main_title, item.title)
            year_info = f"年份: {item.year}" if item.year else "年份: 未知"
            logger.info(f"  {i+1}. '{item.title}' (Provider: {item.provider}, Type: {item.type}, {year_info}, 年份匹配: {year_match}, 标题匹配: {title_match}, 相似度: {similarity}%)")
        # 候选项选择：检查是否启用顺延机制
        if not all_results:
            raise ValueError("没有找到合适的搜索结果")

        # 使用AIMatcherManager进行AI匹配
        ai_selected_index = None
        if await ai_matcher_manager.is_enabled():
            try:
                # 构建查询信息
                query_info = {
                    "title": main_title,
                    "season": payload.season,
                    "episode": payload.episode,
                    "year": year,  # 修正：使用从元数据获取的year变量，而不是payload.year
                    "type": media_type
                }

                # 获取精确标记信息
                favorited_info = {}
                async with scraper_manager._session_factory() as ai_session:
                    from src.db.orm_models import AnimeSource
                    from sqlalchemy import select

                    for result in all_results:
                        # 查找是否有相同provider和mediaId的源被标记
                        stmt = (
                            select(AnimeSource.isFavorited)
                            .where(
                                AnimeSource.providerName == result.provider,
                                AnimeSource.mediaId == result.mediaId
                            )
                            .limit(1)
                        )
                        result_row = await ai_session.execute(stmt)
                        is_favorited = result_row.scalar_one_or_none()
                        if is_favorited:
                            key = f"{result.provider}:{result.mediaId}"
                            favorited_info[key] = True

                # 识别词认知校正上下文（统一函数，命中标记+提示文案；不改排序）
                recognition_info, recognition_hint = (
                    await title_recognition_manager.build_recognition_context_for_results(all_results)
                    if title_recognition_manager else ({}, None)
                )
                if recognition_hint:
                    query_info["recognition_hint"] = recognition_hint

                # 使用AIMatcherManager进行匹配
                ai_selected_index = await ai_matcher_manager.select_best_match(
                    query_info, all_results, favorited_info, None, recognition_info
                )

                if ai_selected_index is not None:
                    logger.info(f"AI匹配成功选择: 索引 {ai_selected_index}")
                else:
                    # 检查是否启用传统匹配兜底
                    ai_fallback_enabled = (await config_manager.get("aiFallbackEnabled", "true")).lower() == 'true'
                    if ai_fallback_enabled:
                        logger.info("AI匹配未找到合适结果，降级到传统匹配")
                    else:
                        logger.warning("AI匹配未找到合适结果，且传统匹配兜底已禁用，将不使用任何结果")

            except Exception as e:
                # 检查是否启用传统匹配兜底
                ai_fallback_enabled = (await config_manager.get("aiFallbackEnabled", "true")).lower() == 'true'
                if ai_fallback_enabled:
                    logger.error(f"AI匹配失败，降级到传统匹配: {e}", exc_info=True)
                else:
                    logger.error(f"AI匹配失败，且传统匹配兜底已禁用: {e}", exc_info=True)
                ai_selected_index = None

        # 检查是否启用外部控制API顺延机制
        fallback_enabled = (await config_manager.get("externalApiFallbackEnabled", "false")).lower() == 'true'

        best_match = None

        # 如果AI选择了结果，使用AI选择的结果
        if ai_selected_index is not None:
            best_match = all_results[ai_selected_index]
            logger.info(f"使用AI选择的最佳匹配: {best_match.title} (Provider: {best_match.provider})")
        # 否则，如果启用了AI匹配，检查传统匹配兜底
        elif await ai_matcher_manager.is_enabled():
            ai_fallback_enabled = (await config_manager.get("aiFallbackEnabled", "true")).lower() == 'true'
            if ai_fallback_enabled and all_results:
                # 传统匹配：验证第一个结果是否满足条件
                first_result = all_results[0]
                type_matched = first_result.type == media_type.value if hasattr(media_type, 'value') else first_result.type == str(media_type)
                similarity = fuzz.token_set_ratio(main_title, first_result.title)

                # 必须满足：类型匹配 AND 相似度 >= 70%
                if type_matched and similarity >= 70:
                    best_match = first_result
                    logger.info(f"AI匹配未选择结果，使用传统匹配: {first_result.title} (Provider: {first_result.provider}, "
                               f"类型匹配: ✓, 相似度: {similarity}%)")
                else:
                    best_match = None
                    logger.warning(f"传统匹配失败: 第一个结果不满足条件 (类型匹配: {'✓' if type_matched else '✗'}, "
                                 f"相似度: {similarity}%, 要求: ≥70%)")
            else:
                logger.warning("AI匹配未选择结果，且传统匹配兜底已禁用，将不使用任何结果")
                best_match = None
        # 如果未启用AI匹配，直接使用传统匹配
        else:
            if all_results:
                # 传统匹配：验证第一个结果是否满足条件
                first_result = all_results[0]
                type_matched = first_result.type == media_type.value if hasattr(media_type, 'value') else first_result.type == str(media_type)
                similarity = fuzz.token_set_ratio(main_title, first_result.title)

                # 必须满足：类型匹配 AND 相似度 >= 70%
                if type_matched and similarity >= 70:
                    best_match = first_result
                    logger.info(f"使用传统匹配: {first_result.title} (Provider: {first_result.provider}, "
                               f"类型匹配: ✓, 相似度: {similarity}%)")
                else:
                    best_match = None
                    logger.warning(f"传统匹配失败: 第一个结果不满足条件 (类型匹配: {'✓' if type_matched else '✗'}, "
                                 f"相似度: {similarity}%, 要求: ≥70%)")
            else:
                best_match = None
                logger.warning("传统匹配失败: 没有搜索结果")

        # 如果没有选择任何结果，抛出错误
        if not best_match:
            raise ValueError("未能选择合适的搜索结果")

        # 如果没有从元数据获取到imageUrl,使用搜索结果的imageUrl
        if not image_url and best_match.imageUrl:
            image_url = best_match.imageUrl
            logger.info(f"使用搜索结果的海报: {image_url}")

        # 顺延机制：如果启用，尝试验证第一集是否可用
        if fallback_enabled:
            logger.info("外部控制API顺延机制已启用，正在验证第一集可用性...")
            await progress_callback(60, "正在验证第一集可用性...")

            # 获取第一集的弹幕
            try:
                # 使用 scraper_manager 获取第一集的弹幕
                scraper = scraper_manager.get_scraper(best_match.provider)
                if not scraper:
                    raise ValueError(f"未找到 {best_match.provider} 的 scraper")

                # 获取分集列表
                episodes_list = await scraper_manager.get_episodes_routed(best_match.provider, best_match.mediaId, db_media_type=media_type)

                # 应用识别词转换
                current_episode_index = 1
                if title_recognition_manager:
                    _, converted_episode, _, _, _ = await title_recognition_manager.apply_title_recognition(best_match.title, current_episode_index, season)
                    if converted_episode is not None:
                        current_episode_index = converted_episode
                        logger.info(f"识别词转换: 原始集数 1 -> 转换后集数 {current_episode_index}")

                # 查找第一集
                first_episode = None
                for ep in episodes_list:
                    if ep.episodeIndex == current_episode_index:
                        first_episode = ep
                        break

                if not first_episode:
                    logger.warning(f"未找到第一集 (索引: {current_episode_index})，尝试下一个候选源...")
                    # 尝试下一个候选源
                    for idx, candidate in enumerate(all_results[1:], start=1):
                        logger.info(f"尝试候选源 {idx}: {candidate.title} (Provider: {candidate.provider})")
                        try:
                            scraper = scraper_manager.get_scraper(candidate.provider)
                            if not scraper:
                                logger.warning(f"未找到 {candidate.provider} 的 scraper，跳过")
                                continue

                            episodes_list = await scraper_manager.get_episodes_routed(candidate.provider, candidate.mediaId, db_media_type=candidate.type if hasattr(candidate, 'type') else None)

                            # 应用识别词转换
                            current_episode_index = 1
                            if title_recognition_manager:
                                _, converted_episode, _, _, _ = await title_recognition_manager.apply_title_recognition(candidate.title, current_episode_index, season)
                                if converted_episode is not None:
                                    current_episode_index = converted_episode
                                    logger.info(f"识别词转换: 原始集数 1 -> 转换后集数 {current_episode_index}")

                            first_episode = None
                            for ep in episodes_list:
                                if ep.episodeIndex == current_episode_index:
                                    first_episode = ep
                                    break

                            if first_episode:
                                logger.info(f"候选源 {idx} 找到第一集，使用该源")
                                best_match = candidate
                                break
                            else:
                                logger.warning(f"候选源 {idx} 未找到第一集，继续尝试下一个")
                        except Exception as e:
                            logger.error(f"验证候选源 {idx} 时出错: {e}")
                            continue

                    if not first_episode:
                        raise ValueError("所有候选源均未找到第一集，无法导入")

                # 获取第一集的弹幕
                comments = await scraper.get_comments(first_episode.episodeId)
                if not comments:
                    logger.warning(f"第一集 (索引: {current_episode_index}) 没有弹幕，尝试下一个候选源...")
                    # 尝试下一个候选源
                    for idx, candidate in enumerate(all_results[1:], start=1):
                        logger.info(f"尝试候选源 {idx}: {candidate.title} (Provider: {candidate.provider})")
                        try:
                            scraper = scraper_manager.get_scraper(candidate.provider)
                            if not scraper:
                                logger.warning(f"未找到 {candidate.provider} 的 scraper，跳过")
                                continue

                            episodes_list = await scraper_manager.get_episodes_routed(candidate.provider, candidate.mediaId, db_media_type=media_type)

                            # 应用识别词转换
                            current_episode_index = 1
                            if title_recognition_manager:
                                _, converted_episode, _, _, _ = await title_recognition_manager.apply_title_recognition(candidate.title, current_episode_index, season)
                                if converted_episode is not None:
                                    current_episode_index = converted_episode
                                    logger.info(f"识别词转换: 原始集数 1 -> 转换后集数 {current_episode_index}")

                            first_episode = None
                            for ep in episodes_list:
                                if ep.episodeIndex == current_episode_index:
                                    first_episode = ep
                                    break

                            if not first_episode:
                                logger.warning(f"候选源 {idx} 未找到第一集，跳过")
                                continue

                            comments = await scraper.get_comments(first_episode.episodeId)
                            if comments:
                                logger.info(f"候选源 {idx} 找到第一集弹幕，使用该源")
                                best_match = candidate
                                break
                            else:
                                logger.warning(f"候选源 {idx} 第一集没有弹幕，继续尝试下一个")
                        except Exception as e:
                            logger.error(f"验证候选源 {idx} 时出错: {e}")
                            continue

                    if not comments:
                        raise ValueError("所有候选源的第一集均没有弹幕，无法导入")

                logger.info(f"第一集验证成功，弹幕数量: {len(comments)}")
            except Exception as e:
                logger.error(f"验证第一集时出错: {e}")
                raise ValueError(f"验证第一集失败: {str(e)}")

        # 5. 创建导入任务
        await progress_callback(70, "正在创建导入任务...")

        # 解析多集参数
        selected_episodes = None
        if payload.episode:
            selected_episodes = parse_episode_ranges(payload.episode)
            logger.info(f"解析集数参数 '{payload.episode}' -> {selected_episodes}")

        # 应用存储后处理规则
        final_title = best_match.title
        if title_recognition_manager:
            processed_title, _, _, postprocessing_applied, _ = await title_recognition_manager.apply_storage_postprocessing(best_match.title, season)
            if postprocessing_applied:
                final_title = processed_title
                logger.info(f"✓ 应用存储后处理: '{best_match.title}' -> '{final_title}'")
            else:
                logger.info(f"○ 存储后处理未生效: '{best_match.title}'")

        task_coro = lambda s, cb: generic_import_task(
            provider=best_match.provider, mediaId=best_match.mediaId,
            animeTitle=final_title, mediaType=best_match.type,
            season=season, year=best_match.year,
            config_manager=config_manager, metadata_manager=metadata_manager,
            currentEpisodeIndex=None, imageUrl=image_url,
            doubanId=douban_id, tmdbId=tmdb_id, imdbId=imdb_id, tvdbId=tvdb_id, bangumiId=bangumi_id,
            progress_callback=cb, session=s, manager=scraper_manager, task_manager=task_manager,
            rate_limiter=rate_limiter,
            title_recognition_manager=title_recognition_manager,
            is_fallback=False,
            selectedEpisodes=selected_episodes,
            enable_incremental_refresh=bool(payload.enableIncrementalRefresh),
        )

        # 构建任务标题
        title_parts = [f"自动导入: {final_title}"]
        if season is not None:
            title_parts.append(f"S{season:02d}")
        if payload.episode:
            title_parts.append(f"E{payload.episode}")
        task_title = " ".join(title_parts)

        # 构建unique_key
        unique_key_parts = ["import", best_match.provider, best_match.mediaId]
        if season is not None:
            unique_key_parts.append(f"s{season}")
        if payload.episode:
            unique_key_parts.append(f"e{payload.episode}")
        unique_key = "-".join(unique_key_parts)

        # 准备任务参数
        task_parameters = {
            "provider": best_match.provider,
            "mediaId": best_match.mediaId,
            "animeTitle": final_title,
            "mediaType": best_match.type,
            "season": season,
            "year": best_match.year,
            "currentEpisodeIndex": None,
            "selectedEpisodes": selected_episodes,
            "imageUrl": image_url,
            "doubanId": douban_id,
            "tmdbId": tmdb_id,
            "imdbId": imdb_id,
            "tvdbId": tvdb_id,
            "bangumiId": bangumi_id,
            "enableIncrementalRefresh": bool(payload.enableIncrementalRefresh),
        }

        execution_task_id, _ = await task_manager.submit_task(
            task_coro,
            task_title,
            unique_key=unique_key,
            task_type="generic_import",
            task_parameters=task_parameters
        )
        timer.finish()  # 打印计时报告
        final_message = f"已为最佳匹配源创建导入任务。执行任务ID: {execution_task_id}"
        raise TaskSuccess(final_message)
    finally:
        if api_key:
            await scraper_manager.release_search_lock(api_key)
            logger.info(f"自动导入任务已为 API key 释放搜索锁。")

