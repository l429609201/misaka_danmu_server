"""Webhook 库内收藏源复用及年份决策，不依赖任务入口。"""

import logging
from typing import Any, Optional

from sqlalchemy.ext.asyncio import AsyncSession

from src.services.service_container import get_database_service

logger = logging.getLogger(__name__)


async def find_webhook_favorite(
    *, title: str, season: int, year: Optional[int],
    session: AsyncSession, recognition_manager: Any,
) -> tuple[Optional[dict[str, Any]], Optional[int]]:
    """使用预处理后的标题和季度查询收藏源，优先沿用库内首播年份。"""
    logger.info(
        f"Webhook 任务: 查找已存在的anime - 标题='{title}', 季数={season}, webhook年份={year}"
    )
    # 不以单集放映年份限制作品查询，也不回退识别规则变更前的输入。
    async with get_database_service().transaction(session) as db:
        anime = await db.anime.find_by_title_season_year_with_recognition(
            title, season, None, recognition_manager, source=None,
        )
        if not anime:
            return None, year
        effective_year = anime.get("year") or year
        source = await db.source.find_favorited_source_for_anime(anime["id"])
    if year and effective_year != year:
        logger.info(
            f"Webhook 任务: 数据库年份({effective_year}) 与 webhook 年份({year}) 不一致，使用数据库年份进行搜索"
        )
    return source, effective_year
