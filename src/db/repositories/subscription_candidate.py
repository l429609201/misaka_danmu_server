"""
SubscriptionCandidateRepository - 订阅候选项数据访问层

对应表 subscription_candidate_item（ORM 类 SubscriptionCandidateItem）。
存储合集/UP主/番剧扫描出的分集候选列表，与 ExternalCalendarItem 为父子关系。
本层只做持久化，事务提交由上层 DatabaseService.transaction() 控制。
"""

import json
import logging
from typing import Any, Dict, List, Optional

from sqlalchemy import delete as sa_delete, func, select
from sqlalchemy.dialects.mysql import insert as mysql_insert
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.timezone import get_now
from src.db.orm_models import Episode, ExternalCalendarItem, SubscriptionCandidateItem

from .base import BaseRepository

logger = logging.getLogger(__name__)


class SubscriptionCandidateRepository(BaseRepository[SubscriptionCandidateItem]):
    """订阅候选项 Repository"""

    async def get_by_id(self, candidate_id: int) -> Optional[SubscriptionCandidateItem]:
        """根据主键 ID 获取候选项"""
        return await self._session.get(SubscriptionCandidateItem, candidate_id)

    async def get_all(self, **filters) -> List[SubscriptionCandidateItem]:
        """获取所有候选项，按创建时间倒序"""
        stmt = select(SubscriptionCandidateItem).order_by(
            SubscriptionCandidateItem.createdAt.desc()
        )
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def get_by_parent_id(self, parent_id: int) -> List[SubscriptionCandidateItem]:
        """获取某个订阅目标（ExternalCalendarItem）下的全部候选分集"""
        stmt = (
            select(SubscriptionCandidateItem)
            .where(SubscriptionCandidateItem.parentId == parent_id)
            .order_by(SubscriptionCandidateItem.createdAt.desc())
        )
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def get_by_external_id(
        self, parent_id: int, external_id: str
    ) -> Optional[SubscriptionCandidateItem]:
        """按 (parent_id, external_id) 唯一约束定位候选项"""
        stmt = select(SubscriptionCandidateItem).where(
            SubscriptionCandidateItem.parentId == parent_id,
            SubscriptionCandidateItem.externalId == external_id,
        )
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def create(self, **kwargs) -> SubscriptionCandidateItem:
        """
        创建候选项

        必填字段：parentId、provider、externalId
        可选字段：title、extraData
        """
        kwargs.setdefault("createdAt", get_now())
        candidate = SubscriptionCandidateItem(**kwargs)
        self._session.add(candidate)
        await self._session.flush()
        return candidate

    async def update(self, candidate_id: int, **kwargs) -> Optional[SubscriptionCandidateItem]:
        """按主键更新候选项字段"""
        candidate = await self.get_by_id(candidate_id)
        if not candidate:
            return None

        for key, value in kwargs.items():
            if hasattr(candidate, key):
                setattr(candidate, key, value)

        await self._session.flush()
        return candidate

    async def upsert_candidates(
        self, parent_id: int, provider: str, items: List[Dict[str, Any]]
    ) -> int:
        """批量写入候选项（ON DUPLICATE KEY UPDATE title/extraData）。

        why: 自 crud/subscription_candidate.upsert_candidates 迁入，逻辑一致，
             但去掉内部 commit —— 事务边界交由上层 transaction() 控制，
             避免与调用方「候选项+扫描时间同批提交」的语义冲突。

        Args:
            parent_id: 父订阅目标 ID（external_calendar_item.id）
            provider: 来源标识（bilibili/...）
            items: 候选项列表，每项需含 externalId，可选 title/extraData

        Returns:
            写入或更新的条目数
        """
        if not items:
            return 0

        values = []
        for item in items:
            external_id = item.get("externalId")
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
                "title": item.get("title") or "",
                "extra_data": extra_json,
            })

        if not values:
            return 0

        stmt = mysql_insert(SubscriptionCandidateItem).values(values)
        stmt = stmt.on_duplicate_key_update(
            title=stmt.inserted.title,
            extra_data=stmt.inserted.extra_data,
        )
        await self._session.execute(stmt)
        await self._session.flush()
        logger.info(
            f"upsert_candidates: parent_id={parent_id}, provider={provider}, count={len(values)}"
        )
        return len(values)

    async def list_with_import_status(self, parent_id: int) -> List[Dict[str, Any]]:
        """查询某订阅目标下全部候选项，JOIN episode 补 isImported 字段。

        why: 自 crud/subscription_candidate.list_candidates_with_import_status 迁入。
             候选项 externalId 可能带 "video:"/"collection:" 前缀，而
             episode.providerEpisodeId 存纯 ID，故用 replace 剥前缀再比较，
             否则永远匹配不上。

        Args:
            parent_id: 父订阅目标 ID

        Returns:
            [{id, externalId, title, provider, extraData, isImported}, ...]
        """
        stmt = (
            select(
                SubscriptionCandidateItem.id,
                SubscriptionCandidateItem.externalId,
                SubscriptionCandidateItem.title,
                SubscriptionCandidateItem.provider,
                SubscriptionCandidateItem.extraData,
                Episode.id.isnot(None).label("is_imported"),
            )
            .outerjoin(
                Episode,
                Episode.providerEpisodeId == func.replace(
                    func.replace(SubscriptionCandidateItem.externalId, "video:", ""),
                    "collection:", ""
                ),
            )
            .where(SubscriptionCandidateItem.parentId == parent_id)
            .order_by(SubscriptionCandidateItem.id)
        )
        result = await self._session.execute(stmt)

        out: List[Dict[str, Any]] = []
        for r in result.all():
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

    async def list_subscription_items(
        self,
        parent_external_id: Optional[str] = None,
        provider: Optional[str] = None,
        subscription_type: Optional[str] = None,
        status: Optional[str] = None,
        keyword: Optional[str] = None,
        page: int = 1,
        page_size: int = 20,
    ) -> Dict[str, Any]:
        """分页查询候选项，并从扩展字段中读取订阅类型和处理状态。"""
        stmt = select(SubscriptionCandidateItem, ExternalCalendarItem).join(
            ExternalCalendarItem,
            ExternalCalendarItem.id == SubscriptionCandidateItem.parentId,
        )
        if parent_external_id:
            stmt = stmt.where(ExternalCalendarItem.externalId == parent_external_id)
        if provider:
            stmt = stmt.where(SubscriptionCandidateItem.provider == provider)
        rows = (await self._session.execute(stmt)).all()
        items: List[Dict[str, Any]] = []
        for candidate, parent in rows:
            extra: Dict[str, Any] = {}
            if candidate.extraData:
                try:
                    loaded = json.loads(candidate.extraData)
                    if isinstance(loaded, dict):
                        extra = loaded
                except (json.JSONDecodeError, TypeError):
                    pass
            if subscription_type and extra.get("subscriptionType") != subscription_type:
                continue
            if status and extra.get("status", "waiting") != status:
                continue
            if keyword:
                haystack = f"{candidate.title or ''}{candidate.externalId or ''}".lower()
                if keyword.lower() not in haystack:
                    continue
            items.append({
                "id": candidate.id,
                "provider": candidate.provider,
                "externalId": candidate.externalId,
                "title": candidate.title,
                "parentId": candidate.parentId,
                "parentExternalId": parent.externalId,
                "subscriptionType": extra.get("subscriptionType"),
                "status": extra.get("status", "waiting"),
                "extraData": extra,
            })
        total = len(items)
        start = max(0, (page - 1) * page_size)
        return {"total": total, "list": items[start:start + page_size]}

    async def update_status(self, candidate_id: int, status: str) -> bool:
        """更新候选项处理状态；状态与平台扩展字段一起保存。"""
        candidate = await self.get_by_id(candidate_id)
        if not candidate:
            return False
        extra: Dict[str, Any] = {}
        if candidate.extraData:
            try:
                loaded = json.loads(candidate.extraData)
                if isinstance(loaded, dict):
                    extra = loaded
            except (json.JSONDecodeError, TypeError):
                pass
        extra["status"] = status
        candidate.extraData = json.dumps(extra, ensure_ascii=False)
        await self._session.flush()
        return True

    async def update_status_by_external_id(
        self, provider: str, external_id: str, status: str
    ) -> bool:
        """按源和候选项外部 ID 更新处理状态。"""
        stmt = select(SubscriptionCandidateItem).where(
            SubscriptionCandidateItem.provider == provider,
            SubscriptionCandidateItem.externalId == external_id,
        )
        candidate = (await self._session.execute(stmt)).scalar_one_or_none()
        if not candidate:
            return False
        return await self.update_status(candidate.id, status)

    async def delete_by_parent_id(self, parent_id: int) -> int:
        """删除某订阅目标的所有候选项（取消订阅/清理用）。

        why: 自 crud/subscription_candidate.delete_candidates_by_parent 迁入，
             去掉内部 commit，事务交上层控制。

        Args:
            parent_id: 父订阅目标 ID

        Returns:
            删除的条目数
        """
        stmt = sa_delete(SubscriptionCandidateItem).where(
            SubscriptionCandidateItem.parentId == parent_id
        )
        result = await self._session.execute(stmt)
        await self._session.flush()
        count = int(result.rowcount or 0)
        logger.info(f"delete_by_parent_id: parent_id={parent_id}, deleted={count}")
        return count

    async def delete(self, candidate_id: int) -> bool:
        """按主键删除候选项"""
        candidate = await self.get_by_id(candidate_id)
        if not candidate:
            return False

        await self._session.delete(candidate)
        await self._session.flush()
        return True
