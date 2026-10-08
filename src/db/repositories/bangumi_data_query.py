"""bangumi-data 查询仓储；结果均为脱离 Session 的值快照。"""
from types import SimpleNamespace
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from src.db.orm_models import BangumiDataIndex

class BangumiDataQueryRepository:
    """集中管理索引筛选与聚合。"""
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    @staticmethod
    def _snapshot(row: BangumiDataIndex) -> SimpleNamespace:
        """只复制列值，避免服务层持有 ORM 实例。"""
        return SimpleNamespace(**{attr.key: getattr(row, attr.key) for attr in BangumiDataIndex.__mapper__.column_attrs})

    async def count_rows(self) -> int:
        """返回索引总数。"""
        result = await self._session.execute(select(func.count(BangumiDataIndex.id)))
        return int(result.scalar() or 0)

    async def by_bangumi_id(self, bangumi_id: str) -> SimpleNamespace | None:
        """按 Bangumi ID 查询完整快照。"""
        result = await self._session.execute(select(BangumiDataIndex).where(BangumiDataIndex.bangumiId == bangumi_id).limit(1))
        row = result.scalar_one_or_none()
        return self._snapshot(row) if row is not None else None

    async def aliases_by_title(self, title: str) -> SimpleNamespace | None:
        """优先精确标题，其次搜索全语言别名。"""
        exact = await self._session.execute(select(BangumiDataIndex).where(or_(
            BangumiDataIndex.titleMain == title, BangumiDataIndex.titleZh == title,
            BangumiDataIndex.titleEn == title,
        )).limit(1))
        row = exact.scalar_one_or_none()
        if row is None:
            fuzzy = await self._session.execute(select(BangumiDataIndex).where(
                BangumiDataIndex.titlesAll.like(f"%{title}%")
            ).order_by(func.length(BangumiDataIndex.titleMain)).limit(1))
            row = fuzzy.scalar_one_or_none()
        return self._snapshot(row) if row is not None else None

    async def search_title(self, title: str, limit: int = 20) -> list[SimpleNamespace]:
        """按任一标题搜索有界候选快照。"""
        result = await self._session.execute(select(BangumiDataIndex).where(or_(
            BangumiDataIndex.titleMain == title, BangumiDataIndex.titleZh == title,
            BangumiDataIndex.titleEn == title, BangumiDataIndex.titlesAll.like(f"%{title}%"),
        )).order_by(func.length(BangumiDataIndex.titleMain)).limit(limit))
        return [self._snapshot(row) for row in result.scalars()]

    async def search_relaxed(self, head: str, tail: str, limit: int = 50) -> list[SimpleNamespace]:
        """根据标题前后半段选取错别字兜底候选。"""
        result = await self._session.execute(select(BangumiDataIndex).where(or_(
            BangumiDataIndex.titlesAll.like(f"%{head}%"), BangumiDataIndex.titlesAll.like(f"%{tail}%"),
        )).order_by(func.length(BangumiDataIndex.titleMain)).limit(limit))
        return [self._snapshot(row) for row in result.scalars()]

    async def airing_candidates(self) -> list[SimpleNamespace]:
        """返回 TV 放送信息快照供服务计算在播日程。"""
        result = await self._session.execute(select(BangumiDataIndex).where(
            BangumiDataIndex.type == "tv", BangumiDataIndex.broadcast.isnot(None),
            BangumiDataIndex.beginDate.isnot(None), BangumiDataIndex.bangumiId.isnot(None),
        ))
        return [self._snapshot(row) for row in result.scalars()]
