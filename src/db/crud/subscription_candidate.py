"""订阅候选项 CRUD（候选池，保留建库 extraData）"""
import json
import logging
from typing import Dict, Any, List, Optional
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, delete, func
from sqlalchemy.dialects.mysql import insert as mysql_insert

from src.db.orm_models import SubscriptionCandidateItem, Episode

logger = logging.getLogger(__name__)


async def upsert_candidates(
    session: AsyncSession,
    parent_id: int,
    provider: str,
    items: List[Dict[str, Any]]
) -> int:
    """批量写入候选项（ON DUPLICATE KEY UPDATE title）。

    :param parent_id: 父订阅目标 ID（external_calendar_item.id）
    :param provider: 来源（bilibili/...）
    :param items: 候选项列表，每项需包含 externalId/title
    :return: 写入/更新的条目数
    """
    if not items:
        return 0

    values = []
    for item in items:
        external_id = item.get("externalId")
        title = item.get("title") or ""
        if not external_id:
            continue
        # 保留建库所需的 extraData（aid/cid/episodeIndex 等），定时导入时需要
        extra = item.get("extraData") or {}
        extra_json = None
        if extra:
            try:
                extra_json = json.dumps(extra, ensure_ascii=False)
            except (TypeError, ValueError):
                extra_json = None
        values.append({
            "parent_id": parent_id,
            "provider": provider,
            "external_id": external_id,
            "title": title,
            "extra_data": extra_json,
        })

    if not values:
        return 0

    # MySQL ON DUPLICATE KEY UPDATE（标题与 extraData 都更新）
    stmt = mysql_insert(SubscriptionCandidateItem).values(values)
    stmt = stmt.on_duplicate_key_update(
        title=stmt.inserted.title,
        extra_data=stmt.inserted.extra_data,
    )
    await session.execute(stmt)
    await session.commit()
    logger.info(f"upsert_candidates: parent_id={parent_id}, provider={provider}, count={len(values)}")
    return len(values)


async def list_candidates_with_import_status(
    session: AsyncSession,
    parent_id: int
) -> List[Dict[str, Any]]:
    """查询某订阅目标的所有候选项，JOIN episode 表返回 is_imported 字段。

    :param parent_id: 父订阅目标 ID
    :return: [{id, external_id, title, is_imported: bool}, ...]
    """
    # LEFT JOIN episode 表，检查 provider_episode_id 是否存在
    # 注意：候选项 externalId 可能带 "video:" 前缀，而 episode.providerEpisodeId 存纯 BV 号，
    # 因此用 func.replace 去掉前缀后再比较，避免前缀差异导致永远匹配不上
    stmt = (
        select(
            SubscriptionCandidateItem.id,
            SubscriptionCandidateItem.externalId,
            SubscriptionCandidateItem.title,
            SubscriptionCandidateItem.provider,
            SubscriptionCandidateItem.extraData,
            Episode.id.isnot(None).label("is_imported")
        )
        .outerjoin(
            Episode,
            Episode.providerEpisodeId == func.replace(
                func.replace(SubscriptionCandidateItem.externalId, "video:", ""),
                "collection:", ""
            )
        )
        .where(SubscriptionCandidateItem.parentId == parent_id)
        .order_by(SubscriptionCandidateItem.id)
    )
    result = await session.execute(stmt)
    rows = result.all()
    out: List[Dict[str, Any]] = []
    for r in rows:
        extra: Dict[str, Any] = {}
        if r.extraData:
            try:
                loaded = json.loads(r.extraData)
                if isinstance(loaded, dict):
                    extra = loaded
            except (json.JSONDecodeError, TypeError):
                extra = {}
        out.append({
            "id": r.id,
            "externalId": r.externalId,
            "title": r.title,
            "provider": r.provider,
            "extraData": extra,
            "isImported": bool(r.is_imported),
        })
    return out


async def delete_candidates_by_parent(session: AsyncSession, parent_id: int) -> int:
    """删除某订阅目标的所有候选项（取消订阅/清理用）。

    :return: 删除的条目数
    """
    stmt = delete(SubscriptionCandidateItem).where(SubscriptionCandidateItem.parentId == parent_id)
    result = await session.execute(stmt)
    await session.commit()
    count = result.rowcount
    logger.info(f"delete_candidates_by_parent: parent_id={parent_id}, deleted={count}")
    return count


async def get_candidate_by_external_id(
    session: AsyncSession,
    parent_id: int,
    external_id: str
) -> Optional[Dict[str, Any]]:
    """按 external_id 查询单个候选项。

    :return: {id, external_id, title, provider} or None
    """
    stmt = (
        select(SubscriptionCandidateItem)
        .where(
            SubscriptionCandidateItem.parentId == parent_id,
            SubscriptionCandidateItem.externalId == external_id
        )
    )
    result = await session.execute(stmt)
    row = result.scalar_one_or_none()
    if not row:
        return None
    return {
        "id": row.id,
        "externalId": row.externalId,
        "title": row.title,
        "provider": row.provider,
    }
