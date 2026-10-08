"""
ExternalCalendarQueryRepository：外部日历查询仓储
替代 crud/external_calendar.py 中的相关方法
"""

import json
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.timezone import get_now
from src.db.orm_models import ExternalCalendarItem


class ExternalCalendarQueryRepository:
    """
    外部日历查询仓储

    替代 external_calendar crud 中的:
    - get_all_fresh
    - upsert_items
    - update_platform_status
    """

    def __init__(self, session: AsyncSession):
        self._session = session

    def _row_to_item(self, row: ExternalCalendarItem) -> Dict[str, Any]:
        """ORM 对象转字典"""
        extra: Dict[str, Any] = {}
        if row.extraData:
            try:
                loaded = json.loads(row.extraData)
                if isinstance(loaded, dict):
                    extra = loaded
            except (json.JSONDecodeError, TypeError):
                pass
        return {
            "id": row.id,
            "provider": row.provider,
            "externalId": row.externalId,
            "animeTitle": row.animeTitle,
            "title": row.animeTitle,
            "titleZh": row.titleZh,
            "animeType": row.animeType,
            "season": row.season,
            "year": row.year,
            "imageUrl": row.imageUrl,
            "episodeCount": row.episodeCount,
            "latestEpisodeIndex": row.latestEpisodeIndex,
            "rating": row.rating,
            "airWeekday": row.airWeekday,
            "airTime": row.airTime,
            "bangumiId": row.bangumiId,
            "traktId": row.traktId,
            "tmdbId": row.tmdbId,
            "isSubscribed": row.isSubscribed,
            "subscriptionStatus": row.subscriptionStatus,
            "subscriptionType": extra.get("subscriptionType"),
            "enabled": extra.get("enabled", True),
            "nextScanAt": extra.get("nextScanAt"),
            "extraData": extra,
        }

    async def get_by_id_as_dict(self, item_id: int) -> Optional[Dict[str, Any]]:
        """按主键获取外部日历条目并转换为 API 字典。"""
        row = await self._session.get(ExternalCalendarItem, item_id)
        return self._row_to_item(row) if row else None

    async def get_by_external_id_as_dict(
        self, provider: str, external_id: str
    ) -> Optional[Dict[str, Any]]:
        """按提供方和外部 ID 获取订阅条目字典。"""
        stmt = select(ExternalCalendarItem).where(
            ExternalCalendarItem.provider == provider,
            ExternalCalendarItem.externalId == str(external_id),
        )
        row = (await self._session.execute(stmt)).scalar_one_or_none()
        return self._row_to_item(row) if row else None


    async def get_all_fresh(
        self,
        max_age_hours: Optional[int] = 24
    ) -> Dict[str, List[Dict[str, Any]]]:
        """
        按 provider 分组返回所有新鲜的日历条目

        替代 external_calendar.get_all_fresh

        Args:
            max_age_hours: 最大时效（小时）

        Returns:
            { 'bangumi': [...], 'trakt': [...] }
        """
        stmt = select(ExternalCalendarItem)
        if max_age_hours is not None and max_age_hours > 0:
            cutoff = get_now() - timedelta(hours=max_age_hours)
            stmt = stmt.where(ExternalCalendarItem.fetchedAt >= cutoff)
        result = await self._session.execute(stmt)

        grouped: Dict[str, List[Dict[str, Any]]] = {}
        for row in result.scalars().all():
            grouped.setdefault(row.provider, []).append(self._row_to_item(row))
        return grouped

    async def upsert_items(
        self,
        provider: str,
        items: List[Dict[str, Any]]
    ) -> int:
        """
        批量 Upsert 外部日历条目

        替代 external_calendar.upsert_items

        Args:
            provider: 数据源标识（'bangumi' | 'trakt' | ...）
            items: 标准化条目列表

        Returns:
            实际写入的条目数
        """
        if not items:
            return 0

        count = 0
        for item in items:
            external_id = item.get("externalId")
            if not external_id:
                continue

            # 查找或创建
            stmt = select(ExternalCalendarItem).where(
                ExternalCalendarItem.provider == provider,
                ExternalCalendarItem.externalId == external_id
            )
            result = await self._session.execute(stmt)
            row = result.scalar_one_or_none()

            if row:
                # 更新
                for key, value in item.items():
                    if key not in {"provider", "externalId"} and hasattr(row, key):
                        setattr(row, key, value)
                row.fetchedAt = get_now()
            else:
                # 创建
                row = ExternalCalendarItem(
                    provider=provider,
                    externalId=external_id,
                    fetchedAt=get_now(),
                    **{
                        k: v for k, v in item.items()
                        if k not in {"provider", "externalId"}
                    }
                )
                self._session.add(row)
            count += 1

        return count

    async def update_platform_status(
        self,
        provider: str,
        statuses: Dict[str, Dict[str, Any]]
    ) -> int:
        """
        批量更新某 provider 下所有条目的「平台用户私人状态」

        替代 external_calendar.update_platform_status

        Args:
            provider: 数据源标识
            statuses: { external_id: {'status': 'watching', 'watchedEps': 5, 'rating': 8.5} }

        Returns:
            实际更新的行数
        """
        if not statuses:
            return 0

        count = 0
        for external_id, status_data in statuses.items():
            stmt = select(ExternalCalendarItem).where(
                ExternalCalendarItem.provider == provider,
                ExternalCalendarItem.externalId == external_id
            )
            result = await self._session.execute(stmt)
            row = result.scalar_one_or_none()

            if row:
                for key, value in status_data.items():
                    if hasattr(row, key):
                        setattr(row, key, value)
                count += 1

        return count

    async def get_subscribed_external_ids(self) -> Dict[str, set]:
        """
        返回 {bangumi: set, trakt: set, tmdb: set} 三个 ID 集合，
        用于 weekly 接口快速判断 isSubscribed。
        """
        stmt = select(
            ExternalCalendarItem.bangumiId,
            ExternalCalendarItem.traktId,
            ExternalCalendarItem.tmdbId,
        ).where(ExternalCalendarItem.isSubscribed == True)  # noqa: E712
        rows = (await self._session.execute(stmt)).all()
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

    async def list_calendar_items(
        self,
        providers: List[str],
        max_age_hours: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        """
        查询弹幕源（如 Bilibili）的追番日历条目，供 /calendar/weekly 聚合进周列/未知列。

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
        rows = (await self._session.execute(stmt)).scalars().all()

        items: List[Dict[str, Any]] = []
        for row in rows:
            data = self._row_to_item(row)
            # 跳过订阅扫描产生的子候选项（如 UP 主下的单个视频），只要平台原生日历条目
            if data.get("parentExternalId"):
                continue
            items.append(data)
        return items

    async def list_subscription_targets(
        self,
        provider: Optional[str] = None,
        subscription_type: Optional[str] = None,
        status: Optional[str] = None,
        keyword: Optional[str] = None,
        page: int = 1,
        page_size: int = 20,
    ) -> Dict[str, Any]:
        """
        查询订阅目标（isSubscribed=True）。

        通过 provider 与 extraData.subscriptionType 做通用过滤；subscriptionType 存于
        extraData，因此在 Python 层过滤（MVP 数据量可接受）。
        :return: {"total": int, "list": [...]}
        """
        stmt = select(ExternalCalendarItem).where(ExternalCalendarItem.isSubscribed == True)  # noqa: E712
        if provider:
            stmt = stmt.where(ExternalCalendarItem.provider == provider)
        rows = (await self._session.execute(stmt)).scalars().all()

        items: List[Dict[str, Any]] = []
        for row in rows:
            data = self._row_to_item(row)
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

    async def get_pending_subscriptions(
        self,
        max_failures: int = 3,
    ) -> List[Dict[str, Any]]:
        """
        获取所有待处理的订阅意向（pending 或 failed 但未超过重试上限）。

        定时任务用：每次扫描这批，触发 auto_search_and_import_task 把它们建库。
        """
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
        rows = (await self._session.execute(stmt)).scalars().all()
        return [self._row_to_item(row) for row in rows]

    async def get_due_subscription_targets(
        self,
        provider: Optional[str] = None,
        limit: int = 50,
    ) -> List[Dict[str, Any]]:
        """
        读取到期、且启用中的订阅目标，供 SubscriptionScanJob 扫描。

        到期判断：extraData.nextScanAt 为空（从未扫描）或已过当前时间。
        仅返回带 subscriptionType 的目标，避免把普通日历订阅当订阅扫描目标。
        """
        stmt = select(ExternalCalendarItem).where(ExternalCalendarItem.isSubscribed == True)  # noqa: E712
        if provider:
            stmt = stmt.where(ExternalCalendarItem.provider == provider)
        rows = (await self._session.execute(stmt)).scalars().all()

        now = get_now()
        due: List[Dict[str, Any]] = []
        for row in rows:
            data = self._row_to_item(row)
            if not data.get("subscriptionType"):
                continue
            if data.get("enabled") is False:
                continue
            next_scan_str = data.get("nextScanAt")
            if next_scan_str:
                try:
                    next_scan = datetime.fromisoformat(next_scan_str)
                    if next_scan > now:
                        continue
                except (ValueError, TypeError):
                    pass
            due.append(data)
            if len(due) >= limit:
                break

        return due

    async def list_explore_items(
        self,
        provider: Optional[str] = None,
        category: Optional[str] = None,
        keyword: Optional[str] = None,
        page: int = 1,
        page_size: int = 30,
    ) -> Dict[str, Any]:
        """
        查询探索榜单条目（airWeekday 为空、非订阅候选项的外部探索数据）。

        供「探索发现」海报网格分页展示。category 对应 extraData.exploreCategory。
        :return: {"total": int, "list": [...]}
        """
        stmt = select(ExternalCalendarItem).where(ExternalCalendarItem.isSubscribed == False)  # noqa: E712
        if provider:
            stmt = stmt.where(ExternalCalendarItem.provider == provider)

        rows = (await self._session.execute(stmt)).scalars().all()

        items: List[Dict[str, Any]] = []
        for row in rows:
            data = self._row_to_item(row)
            # 跳过日历条目（有 airWeekday）和订阅候选项（有 parentExternalId）
            if data.get("airWeekday") or data.get("parentExternalId"):
                continue
            if category and data.get("exploreCategory") != category:
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
