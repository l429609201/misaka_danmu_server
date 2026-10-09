"""自动导入的标题解析、元数据获取与别名准备。"""
import logging
from typing import Any, Callable, Dict, Optional

from src.schemas.auth import User

from src.schemas.import_schemas import AutoImportMediaType, ControlAutoImportRequest
from src.utils.parsing.filename_parser import parse_filename, is_chinese_title
from src.utils import format_parse_result_log

from src.workflows.search.ui_results import search_aliases_from_enabled_sources

logger = logging.getLogger(__name__)


async def prepare_auto_import_metadata(
    payload: ControlAutoImportRequest, user: Any, metadata_manager: Any,
    progress_callback: Callable, timer: Any, profiler: Any,
) -> Dict[str, Any]:
    """解析显式参数优先的搜索身份，数字关键词失败时仍保留关键词回退。"""
    search_type = payload.searchType
    search_term = payload.searchTerm
    media_type, season = payload.mediaType, payload.season
    main_title, aliases = search_term, {search_term}
    year, image_url, details = None, None, None
    tmdb_id = bangumi_id = douban_id = tvdb_id = imdb_id = None
    effective_search_type = search_type.value
    if search_type == "keyword" and search_term.isdigit():
        effective_search_type = "tmdb"
    if effective_search_type == "keyword":
        parsed = parse_filename(search_term)
        if parsed is not None:
            logger.info(format_parse_result_log("外部自动导入", search_term, parsed))
            if parsed.title:
                main_title = search_term = parsed.title
                aliases = {main_title}
            if season is None and parsed.season is not None:
                season = parsed.season
            if payload.episode is None and parsed.episode is not None:
                payload.episode = str(parsed.episode)
            if parsed.year:
                try:
                    year = int(parsed.year)
                except (ValueError, TypeError):
                    pass
    else:
        timer.step_start("元数据查询")
        await progress_callback(10, f"正在从 {effective_search_type.upper()} 获取元数据...")
        try:
            if media_type and effective_search_type in ("tmdb", "tvdb"):
                media_types = [
                    ("tv" if media_type == "tv_series" else "movie")
                    if effective_search_type == "tmdb"
                    else ("series" if media_type == "tv_series" else "movies")
                ]
            else:
                media_types = ["tv", "movie"] if effective_search_type == "tmdb" else ["series", "movies"]
            for provider_type in media_types:
                details = await metadata_manager.get_details(
                    provider=effective_search_type, item_id=search_term,
                    user=user, mediaType=provider_type,
                )
                if details:
                    break
        except Exception:
            logger.exception("从 %s 获取元数据失败，继续后续搜索", effective_search_type.upper())
        if details:
            duration = timer.step_end(details=f"找到: {details.title}")
            profiler.record_step("元数据查询", duration)
    if details:
        main_title = details.title or main_title
        image_url = details.imageUrl
        aliases.update({main_title, details.nameEn, details.nameJp})
        aliases.update(details.aliasesCn or [])
        tmdb_id, bangumi_id, douban_id, tvdb_id, imdb_id = (
            details.tmdbId, details.bangumiId, details.doubanId,
            details.tvdbId, details.imdbId,
        )
        if effective_search_type != "tmdb" and main_title and not is_chinese_title(main_title):
            enabled = await metadata_manager.is_tmdb_reverse_lookup_enabled(effective_search_type)
            if enabled:
                chinese_title = await reverse_lookup_tmdb_chinese_title(metadata_manager,
                    user, effective_search_type, search_term, tmdb_id,
                    imdb_id if effective_search_type != "imdb" else search_term,
                    tvdb_id if effective_search_type != "tvdb" else search_term,
                    douban_id if effective_search_type != "douban" else search_term,
                    bangumi_id if effective_search_type != "bangumi" else search_term,
                )
                if chinese_title:
                    main_title = chinese_title
                    aliases.add(chinese_title)
        if getattr(details, "type", None):
            media_type = AutoImportMediaType(details.type)
        if getattr(details, "year", None):
            year = details.year
        enriched = await search_aliases_from_enabled_sources(metadata_manager, main_title, user)
        if enriched:
            aliases.update(enriched)
    return {
        "search_term": search_term, "media_type": media_type, "season": season,
        "main_title": main_title, "aliases": aliases, "image_url": image_url,
        "year": year, "tmdb_id": tmdb_id, "bangumi_id": bangumi_id,
        "douban_id": douban_id, "tvdb_id": tvdb_id, "imdb_id": imdb_id,
        "details": details, "effective_search_type": effective_search_type,
    }


async def reverse_lookup_tmdb_chinese_title(
    metadata_manager: Any, user: User, source_type: str, source_id: str,
    tmdb_id: Optional[str], imdb_id: Optional[str], tvdb_id: Optional[str],
    douban_id: Optional[str], bangumi_id: Optional[str],
) -> Optional[str]:
    """优先通过已知TMDB编号获取中文标题，失败时使用外部编号反查。"""
    try:
        if tmdb_id:
            title = await _get_tmdb_chinese_title(metadata_manager, tmdb_id, user)
            if title:
                return title
        external_ids = {
            key: value for key, value in (
                ("imdb_id", imdb_id), ("tvdb_id", tvdb_id),
                ("douban_id", douban_id), ("bangumi_id", bangumi_id),
            ) if value
        }
        if external_ids:
            found_id = await find_tmdb_by_external_ids(metadata_manager, user, external_ids)
            if found_id:
                title = await _get_tmdb_chinese_title(metadata_manager, found_id, user)
                if title:
                    return title
        logger.info(f"未能通过 {source_type} ID {source_id} 反查到中文标题")
    except Exception as exc:
        logger.warning(f"TMDB反查失败: {exc}")
    return None



async def _get_tmdb_chinese_title(metadata_manager: Any, tmdb_id: str, user: User) -> Optional[str]:
    """保持原有先电视剧、详情不存在时再电影的查询顺序。"""
    details = await metadata_manager.get_details(provider="tmdb", item_id=tmdb_id, user=user, mediaType="tv")
    if not details:
        details = await metadata_manager.get_details(provider="tmdb", item_id=tmdb_id, user=user, mediaType="movie")
    if details and details.title and is_chinese_title(details.title):
        return details.title
    return None



async def find_tmdb_by_external_ids(metadata_manager: Any, user: User, external_ids: Dict[str, str]) -> Optional[str]:
    """使用TMDB外部编号接口查询，失败时回退到各元数据源搜索。"""
    tmdb_source = metadata_manager.sources.get("tmdb")
    if tmdb_source:
        # 源实例与网络请求由元数据管理器协调，不暴露给任务层。
        for key in ("imdb_id", "tvdb_id"):
            if key not in external_ids:
                continue
            ext_id = external_ids[key]
            try:
                found_id = await tmdb_source.find_by_external_id(ext_id, key)
                if found_id:
                    return found_id
            except Exception as exc:
                logger.warning(f"TMDB find API 查找失败 ({key}={ext_id}): {exc}")
    for key, provider in (
        ("imdb_id", "imdb"), ("tvdb_id", "tvdb"),
        ("douban_id", "douban"), ("bangumi_id", "bangumi"),
    ):
        if key not in external_ids:
            continue
        try:
            results = await metadata_manager.search(provider, external_ids[key], user)
            for result in results:
                if getattr(result, "tmdbId", None):
                    return result.tmdbId
        except Exception as exc:
            logger.warning(f"通过 {provider} 查找 TMDB 失败: {exc}")
    return None
