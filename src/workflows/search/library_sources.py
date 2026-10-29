"""库内已有源识别。

职责：判断搜索结果中哪些候选源已存在于弹幕库（AnimeSource 表），
供统一评分（library_source_bonus）与 AI 匹配上下文（existing_info）复用。

why 独立成模块：
    此前该逻辑内联在 webhook 任务中约 45 行，导致 auto_import 想复用
    "库内源加权" 时只能重写一遍，形成第二个真相源。抽成纯能力函数后，
    任何流程只需一次调用即可获得一致的判定结果。
"""

import logging
from typing import Any, Dict, Sequence, Set

from sqlalchemy.ext.asyncio import AsyncSession

# 编排层仅通过服务访问数据库，跨表查询由 QueryRepository 承担。
from src.services.service_container import get_database_service

logger = logging.getLogger(__name__)


def _title_season_key(title: str, season: Any) -> str:
    """构造 "标题:S季度" 形式的模糊匹配键。"""
    return f"{title}:S{season or 1}"


async def collect_existing_source_keys(
    session: AsyncSession,
    results: Sequence[Any],
    *,
    log_prefix: str = "",
) -> Set[str]:
    """识别搜索结果中已存在于弹幕库的源，返回 "provider:mediaId" 键集合。

    判定分两轮：
        1. 精确匹配：provider + mediaId 完全一致。
        2. 模糊匹配：provider + 标题 + 季度 一致但 mediaId 不同。
           why: 爱奇艺等源的专辑 ID 会随时间变化，仅比对 mediaId 会把
           同一部作品误判为新源，导致重复入库。

    Args:
        session: 数据库会话。
        results: 搜索结果列表（需具备 provider/mediaId/title/season 属性）。
        log_prefix: 日志前缀，便于区分调用方（如 "Webhook:"）。

    Returns:
        已存在于库中的源键集合。
    """
    existing_keys: Set[str] = set()
    if not results:
        return existing_keys

    # provider -> {"标题:S季度": mediaId}，用于第二轮模糊匹配
    by_provider_title: Dict[str, Dict[str, str]] = {}

    # 批量读取后在编排层生成匹配上下文，借用会话不提交调用方事务。
    pairs = [(item.provider, item.mediaId) for item in results]
    async with get_database_service().transaction(session) as db:
        rows = await db.source.get_source_titles_by_provider_media_pairs(pairs)
    for row in rows:
        provider, media_id = row["providerName"], row["mediaId"]
        existing_keys.add(f"{provider}:{media_id}")
        by_provider_title.setdefault(provider, {})[
            _title_season_key(row["title"], row["season"])
        ] = media_id

    # 第二轮：同 provider 下标题+季度一致即视为已存在（容忍 mediaId 变更）
    for item in results:
        provider_map = by_provider_title.get(item.provider)
        if not provider_map:
            continue
        current_key = _title_season_key(item.title, item.season)
        existing_media_id = provider_map.get(current_key)
        if existing_media_id is None:
            continue
        source_key = f"{item.provider}:{item.mediaId}"
        if source_key in existing_keys:
            continue
        logger.info(
            f"{log_prefix} 发现库内已有源的标题+季度匹配 "
            f"(provider={item.provider}, title={item.title}, "
            f"season={item.season or 1}, 当前mediaId={item.mediaId}, "
            f"库内mediaId={existing_media_id})，标记为已存在".strip()
        )
        existing_keys.add(source_key)

    if existing_keys:
        logger.info(
            f"{log_prefix} 发现 {len(existing_keys)} 个库内已有源: {existing_keys}".strip()
        )
    return existing_keys


async def collect_favorited_source_keys(
    session: AsyncSession,
    results: Sequence[Any],
) -> Set[str]:
    """识别搜索结果中被标记为「精确源」的源，返回 "provider:mediaId" 键集合。"""
    # 复用已有批量仓储接口，ORM 字段仅在绑定会话内读取。
    pairs = list(dict.fromkeys((item.provider, item.mediaId) for item in results))
    if not pairs:
        return set()
    async with get_database_service().transaction(session) as db:
        sources = await db.source.get_favorited_by_provider_media_pairs(pairs)
        return {f"{source.providerName}:{source.mediaId}" for source in sources}
