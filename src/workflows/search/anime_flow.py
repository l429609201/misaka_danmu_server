"""
弹弹Play 兼容搜索作品业务流程
"""

from src.services.ai_service import get_ai_service
from src.services.service_container import get_database_service
import json
import logging
import re
from typing import Optional
from datetime import datetime

from fastapi import HTTPException, status

from src.core import get_app_timezone
from src.services.config_service import ConfigService

from src.utils import parse_search_keyword
from src.commands import handle_command
from src.schemas.dandan import (
    DandanSearchAnimeItem,
    DandanSearchAnimeResponse,
)

from src.utils.dandan.constants import (
    DANDAN_TYPE_MAPPING,
    DANDAN_TYPE_DESC_MAPPING,
    FALLBACK_SEARCH_CACHE_PREFIX,
)
from src.workflows.search.fallback_search import (
    get_db_cache,
    handle_fallback_search,
)
from src.utils.parsing.filename_parser import format_episode_ranges
from .dandan_helpers import format_db_results

logger = logging.getLogger(__name__)


async def search_anime_flow(
    keyword: Optional[str],
    anime: Optional[str],
    _episode: Optional[str],
    token: str,
    db,
    scraper_manager,
    config_service,
    metadata_manager,
    rate_limiter,
    title_recognition_manager,
    task_manager,
):
    """
    搜索作品的业务流程

    模拟 dandanplay 的 /api/v2/search/anime 接口。
    它会搜索 **本地弹幕库** 中的番剧信息，不包含分集列表。
    新增：支持后备搜索功能，当库内无结果或指定集数不存在时，触发全网搜索。
    支持SXXEXX格式的季度和集数搜索。
    支持指令功能：以@开头的搜索词作为指令。

    Args:
        keyword: 节目名称（兼容 keyword）
        anime: 节目名称（兼容 anime）
        episode: 分集标题（此接口中未使用）
        token: API Token
        db: DatabaseService 数据访问服务
        scraper_manager: 弹幕源管理器
        config_service: 配置服务
        metadata_manager: 元数据管理器
        rate_limiter: 速率限制器
        title_recognition_manager: 标题识别管理器
        task_manager: 任务管理器

    Returns:
        DandanSearchAnimeResponse 对象

    Raises:
        HTTPException: 当缺少必需的查询参数时
    """
    search_term = keyword or anime
    if not search_term:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Missing required query parameter: 'keyword' or 'anime'"
        )

    # 该兼容参数由接口保留；记录到调试日志以明确其未参与作品搜索。
    if _episode is not None:
        logger.debug("作品搜索接口收到未使用的 episode 参数: %s", _episode)

    # ===== 指令处理 =====
    command_response = await handle_command(
        search_term, token, db._session, config_service,
        scraper_manager=scraper_manager,
        metadata_manager=metadata_manager,
        rate_limiter=rate_limiter,
        title_recognition_manager=title_recognition_manager,
        task_manager=task_manager
    )
    if command_response:
        return command_response
    # ===== 指令处理结束 =====

    # 解析搜索关键词，提取标题、季数和集数
    parsed_info = parse_search_keyword(search_term)
    title_to_search = parsed_info["title"]
    season_to_search = parsed_info.get("season")
    episode_to_search = parsed_info.get("episode")

    # 首先搜索本地库（使用解析后的标题，而非原始搜索词）
    db_results = await db.anime.search_animes_for_dandan(title_to_search)

    # 如果指定了具体集数，需要检查该集数是否存在。
    should_trigger_fallback = False

    # 先读取后备搜索开关，再决定并行搜索是否生效；并行搜索不能绕过后备搜索总开关。
    search_fallback_enabled = (
        str(await config_service.get("searchFallbackEnabled", "false")).strip().lower() == "true"
    )
    parallel_setting_enabled = (
        str(await config_service.get("parallelSearchEnabled", "false")).strip().lower() == "true"
    )
    parallel_search_enabled = parallel_setting_enabled and search_fallback_enabled
    logger.debug(
        "作品搜索配置: searchFallbackEnabled=%s, parallelSearchEnabled=%s, 库内结果数=%d",
        search_fallback_enabled,
        parallel_setting_enabled,
        len(db_results),
    )
    if parallel_setting_enabled and not search_fallback_enabled:
        # 配置可能由旧版本或直接 API 写入，后端必须强制遵守开关依赖，避免吞掉库内结果。
        logger.info("并行搜索配置已开启但后备搜索已关闭，将仅返回库内结果")
    elif parallel_search_enabled:
        should_trigger_fallback = True
        logger.info("并行搜索已启用，将同时搜索库内和在线源站")

    if search_fallback_enabled and not should_trigger_fallback and db_results and episode_to_search is not None:
        # 仅在后备搜索开启时，才因指定集数缺失而触发在线搜索。
        episode_exists = False
        for anime_result in db_results:
            anime_id = anime_result['animeId']
            episodes = await db.episode.search_episodes_in_library(
                keyword=title_to_search,
                anime_id=anime_id,
            )
            if episodes:
                episode_exists = True
                break

        if not episode_exists:
            logger.info(f"本地库中找到番剧但不存在指定集数 E{episode_to_search:02d}，将触发后备搜索")
            should_trigger_fallback = True

    # 如果本地库有结果且不需要触发后备搜索，直接返回
    if db_results and not should_trigger_fallback:
        return DandanSearchAnimeResponse(animes=format_db_results(db_results))

    # 如果本地库无结果或需要触发后备搜索，检查是否启用了后备搜索
    if search_fallback_enabled and (not db_results or should_trigger_fallback):
        # 检查Token是否被允许使用后备搜索功能
        if not await _check_fallback_token_permission(token, db, config_service):
            return DandanSearchAnimeResponse(animes=[])

        # 使用解析后的标题进行后备搜索，但保留原始搜索词用于缓存键
        search_title_for_fallback = _build_fallback_search_title(
            title_to_search, season_to_search, episode_to_search
        )

        # 复用应用启动时创建的共享 AI 服务，禁止在请求内重复实例化。
        ai_service = get_ai_service()

        fallback_response = await handle_fallback_search(
            search_title_for_fallback, token, db._session, scraper_manager,
            metadata_manager, config_service, rate_limiter, title_recognition_manager,
            task_manager, ai_service
        )

        # 并行搜索：将库内结果和后备搜索结果合并返回
        if parallel_search_enabled and fallback_response.animes:
            return await _merge_parallel_search_results(
                fallback_response, search_title_for_fallback, token, db,
                scraper_manager, db_results
            )

        # 并行搜索模式下后备搜索无结果，回退到库内结果
        if parallel_search_enabled and db_results and not fallback_response.animes:
            logger.info(f"并行搜索: 后备搜索无结果，回退到库内 {len(db_results)} 个结果")
            return DandanSearchAnimeResponse(animes=format_db_results(db_results))

        return fallback_response

    # 本地库无结果且未启用后备搜索，返回空结果
    return DandanSearchAnimeResponse(animes=[])


