"""离线元数据平台映射到已加载弹幕源的查询流程。"""

from typing import Any

from src.services.metadata_service import MetadataService
from src.services.scraper_manager import ScraperManager


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
