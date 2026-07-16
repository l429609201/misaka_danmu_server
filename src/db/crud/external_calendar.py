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
from datetime import datetime, timedelta
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

async def upsert_items(
    session: AsyncSession,
    provider: str,
    items: List[Dict[str, Any]],
    commit: bool = True,
) -> int:
    """批量 Upsert 外部日历条目。

    :param provider: 数据源标识（'bangumi' | 'trakt' | ...）
    :param items: 标准化条目列表，每项至少包含 externalId（或可推导出的 bangumiId/traktId）
    :param commit: 是否立即提交；组合业务应传 False 并在外层统一提交
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
    if commit:
        await session.commit()
    logger.debug(f"ExternalCalendarItem upsert: provider={provider} count={len(rows)}")
    return len(rows)


def _row_to_item(item: ExternalCalendarItem) -> Dict[str, Any]:
    """ORM 行转换为前端友好的 dict（驼峰命名 + 反序列化 extraData）。"""
    base = {
        "id": item.id,
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


async def get_by_id(
    session: AsyncSession,
    item_id: int,
) -> Optional[Dict[str, Any]]:
    """按主键查询单条记录，供订阅 API 按 id 操作时反查 provider/externalId。"""
    stmt = select(ExternalCalendarItem).where(ExternalCalendarItem.id == item_id)
    row = (await session.execute(stmt)).scalar_one_or_none()
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
    commit: bool = True,
) -> bool:
    """把某个外部条目标记为已订阅（订阅意向）。

    :param status: 'pending' | 'importing' | 'imported' | 'failed'
    :param item: 找不到精确 external_id 时用于按 ID 反查现有外部记录；极端情况下才创建外部表记录
    :param commit: 是否立即提交；组合业务应传 False 并在外层统一提交
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
        if commit:
            await session.commit()
        else:
            await session.flush()
        return True
    row.isSubscribed = True
    row.subscriptionStatus = status
    if status == "importing":
        row.subscriptionLastAttemptAt = get_now()
    row.updatedAt = get_now()
    if commit:
        await session.commit()
    else:
        await session.flush()
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


# ============ 通用订阅目标 / 候选项 CRUD ============
# 设计依据：docs/subscription_page_implementation_plan.md 第 10.4 节。
# 不新增 Bilibili 专表，全部复用 external_calendar_item：
#   - 订阅目标：isSubscribed=True，extraData.subscriptionType 区分类型
#   - 候选项：  isSubscribed=False，extraData.parentExternalId 关联父目标


def _now_iso() -> str:
    """统一的 ISO 时间字符串，用于写入 extraData 内的时间字段。"""
    return get_now().isoformat()


def _parse_iso(value: Optional[str]) -> Optional[datetime]:
    """解析 extraData 内的 ISO 时间字符串；解析失败返回 None。"""
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except (ValueError, TypeError):
        return None


async def _merge_extra_data(
    row: ExternalCalendarItem,
    patch: Optional[Dict[str, Any]],
) -> None:
    """把 patch 合并进某行的 extraData（读出-合并-写回），不提交事务。

    upsert_items 会整体覆盖 extra_data，因此「局部更新 extraData」必须走这里。
    """
    if not patch:
        return
    current: Dict[str, Any] = {}
    if row.extraData:
        try:
            loaded = json.loads(row.extraData)
            if isinstance(loaded, dict):
                current = loaded
        except (json.JSONDecodeError, TypeError):
            current = {}
    current.update(patch)
    try:
        row.extraData = json.dumps(current, ensure_ascii=False)
    except (TypeError, ValueError):
        # 不可序列化时丢弃本次 patch，避免写坏整列
        logger.warning("订阅目标 extraData 合并失败（不可序列化），已跳过本次 patch")