async def _check_fallback_token_permission(
    token: str,
    db,
    config_service: ConfigService,
) -> bool:
    """
    检查Token是否被允许使用后备搜索功能

    Args:
        token: API Token
        db: DatabaseService 数据访问服务
        config_service: 配置服务

    Returns:
        True 表示允许使用后备搜索，False 表示不允许
    """
    try:
        # 获取当前token的信息
        current_token_obj = await db.api_token.get_by_token_str(token)

        if current_token_obj:
            # 获取允许的token列表
            allowed_tokens_str = await config_service.get("matchFallbackTokens", "[]")
            allowed_token_ids = json.loads(allowed_tokens_str)

            # 如果配置了允许的token列表且当前token不在列表中，跳过后备搜索
            if allowed_token_ids and current_token_obj.id not in allowed_token_ids:
                logger.info(
                    f"Token '{current_token_obj.name}' (ID: {current_token_obj.id}) "
                    f"未被授权使用后备搜索功能，跳过后备搜索。"
                )
                return False
            else:
                logger.info(
                    f"Token '{current_token_obj.name}' (ID: {current_token_obj.id}) "
                    f"已被授权使用后备搜索功能。"
                )
    except (json.JSONDecodeError, Exception) as e:
        logger.warning(f"检查后备搜索Token授权时发生错误: {e}，继续执行后备搜索")

    return True


def _build_fallback_search_title(
    title_to_search: str,
    season_to_search: Optional[int],
    episode_to_search: Optional[int]
) -> str:
    """
    构建用于后备搜索的标题

    Args:
        title_to_search: 解析后的标题
        season_to_search: 季度号
        episode_to_search: 集数号

    Returns:
        格式化后的搜索标题
    """
    search_title = title_to_search

    if episode_to_search is not None:
        # 如果指定了集数，在后备搜索中包含季度和集数信息
        if season_to_search is not None:
            search_title = f"{title_to_search} S{season_to_search:02d}E{episode_to_search:02d}"
        else:
            search_title = f"{title_to_search} E{episode_to_search:02d}"
    elif season_to_search is not None:
        search_title = f"{title_to_search} S{season_to_search:02d}"

    return search_title


