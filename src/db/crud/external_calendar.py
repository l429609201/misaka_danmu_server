"""
ExternalCalendarItem 相关 CRUD 操作 - 通用外部日历条目持久化层。

设计要点：
1. Upsert 而非全量替换：按 (provider, externalId) 联合唯一键更新，保留累积数据
2. 数据新鲜度：通过 fetchedAt 字段判断，超过 max_age_hours 视为过期需重新拉取
3. 跨数据库兼容：同时支持 MySQL (on_duplicate_key_update) 和 PostgreSQL (on_conflict_do_update)
4. extraData 用 JSON 字符串存储平台特有字段，读取时反序列化
"""

import json
import logging
from datetime import timedelta
from typing import Any, Dict, List, Optional

from sqlalchemy import and_, delete, or_, select
from sqlalchemy.dialects.mysql import insert as mysql_insert
from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.timezone import get_now
from ..orm_models import ExternalCalendarItem

logger = logging.getLogger(__name__)


# ============ 通用字段映射 ============

# 标准化字段名（输入 dict 的 key）→ 数据库列对象的映射
# 缺失的输入字段使用列默认值/None
_FIELD_MAP = {
    "animeTitle": "anime_title",
    "titleZh": "title_zh",
    "animeType": "anime_type",
    "season": "season",
    "year": "year",
    "airWeekday": "air_weekday",
    "airTime": "air_time",
    "airDate": "air_date",
    "episodeCount": "episode_count",
    "latestEpisodeIndex": "latest_episode_index",
    "imageUrl": "image_url",
    "rating": "rating",
    "bangumiId": "bangumi_id",
    "traktId": "trakt_id",
    "tmdbId": "tmdb_id",
    "imdbId": "imdb_id",
    # 平台用户私人状态（OAuth 账号下「我在追」相关）
    "platformWatchStatus": "platform_watch_status",
    "platformWatchedEpisodes": "platform_watched_episodes",
    "platformRating": "platform_rating",
}

# 已知的标准字段（剩下的会被塞进 extraData JSON）
_KNOWN_KEYS = set(_FIELD_MAP.keys()) | {"provider", "externalId", "extraData"}