async def upsert_subscription_target(
    session: AsyncSession,
    provider: str,
    external_id: str,
    title: str,
    subscription_type: str,
    extra: Optional[Dict[str, Any]] = None,
    status: str = "pending",
    commit: bool = True,
) -> Dict[str, Any]:
    """通用创建/更新一个订阅目标。

    内部复用 upsert_items + mark_subscribed，把 subscriptionType 与 provider 私有字段
    平铺进 payload，由 _build_row_values 收集到 extraData。
    :param commit: 是否立即提交；批量扫描应传 False 并在外层统一提交
    :return: 写入后的订阅目标 dict
    """
    extra = dict(extra or {})
    extra.setdefault("enabled", True)
    payload: Dict[str, Any] = {
        "provider": provider,
        "externalId": str(external_id),
        "animeTitle": title or "",
        "animeType": extra.get("animeType", "subscription"),
        "subscriptionType": subscription_type,
        **extra,
    }
    try:
        await upsert_items(session, provider, [payload], commit=False)
        await mark_subscribed(
            session,
            provider,
            str(external_id),
            status=status,
            item=payload,
            commit=False,
        )
        # why：返回值查询仍属于写入事务，必须在提交前完成；否则提交后查询失败会出现“已落库但接口报错”。
        item = await get_by_external_id(session, provider, str(external_id))
        if commit:
            # why：基础条目与订阅状态是同一业务对象，避免任一步失败留下半成品。
            await session.commit()
        return item or payload
    except BaseException:
        if commit:
            await session.rollback()
        raise


async def list_subscription_targets(
    session: AsyncSession,
    provider: Optional[str] = None,
    subscription_type: Optional[str] = None,
    status: Optional[str] = None,
    keyword: Optional[str] = None,
    page: int = 1,
    page_size: int = 20,
) -> Dict[str, Any]:
    """查询订阅目标（isSubscribed=True）。

    通过 provider 与 extraData.subscriptionType 做通用过滤；subscriptionType 存于
    extraData，因此在 Python 层过滤（MVP 数据量可接受）。
    :return: {"total": int, "list": [...]}
    """
    stmt = select(ExternalCalendarItem).where(ExternalCalendarItem.isSubscribed == True)  # noqa: E712
    if provider:
        stmt = stmt.where(ExternalCalendarItem.provider == provider)
    rows = (await session.execute(stmt)).scalars().all()

    items: List[Dict[str, Any]] = []
    for row in rows:
        data = _row_to_item(row)
        if subscription_type and data.get("subscriptionType") != subscription_type:
            continue
        if status and data.get("subscriptionStatus") != status:
            continue
        if keyword:
            kw = keyword.lower()
            haystack = f"{data.get('animeTitle') or ''}{data.get('externalId') or ''}".lower()
            if kw not in haystack:
                continue
        items.append(data)

    total = len(items)
    start = max(0, (page - 1) * page_size)
    return {"total": total, "list": items[start:start + page_size]}


async def update_subscription_target(
    session: AsyncSession,
    provider: str,
    external_id: str,
    enabled: Optional[bool] = None,
    extra_patch: Optional[Dict[str, Any]] = None,
    status: Optional[str] = None,
) -> bool:
    """修改订阅目标的启用状态、状态、备注/过滤条件等通用字段。

    enabled 暂停订阅写 extraData.enabled，保持 isSubscribed=True，避免暂停与取消混淆。
    """
    stmt = select(ExternalCalendarItem).where(
        and_(
            ExternalCalendarItem.provider == provider,
            ExternalCalendarItem.externalId == str(external_id),
        )
    )
    row = (await session.execute(stmt)).scalar_one_or_none()
    if not row:
        return False

    patch = dict(extra_patch or {})
    if enabled is not None:
        patch["enabled"] = bool(enabled)
    await _merge_extra_data(row, patch)

    if status is not None:
        row.subscriptionStatus = status
    row.updatedAt = get_now()
    await session.commit()
    return True


async def upsert_subscription_item(
    session: AsyncSession,
    provider: str,
    external_id: str,
    title: str,
    subscription_type: str,
    parent_external_id: str,
    extra: Optional[Dict[str, Any]] = None,
    status: str = "waiting",
    commit: bool = True,
) -> Dict[str, Any]:
    """写入扫描产生的候选项（视频候选 / 番剧分集候选）。

    候选项 isSubscribed=False，通过 extraData.parentExternalId 关联父订阅目标。
    status 存入 extraData.itemStatus，避免与父目标的 subscriptionStatus 语义混淆。
    :param commit: 是否立即提交；批量扫描应传 False 并在外层统一提交
    """
    extra = dict(extra or {})
    extra["parentExternalId"] = parent_external_id
    extra["itemStatus"] = status
    payload: Dict[str, Any] = {
        "provider": provider,
        "externalId": str(external_id),
        "animeTitle": title or "",
        "animeType": extra.get("animeType", "episode_candidate"),
        "subscriptionType": subscription_type,
        **extra,
    }
    try:
        await upsert_items(session, provider, [payload], commit=False)
        # why：候选项返回值查询必须早于提交，确保查询失败时仍能完整回滚写入。
        item = await get_by_external_id(session, provider, str(external_id))
        if commit:
            await session.commit()
        return item or payload
    except BaseException:
        if commit:
            await session.rollback()
        raise


