"""
ExternalCalendarRepository - 外部日历数据访问层
"""

import json
import logging
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional
from sqlalchemy import select, delete, and_, or_
from sqlalchemy.ext.asyncio import AsyncSession

from ..orm_models import ExternalCalendarItem, AnimeMetadata, AnimeSource
from .base import BaseRepository
from src.core.timezone import get_now

logger = logging.getLogger(__name__)


class ExternalCalendarRepository(BaseRepository[ExternalCalendarItem]):
    """外部日历 Repository"""

    async def clear_calendar_cache_items(self) -> int:
        """删除纯日历缓存，保留订阅意向及订阅扫描子候选项。"""
        stmt = select(ExternalCalendarItem.id, ExternalCalendarItem.extraData).where(
            ExternalCalendarItem.isSubscribed.is_(False)
        )
        rows = (await self._session.execute(stmt)).all()
        ids_to_delete = []
        for row_id, extra_raw in rows:
            if extra_raw:
                try:
                    extra = json.loads(extra_raw)
                    if isinstance(extra, dict) and extra.get("parentExternalId"):
                        continue
                except (ValueError, TypeError):
                    pass
            ids_to_delete.append(row_id)
        deleted = 0
        # 分批删除避免数据库参数数量上限；提交由 DatabaseService 统一负责。
        for offset in range(0, len(ids_to_delete), 500):
            result = await self._session.execute(
                delete(ExternalCalendarItem).where(
                    ExternalCalendarItem.id.in_(ids_to_delete[offset:offset + 500])
                )
            )
            deleted += result.rowcount or 0
        await self._session.flush()
        return deleted


    async def reconcile_imported_subscriptions(self) -> int:
        """通过本地元数据外部 ID 将已建库订阅收敛为 imported。"""
        rows = (await self._session.execute(
            select(ExternalCalendarItem).where(
                ExternalCalendarItem.isSubscribed.is_(True),
                ExternalCalendarItem.subscriptionStatus == "importing",
            )
        )).scalars().all()
        if not rows:
            return 0
        candidates = (await self._session.execute(
            select(
                AnimeMetadata.animeId, AnimeMetadata.bangumiId,
                AnimeMetadata.tmdbId, AnimeMetadata.traktId,
                AnimeSource.id, AnimeSource.incrementalRefreshEnabled,
            ).join(AnimeSource, AnimeSource.animeId == AnimeMetadata.animeId)
        )).all()
        lookup: Dict[str, Dict[str, tuple]] = {name: {} for name in ("bangumiId", "tmdbId", "traktId")}
        for anime_id, bgm, tmdb, trakt, source_id, enabled in candidates:
            for name, value in (("bangumiId", bgm), ("tmdbId", tmdb), ("traktId", trakt)):
                if value is None:
                    continue
                old = lookup[name].get(str(value))
                if old is None or (not old[2] and enabled):
                    lookup[name][str(value)] = (anime_id, source_id, enabled)
        count = 0
        for row in rows:
            hit = next((lookup[name].get(str(value)) for name, value in (
                ("bangumiId", row.bangumiId), ("tmdbId", row.tmdbId),
                ("traktId", row.traktId),
            ) if value and lookup[name].get(str(value))), None)
            if hit is None:
                continue
            row.localAnimeId, row.localSourceId = hit[:2]
            row.subscriptionStatus = "imported"
            row.updatedAt = get_now()
            count += 1
        if count:
            await self._session.flush()
        return count

    async def get_by_id(self, item_id: int) -> Optional[ExternalCalendarItem]:
        """根据 ID 获取日历项"""
        return await self._session.get(ExternalCalendarItem, item_id)

    async def get_all(self, **filters) -> List[ExternalCalendarItem]:
        """获取所有日历项"""
        stmt = select(ExternalCalendarItem)

        if 'provider' in filters:
            stmt = stmt.where(ExternalCalendarItem.provider == filters['provider'])

        stmt = stmt.order_by(ExternalCalendarItem.airDate.desc())
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def create(
        self,
        provider: str,
        external_id: str,
        anime_title: str,
        anime_type: str = 'tv_series',
        **extra_data
    ) -> ExternalCalendarItem:
        """创建日历项"""
        now = get_now()

        item = ExternalCalendarItem(
            provider=provider,
            externalId=external_id,
            animeTitle=anime_title,
            animeType=anime_type,
            fetchedAt=now,
            updatedAt=now,
            **extra_data
        )
        self._session.add(item)
        await self._session.flush()
        return item

    async def update(self, item_id: int, **data) -> Optional[ExternalCalendarItem]:
        """更新日历项"""
        item = await self.get_by_id(item_id)
        if not item:
            return None

        for key, value in data.items():
            if hasattr(item, key):
                setattr(item, key, value)

        item.updatedAt = get_now()
        await self._session.flush()
        return item

    async def delete(self, item_id: int) -> bool:
        """删除日历项"""
        item = await self.get_by_id(item_id)
        if not item:
            return False

        await self._session.delete(item)
        await self._session.flush()
        return True

    async def get_by_provider_and_external_id(
        self,
        provider: str,
        external_id: str
    ) -> Optional[ExternalCalendarItem]:
        """根据 provider 和 externalId 获取日历项"""
        stmt = select(ExternalCalendarItem).where(
            ExternalCalendarItem.provider == provider,
            ExternalCalendarItem.externalId == external_id
        )
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def get_recent_items(
        self,
        provider: Optional[str] = None,
        days: int = 30,
        limit: int = 100
    ) -> List[ExternalCalendarItem]:
        """获取最近的日历项"""
        cutoff_date = get_now() - timedelta(days=days)

        stmt = select(ExternalCalendarItem).where(
            ExternalCalendarItem.airDate >= cutoff_date
        )

        if provider:
            stmt = stmt.where(ExternalCalendarItem.provider == provider)

        stmt = stmt.order_by(ExternalCalendarItem.airDate.desc()).limit(limit)
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def check_items_freshness(
        self,
        provider: str,
        max_age_hours: int = 24
    ) -> bool:
        """检查数据是否新鲜（在 max_age_hours 内）"""
        cutoff_time = get_now() - timedelta(hours=max_age_hours)

        stmt = select(ExternalCalendarItem.id).where(
            ExternalCalendarItem.provider == provider,
            ExternalCalendarItem.fetchedAt >= cutoff_time
        ).limit(1)

        result = await self._session.execute(stmt)
        return result.scalar_one_or_none() is not None

    async def delete_by_provider(self, provider: str) -> int:
        """删除指定 provider 的所有项"""
        stmt = delete(ExternalCalendarItem).where(
            ExternalCalendarItem.provider == provider
        )
        result = await self._session.execute(stmt)
        await self._session.flush()
        return result.rowcount

    async def upsert_item(
        self,
        provider: str,
        external_id: str,
        anime_title: str,
        **data
    ) -> ExternalCalendarItem:
        """更新或插入日历项"""
        # 查找现有项
        existing = await self.get_by_provider_and_external_id(provider, external_id)

        if existing:
            # 更新现有项
            for key, value in data.items():
                if hasattr(existing, key):
                    setattr(existing, key, value)
            existing.animeTitle = anime_title
            existing.updatedAt = get_now()
            existing.fetchedAt = get_now()
            await self._session.flush()
            return existing
        else:
            # 创建新项
            return await self.create(
                provider=provider,
                external_id=external_id,
                anime_title=anime_title,
                **data
            )

    async def mark_subscribed(
        self,
        provider: str,
        external_id: str,
        status: str = "pending",
        item: Optional[Dict[str, Any]] = None,
    ) -> bool:
        """把某个外部条目标记为已订阅（订阅意向）。

        承接原 crud.external_calendar.mark_subscribed。
        与 crud 版的差异：不再接收 commit 参数，事务提交统一由
        DatabaseService.transaction() 负责，本方法只做 flush。

        Args:
            provider: 提供商名称
            external_id: 外部条目 ID
            status: 'pending' | 'importing' | 'imported' | 'failed'
            item: 精确 external_id 查不到时，用于按第三方 ID 反查现有记录；
                  极端情况下（仍查不到）才据此新建外部表记录

        Returns:
            True 表示成功标记或创建
        """
        row = await self.get_by_provider_and_external_id(provider, str(external_id))
        item = item or {}

        # why：外部源可能变更 externalId，此处按 bangumi/trakt/tmdb 等第三方 ID 兜底反查，
        # 避免重复插入同一部作品
        if not row:
            fallback_conditions = []
            if item.get("bangumiId"):
                fallback_conditions.append(
                    ExternalCalendarItem.bangumiId == str(item.get("bangumiId"))
                )
            if item.get("traktId"):
                fallback_conditions.append(
                    ExternalCalendarItem.traktId == str(item.get("traktId"))
                )
            if item.get("tmdbId") or item.get("traktTmdbId"):
                fallback_conditions.append(
                    ExternalCalendarItem.tmdbId
                    == str(item.get("tmdbId") or item.get("traktTmdbId"))
                )
            if fallback_conditions:
                stmt = select(ExternalCalendarItem).where(
                    ExternalCalendarItem.provider == provider,
                    or_(*fallback_conditions),
                )
                row = (await self._session.execute(stmt)).scalar_one_or_none()

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
            self._session.add(row)
            await self._session.flush()
            return True

        row.isSubscribed = True
        row.subscriptionStatus = status
        if status == "importing":
            row.subscriptionLastAttemptAt = get_now()
        row.updatedAt = get_now()
        await self._session.flush()
        return True

    async def upsert_subscription_target(
        self,
        provider: str,
        external_id: str,
        title: str,
        subscription_type: str,
        extra: Optional[Dict[str, Any]] = None,
        status: str = "pending",
    ) -> Dict[str, Any]:
        """创建或更新订阅目标，并返回 API 层使用的字典。"""
        row = await self.get_by_provider_and_external_id(provider, str(external_id))
        payload = dict(extra or {})
        payload["subscriptionType"] = subscription_type
        if row is None:
            row = ExternalCalendarItem(
                provider=provider,
                externalId=str(external_id),
                animeTitle=title,
                animeType=payload.get("animeType", "tv_series"),
                isSubscribed=True,
                subscriptionStatus=status,
                extraData=json.dumps(payload, ensure_ascii=False),
                fetchedAt=get_now(),
                updatedAt=get_now(),
            )
            self._session.add(row)
        else:
            row.animeTitle = title
            row.animeType = payload.get("animeType", row.animeType or "tv_series")
            row.isSubscribed = True
            row.subscriptionStatus = status
            row.extraData = json.dumps(payload, ensure_ascii=False)
            row.updatedAt = get_now()
        await self._session.flush()
        return {
            "id": row.id,
            "provider": row.provider,
            "externalId": row.externalId,
            "animeTitle": row.animeTitle,
            "subscriptionType": subscription_type,
            "subscriptionStatus": row.subscriptionStatus,
            "extraData": payload,
        }

    async def update_subscription_status(
        self,
        provider: str,
        external_id: str,
        status: str,
        increment_failure: bool = False
    ) -> bool:
        """更新订阅状态"""
        row = await self.get_by_provider_and_external_id(provider, str(external_id))
        if not row:
            return False

        row.subscriptionStatus = status
        row.subscriptionLastAttemptAt = get_now()
        if increment_failure:
            row.subscriptionFailureCount = (row.subscriptionFailureCount or 0) + 1
        row.updatedAt = get_now()
        await self._session.flush()
        return True

    async def unsubscribe(
        self,
        provider: str,
        external_id: str,
    ) -> bool:
        """取消订阅（重置订阅意向相关字段，不影响公共日历数据）"""
        row = await self.get_by_provider_and_external_id(provider, str(external_id))
        if not row:
            return False

        row.isSubscribed = False
        row.subscriptionStatus = None
        row.subscriptionFailureCount = 0
        row.subscriptionLastAttemptAt = None
        row.updatedAt = get_now()
        await self._session.flush()
        return True

    async def update_subscription_target(
        self,
        provider: str,
        external_id: str,
        enabled: Optional[bool] = None,
        extra_patch: Optional[Dict[str, Any]] = None,
        status: Optional[str] = None,
    ) -> bool:
        """
        修改订阅目标的启用状态、状态、备注/过滤条件等通用字段。

        enabled 暂停订阅写 extraData.enabled，保持 isSubscribed=True，避免暂停与取消混淆。
        """
        row = await self.get_by_provider_and_external_id(provider, str(external_id))
        if not row:
            return False

        extra: Dict[str, Any] = {}
        if row.extraData:
            if isinstance(row.extraData, dict):
                extra = dict(row.extraData)
            else:
                try:
                    loaded = json.loads(row.extraData)
                    if isinstance(loaded, dict):
                        extra = loaded
                except (json.JSONDecodeError, TypeError):
                    pass
        if enabled is not None:
            extra["enabled"] = enabled
        if extra_patch:
            extra.update(extra_patch)
        if status is not None:
            row.subscriptionStatus = status

        row.extraData = json.dumps(extra, ensure_ascii=False)
        row.updatedAt = get_now()
        await self._session.flush()
        return True

    async def update_subscription_next_scan(
        self,
        provider: str,
        external_id: str,
        next_scan_at: Optional[datetime] = None,
        last_error: Optional[str] = None,
    ) -> bool:
        """更新订阅目标的下次扫描时间和错误信息"""
        row = await self.get_by_provider_and_external_id(provider, str(external_id))
        if not row:
            return False

        extra: Dict[str, Any] = {}
        if row.extraData:
            if isinstance(row.extraData, dict):
                extra = dict(row.extraData)
            else:
                try:
                    loaded = json.loads(row.extraData)
                    if isinstance(loaded, dict):
                        extra = loaded
                except (json.JSONDecodeError, TypeError):
                    pass
        if next_scan_at is not None:
            extra["nextScanAt"] = next_scan_at.isoformat()
        if last_error is not None:
            extra["lastError"] = last_error
        else:
            extra.pop("lastError", None)

        row.extraData = json.dumps(extra, ensure_ascii=False)
        row.updatedAt = get_now()
        await self._session.flush()
        return True

    async def upsert_items(
        self,
        provider: str,
        items: List[Dict[str, Any]]
    ) -> int:
        """批量更新或插入日历项

        Args:
            provider: 提供商名称
            items: 日历项列表，每项必须包含 externalId 和 animeTitle

        Returns:
            成功插入/更新的条目数
        """
        count = 0
        for item_data in items:
            external_id = item_data.get("externalId")
            anime_title = item_data.get("animeTitle")

            if not external_id or not anime_title:
                logger.warning(f"跳过无效日历项: {item_data}")
                continue

            try:
                await self.upsert_item(
                    provider=provider,
                    external_id=str(external_id),
                    anime_title=anime_title,
                    animeType=item_data.get("animeType", "tv_series"),
                    season=item_data.get("season"),
                    year=item_data.get("year"),
                    airWeekday=item_data.get("airWeekday"),
                    airTime=item_data.get("airTime"),
                    airDate=item_data.get("airDate"),
                    imageUrl=item_data.get("imageUrl"),
                    bangumiId=item_data.get("bangumiId"),
                    traktId=item_data.get("traktId"),
                    tmdbId=item_data.get("tmdbId"),
                    rating=item_data.get("rating"),
                    titleZh=item_data.get("titleZh"),
                    titleEn=item_data.get("titleEn"),
                    titleJp=item_data.get("titleJp"),
                    extraData=item_data.get("extraData"),
                )
                count += 1
            except Exception as e:
                logger.error(f"批量插入日历项失败 {external_id}: {e}")

        return count

    async def get_subscribed_external_ids(self) -> Dict[str, set]:
        """获取所有已订阅条目的外部ID集合

        Returns:
            字典，键为 'bangumi', 'trakt', 'tmdb'，值为对应的 ID 集合
        """
        stmt = select(
            ExternalCalendarItem.bangumiId,
            ExternalCalendarItem.traktId,
            ExternalCalendarItem.tmdbId
        ).where(ExternalCalendarItem.isSubscribed == True)

        result = await self._session.execute(stmt)
        rows = result.all()

        bangumi_ids = set()
        trakt_ids = set()
        tmdb_ids = set()

        for row in rows:
            if row.bangumiId:
                bangumi_ids.add(str(row.bangumiId))
            if row.traktId:
                trakt_ids.add(str(row.traktId))
            if row.tmdbId:
                tmdb_ids.add(str(row.tmdbId))

        return {
            "bangumi": bangumi_ids,
            "trakt": trakt_ids,
            "tmdb": tmdb_ids,
        }

    async def list_calendar_items(
        self,
        providers: Optional[List[str]] = None,
        max_age_hours: int = 24,
        subscribed_only: bool = False
    ) -> List[Dict[str, Any]]:
        """列出日历项（可按 provider 和新鲜度过滤）

        Args:
            providers: 提供商列表，None 表示所有
            max_age_hours: 最大数据年龄（小时）
            subscribed_only: 是否只返回已订阅的项

        Returns:
            日历项字典列表
        """
        cutoff_time = get_now() - timedelta(hours=max_age_hours)

        stmt = select(ExternalCalendarItem).where(
            ExternalCalendarItem.fetchedAt >= cutoff_time
        )

        if providers:
            stmt = stmt.where(ExternalCalendarItem.provider.in_(providers))

        if subscribed_only:
            stmt = stmt.where(ExternalCalendarItem.isSubscribed == True)

        stmt = stmt.order_by(ExternalCalendarItem.airDate.desc())
        result = await self._session.execute(stmt)
        items = result.scalars().all()

        # 转换为字典列表
        return [
            {
                "id": item.id,
                "provider": item.provider,
                "externalId": item.externalId,
                "animeTitle": item.animeTitle,
                "animeType": item.animeType,
                "season": item.season,
                "year": item.year,
                "airWeekday": item.airWeekday,
                "airTime": item.airTime,
                "airDate": item.airDate,
                "imageUrl": item.imageUrl,
                "bangumiId": item.bangumiId,
                "traktId": item.traktId,
                "tmdbId": item.tmdbId,
                "rating": item.rating,
                "titleZh": item.titleZh,
                "titleEn": item.titleEn,
                "titleJp": item.titleJp,
                "isSubscribed": item.isSubscribed,
                "subscriptionStatus": item.subscriptionStatus,
                "extraData": item.extraData,
                "fetchedAt": item.fetchedAt,
                "updatedAt": item.updatedAt,
            }
            for item in items
        ]
