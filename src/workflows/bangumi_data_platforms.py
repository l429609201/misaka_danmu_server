"""离线元数据平台映射到已加载弹幕源的查询流程。"""

import asyncio
from typing import Any, Callable

from src.metadata_sources.bangumi_platform_adapter import resolve_media_id
from src.services.bangumi_data_service import BangumiDataService
from src.utils.parsing.bangumi_platforms import SITE_TO_PROVIDER

from src.services.metadata_service import MetadataService
from src.services.scraper_manager import ScraperManager


async def resolve_to_danmaku_sources(
    index_service: BangumiDataService, bangumi_id: str, client_factory: Callable | None = None,
) -> list[dict[str, Any]]:
    """查询离线平台映射并并行执行平台协议转换，单个平台失败不影响其余平台。"""
    sites = await index_service.get_all_platform_ids(bangumi_id)
    candidates = [(site, raw_id) for site, raw_id in sites.items() if site in SITE_TO_PROVIDER]
    results = await asyncio.gather(
        *(resolve_media_id(site, raw_id, client_factory) for site, raw_id in candidates),
        return_exceptions=True,
    )
    sources = []
    seen = set()
    for (site, _), result in zip(candidates, results):
        if isinstance(result, BaseException) or not result:
            continue
        provider, media_id = result
        if provider not in seen:
            seen.add(provider)
            sources.append({"site": site, "provider": provider, "mediaId": media_id})
    return sources


async def resolve_sources_by_title(
    index_service: BangumiDataService, title: str, client_factory: Callable | None = None,
) -> list[dict[str, Any]]:
    """根据离线标题命中首条索引，补充可抓取的源与展示元信息。"""
    rows = await index_service.search_rows_by_title(title, limit=1)
    if not rows or not rows[0].bangumiId:
        return []
    row = rows[0]
    sources = await resolve_to_danmaku_sources(index_service, str(row.bangumiId), client_factory)
    for source in sources:
        source.update(title=row.titleZh or row.titleMain,
                      type="movie" if row.type == "movie" else "tv_series", year=row.beginYear)
    return sources


async def resolve_bangumi_danmaku_sources(
    bangumi_id: str, metadata_service: MetadataService, scraper_manager: ScraperManager,
) -> dict[str, Any]:
    """按平台链接判断对应弹幕源是否可直接抓取。"""
    platforms = await metadata_service.get_bangumi_platform_urls(str(bangumi_id))
    sources = []
    for platform in platforms:
        url = platform.get("url")
        scraper = scraper_manager.get_scraper_by_domain(url) if url else None
        sources.append({
            "site": platform.get("site"),
            "id": platform.get("id"),
            "title": platform.get("title"),
            "type": platform.get("type"),
            "url": url,
            "provider": scraper.provider_name if scraper else None,
            "available": scraper is not None,
        })
    return {"bangumiId": bangumi_id, "sources": sources}