async def _merge_parallel_search_results(
    fallback_response: DandanSearchAnimeResponse,
    search_title_for_fallback: str,
    token: str,
    db,
    scraper_manager,
    db_results
) -> DandanSearchAnimeResponse:
    """
    合并库内结果和后备搜索结果（并行搜索模式）

    Args:
        fallback_response: 后备搜索返回的响应
        search_title_for_fallback: 用于后备搜索的标题
        token: API Token
        db: DatabaseService 数据访问服务
        scraper_manager: 弹幕源管理器
        db_results: 库内搜索结果

    Returns:
        合并后的 DandanSearchAnimeResponse
    """
    # 从缓存获取 bangumi_mapping，用于精确匹配 provider+mediaId
    search_key = f"search_{hash(search_title_for_fallback + token)}"
    bangumi_mapping = {}
    try:
        cached_data = await get_db_cache(db._session, FALLBACK_SEARCH_CACHE_PREFIX, search_key)
        if cached_data and isinstance(cached_data, dict):
            bangumi_mapping = cached_data.get("bangumi_mapping", {})
    except Exception as e:
        logger.debug(f"读取 bangumi_mapping 缓存失败: {e}")

    # 获取源 displayOrder 映射，用于排序
    source_order_map = {}
    try:
        for provider_name, settings in scraper_manager.scraper_settings.items():
            source_order_map[provider_name] = settings.get('displayOrder', 99)
    except Exception:
        pass

    labeled_fallback = []
    for a in fallback_response.animes:
        # 通过 bangumi_mapping 精确获取该结果的 provider 和 media_id
        mapping = bangumi_mapping.get(a.bangumiId)
        provider = None
        media_id = None
        if mapping:
            provider = mapping.get("provider")
            media_id = mapping.get("media_id")
        else:
            # 降级：从 animeTitle 中正则提取 provider
            m = re.search(r'（来源：(\S+?)[\s）]', a.animeTitle)
            if m:
                provider = m.group(1)

        # 用精确的 provider+mediaId 查库内已有集数
        library_eps = []
        if provider and media_id:
            try:
                library_eps = await db.episode.get_episode_indices_by_source_media_id(
                    provider, media_id
                )
            except Exception:
                library_eps = []

        source_total = a.episodeCount or 0

        if library_eps:
            # 该精确源在库内存在：标注「并行」
            new_title = a.animeTitle.replace('（来源：', '（并行 来源：')
            # 计算搜索补充集数范围：源站 1~N 中，库内没有的集数
            library_eps_set = set(library_eps)
            search_eps = [i for i in range(1, source_total + 1) if i not in library_eps_set]
            search_label = f"（搜索：{format_episode_ranges(search_eps)}）" if search_eps else ""
            new_type_desc = f"{a.typeDescription}{search_label}"
            labeled_fallback.append(a.model_copy(update={
                'animeTitle': new_title,
                'typeDescription': new_type_desc,
            }))
        else:
            # 该精确源不在库内，保持原样
            labeled_fallback.append(a)

    # 排序：并行结果优先（按源 displayOrder），后备结果其次（按源 displayOrder）
    def _get_provider(item):
        """从 animeTitle 中提取 provider 名称"""
        m = re.search(r'（(?:并行 )?来源：(\S+?)[\s）]', item.animeTitle)
        return m.group(1) if m else ""

    def _sort_key(item):
        p = _get_provider(item)
        order = source_order_map.get(p, 99)
        is_parallel = 0 if '（并行' in item.animeTitle else 1
        return (is_parallel, order)

    labeled_fallback.sort(key=_sort_key)

    # 补充自定义源条目：从 db_results 中找到有 custom 源的 anime，追加到最后
    if db_results:
        db = get_database_service()
        async with db.transaction():
            custom_anime_ids = await db.anime.get_ids_with_custom_source(
                [res['animeId'] for res in db_results]
            )
        if custom_anime_ids:
            custom_anime_ids_set = set(custom_anime_ids)
            for res in db_results:
                if res['animeId'] in custom_anime_ids_set:
                    dandan_type = DANDAN_TYPE_MAPPING.get(res.get('type'), "other")
                    dandan_type_desc = DANDAN_TYPE_DESC_MAPPING.get(res.get('type'), "其他")
                    year = res.get('year')
                    start_date_str = None
                    if year:
                        start_date_str = datetime(year, 1, 1, tzinfo=get_app_timezone()).isoformat()
                    elif res.get('startDate'):
                        start_date_str = res.get('startDate').isoformat()
                    labeled_fallback.append(DandanSearchAnimeItem(
                        animeId=res['animeId'],
                        bangumiId=res.get('bangumiId') or f"A{res['animeId']}",
                        animeTitle=f"{res['animeTitle']}（自定义源）",
                        type=dandan_type,
                        typeDescription=dandan_type_desc,
                        imageUrl=res.get('imageUrl'),
                        startDate=start_date_str,
                        year=year,
                        episodeCount=res.get('episodeCount', 0),
                        rating=0.0,
                        isFavorited=False
                    ))
            logger.info(f"并行搜索: 追加 {len(custom_anime_ids)} 个自定义源条目")

    parallel_count = sum(1 for a in labeled_fallback if '（并行' in a.animeTitle)
    logger.info(
        f"并行搜索: 后备 {len(fallback_response.animes)} 个结果，"
        f"其中 {parallel_count} 个精确源匹配标注并行"
    )
    return DandanSearchAnimeResponse(animes=labeled_fallback)