def _build_row_values(provider: str, item: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """把外部源返回的 item dict 标准化为数据库行字典。

    返回 None 表示该 item 无法标识唯一性（缺 externalId），需跳过。
    未知字段会被收集到 extraData (JSON) 中保留。
    """
    external_id = item.get("externalId") or item.get("bangumiId") or item.get("traktId")
    if not external_id:
        return None

    row: Dict[str, Any] = {
        "provider": provider,
        "external_id": str(external_id),
        "anime_type": "tv_series",  # 默认
    }
    extra: Dict[str, Any] = {}

    for key, value in item.items():
        if key in _FIELD_MAP:
            row[_FIELD_MAP[key]] = value
        elif key not in _KNOWN_KEYS:
            extra[key] = value

    # animeTitle 必填，保险起见兜底
    row.setdefault("anime_title", item.get("animeTitle", ""))

    if extra:
        try:
            row["extra_data"] = json.dumps(extra, ensure_ascii=False)
        except (TypeError, ValueError):
            row["extra_data"] = None

    now = get_now()
    row["fetched_at"] = now
    row["updated_at"] = now
    return row


# ============ 公开 API ============

async def upsert_items(session: AsyncSession, provider: str, items: List[Dict[str, Any]]) -> int:
    """批量 Upsert 外部日历条目。

    :param provider: 数据源标识（'bangumi' | 'trakt' | ...）
    :param items: 标准化条目列表，每项至少包含 externalId（或可推导出的 bangumiId/traktId）
    :return: 实际写入的条目数（跳过无 externalId 的项）
    """
    if not items:
        return 0

    rows = [r for r in (_build_row_values(provider, it) for it in items) if r is not None]
    if not rows:
        return 0

    dialect = session.bind.dialect.name
    if dialect == "mysql":
        stmt = mysql_insert(ExternalCalendarItem).values(rows)
        update_cols = {col: stmt.inserted[col] for col in rows[0].keys()
                       if col not in ("provider", "external_id")}
        stmt = stmt.on_duplicate_key_update(**update_cols)
    elif dialect == "postgresql":
        stmt = postgresql_insert(ExternalCalendarItem).values(rows)
        update_cols = {col: stmt.excluded[col] for col in rows[0].keys()
                       if col not in ("provider", "external_id")}
        stmt = stmt.on_conflict_do_update(
            index_elements=["provider", "external_id"],
            set_=update_cols,
        )
    else:
        raise NotImplementedError(f"upsert_items 尚未为数据库类型 '{dialect}' 实现")

    await session.execute(stmt)
    await session.commit()
    logger.debug(f"ExternalCalendarItem upsert: provider={provider} count={len(rows)}")
    return len(rows)


def _row_to_item(item: ExternalCalendarItem) -> Dict[str, Any]:
    """ORM 行转换为前端友好的 dict（驼峰命名 + 反序列化 extraData）。"""
    base = {
        "provider": item.provider,
        "externalId": item.externalId,
        "animeTitle": item.animeTitle,
        "titleZh": item.titleZh,
        "animeType": item.animeType,
        "season": item.season,
        "year": item.year,
        "airWeekday": item.airWeekday,
        "airTime": item.airTime,
        "airDate": item.airDate,
        "episodeCount": item.episodeCount,
        "latestEpisodeIndex": item.latestEpisodeIndex,
        "imageUrl": item.imageUrl,
        "rating": float(item.rating) if item.rating is not None else None,
        "bangumiId": item.bangumiId,
        "traktId": item.traktId,
        "tmdbId": item.tmdbId,
        "imdbId": item.imdbId,
        "localAnimeId": item.localAnimeId,
        "localSourceId": item.localSourceId,
        # 平台用户私人状态（OAuth 账号下的「我在追」记录）
        "platformWatchStatus": item.platformWatchStatus,
        "platformWatchedEpisodes": item.platformWatchedEpisodes,
        "platformRating": float(item.platformRating) if item.platformRating is not None else None,
        # 订阅意向
        "isSubscribed": bool(item.isSubscribed),
        "subscriptionStatus": item.subscriptionStatus,
        "subscriptionFailureCount": int(item.subscriptionFailureCount or 0),
        "fetchedAt": item.fetchedAt.isoformat() if item.fetchedAt else None,
    }
    # 合并 extraData 中的平台特有字段（不覆盖已有标准字段）
    if item.extraData:
        try:
            extra = json.loads(item.extraData)
            if isinstance(extra, dict):
                for k, v in extra.items():
                    base.setdefault(k, v)
        except (json.JSONDecodeError, TypeError):
            pass
    return base


async def get_by_provider(
    session: AsyncSession,
    provider: str,
    max_age_hours: Optional[int] = 24,
) -> List[Dict[str, Any]]:
    """读取某个 provider 的日历条目。

    :param max_age_hours: 仅返回 fetchedAt 在 N 小时内的数据；None 表示不过滤鲜度
    :return: 条目列表（dict 形式，已反序列化 extraData）
    """
    stmt = select(ExternalCalendarItem).where(ExternalCalendarItem.provider == provider)
    if max_age_hours is not None and max_age_hours > 0:
        cutoff = get_now() - timedelta(hours=max_age_hours)
        stmt = stmt.where(ExternalCalendarItem.fetchedAt >= cutoff)
    stmt = stmt.order_by(ExternalCalendarItem.airWeekday.asc(), ExternalCalendarItem.airTime.asc())
    result = await session.execute(stmt)
    return [_row_to_item(row) for row in result.scalars().all()]


async def get_all_fresh(
    session: AsyncSession,
    max_age_hours: Optional[int] = 24,
) -> Dict[str, List[Dict[str, Any]]]:
    """按 provider 分组返回所有新鲜的日历条目。

    :return: { 'bangumi': [...], 'trakt': [...] }
    """
    stmt = select(ExternalCalendarItem)
    if max_age_hours is not None and max_age_hours > 0:
        cutoff = get_now() - timedelta(hours=max_age_hours)
        stmt = stmt.where(ExternalCalendarItem.fetchedAt >= cutoff)
    result = await session.execute(stmt)
    grouped: Dict[str, List[Dict[str, Any]]] = {}
    for row in result.scalars().all():
        grouped.setdefault(row.provider, []).append(_row_to_item(row))
    return grouped


async def get_by_external_id(
    session: AsyncSession,
    provider: str,
    external_id: str,
) -> Optional[Dict[str, Any]]:
    """查询单条记录，常用于订阅/匹配场景的精确反查。"""
    stmt = select(ExternalCalendarItem).where(
        and_(
            ExternalCalendarItem.provider == provider,
            ExternalCalendarItem.externalId == str(external_id),
        )
    )
    result = await session.execute(stmt)
    row = result.scalar_one_or_none()
    return _row_to_item(row) if row else None


async def update_title_zh(
    session: AsyncSession,
    provider: str,
    external_id: str,
    title_zh: Optional[str],
    year: Optional[int] = None,
) -> bool:
    """补充某条记录的中文标题/年份（TMDB 懒加载场景使用）。"""
    stmt = select(ExternalCalendarItem).where(
        and_(
            ExternalCalendarItem.provider == provider,
            ExternalCalendarItem.externalId == str(external_id),
        )
    )
    row = (await session.execute(stmt)).scalar_one_or_none()
    if not row:
        return False
    changed = False
    if title_zh and row.titleZh != title_zh:
        row.titleZh = title_zh
        changed = True
    if year and not row.year:
        row.year = year
        changed = True
    if changed:
        row.updatedAt = get_now()
        await session.commit()
    return changed


async def update_platform_status(
    session: AsyncSession,
    provider: str,
    statuses: Dict[str, Dict[str, Any]],
) -> int:
    """批量更新某 provider 下所有条目的「平台用户私人状态」。

    :param statuses: { external_id: {'status': 'watching', 'watchedEps': 5, 'rating': 8.5} }
    :return: 实际更新的行数

    与 upsert_items 的区别：
    - upsert_items 用于公共日历数据（拉公共 API 时调用，会刷新 fetchedAt）
    - update_platform_status 仅更新平台用户私人字段（拉用户私有 API 时调用，不影响公共数据时间戳）
    """
    if not statuses:
        return 0

    stmt = select(ExternalCalendarItem).where(ExternalCalendarItem.provider == provider)
    rows = (await session.execute(stmt)).scalars().all()
    if not rows:
        return 0

    # 用 dict 索引提速
    row_by_id = {r.externalId: r for r in rows}
    now = get_now()
    updated = 0

    for ext_id, st in statuses.items():
        row = row_by_id.get(str(ext_id))
        if not row:
            continue
        new_status = st.get("status")
        new_eps = st.get("watchedEps")
        new_rating = st.get("rating")
        if (row.platformWatchStatus != new_status
                or row.platformWatchedEpisodes != new_eps
                or row.platformRating != new_rating):
            row.platformWatchStatus = new_status
            row.platformWatchedEpisodes = new_eps
            row.platformRating = new_rating
            row.updatedAt = now
            updated += 1

    if updated > 0:
        await session.commit()
        logger.debug(f"update_platform_status: provider={provider} 更新 {updated} 条")
    return updated


async def clear_platform_status(session: AsyncSession, provider: str) -> int:
    """清除某 provider 下所有条目的平台用户状态（断开 OAuth 后调用，避免显示陈旧数据）。"""
    from sqlalchemy import update as sa_update
    stmt = sa_update(ExternalCalendarItem).where(
        ExternalCalendarItem.provider == provider
    ).values(
        platformWatchStatus=None,
        platformWatchedEpisodes=None,
        platformRating=None,
        updatedAt=get_now(),
    )
    result = await session.execute(stmt)
    await session.commit()
    return result.rowcount or 0


async def cleanup_stale(session: AsyncSession, days: int = 30) -> int:
    """删除超过 N 天未更新的过期数据。返回删除条数。"""
    cutoff = get_now() - timedelta(days=days)
    stmt = delete(ExternalCalendarItem).where(ExternalCalendarItem.fetchedAt < cutoff)
    result = await session.execute(stmt)
    await session.commit()
    return result.rowcount


# ============ 订阅意向相关 ============

async def mark_subscribed(
    session: AsyncSession,
    provider: str,
    external_id: str,
    status: str = "pending",
    item: Optional[Dict[str, Any]] = None,
) -> bool:
    """把某个外部条目标记为已订阅（订阅意向）。

    :param status: 'pending' | 'importing' | 'imported' | 'failed'
    :param item: 找不到精确 external_id 时用于按 ID 反查现有外部记录；极端情况下才创建外部表记录
    :return: True 表示成功标记/创建
    """
    stmt = select(ExternalCalendarItem).where(
        and_(
            ExternalCalendarItem.provider == provider,
            ExternalCalendarItem.externalId == str(external_id),
        )
    )
    row = (await session.execute(stmt)).scalar_one_or_none()
    item = item or {}
    if not row:
        fallback_conditions = []
        if item.get("bangumiId"):
            fallback_conditions.append(ExternalCalendarItem.bangumiId == str(item.get("bangumiId")))
        if item.get("traktId"):
            fallback_conditions.append(ExternalCalendarItem.traktId == str(item.get("traktId")))
        if item.get("tmdbId") or item.get("traktTmdbId"):
            fallback_conditions.append(ExternalCalendarItem.tmdbId == str(item.get("tmdbId") or item.get("traktTmdbId")))
        if fallback_conditions:
            fallback_stmt = select(ExternalCalendarItem).where(
                ExternalCalendarItem.provider == provider,
                or_(*fallback_conditions),
            )
            row = (await session.execute(fallback_stmt)).scalar_one_or_none()
    if not row:
        row = ExternalCalendarItem(
            provider=provider,
            externalId=str(external_id),
            animeTitle=item.get("animeTitle") or "",
            animeType=item.get("animeType") or "tv_series",
            season=item.get("season"),
            year=item.get("year"),
            airWeekday=item.get("airWeekday"),
            airTime=item.get("airTime"),
            imageUrl=item.get("imageUrl"),
            bangumiId=item.get("bangumiId"),
            traktId=item.get("traktId"),
            tmdbId=item.get("tmdbId") or item.get("traktTmdbId"),
            isSubscribed=True,
            subscriptionStatus=status,
            subscriptionFailureCount=0,
            subscriptionLastAttemptAt=get_now() if status == "importing" else None,
            fetchedAt=get_now(),
            updatedAt=get_now(),
        )
        session.add(row)
        await session.commit()
        return True
    row.isSubscribed = True
    row.subscriptionStatus = status
    if status == "importing":
        row.subscriptionLastAttemptAt = get_now()
    row.updatedAt = get_now()
    await session.commit()
    return True


async def update_subscription_status(
    session: AsyncSession,
    provider: str,
    external_id: str,
    status: str,
    increment_failure: bool = False,
) -> bool:
    """更新订阅状态，可选自增失败计数。"""
    from sqlalchemy import update as sa_update
    values: Dict[str, Any] = {
        "subscriptionStatus": status,
        "updatedAt": get_now(),
    }
    if status == "importing":
        values["subscriptionLastAttemptAt"] = get_now()
    if increment_failure:
        values["subscriptionFailureCount"] = ExternalCalendarItem.subscriptionFailureCount + 1

    stmt = sa_update(ExternalCalendarItem).where(
        and_(
            ExternalCalendarItem.provider == provider,
            ExternalCalendarItem.externalId == str(external_id),
        )
    ).values(**values)
    result = await session.execute(stmt)
    await session.commit()
    return (result.rowcount or 0) > 0


async def get_pending_subscriptions(
    session: AsyncSession,
    max_failures: int = 3,
) -> List[Dict[str, Any]]:
    """获取所有待处理的订阅意向（pending 或 failed 但未超过重试上限）。

    定时任务用：每次扫描这批，触发 auto_search_and_import_task 把它们建库。
    """
    from sqlalchemy import or_
    stmt = select(ExternalCalendarItem).where(
        and_(
            ExternalCalendarItem.isSubscribed == True,  # noqa: E712
            or_(
                ExternalCalendarItem.subscriptionStatus == "pending",
                and_(
                    ExternalCalendarItem.subscriptionStatus == "failed",
                    ExternalCalendarItem.subscriptionFailureCount < max_failures,
                ),
            ),
        )
    )
    result = await session.execute(stmt)
    return [_row_to_item(row) for row in result.scalars().all()]


async def get_subscribed_external_ids(
    session: AsyncSession,
) -> Dict[str, set]:
    """返回 {bangumi: set, trakt: set, tmdb: set} 三个 ID 集合，
    用于 weekly 接口快速判断 isSubscribed。
    """
    stmt = select(
        ExternalCalendarItem.bangumiId,
        ExternalCalendarItem.traktId,
        ExternalCalendarItem.tmdbId,
    ).where(ExternalCalendarItem.isSubscribed == True)  # noqa: E712
    rows = (await session.execute(stmt)).all()
    bgm_ids: set = set()
    trakt_ids: set = set()
    tmdb_ids: set = set()
    for bgm, trakt, tmdb in rows:
        if bgm:
            bgm_ids.add(str(bgm))
        if trakt:
            trakt_ids.add(str(trakt))
        if tmdb:
            tmdb_ids.add(str(tmdb))
    return {"bangumi": bgm_ids, "trakt": trakt_ids, "tmdb": tmdb_ids}


async def unsubscribe(
    session: AsyncSession,
    provider: str,
    external_id: str,
) -> bool:
    """取消订阅（重置订阅意向相关字段，不影响公共日历数据）。"""
    from sqlalchemy import update as sa_update
    stmt = sa_update(ExternalCalendarItem).where(
        and_(
            ExternalCalendarItem.provider == provider,
            ExternalCalendarItem.externalId == str(external_id),
        )
    ).values(
        isSubscribed=False,
        subscriptionStatus=None,
        subscriptionFailureCount=0,
        subscriptionLastAttemptAt=None,
        updatedAt=get_now(),
    )
    result = await session.execute(stmt)
    await session.commit()
    return (result.rowcount or 0) > 0


