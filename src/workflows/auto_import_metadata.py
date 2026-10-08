"""自动导入的标题解析、元数据获取与别名准备。"""
import logging
from typing import Any, Callable, Dict

from src.schemas.import_schemas import AutoImportMediaType, ControlAutoImportRequest
from src.utils.parsing.filename_parser import parse_filename, is_chinese_title
from src.utils import format_parse_result_log

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
                chinese_title = await metadata_manager.reverse_lookup_tmdb_chinese_title(
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
        enriched = await metadata_manager.search_aliases_from_enabled_sources(main_title, user)
        if enriched:
            aliases.update(enriched)
    return {
        "search_term": search_term, "media_type": media_type, "season": season,
        "main_title": main_title, "aliases": aliases, "image_url": image_url,
        "year": year, "tmdb_id": tmdb_id, "bangumi_id": bangumi_id,
        "douban_id": douban_id, "tvdb_id": tvdb_id, "imdb_id": imdb_id,
        "details": details, "effective_search_type": effective_search_type,
    }