async def list_subscription_items(
    session: AsyncSession,
    parent_external_id: Optional[str] = None,
    provider: Optional[str] = None,
    subscription_type: Optional[str] = None,
    status: Optional[str] = None,
    keyword: Optional[str] = None,
    page: int = 1,
    page_size: int = 20,
) -> Dict[str, Any]:
    """查询订阅目标产生的候选项（isSubscribed=False）。

    通过 extraData.parentExternalId / itemStatus 过滤，均在 Python 层完成。
    :return: {"total": int, "list": [...]}
    """
    stmt = select(ExternalCalendarItem).where(ExternalCalendarItem.isSubscribed == False)  # noqa: E712
    if provider:
        stmt = stmt.where(ExternalCalendarItem.provider == provider)
    rows = (await session.execute(stmt)).scalars().all()

    items: List[Dict[str, Any]] = []
    for row in rows:
        data = _row_to_item(row)
        # 只返回真正的候选项（带 parentExternalId）
        if not data.get("parentExternalId"):
            continue
        if parent_external_id and data.get("parentExternalId") != parent_external_id:
            continue
        if subscription_type and data.get("subscriptionType") != subscription_type:
            continue
        if status and data.get("itemStatus") != status:
            continue
        if keyword:
            kw = keyword.lower()
            haystack = f"{data.get('animeTitle') or ''}{data.get('externalId') or ''}".lower()
            if kw not in haystack:
                continue
        items.append(data)

    total = len(items)
    start = max(0, (page - 1) * page_size)
    return {"total": total, "list": items[start:start + page_size]}


async def _set_item_status(
    session: AsyncSession,
    provider: str,
    external_id: str,
    item_status: str,
) -> bool:
    """更新候选项的 extraData.itemStatus。"""
    stmt = select(ExternalCalendarItem).where(
        and_(
            ExternalCalendarItem.provider == provider,
            ExternalCalendarItem.externalId == str(external_id),
        )
    )
    row = (await session.execute(stmt)).scalar_one_or_none()
    if not row:
        return False
    await _merge_extra_data(row, {"itemStatus": item_status})
    row.updatedAt = get_now()
    await session.commit()
    return True


async def set_subscription_item_status(
    session: AsyncSession,
    provider: str,
    external_id: str,
    item_status: str,
) -> bool:
    """公开：推进候选项状态（如 importing/imported/failed），供 SubscriptionScanJob 使用。"""
    return await _set_item_status(session, provider, str(external_id), item_status)


async def retry_subscription_item(
    session: AsyncSession,
    provider: str,
    external_id: str,
) -> bool:
    """将候选项重置为 waiting，等待下一轮扫描重新处理。"""
    return await _set_item_status(session, provider, str(external_id), "waiting")


async def ignore_subscription_item(
    session: AsyncSession,
    provider: str,
    external_id: str,
) -> bool:
    """忽略候选项。"""
    return await _set_item_status(session, provider, str(external_id), "ignored")


async def get_due_subscription_targets(
    session: AsyncSession,
    provider: Optional[str] = None,
    limit: int = 50,
) -> List[Dict[str, Any]]:
    """读取到期、且启用中的订阅目标，供 SubscriptionScanJob 扫描。

    到期判断：extraData.nextScanAt 为空（从未扫描）或已过当前时间。
    仅返回带 subscriptionType 的目标，避免把普通日历订阅当订阅扫描目标。
    """
    stmt = select(ExternalCalendarItem).where(ExternalCalendarItem.isSubscribed == True)  # noqa: E712
    if provider:
        stmt = stmt.where(ExternalCalendarItem.provider == provider)
    rows = (await session.execute(stmt)).scalars().all()

    now = get_now()
    due: List[Dict[str, Any]] = []
    for row in rows:
        data = _row_to_item(row)
        if not data.get("subscriptionType"):
            continue
        if data.get("enabled") is False:
            continue
        next_scan = _parse_iso(data.get("nextScanAt"))
        if next_scan is None or next_scan <= now:
            due.append(data)
        if len(due) >= limit:
            break
    return due


async def update_subscription_next_scan(
    session: AsyncSession,
    provider: str,
    external_id: str,
    next_scan_at: Optional[datetime] = None,
    last_error: Optional[str] = None,
) -> bool:
    """扫描完成后更新 extraData.lastScanAt / nextScanAt / lastError。"""
    stmt = select(ExternalCalendarItem).where(
        and_(
            ExternalCalendarItem.provider == provider,
            ExternalCalendarItem.externalId == str(external_id),
        )
    )
    row = (await session.execute(stmt)).scalar_one_or_none()
    if not row:
        return False
    patch: Dict[str, Any] = {
        "lastScanAt": _now_iso(),
        "lastError": last_error,
    }
    if next_scan_at is not None:
        patch["nextScanAt"] = next_scan_at.isoformat()
    await _merge_extra_data(row, patch)
    row.updatedAt = get_now()
    await session.commit()
    return True


async def list_explore_items(
    session: AsyncSession,
    provider: Optional[str] = None,
    category: Optional[str] = None,
    keyword: Optional[str] = None,
    page: int = 1,
    page_size: int = 30,
) -> Dict[str, Any]:
    """查询探索榜单条目（airWeekday 为空、非订阅候选项的外部探索数据）。

    供「探索发现」海报网格分页展示。category 对应 extraData.exploreCategory。
    :return: {"total": int, "list": [...]}
    """
    stmt = select(ExternalCalendarItem).where(ExternalCalendarItem.isSubscribed == False)  # noqa: E712
    if provider:
        stmt = stmt.where(ExternalCalendarItem.provider == provider)
    rows = (await session.execute(stmt)).scalars().all()

    items: List[Dict[str, Any]] = []
    for row in rows:
        data = _row_to_item(row)
        # 只要探索榜单条目：带 exploreCategory，且不是订阅产生的候选项（无 parentExternalId）
        if not data.get("exploreCategory"):
            continue
        if data.get("parentExternalId"):
            continue
        if category and data.get("exploreCategory") != category:
            continue
        if keyword:
            kw = keyword.lower()
            haystack = f"{data.get('animeTitle') or ''}{data.get('titleZh') or ''}".lower()
            if kw not in haystack:
                continue
        items.append(data)

    # 探索榜单按评分倒序（无评分排后）
    items.sort(key=lambda x: (x.get("rating") is None, -(x.get("rating") or 0)))
    total = len(items)
    start = max(0, (page - 1) * page_size)
    return {"total": total, "list": items[start:start + page_size]}


async def list_calendar_items(
    session: AsyncSession,
    providers: List[str],
    max_age_hours: Optional[int] = None,
) -> List[Dict[str, Any]]:
    """查询弹幕源（如 Bilibili）的追番日历条目，供 /calendar/weekly 聚合进周列/未知列。

    与 list_explore_items 区别：本方法不分页、不要求 exploreCategory，只按 provider 过滤，
    返回所有非订阅候选项（无 parentExternalId）的条目。airWeekday 有值进周列，为空进未知列。

    :param providers: 数据源标识列表（如 ['bilibili']）
    :param max_age_hours: 仅返回 fetchedAt 在 N 小时内的数据；None 表示不过滤鲜度
    :return: 条目列表（dict 形式，已反序列化 extraData）
    """
    if not providers:
        return []
    stmt = select(ExternalCalendarItem).where(ExternalCalendarItem.provider.in_(providers))
    if max_age_hours is not None and max_age_hours > 0:
        cutoff = get_now() - timedelta(hours=max_age_hours)
        stmt = stmt.where(ExternalCalendarItem.fetchedAt >= cutoff)
    rows = (await session.execute(stmt)).scalars().all()

    items: List[Dict[str, Any]] = []
    for row in rows:
        data = _row_to_item(row)
        # 跳过订阅扫描产生的子候选项（如 UP 主下的单个视频），只要平台原生日历条目
        if data.get("parentExternalId"):
            continue
        items.append(data)
    return items


